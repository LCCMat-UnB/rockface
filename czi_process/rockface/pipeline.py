"""
pipeline.py
==================================================================
End-to-end RockFace orchestration, plus the ``rockface`` command-line
entry point.

:func:`run_pipeline` is a module-level function rather than a method on
:class:`~rockface.czi.CZI`: it is a recipe that coordinates
``CZI``/``Patching``/``Mask`` together, not the responsibility of any
one of those objects alone. ``CZI.run(**kwargs)`` is a thin convenience
wrapper around it, so the one-line usage still exists:

    >>> import rockface
    >>> czi = rockface.CZI("sample.czi")
    >>> summary = czi.run(patch_size=4096, stride=3800)
"""

import argparse
import json
import logging
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Dict, List, Optional, Tuple

if TYPE_CHECKING:
    from .czi import CZI

logger = logging.getLogger(__name__)


def run_pipeline(
    czi: "CZI",
    coords: Optional[List[Tuple[int, int]]] = None,
    patch_size: int = 4096,
    stride: int = 3800,
    polarization_channels: Optional[List[int]] = None,
    normal_mode: str = "mean",
    patch_formats: Optional[List[str]] = None,
    mask_formats: Optional[List[str]] = None,
    max_workers: Optional[int] = None,
    on_progress: Optional[Callable[[str, float, str], None]] = None,
    verbose: bool = False,
    restitch_channels: Optional[List[str]] = None,
    write_manifest: bool = True,
    include_petrophysical: bool = False,
) -> Dict[str, Any]:
    """Run the full RockFace pipeline for one CZI slide, end to end.

    Stages:
        1. Save image metadata (``CZI.save_metadata``).
        2. Compute the patch grid and save patching metadata
           (``Patching.get_patches``, ``CZI.save_patching_data``).
        3. Extract patches: every polarization channel plus the
           synthesized normal image, for either the full grid or an
           explicit ``coords`` subset (``Patching.run``). Saving each
           patch happens as an inherent part of this stage -- there is
           no separate "save patching" step in this pipeline.
        4. Generate a pore mask for every patch just extracted
           (``Mask.run``) -- the same ``coords`` subset is passed
           through, so a partial run masks exactly what it patched,
           not whatever else happens to already be on disk.
        5. Optionally reassemble ("restitch") patches into full
           mosaics, one per requested channel, and save each mosaic as
           ``.npy`` + ``.png`` (``Patching.restitch``).
        6. Optionally write ``manifest.json`` describing every patch and
           mask (``Patching.generate_manifest``).

    Args:
        czi: An already-opened :class:`~rockface.czi.CZI` instance.
        coords: Explicit ``(y, x)`` patch coordinates to process,
            instead of the slide's full grid. Applies to both patch
            extraction and mask generation (see ``Patching.run`` and
            ``Mask.run``). ``None`` (default) processes the full grid.
        patch_size: Patch side length, in pixels.
        stride: Step between patches, in pixels.
        polarization_channels: Channels to extract. Defaults to every
            channel available on the slide.
        normal_mode: ``"mean"`` or ``"max"``.
        patch_formats: File formats to save per patch, relayed to
            ``Patching.run(formats=...)``. Any subset of
            ``{"npy", "npz", "png", "jpg"}``; defaults to
            ``["npy", "png"]`` when ``None``. If ``restitch_channels``
            is also given, ``patch_formats`` must include ``"npy"`` or
            ``"npz"`` -- checked up front (see Raises).
        mask_formats: File formats to save per mask, relayed to
            ``Mask.run(formats=...)``. Any subset of
            ``{"npy", "npz", "png", "jpg"}``; defaults to ``["png"]``
            when ``None``. If ``include_petrophysical`` is ``True``,
            ``mask_formats`` must include ``"png"`` -- checked up front
            (see Raises).
        max_workers: Worker process count for both the patching and
            masking stages. If ``None`` (default), falls back to the
            session-wide default set via ``rockface.set_max_workers()``,
            or if that is also unset, to ``min(16, os.cpu_count())``.
        on_progress: Optional ``on_progress(stage, progress, message)``
            callback, relayed to both ``Patching.run`` and ``Mask.run``.
            See those methods for the exact calling convention.
        verbose: If ``True``, relayed to both ``Patching.run`` and
            ``Mask.run`` to print rate-limited percentage progress to
            stderr, plus a short header line before each stage.
        restitch_channels: Channel names to restitch into full mosaics,
            e.g. ``["normal"]`` or ``["normal", "pol0"]``. ``None``
            (default) skips restitching -- full mosaics can be large,
            so this is opt-in. If ``coords`` was a partial subset, the
            resulting mosaic will have gaps -- see
            ``Patching.restitch``'s docstring.
        write_manifest: Whether to write ``manifest.json`` at the end.
        include_petrophysical: If ``True``, additionally computes
            porosity statistics via the out-of-scope
            ``legacy/petrophysical_properties.py`` module. Imported
            lazily; if it cannot be imported, this is skipped with a
            warning rather than failing the whole pipeline. Note: if
            ``coords`` was a partial subset (or a previous partial run
            left extra files on disk), these statistics reflect
            whatever patches/masks actually exist in the output
            directories at this point -- not necessarily the whole slide.

    Raises:
        ValueError: If ``restitch_channels`` is given but
            ``patch_formats`` excludes both ``"npy"`` and ``"npz"``
            (restitch would have nothing to read), or if
            ``include_petrophysical`` is ``True`` but ``mask_formats``
            excludes ``"png"`` (the legacy module reads
            ``{base}_mask.png`` specifically). Also propagates any
            ``ValueError`` from ``Patching.run``/``Mask.run`` for an
            unrecognized format string.

    Returns:
        A summary dict:
        ``{"image_metadata_path", "patching_metadata_path", "patching"
        (Patching.run's return value, includes "partial"), "masking"
        (Mask.run's return value, includes "missing"), "restitched"
        (per-channel {"npy_path", "png_path"}), "manifest_path",
        "manifest_count", "petrophysical" (stats dict, or None)}``.
    """
    if restitch_channels and patch_formats is not None and not ({"npy", "npz"} & set(patch_formats)):
        raise ValueError(
            "restitch_channels was given, but patch_formats excludes both 'npy' "
            "and 'npz' -- restitch() needs a raw array format to reassemble a "
            "mosaic from. Add 'npy' or 'npz' to patch_formats, or drop restitch_channels."
        )
    if include_petrophysical and mask_formats is not None and "png" not in mask_formats:
        raise ValueError(
            "include_petrophysical=True, but mask_formats excludes 'png' -- "
            "legacy/petrophysical_properties.py reads '{base}_mask.png' specifically. "
            "Add 'png' to mask_formats, or set include_petrophysical=False."
        )
    summary: Dict[str, Any] = {}

    # --- Stage 1: image metadata ---
    summary["image_metadata_path"] = czi.save_metadata()

    # --- Stage 2: prepare patching (grid + patching metadata) ---
    full_grid = czi.patching.get_patches(patch_size, stride)
    active_coords = coords if coords is not None else full_grid

    available = czi.overview["channels"]
    requested = polarization_channels if polarization_channels is not None else available
    channels = [c for c in requested if c in available] or available

    summary["patching_metadata_path"] = czi.save_patching_data(
        active_coords,
        patch_size=patch_size,
        stride=stride,
        polarization_channels=channels,
        normal_mode=normal_mode,
        full_grid_total=len(full_grid),
    )

    if verbose:
        scope = f"{len(active_coords)}/{len(full_grid)} patches (partial)" if coords is not None \
            else f"{len(full_grid)} patches (full grid)"
        print(f"[pipeline] stage 1/4: extracting {scope}...", file=sys.stderr)

    # --- Stage 3: extract patches (parallel) ---
    summary["patching"] = czi.patching.run(
        coords=active_coords,
        patch_size=patch_size,
        stride=stride,
        polarization_channels=channels,
        normal_mode=normal_mode,
        formats=patch_formats,
        max_workers=max_workers,
        on_progress=on_progress,
        verbose=verbose,
    )

    if verbose:
        print(f"[pipeline] stage 2/4: generating masks for {len(active_coords)} patch(es)...", file=sys.stderr)

    # --- Stage 4: generate masks (parallel), same coords subset as patching ---
    summary["masking"] = czi.mask.run(
        coords=active_coords,
        formats=mask_formats,
        max_workers=max_workers,
        on_progress=on_progress,
        verbose=verbose,
    )

    # --- Stage 5: restitch (optional) ---
    restitched: Dict[str, Any] = {}
    if restitch_channels:
        from PIL import Image

        for channel_name in restitch_channels:
            try:
                mosaic = czi.patching.restitch(stride=stride, channel=channel_name)
            except FileNotFoundError as exc:
                logger.warning("Skipping restitch for channel %r: %s", channel_name, exc)
                continue

            npy_path = czi.save_npy(mosaic, czi.output_dir / f"mosaic_{channel_name}.npy")
            if mosaic.ndim == 3 and mosaic.shape[-1] == 3:
                rgb = czi.convert_to_rgb(mosaic)
            else:
                rgb = mosaic
            png_path = czi.save_image(Image.fromarray(rgb), czi.output_dir / f"mosaic_{channel_name}.png", lossless=True)

            restitched[channel_name] = {"npy_path": npy_path, "png_path": png_path}

    summary["restitched"] = restitched

    # --- Stage 6: manifest (optional) ---
    if write_manifest:
        manifest_path = czi.output_dir / "manifest.json"
        count = czi.patching.generate_manifest(
            patch_dir=czi.output_dir / "patches",
            mask_dir=czi.output_dir / "masks",
            output_path=manifest_path,
        )
        summary["manifest_path"] = manifest_path
        summary["manifest_count"] = count

    # --- Optional: porosity statistics via the out-of-scope legacy module ---
    summary["petrophysical"] = None
    if include_petrophysical:
        summary["petrophysical"] = _run_petrophysical(czi, stride, summary["image_metadata_path"])

    return summary


def _run_petrophysical(czi: "CZI", stride: int, image_metadata_path: Path) -> Optional[Dict[str, Any]]:
    """Optionally compute porosity statistics via ``legacy/petrophysical_properties.py``.

    That module is out of scope for this package and lives outside the
    ``rockface`` package proper, so it is imported lazily and only when
    explicitly requested (``include_petrophysical=True``). It expects a
    single metadata dict containing ``physical_scaling.pixel_size_x_meters``
    -- which, after the image/patching metadata split (see
    ``CZI.save_metadata`` / ``CZI.save_patching_data``), lives only in
    ``image_metadata.json``. This loads exactly that file, not a merged
    dict, to match what ``calculate_petrophysical_stats`` expects.

    Args:
        czi: The slide being processed.
        stride: The stride used for this patching run (defines the
            non-overlapping counting window).
        image_metadata_path: Path to ``image_metadata.json``, as
            returned by ``CZI.save_metadata``.

    Returns:
        The stats dict from ``calculate_petrophysical_stats``, or
        ``None`` if the legacy module could not be imported.
    """
    try:
        import sys as _sys

        # legacy/ lives alongside the rockface package, not inside it --
        # add its parent to sys.path so it can be imported by name.
        legacy_parent = Path(__file__).resolve().parent.parent
        if str(legacy_parent) not in _sys.path:
            _sys.path.insert(0, str(legacy_parent))
        from legacy.petrophysical_properties import calculate_petrophysical_stats
    except ImportError:
        logger.warning(
            "include_petrophysical=True but legacy.petrophysical_properties "
            "could not be imported -- skipping porosity statistics."
        )
        return None

    with open(image_metadata_path, "r", encoding="utf-8") as file:
        image_metadata = json.load(file)

    stats = calculate_petrophysical_stats(
        patch_dir=czi.output_dir / "patches",
        mask_dir=czi.output_dir / "masks",
        stride=stride,
        metadata=image_metadata,
    )

    output_path = czi.output_dir / "petrophysical_results.json"
    with open(output_path, "w", encoding="utf-8") as file:
        json.dump(stats, file, indent=4)

    return stats


def cli_main(argv: Optional[List[str]] = None) -> int:
    """Command-line entry point for RockFace (``rockface`` console script).

    Example:
        .. code-block:: console

            $ rockface path/to/sample.czi --patch-size 4096 --stride 3800 --output ./output_project

    Args:
        argv: Argument list to parse. Defaults to ``sys.argv[1:]``.

    Returns:
        Process exit code: ``0`` on success, ``1`` on failure.
    """
    parser = argparse.ArgumentParser(
        prog="rockface",
        description=(
            "Process a Zeiss CZI petrographic thin-section slide: "
            "extract patches and generate pore-space masks."
        ),
    )
    parser.add_argument("czi_path", type=str, help="Path to the .czi slide file.")
    parser.add_argument(
        "--output", type=str, default="./output_project",
        help="Output base directory (default: ./output_project).",
    )
    parser.add_argument(
        "--patch-size", type=int, default=4096,
        help="Patch side length, in pixels (default: 4096).",
    )
    parser.add_argument(
        "--stride", type=int, default=3800,
        help="Step between patches, in pixels (default: 3800).",
    )
    parser.add_argument(
        "--normal-mode", choices=["mean", "max"], default="mean",
        help="Normal-image synthesis mode (default: mean).",
    )
    parser.add_argument(
        "--max-workers", type=int, default=None,
        help="Worker process count (default: min(16, cpu_count())).",
    )
    parser.add_argument(
        "--restitch", type=str, default=None,
        help="Comma-separated channel names to restitch into full mosaics, "
             "e.g. 'normal' or 'normal,pol0'. Omit to skip restitching.",
    )
    parser.add_argument(
        "--coords-file", type=str, default=None,
        help="Path to a JSON file containing a list of [y, x] patch coordinates "
             "to process, e.g. '[[0, 0], [0, 3800]]', instead of the full grid. "
             "Applies to both patch extraction and mask generation.",
    )
    parser.add_argument(
        "--patch-formats", type=str, default=None,
        help="Comma-separated formats to save per patch: any of npy,npz,png,jpg "
             "(default: npy,png). Restitching (--restitch) needs npy or npz.",
    )
    parser.add_argument(
        "--mask-formats", type=str, default=None,
        help="Comma-separated formats to save per mask: any of npy,npz,png,jpg "
             "(default: png).",
    )
    parser.add_argument(
        "--verbose", action="store_true",
        help="Print percentage progress for each stage, and enable INFO-level logging.",
    )

    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s",
    )

    from .czi import CZI  # local import: keeps `import rockface.pipeline` cheap when just parsing args

    try:
        czi = CZI(args.czi_path, output_base=args.output)
    except FileNotFoundError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    coords = None
    if args.coords_file:
        with open(args.coords_file, "r", encoding="utf-8") as file:
            raw_coords = json.load(file)
        coords = [tuple(pair) for pair in raw_coords]

    restitch_channels = args.restitch.split(",") if args.restitch else None
    patch_formats = args.patch_formats.split(",") if args.patch_formats else None
    mask_formats = args.mask_formats.split(",") if args.mask_formats else None

    summary = run_pipeline(
        czi,
        coords=coords,
        patch_size=args.patch_size,
        stride=args.stride,
        normal_mode=args.normal_mode,
        patch_formats=patch_formats,
        mask_formats=mask_formats,
        max_workers=args.max_workers,
        verbose=args.verbose,
        restitch_channels=restitch_channels,
    )

    patching_result = summary["patching"]
    masking_result = summary["masking"]

    print(f"Done. Slide ID: {czi.id}")
    print(
        f"Patches: {patching_result['ok']}/{patching_result['total']} ok"
        + (" (partial run)" if patching_result.get("partial") else "")
    )
    print(
        f"Masks:   {masking_result['ok']}/{masking_result['total']} ok"
        + (f", {masking_result['missing']} missing" if masking_result.get("missing") else "")
    )
    if summary.get("manifest_path"):
        print(f"Manifest: {summary['manifest_path']}")
    for channel_name, paths in summary.get("restitched", {}).items():
        print(f"Mosaic ({channel_name}): {paths['png_path']}")

    return 0


if __name__ == "__main__":
    sys.exit(cli_main())
