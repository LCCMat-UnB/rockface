"""
patching.py
==================================================================
The :class:`Patching` class -- CZI mosaic access and tiling for
RockFace.

Responsibilities:
  * Read the slide's mosaic overview (:meth:`Patching.read_overview`,
    called once by :class:`~rockface.czi.CZI` at construction time).
  * Compute a deterministic patch grid (:meth:`Patching.get_patches`).
  * Extract, for every patch coordinate, **every polarization channel**
    (typically 0-6) as its own raw image, in parallel
    (:meth:`Patching.run`). Channel 0 is the slide's own
    unpolarized/"normal" view -- no composite image is synthesized.
  * Reassemble extracted patches back into a full-resolution mosaic
    (:meth:`Patching.restitch`).
  * Describe a patching run for downstream consumers
    (:meth:`Patching.generate_manifest`).

Multiprocessing note
---------------------
:meth:`Patching.run` extracts patches in parallel with
``multiprocessing.Pool``. Pool workers cannot receive a ``Patching`` or
``CZI`` instance as an argument (the open native CZI file handle is not
picklable), so the actual per-patch work lives in the module-level
function :func:`_process_single_patch_worker` below, which re-opens its
own CZI handle and takes only plain, picklable arguments. That function
is **not part of the public API** -- use :meth:`Patching.run` (parallel,
whole slide) or :meth:`Patching.process_single_patch` (single patch,
synchronous, for interactive use / debugging).
"""

import json
import logging
import multiprocessing
import re
from functools import partial
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Dict, List, Optional, Tuple, Union

import numpy as np
from aicspylibczi import CziFile

from ._formats import validate_formats as _validate_formats
from ._progress import make_reporter
from .config import resolve_max_workers

if TYPE_CHECKING:
    from .czi import CZI

logger = logging.getLogger(__name__)

# Matches "patch_y{y}_x{x}_" at the start of any patch-family filename.
_PATCH_COORD_RE = re.compile(r"patch_y(\d+)_x(\d+)_")

# Output formats Patching.run() / process_single_patch() can save per patch:
#   "npy"/"npz" -- raw array, native channel order (lossless, for processing/ML).
#   "png"       -- RGB-converted, lossless (for visual inspection).
#   "jpg"       -- RGB-converted, lossy (smaller files, visual inspection only).
_VALID_PATCH_FORMATS = {"npy", "npz", "png", "jpg"}
_DEFAULT_PATCH_FORMATS = ["npy", "png"]  # matches the original codebase's behavior


def _load_patch_array(path: Path) -> np.ndarray:
    """Load a patch array from either ``.npy`` or ``.npz``.

    Module-level: used by :meth:`Patching.restitch` to accept whichever
    raw format was actually saved during extraction.
    """
    data = np.load(path)
    if path.suffix == ".npz":
        return data["arr_0"]
    return data


def find_channel_source(patch_dir: Union[str, Path], y: int, x: int, channel: int) -> Optional[Path]:
    """Locate one patch coordinate's raw file for a specific polarization channel.

    Module-level: shared by :meth:`Mask.run`/:meth:`Mask.process_patch`
    (via :mod:`rockface.masks`) and
    :meth:`Patching.restitch_with_mask_overlay`, to resolve
    ``patch_y{y}_x{x}_pol{channel}.npy``/``.npz`` -- typically channel
    ``0``, the slide's own unpolarized/normal view (see this module's
    docstring).

    Args:
        patch_dir: Directory containing extracted patch files.
        y: Patch row coordinate (as encoded in the filename).
        x: Patch column coordinate (as encoded in the filename).
        channel: Polarization channel index, e.g. ``0``.

    Returns:
        Path to ``patch_y{y}_x{x}_pol{channel}.npy`` if it exists,
        else ``patch_y{y}_x{x}_pol{channel}.npz`` if that exists
        instead, else ``None``.
    """
    patch_dir = Path(patch_dir)
    for suffix in ("npy", "npz"):
        candidate = patch_dir / f"patch_y{y}_x{x}_pol{channel}.{suffix}"
        if candidate.exists():
            return candidate
    return None


def _read_channel_region(handle: CziFile, region: Tuple[int, int, int, int], channel: int) -> np.ndarray:
    """Read one channel's pixel data for a region of the mosaic.

    Module-level (not a method): called both from
    :meth:`Patching.process_single_patch` and from the multiprocessing
    worker :func:`_process_single_patch_worker`, always against an
    explicit, already-open CZI handle -- never through ``self``, since
    the worker's handle belongs to a different process than any
    ``Patching`` instance's.

    Args:
        handle: An opened ``aicspylibczi.CziFile``.
        region: Absolute ``(x, y, width, height)`` region to read.
        channel: Channel index to read.

    Returns:
        The region's pixel array, with any color channel axis moved to
        the last position (``HxWx3`` or ``HxW``).
    """
    patch_data = handle.read_mosaic(region=region, scale_factor=1.0, C=channel)
    array = np.squeeze(patch_data)  # drop unit-length S/T/Z/... axes
    if array.ndim == 3 and array.shape[0] == 3:
        array = np.moveaxis(array, 0, -1)
    return array


def _process_single_patch_worker(
    coord: Tuple[int, int],
    czi_path: Union[str, Path],
    patch_size: int,
    bbox_origin: Tuple[int, int],
    output_dir: Union[str, Path],
    polarization_channels: List[int],
    formats: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Extract one patch at every requested polarization channel.

    Module-level by design: must stay picklable for
    ``multiprocessing.Pool``. Not part of the public API -- use
    :meth:`Patching.run` (parallel) or
    :meth:`Patching.process_single_patch` (single patch, in-process).

    Re-opens its own ``CziFile`` handle (a handle opened by a parent
    ``CZI`` instance cannot be shared across processes). The ``czi.py``
    helpers used here are imported locally, inside this function, both
    to avoid a circular import at module load time (``czi.py`` imports
    ``Patching`` from this module) and because each multiprocessing
    worker process re-executes this import on its own regardless.

    For each channel, saves whichever of ``formats`` were requested:
    ``{base}_pol{c}.npy``/``.npz`` (raw array, native channel order)
    and/or ``{base}_pol{c}.png``/``.jpg`` (RGB-converted), where
    ``base = "patch_y{y_rel}_x{x_rel}"``. No composite image is
    synthesized or saved -- channel ``0`` is the slide's own
    unpolarized/normal view and is used directly wherever a "normal"
    image is needed (see :mod:`rockface.masks`).

    Never raises: any exception is caught and returned as an error
    dict, so one bad patch does not stop the whole
    ``multiprocessing.Pool``.

    Args:
        coord: Patch coordinate ``(y_rel, x_rel)``, relative to the
            mosaic bounding box.
        czi_path: Path to the source .czi file.
        patch_size: Patch side length, in pixels.
        bbox_origin: The mosaic's global ``(x, y)`` origin.
        output_dir: Directory to write this patch's files into.
        polarization_channels: Requested channel indices. Channels not
            actually present in this CZI are dropped; if none of the
            requested channels are present, every available channel is
            used instead.
        formats: Which file formats to save per channel. Any subset of
            ``{"npy", "npz", "png", "jpg"}``. Defaults to
            ``["npy", "png"]`` (the original codebase's behavior) when
            not given by the caller.

    Returns:
        ``{"status": "ok", "channels": [...]}`` on success, or
        ``{"status": "error", "error": "...", "coord": coord}`` on failure.
    """
    from PIL import Image

    from .czi import (
        available_channels_from_handle,
        convert_array_to_rgb,
        pixel_format_from_handle,
        save_image_file,
        save_npy_array,
        save_npz_array,
    )

    active_formats = formats if formats is not None else _DEFAULT_PATCH_FORMATS
    want_npy = "npy" in active_formats
    want_npz = "npz" in active_formats
    want_png = "png" in active_formats
    want_jpg = "jpg" in active_formats
    want_rgb = want_png or want_jpg

    y_rel, x_rel = coord
    abs_x = bbox_origin[0] + x_rel
    abs_y = bbox_origin[1] + y_rel
    region = (abs_x, abs_y, patch_size, patch_size)
    base = f"patch_y{y_rel}_x{x_rel}"
    output_dir = Path(output_dir)

    try:
        handle = CziFile(str(czi_path))
        pixel_format = pixel_format_from_handle(handle)

        def _save(array: np.ndarray, name_stem: str) -> None:
            if want_npy:
                save_npy_array(array, output_dir / f"{name_stem}.npy")
            if want_npz:
                save_npz_array(array, output_dir / f"{name_stem}.npz")
            if want_rgb:
                rgb = convert_array_to_rgb(array, pixel_format=pixel_format)
                if want_png:
                    save_image_file(Image.fromarray(rgb), output_dir / f"{name_stem}.png", lossless=True)
                if want_jpg:
                    save_image_file(Image.fromarray(rgb), output_dir / f"{name_stem}.jpg", lossless=False)

        available = set(available_channels_from_handle(handle))
        channels = [c for c in polarization_channels if c in available]
        if not channels:
            channels = sorted(available)

        for channel in channels:
            array = _read_channel_region(handle, region, channel)
            _save(array, f"{base}_pol{channel}")

        return {"status": "ok", "channels": channels}
    except Exception as exc:  # noqa: BLE001 - deliberate: keep the Pool alive
        logger.error("Patch %s failed: %s", base, exc)
        return {"status": "error", "error": str(exc), "coord": coord}


class Patching:
    """Cuts a CZI slide into overlapping patches and reassembles them.

    Accessed via ``CZI.patching`` -- holds no state of its own beyond a
    back-reference to its parent :class:`~rockface.czi.CZI` instance;
    slide identity, paths, and overview metadata are all read from that
    parent.

    Example:
        >>> czi = rockface.CZI("sample.czi")
        >>> coords = czi.patching.get_patches(patch_size=4096, stride=3800)
        >>> result = czi.patching.run(patch_size=4096, stride=3800)
        >>> mosaic = czi.patching.restitch(stride=3800)
        >>> czi.mask.run(formats=["npy", "png"])
        >>> overlaid = czi.patching.restitch_with_mask_overlay(stride=3800)
    """

    def __init__(self, czi: "CZI") -> None:
        self._czi = czi

    def read_overview(self) -> Dict[str, Any]:
        """Read the slide's mosaic overview: bounding box, scale, channels.

        Formerly ``read_czi_overview`` in ``patching_engine.py``. Called
        once by ``CZI.__init__`` to populate ``CZI.overview`` -- not
        normally called again afterwards, but safe to call any time.

        Returns:
            A dict with ``bbox`` (``{x, y, w, h}``),
            ``pixel_size_x_meters``, ``pixel_size_y_meters``, ``dims``
            (dimension order string), and ``channels`` (available
            channel indices).

        Raises:
            Exception: Re-raises any error from the underlying CZI
                library after logging it -- there is no sensible
                fallback for an unreadable slide.
        """
        try:
            handle = self._czi._handle
            bbox = handle.get_mosaic_bounding_box()
            pixel_size_x, pixel_size_y = self._czi.size()
            channels = self._czi.get_available_channels()
            dims = getattr(handle, "dims", "") or ""

            return {
                "bbox": {"x": bbox.x, "y": bbox.y, "w": bbox.w, "h": bbox.h},
                "pixel_size_x_meters": pixel_size_x,
                "pixel_size_y_meters": pixel_size_y,
                "dims": dims,
                "channels": channels,
            }
        except Exception as exc:
            logger.error("Failed to read CZI overview for %s: %s", self._czi.path, exc)
            raise

    def get_patches(
        self,
        patch_size: int,
        stride: int,
        image_shape: Optional[Tuple[int, int]] = None,
    ) -> List[Tuple[int, int]]:
        """Generate deterministic patch coordinates, as ``(y, x)`` pairs.

        Slides the window across the image in steps of ``stride`` and,
        at the bottom/right edges, anchors the patch so it never
        crosses the image bounds -- producing controlled extra overlap
        on the last row/column instead of a truncated final patch.

        Args:
            patch_size: Patch side length, in pixels.
            stride: Step between patches, in pixels.
            image_shape: Optional ``(height, width)`` override. Defaults
                to this slide's own bounding box
                (``self._czi.overview["bbox"]``). Mainly useful for
                testing the grid math without a real .czi file.

        Returns:
            A sorted, deduplicated list of ``(y_start, x_start)`` tuples.
        """
        if image_shape is None:
            bbox = self._czi.overview["bbox"]
            image_shape = (bbox["h"], bbox["w"])

        height, width = image_shape[:2]
        coordinates = []
        y_start = 0

        for y in range(0, height, stride):
            for x in range(0, width, stride):
                y_start = max(0, min(y, height - patch_size))
                x_start = max(0, min(x, width - patch_size))
                coordinates.append((y_start, x_start))
                if x_start + patch_size >= width:
                    break
            if y_start + patch_size >= height:
                break

        return sorted(set(coordinates))

    def process_single_patch(
        self,
        coord: Tuple[int, int],
        patch_size: int = 4096,
        polarization_channels: Optional[List[int]] = None,
        output_dir: Optional[Union[str, Path]] = None,
        formats: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """Extract a single patch, synchronously, without a multiprocessing Pool.

        Convenience wrapper for interactive use or debugging one patch.
        For a full slide, use :meth:`run` instead, which parallelizes
        this same work across many patches.

        Args:
            coord: Patch coordinate ``(y_rel, x_rel)``, relative to the
                mosaic bounding box.
            patch_size: Patch side length, in pixels.
            polarization_channels: Channel indices to extract. Defaults
                to every channel available on the slide (typically
                ``0`` through ``6``). Each is saved as its own raw
                image -- channel ``0`` is the slide's unpolarized/
                "normal" view; no composite is synthesized.
            output_dir: Directory to write into. Defaults to
                ``CZI.output_dir / "patches"``.
            formats: Which file formats to save, any subset of
                ``{"npy", "npz", "png", "jpg"}``. Defaults to
                ``["npy", "png"]``. See :meth:`run` for details.

        Returns:
            Same shape as :func:`_process_single_patch_worker`'s return
            value: ``{"status": "ok", "channels": [...]}`` or
            ``{"status": "error", "error": "...", "coord": coord}``.
        """
        _validate_formats(formats, _VALID_PATCH_FORMATS)

        bbox = self._czi.overview["bbox"]
        channels = polarization_channels if polarization_channels is not None else self._czi.overview["channels"]
        target_dir = Path(output_dir) if output_dir is not None else self._czi.output_dir / "patches"
        target_dir.mkdir(parents=True, exist_ok=True)

        return _process_single_patch_worker(
            coord=coord,
            czi_path=self._czi.path,
            patch_size=patch_size,
            bbox_origin=(bbox["x"], bbox["y"]),
            output_dir=target_dir,
            polarization_channels=channels,
            formats=formats,
        )

    def run(
        self,
        coords: Optional[List[Tuple[int, int]]] = None,
        patch_size: int = 4096,
        stride: int = 3800,
        polarization_channels: Optional[List[int]] = None,
        formats: Optional[List[str]] = None,
        max_workers: Optional[int] = None,
        on_progress: Optional[Callable[[str, float, str], None]] = None,
        verbose: bool = False,
    ) -> Dict[str, Any]:
        """Extract patches of the slide, in parallel.

        By default extracts every patch of the full grid (see
        :meth:`get_patches`). Pass ``coords`` to restrict this to an
        explicit subset instead -- useful for re-running only a few
        failed patches, processing a specific region of interest, or
        trying the pipeline on a handful of patches before committing
        to a full slide. ``coords`` does not need to be grid-aligned:
        any list of ``(y, x)`` pairs is accepted. A coordinate outside
        the mosaic is not validated up front -- it is simply reported
        as a failed patch in the return value (see
        :func:`_process_single_patch_worker`, which never raises),
        rather than stopping the whole run.

        For every coordinate, extracts every requested polarization
        channel as its own raw image, saving each to
        ``CZI.output_dir / "patches"``. No composite "normal" image is
        synthesized -- channel ``0`` is the slide's own
        unpolarized/normal view (used directly by
        :meth:`~rockface.masks.Mask.run`).

        Args:
            coords: Explicit ``(y, x)`` coordinates to process. If
                ``None`` (default), the full grid from
                ``get_patches(patch_size, stride)`` is used.
            patch_size: Patch side length, in pixels.
            stride: Step between patches, in pixels. Only used to
                compute the grid when ``coords`` is ``None`` -- has no
                effect on extraction itself when an explicit ``coords``
                list is given, but still worth passing consistently if
                you plan to call :meth:`restitch` afterwards.
            polarization_channels: Channel indices to extract. Defaults
                to every channel available on the slide (typically
                ``0`` through ``6``). Channels not actually present
                are dropped per-patch (see
                :func:`_process_single_patch_worker`).
            formats: Which file formats to save per channel. Any subset
                of ``{"npy", "npz", "png", "jpg"}``:

                - ``"npy"``/``"npz"`` -- raw array, native channel
                  order (lossless; what :meth:`restitch` and most
                  downstream/ML processing need).
                - ``"png"`` -- RGB-converted, lossless (visual inspection).
                - ``"jpg"`` -- RGB-converted, lossy (smaller files,
                  visual inspection only).

                Defaults to ``["npy", "png"]`` when not given (matches
                the original codebase's behavior). Pass e.g. ``["npy"]``
                to skip PNGs entirely, or ``["png", "jpg"]`` to skip raw
                arrays -- but note :meth:`restitch` then has nothing to
                read from and will raise a clear error explaining that
                ``"npy"`` or ``"npz"`` must be included.
            max_workers: Number of worker processes for this call. If
                ``None`` (default), falls back to the session-wide
                default set via :func:`rockface.config.set_max_workers`
                (:func:`rockface.set_max_workers`), or if that is also
                unset, to ``min(16, os.cpu_count())``.
            on_progress: Optional callback ``on_progress(stage, progress,
                message)``, called after each patch completes (full
                resolution, every patch), with ``stage="patching"`` and
                ``progress`` in ``[0.0, 1.0]``.
            verbose: If ``True``, also prints a rate-limited percentage
                line to stderr as patches complete (at most once per
                whole percentage point reached), independent of
                ``on_progress`` -- use this for a quick "how far along
                is this" view without writing your own callback.

        Raises:
            ValueError: If ``formats`` contains anything outside
                ``{"npy", "npz", "png", "jpg"}``.

        Returns:
            ``{"total": int, "ok": int, "failed": int, "failed_coords":
            [...], "partial": bool}`` -- ``partial`` is ``True`` when
            ``coords`` was explicitly given, meaning this run may not
            cover the slide's full patch grid. Downstream steps
            (:meth:`restitch`, :meth:`generate_manifest`, and porosity
            statistics in ``legacy/petrophysical_properties.py``) all
            simply operate on whatever patches exist on disk, so a
            partial run naturally produces partial results there too --
            see :meth:`restitch`'s docstring for the specific effect on
            reassembled mosaics.
        """
        _validate_formats(formats, _VALID_PATCH_FORMATS)

        bbox = self._czi.overview["bbox"]
        available = self._czi.overview["channels"]
        requested = polarization_channels if polarization_channels is not None else available
        channels = [c for c in requested if c in available] or available

        active_coords = coords if coords is not None else self.get_patches(patch_size, stride)
        output_dir = self._czi.output_dir / "patches"
        output_dir.mkdir(parents=True, exist_ok=True)

        workers = resolve_max_workers(max_workers)
        worker_fn = partial(
            _process_single_patch_worker,
            czi_path=self._czi.path,
            patch_size=patch_size,
            bbox_origin=(bbox["x"], bbox["y"]),
            output_dir=output_dir,
            polarization_channels=channels,
            formats=formats,
        )

        total = len(active_coords)
        ok_count = 0
        failed_count = 0
        failed_coords = []
        report = make_reporter("patching", total, verbose=verbose, on_progress=on_progress)

        with multiprocessing.Pool(workers) as pool:
            for i, result in enumerate(pool.imap_unordered(worker_fn, active_coords), 1):
                if result.get("status") == "ok":
                    ok_count += 1
                else:
                    failed_count += 1
                    failed_coords.append(result.get("coord"))
                report(i, f"patch {i}/{total}")

        if failed_count:
            logger.warning("%d/%d patch(es) failed during extraction.", failed_count, total)

        return {
            "total": total,
            "ok": ok_count,
            "failed": failed_count,
            "failed_coords": failed_coords,
            "partial": coords is not None,
        }

    def generate_manifest(
        self,
        patch_dir: Union[str, Path],
        mask_dir: Union[str, Path],
        output_path: Union[str, Path],
        url_base: Optional[str] = None,
        normal_channel: int = 0,
    ) -> int:
        """Write a manifest describing every extracted patch and its mask.

        Formerly a free function in ``main.py``, moved here since it
        purely describes patching output.

        Args:
            patch_dir: Directory containing the extracted patch files.
            mask_dir: Directory containing the generated mask files.
            output_path: Destination path for ``manifest.json``.
            url_base: If given, entries reference
                ``f"{url_base}/{relative_path}"`` (reproduces the
                original ``http://localhost:8000/results/...``-style
                manifest for a UI server). If ``None`` (default),
                entries use plain relative filesystem paths
                (``"patches/patch_y0_x0_pol0.png"``), with no
                assumption of any server being involved.
            normal_channel: Which polarization channel is this slide's
                unpolarized/normal view, used as each entry's primary
                ``normal_url`` reference. Defaults to ``0``.

        Returns:
            The number of patch entries written.
        """
        patch_dir = Path(patch_dir)
        mask_dir = Path(mask_dir)

        def _ref(relative_path: str) -> str:
            return f"{url_base}/{relative_path}" if url_base else relative_path

        entries = []
        for normal_path in sorted(patch_dir.glob(f"*_pol{normal_channel}.png")):
            match = _PATCH_COORD_RE.search(normal_path.name)
            if not match:
                continue
            y, x = int(match.group(1)), int(match.group(2))
            base = f"patch_y{y}_x{x}"

            pol_files = sorted(patch_dir.glob(f"{base}_pol*.png"))

            entries.append({
                "id": base,
                "x": x,
                "y": y,
                "normal_url": _ref(f"patches/{normal_path.name}"),
                "mask_url": _ref(f"masks/{base}_mask.png"),
                "polarized_urls": [_ref(f"patches/{pf.name}") for pf in pol_files],
            })

        entries.sort(key=lambda item: (item["y"], item["x"]))

        manifest = {
            "slide_id": self._czi.id,
            "source_file": self._czi.path.name,
            "patches": entries,
        }

        output_path = Path(output_path)
        with open(output_path, "w", encoding="utf-8") as file:
            json.dump(manifest, file, indent=4)

        return len(entries)

    def restitch(
        self,
        patch_dir: Optional[Union[str, Path]] = None,
        stride: Optional[int] = None,
        channel: str = "pol0",
    ) -> np.ndarray:
        """Reassemble extracted patches into a single full-resolution mosaic.

        New capability -- nothing in the original codebase reassembled
        patches back into a full image. Uses the same non-overlapping
        stride window that ``petrophysical_properties.py`` (see
        ``legacy/``) already uses for pore counting: each patch
        contributes only its top-left ``[0:stride, 0:stride]`` region to
        the canvas, at the position implied by its filename coordinates
        (``patch_y{y}_x{x}``). This avoids double-writing the overlap
        band shared between adjacent patches (``patch_size - stride``
        pixels), giving seamless tiling.

        At the bottom/right edges, a patch may itself be smaller than a
        full ``stride x stride`` block, because :meth:`get_patches`
        anchors the last row/column to the image bounds -- this is
        handled by clamping each patch's contribution to whatever
        canvas space actually remains.

        If ``patch_dir`` only contains a subset of the full grid (for
        example, after a partial ``run(coords=...)`` call), the
        returned mosaic will have zero-filled gaps at every grid
        position that has no matching file on disk -- restitch does
        not know which positions were skipped on purpose versus never
        extracted, so it simply reassembles whatever it finds.

        Args:
            patch_dir: Directory containing
                ``"patch_y{y}_x{x}_{channel}.npy"`` (or ``.npz``) files.
                Defaults to ``CZI.output_dir / "patches"``.
            stride: The stride used during patch extraction. Required --
                not inferred from the files themselves, since two
                adjacent patch filenames alone don't disambiguate stride
                from patch size.
            channel: Which polarization channel to restitch, as a
                ``"pol{c}"`` name, e.g. ``"pol0"`` (default -- the
                slide's own unpolarized/normal view) or ``"pol3"``.

        Returns:
            A single array of shape ``(full_height_px, full_width_px)``
            or ``(full_height_px, full_width_px, C)``, dtype matching
            the source patches.

        Raises:
            ValueError: If ``stride`` is not given.
            FileNotFoundError: If no ``.npy``/``.npz`` files matching
                ``channel`` are found. Restitching needs a raw array
                format -- if patches were only saved as ``"png"``/
                ``"jpg"`` (see ``Patching.run(formats=...)``), this is
                raised with a message pointing at that.

        Warning:
            Full-resolution mosaics for large slides (tens of thousands
            of pixels per side, several channels) can occupy multiple
            gigabytes of memory. Consider restitching one channel at a
            time, or downsampling the source patches first, for very
            large slides.
        """
        if stride is None:
            raise ValueError("stride is required (the stride used during patch extraction).")

        patch_dir = Path(patch_dir) if patch_dir is not None else self._czi.output_dir / "patches"

        bbox = self._czi.overview["bbox"]
        height, width = bbox["h"], bbox["w"]

        pattern = re.compile(rf"patch_y(\d+)_x(\d+)_{re.escape(channel)}\.(npy|npz)$")
        # Prefer .npy over .npz when both exist for the same coordinate
        # (arbitrary but consistent tie-break) -- iterate .npz first so a
        # later .npy overwrites it in the dict.
        by_coord: Dict[Tuple[int, int], Path] = {}
        for suffix in ("npz", "npy"):
            for path in patch_dir.glob(f"patch_y*_x*_{channel}.{suffix}"):
                match = pattern.search(path.name)
                if match:
                    by_coord[(int(match.group(1)), int(match.group(2)))] = path

        if not by_coord:
            raise FileNotFoundError(
                f"No 'patch_y{{y}}_x{{x}}_{channel}.npy' or '.npz' files found in "
                f"{patch_dir}. restitch() needs a raw array format saved during "
                f"patching -- make sure Patching.run(formats=[...]) included "
                f"'npy' or 'npz' (not just 'png'/'jpg')."
            )

        canvas = None
        for (y, x), path in by_coord.items():
            array = _load_patch_array(path)

            if canvas is None:
                shape = (height, width) if array.ndim == 2 else (height, width, array.shape[-1])
                canvas = np.zeros(shape, dtype=array.dtype)

            eff_h = min(stride, height - y)
            eff_w = min(stride, width - x)
            canvas[y:y + eff_h, x:x + eff_w] = array[:eff_h, :eff_w]

        return canvas

    def restitch_with_mask_overlay(
        self,
        patch_dir: Optional[Union[str, Path]] = None,
        mask_dir: Optional[Union[str, Path]] = None,
        stride: Optional[int] = None,
        source_channel: int = 0,
        color: Tuple[int, int, int] = (0, 255, 0),
        filled_mode: bool = True,
        thickness: int = 2,
    ) -> np.ndarray:
        """Reassemble the full slide with its pore mask drawn on top.

        This is the "whole-slide reconstruction with the mask
        reconstruction overlaid" variant: restitches the
        ``source_channel`` mosaic (:meth:`restitch`) and the mask
        mosaic (from ``{base}_mask.npy``/``.npz`` files saved by
        :meth:`~rockface.masks.Mask.run`), then composites the mask on
        top as one combined RGB image. For a single patch instead of
        the whole slide, see
        :meth:`~rockface.masks.Mask.overlay_on_patch`.

        Args:
            patch_dir: Directory containing extracted patch files
                (``patch_y{y}_x{x}_pol{c}.npy`` etc). Defaults to
                ``CZI.output_dir / "patches"``.
            mask_dir: Directory containing mask files
                (``patch_y{y}_x{x}_mask.npy``/``.npz``, as saved by
                ``Mask.run(formats=[...])`` with ``"npy"`` or ``"npz"``
                included). Defaults to ``CZI.output_dir / "masks"``.
            stride: The stride used during patch extraction. Required,
                same as :meth:`restitch`.
            source_channel: Which polarization channel to restitch as
                the base image the mask is drawn on. Defaults to
                ``0``, the slide's own unpolarized/normal view --
                match this to whatever channel :meth:`~rockface.masks.Mask.run`
                used to generate the masks being overlaid (also ``0``
                by default).
            color: Overlay color for the pore mask.
            filled_mode: If ``True`` (default here -- unlike
                :meth:`~rockface.masks.Mask.generate_transparent_overlay`'s
                own default), fills the masked region solid rather than
                just its contours; solid fill reads more clearly at
                full-slide scale than thin contour lines do.
            thickness: Contour line thickness (only used when
                ``filled_mode=False``).

        Returns:
            An RGB ``uint8`` array (``full_height_px``,
            ``full_width_px``, 3): the restitched ``source_channel``
            mosaic with the restitched pore mask drawn on top.

        Raises:
            ValueError: If ``stride`` is not given.
            FileNotFoundError: If no source-channel or no mask files
                are found to restitch (see :meth:`restitch`'s
                ``Raises`` for the source-channel case; the mask case
                requires ``Mask.run(formats=[...])`` to have included
                ``"npy"`` or ``"npz"``, not just ``"png"``/``"jpg"``).

        Warning:
            Same memory caveat as :meth:`restitch`: full-resolution
            mosaics for large slides can occupy multiple gigabytes of
            RAM -- here, twice over (source-channel mosaic + mask
            mosaic) plus the composited result.
        """
        from .czi import convert_array_to_rgb
        from .masks import composite_overlay_on_image, generate_transparent_overlay_array

        if stride is None:
            raise ValueError("stride is required (the stride used during patch extraction).")

        mask_dir = Path(mask_dir) if mask_dir is not None else self._czi.output_dir / "masks"

        base_mosaic = self.restitch(patch_dir=patch_dir, stride=stride, channel=f"pol{source_channel}")

        # Mask files are named "{base}_mask.*", not "{base}_{channel}.*"
        # like patches -- restitch() can't be reused directly for them,
        # so the same non-overlapping-window assembly is repeated here
        # against the mask_dir / "*_mask.{npy,npz}" naming convention.
        bbox = self._czi.overview["bbox"]
        height, width = bbox["h"], bbox["w"]
        mask_pattern = re.compile(r"patch_y(\d+)_x(\d+)_mask\.(npy|npz)$")
        by_coord: Dict[Tuple[int, int], Path] = {}
        for suffix in ("npz", "npy"):
            for path in mask_dir.glob(f"patch_y*_x*_mask.{suffix}"):
                match = mask_pattern.search(path.name)
                if match:
                    by_coord[(int(match.group(1)), int(match.group(2)))] = path

        if not by_coord:
            raise FileNotFoundError(
                f"No 'patch_y{{y}}_x{{x}}_mask.npy' or '.npz' files found in "
                f"{mask_dir}. restitch_with_mask_overlay() needs raw mask arrays -- "
                f"make sure Mask.run(formats=[...]) included 'npy' or 'npz' "
                f"(not just 'png'/'jpg')."
            )

        mask_canvas = np.zeros((height, width), dtype=np.uint8)
        for (y, x), path in by_coord.items():
            array = _load_patch_array(path)
            eff_h = min(stride, height - y)
            eff_w = min(stride, width - x)
            mask_canvas[y:y + eff_h, x:x + eff_w] = array[:eff_h, :eff_w]

        pixel_format = self._czi._pixel_format()
        base_rgb = convert_array_to_rgb(base_mosaic, pixel_format=pixel_format)
        overlay = generate_transparent_overlay_array(
            mask_canvas, color=color, filled_mode=filled_mode, thickness=thickness
        )
        return composite_overlay_on_image(base_rgb, overlay)
