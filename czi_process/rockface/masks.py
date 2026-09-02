"""
masks.py
==================================================================
The :class:`Mask` class -- pore-space segmentation for RockFace.

Detects pore space (blue/cyan epoxy resin impregnation, standard
LCCMat thin-section preparation) in a patch's channel-0 image --
the slide's own unpolarized/"normal" view -- and produces a
transparent overlay suitable for a digital mosaic viewer. Masking is
never performed on a polarized channel (channel != 0): pore contrast
against the resin is only reliable under unpolarized light, so this
is a fixed domain rule, not a configurable option left to guesswork
(see :meth:`Mask.run`'s ``source_channel``, which exists for the rare
case a caller explicitly wants a different fixed channel, and still
defaults to ``0``).

Multiprocessing note
---------------------
Like :mod:`rockface.patching`, :meth:`Mask.run` parallelizes mask
generation across patches with ``multiprocessing.Pool``. The per-patch
work lives in the module-level function :func:`_process_patch_worker`,
which takes only plain, picklable arguments -- see that module's
docstring for the full rationale. It is **not part of the public API**
-- use :meth:`Mask.run` or :meth:`Mask.process_patch`.
"""

import logging
import multiprocessing
import re
from functools import partial
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Dict, List, Optional, Tuple, Union

import cv2
import numpy as np

from ._formats import validate_formats as _validate_formats
from ._progress import make_reporter
from .config import resolve_max_workers

if TYPE_CHECKING:
    from .czi import CZI

logger = logging.getLogger(__name__)

# Output formats Mask.run() / process_patch() can save per mask:
#   "npy"/"npz" -- the raw binary mask array (0/255, HxW), lossless.
#   "png"       -- the transparent RGBA overlay (visual inspection / UI).
#   "jpg"       -- the overlay flattened onto a solid background (JPEG has
#                  no alpha channel, so transparency cannot be preserved).
_VALID_MASK_FORMATS = {"npy", "npz", "png", "jpg"}
_DEFAULT_MASK_FORMATS = ["png"]  # matches the original codebase's behavior


def _flatten_rgba_to_rgb(rgba: np.ndarray, background: Tuple[int, int, int] = (0, 0, 0)) -> np.ndarray:
    """Composite an RGBA array onto a solid background, dropping alpha.

    JPEG has no alpha channel, so saving the mask overlay as ``.jpg``
    requires flattening transparency first. Non-pore (transparent)
    areas become solid ``background`` (black by default); pore areas
    keep the overlay's color at full opacity.

    Args:
        rgba: An RGBA ``uint8`` array (``HxWx4``), as produced by
            :func:`generate_transparent_overlay_array`.
        background: The RGB color to show through transparent areas.

    Returns:
        An RGB ``uint8`` array (``HxWx3``).
    """
    rgb = rgba[..., :3].astype(np.float32)
    alpha = rgba[..., 3:4].astype(np.float32) / 255.0
    bg = np.array(background, dtype=np.float32)
    flattened = rgb * alpha + bg * (1 - alpha)
    return np.clip(flattened, 0, 255).astype(np.uint8)


def composite_overlay_on_image(base_rgb: np.ndarray, overlay_rgba: np.ndarray) -> np.ndarray:
    """Alpha-composite a transparent mask overlay on top of an RGB image.

    Module-level (not a method): shared by :meth:`Mask.overlay_on_patch`
    and :meth:`Patching.restitch_with_mask_overlay`. This is the
    "mask drawn on top of the patch/slide image" compositing that
    :func:`_flatten_rgba_to_rgb` deliberately does not do (that
    function flattens onto a *solid background color*, for JPEG
    export; this flattens onto an *underlying image*, for visual
    inspection of where pores sit within the rock texture).

    Args:
        base_rgb: The underlying image to draw on top of (``HxWx3``,
            ``uint8``, RGB order) -- typically a channel-0 patch or a
            restitched mosaic, already converted via
            :meth:`~rockface.czi.CZI.convert_to_rgb`.
        overlay_rgba: The transparent overlay to draw
            (``HxWx4``, ``uint8``), as produced by
            :func:`generate_transparent_overlay_array`. Must have the
            same height/width as ``base_rgb``.

    Returns:
        An RGB ``uint8`` array (``HxWx3``): ``base_rgb`` with the
        overlay's pore-marking pixels drawn on top at full color,
        everywhere else unchanged.

    Raises:
        ValueError: If ``base_rgb`` and ``overlay_rgba`` have
            different height/width.
    """
    if base_rgb.shape[:2] != overlay_rgba.shape[:2]:
        raise ValueError(
            f"base_rgb and overlay_rgba must have matching height/width, got "
            f"{base_rgb.shape[:2]} vs {overlay_rgba.shape[:2]}."
        )
    base = base_rgb.astype(np.float32)
    overlay_rgb = overlay_rgba[..., :3].astype(np.float32)
    alpha = overlay_rgba[..., 3:4].astype(np.float32) / 255.0
    composited = overlay_rgb * alpha + base * (1 - alpha)
    return np.clip(composited, 0, 255).astype(np.uint8)


def generate_pore_mask_array(bgr_array: np.ndarray) -> np.ndarray:
    """Segment resin-impregnated pore space (blue/cyan) in a BGR image.

    Module-level (not a method): shared by :meth:`Mask.generate_pore_mask`
    and the multiprocessing worker :func:`_process_patch_worker`.

    Strategy:
        1. Gaussian blur to reduce noise.
        2. Convert to HSV and threshold by hue (cyan/blue resin range).
        3. Refine by requiring a minimum saturation*value product
           (discards dark/unsaturated false positives).
        4. Morphological opening to remove small isolated noise.

    Args:
        bgr_array: Patch image in **BGR** order (``HxWx3``, ``uint8``).

    Returns:
        A binary ``uint8`` mask (``0`` or ``255``), where ``255``
        marks pore space.
    """
    blurred = cv2.GaussianBlur(bgr_array, (5, 5), 0)
    hsv = cv2.cvtColor(blurred, cv2.COLOR_BGR2HSV)
    hue, saturation, value = cv2.split(hsv)

    # Hue threshold: typical cyan/blue range of the impregnation resin.
    lower_hue, upper_hue = 75, 125
    coarse_mask = cv2.inRange(hue, lower_hue, upper_hue)

    # Refine by S*V: discard weakly saturated / dark pixels.
    saturation_norm = saturation.astype(float) / 255.0
    value_norm = value.astype(float) / 255.0
    sv_product = saturation_norm * value_norm

    min_sv = 0.1
    refined_mask = np.where((coarse_mask > 0) & (sv_product >= min_sv), 255, 0).astype(np.uint8)

    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))
    return cv2.morphologyEx(refined_mask, cv2.MORPH_OPEN, kernel)


def generate_transparent_overlay_array(
    mask: np.ndarray,
    color: Tuple[int, int, int] = (0, 255, 0),
    filled_mode: bool = False,
    thickness: int = 2,
) -> np.ndarray:
    """Build a transparent RGBA overlay from a binary mask.

    Module-level (not a method): shared by
    :meth:`Mask.generate_transparent_overlay` and the multiprocessing
    worker :func:`_process_patch_worker`.

    In contour mode, segments that lie exactly on the patch border are
    skipped, to avoid drawing artificial seam lines at mosaic tile
    joins.

    Args:
        mask: Binary mask (``HxW``, values ``0``/``255``).
        color: BGR color for the overlay.
        filled_mode: If ``True``, fills the masked region solid; if
            ``False`` (default), draws only the region's contours.
        thickness: Contour line thickness (contour mode only).

    Returns:
        An RGBA ``uint8`` array (``HxWx4``) with a transparent background.
    """
    height, width = mask.shape[:2]
    overlay = np.zeros((height, width, 4), dtype=np.uint8)
    bgra_color = (*color, 255)

    if filled_mode:
        overlay[mask == 255] = bgra_color
    else:
        contours, _ = cv2.findContours(mask, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
        for contour in contours:
            for i in range(len(contour)):
                pt1, pt2 = contour[i][0], contour[(i + 1) % len(contour)][0]
                on_border = (
                    (pt1[0] <= 0 and pt2[0] <= 0)
                    or (pt1[0] >= width - 1 and pt2[0] >= width - 1)
                    or (pt1[1] <= 0 and pt2[1] <= 0)
                    or (pt1[1] >= height - 1 and pt2[1] >= height - 1)
                )
                if not on_border:
                    cv2.line(overlay, tuple(pt1), tuple(pt2), bgra_color, thickness)

    return overlay


def _process_patch_worker(
    source_npy_path: Union[str, Path],
    mask_dir: Union[str, Path],
    formats: Optional[List[str]] = None,
) -> bool:
    """Generate and save the pore mask for a single patch.

    Module-level by design: must stay picklable for
    ``multiprocessing.Pool``. Not part of the public API -- use
    :meth:`Mask.run` (parallel) or :meth:`Mask.process_patch` (single
    patch, in-process).

    Loads a source patch array (a single polarization channel's
    ``patch_y{y}_x{x}_pol{c}.npy`` file -- normally channel 0, the
    slide's own unpolarized/normal view; see :meth:`Mask.run`'s
    ``source_channel``), segments it, and saves whichever of
    ``formats`` were requested as ``{base}_mask.*`` -- ``base`` derived
    by stripping the trailing ``_pol{c}`` suffix from the source
    filename, so the mask is always named after its patch coordinate
    alone (``patch_y{y}_x{x}_mask.*``).

    Never raises: any exception is caught, logged, and ``False`` is
    returned, so one bad patch does not stop the whole
    ``multiprocessing.Pool``.

    Args:
        source_npy_path: Path to a ``patch_..._pol{c}.npy`` file
            (normally channel 0).
        mask_dir: Directory to write the mask file(s) into.
        formats: Which file formats to save, any subset of
            ``{"npy", "npz", "png", "jpg"}``. Defaults to ``["png"]``
            when not given.

    Returns:
        ``True`` on success, ``False`` on failure.
    """
    from PIL import Image

    from .czi import save_image_file, save_npy_array, save_npz_array

    active_formats = formats if formats is not None else _DEFAULT_MASK_FORMATS
    want_npy = "npy" in active_formats
    want_npz = "npz" in active_formats
    want_png = "png" in active_formats
    want_jpg = "jpg" in active_formats

    source_npy_path = Path(source_npy_path)
    mask_dir = Path(mask_dir)
    base = re.sub(r"_pol\d+$", "", source_npy_path.stem)

    try:
        channel_array = np.load(source_npy_path)
        mask = generate_pore_mask_array(channel_array)

        if want_npy:
            save_npy_array(mask, mask_dir / f"{base}_mask.npy")
        if want_npz:
            save_npz_array(mask, mask_dir / f"{base}_mask.npz")
        if want_png or want_jpg:
            overlay = generate_transparent_overlay_array(mask, filled_mode=True)
            if want_png:
                save_image_file(Image.fromarray(overlay), mask_dir / f"{base}_mask.png", lossless=True)
            if want_jpg:
                flattened = _flatten_rgba_to_rgb(overlay)
                save_image_file(Image.fromarray(flattened), mask_dir / f"{base}_mask.jpg", lossless=False)

        return True
    except Exception as exc:  # noqa: BLE001 - deliberate: keep the Pool alive
        logger.error("Mask generation failed for %s: %s", source_npy_path.name, exc)
        return False


class Mask:
    """Generates pore-space segmentation masks from normal-light patches.

    Accessed via ``CZI.mask``. Pore detection assumes blue/cyan epoxy
    resin impregnation under polarized light (standard LCCMat
    thin-section preparation).

    Example:
        >>> czi = rockface.CZI("sample.czi")
        >>> czi.patching.run()
        >>> result = czi.mask.run()
    """

    def __init__(self, czi: "CZI") -> None:
        self._czi = czi

    def generate_pore_mask(self, bgr_array: np.ndarray) -> np.ndarray:
        """Segment resin-impregnated pore space (blue/cyan) in a BGR image.

        Formerly ``generate_pore_mask`` in ``image_functions.py``.

        Args:
            bgr_array: Patch image in **BGR** order (``HxWx3``, ``uint8``).

        Returns:
            A binary ``uint8`` mask (``0`` or ``255``), where ``255``
            marks pore space.
        """
        return generate_pore_mask_array(bgr_array)

    def generate_transparent_overlay(
        self,
        mask: np.ndarray,
        color: Tuple[int, int, int] = (0, 255, 0),
        filled_mode: bool = False,
        thickness: int = 2,
    ) -> np.ndarray:
        """Build a transparent RGBA overlay from a binary mask.

        Formerly ``generate_transparent_overlay`` in
        ``image_functions.py``.

        Args:
            mask: Binary mask (``HxW``, values ``0``/``255``).
            color: BGR color for the overlay.
            filled_mode: If ``True``, fills the masked region solid; if
                ``False`` (default), draws only the region's contours.
            thickness: Contour line thickness (contour mode only).

        Returns:
            An RGBA ``uint8`` array (``HxWx4``) with a transparent background.
        """
        return generate_transparent_overlay_array(mask, color=color, filled_mode=filled_mode, thickness=thickness)

    def overlay_on_patch(
        self,
        channel_array: np.ndarray,
        mask: Optional[np.ndarray] = None,
        color: Tuple[int, int, int] = (0, 255, 0),
        filled_mode: bool = False,
        thickness: int = 2,
        pixel_format: str = "unknown",
    ) -> np.ndarray:
        """Composite a pore mask directly on top of one patch's image.

        This is the "single patch with its mask overlaid" variant --
        one RGB image you can look at directly, rather than a separate
        transparent-overlay file next to the plain patch. For the
        whole-slide equivalent, see
        :meth:`~rockface.patching.Patching.restitch_with_mask_overlay`.

        Args:
            channel_array: The patch's polarization-channel array (as
                saved to ``patch_y{y}_x{x}_pol{c}.npy``), in the
                slide's native channel order. Normally channel 0 --
                the slide's own unpolarized/normal view -- matching
                whichever channel the mask was segmented from (see
                :meth:`Mask.run`'s ``source_channel``).
            mask: A binary mask (``HxW``, ``0``/``255``) already
                computed for this patch, e.g. via
                :meth:`generate_pore_mask`. If ``None`` (default), it
                is computed from ``channel_array`` directly (requires
                ``channel_array`` to already be in BGR order, matching
                :meth:`generate_pore_mask`'s input convention).
            color: Overlay color, in the same channel-order convention
                as :meth:`generate_transparent_overlay`.
            filled_mode: If ``True``, fills the masked region solid; if
                ``False`` (default), draws only region contours.
            thickness: Contour line thickness (contour mode only).
            pixel_format: The slide's ``PixelType`` metadata value
                (see :meth:`~rockface.czi.CZI._pixel_format`), used to
                convert ``channel_array`` to RGB before compositing.
                Pass ``czi._pixel_format()``, or leave as ``"unknown"``
                to use the safe BGR-assumption fallback (see
                :func:`rockface.czi.convert_array_to_rgb`).

        Returns:
            An RGB ``uint8`` array (``HxWx3``): the patch's channel
            image with the mask drawn on top.
        """
        from .czi import convert_array_to_rgb

        if mask is None:
            mask = generate_pore_mask_array(channel_array)
        overlay = generate_transparent_overlay_array(mask, color=color, filled_mode=filled_mode, thickness=thickness)
        base_rgb = convert_array_to_rgb(channel_array, pixel_format=pixel_format)
        return composite_overlay_on_image(base_rgb, overlay)

    def process_patch(
        self,
        source_npy_path: Union[str, Path],
        mask_dir: Union[str, Path],
        formats: Optional[List[str]] = None,
    ) -> bool:
        """Generate and save the pore mask for a single patch, synchronously.

        Formerly ``mask_worker`` in ``main.py`` (renamed -- "worker" no
        longer fits once this is a class method rather than a bare
        multiprocessing target). Convenience wrapper for interactive
        use or debugging one patch; for a full slide, use :meth:`run`.

        Args:
            source_npy_path: Path to a ``patch_..._pol{c}.npy`` file
                (normally channel 0 -- the slide's own
                unpolarized/normal view).
            mask_dir: Directory to write the mask file(s) into. Created
                (with parents) if it does not already exist.
            formats: Which file formats to save, any subset of
                ``{"npy", "npz", "png", "jpg"}``. Defaults to
                ``["png"]``. See :meth:`run` for details.

        Returns:
            ``True`` on success, ``False`` on failure.
        """
        _validate_formats(formats, _VALID_MASK_FORMATS)
        mask_dir = Path(mask_dir)
        mask_dir.mkdir(parents=True, exist_ok=True)
        return _process_patch_worker(source_npy_path, mask_dir, formats=formats)

    def run(
        self,
        coords: Optional[List[Tuple[int, int]]] = None,
        patch_dir: Optional[Union[str, Path]] = None,
        mask_dir: Optional[Union[str, Path]] = None,
        source_channel: int = 0,
        formats: Optional[List[str]] = None,
        max_workers: Optional[int] = None,
        on_progress: Optional[Callable[[str, float, str], None]] = None,
        verbose: bool = False,
    ) -> Dict[str, Any]:
        """Generate pore masks for extracted patches, in parallel.

        This always operates "per patch", never on the whole slide at
        once: masking only ever runs against a single polarization
        channel's image that ``Patching`` has already extracted to
        disk -- never against a polarized channel (see
        ``source_channel`` below). By default, every
        ``*_pol{source_channel}.npy`` file already present in
        ``patch_dir`` is processed. Pass ``coords`` to restrict this to
        an explicit subset instead -- for example, the same subset just
        extracted by a partial ``Patching.run(coords=...)`` call, or to
        re-mask a handful of patches without touching the rest.

        Args:
            coords: Explicit ``(y, x)`` coordinates to mask. Each is
                mapped to ``patch_dir / "patch_y{y}_x{x}_pol{source_channel}.npy"``
                (or ``.npz``); a coordinate with no matching file on
                disk is counted under ``"missing"`` in the return value
                and skipped -- this is not treated as a failure, since
                it usually just means that patch/channel hasn't been
                extracted yet. If ``None`` (default), every
                ``*_pol{source_channel}.npy`` file found in
                ``patch_dir`` is used.
            patch_dir: Directory containing ``*_pol{source_channel}.npy``
                files. Defaults to ``CZI.output_dir / "patches"``.
            mask_dir: Directory to write mask file(s) into. Defaults to
                ``CZI.output_dir / "masks"``.
            source_channel: Which polarization channel to segment masks
                from. Defaults to ``0``, the slide's own
                unpolarized/normal view -- this is a fixed rule of the
                segmentation method (pore contrast against the resin is
                only reliable under unpolarized light), not a
                free-form choice: pass a different value only if your
                slide's unpolarized channel is genuinely indexed
                differently, never to mask a polarized channel for its
                own sake.
            formats: Which file formats to save per mask. Any subset of
                ``{"npy", "npz", "png", "jpg"}``:

                - ``"npy"``/``"npz"`` -- the raw binary mask array
                  (0/255, ``HxW``), lossless.
                - ``"png"`` -- the transparent RGBA overlay, lossless
                  (visual inspection / UI). This is the format
                  ``legacy/petrophysical_properties.py`` reads
                  (``{base}_mask.png``) -- include it if you plan to
                  compute porosity statistics from this run.
                - ``"jpg"`` -- the overlay flattened onto a solid black
                  background (JPEG has no alpha channel, so
                  transparency cannot be preserved).

                Defaults to ``["png"]`` when not given (matches the
                original codebase's behavior).
            max_workers: Number of worker processes for this call. If
                ``None`` (default), falls back to the session-wide
                default set via :func:`rockface.config.set_max_workers`
                (:func:`rockface.set_max_workers`), or if that is also
                unset, to ``min(16, os.cpu_count())``.
            on_progress: Optional callback ``on_progress(stage, progress,
                message)``, called after each mask completes (full
                resolution, every patch), with ``stage="masking"`` and
                ``progress`` in ``[0.0, 1.0]``.
            verbose: If ``True``, also prints a rate-limited percentage
                line to stderr as masks complete, independent of
                ``on_progress``.

        Raises:
            ValueError: If ``formats`` contains anything outside
                ``{"npy", "npz", "png", "jpg"}``.

        Returns:
            ``{"total": int, "ok": int, "failed": int, "missing": int}``
            -- ``total`` counts only patches that actually had a source
            file to process; ``missing`` counts requested coordinates
            that had none (only possible when ``coords`` is given).
        """
        # Local import: avoids a module-level circular import with
        # patching.py (see this module's docstring).
        from .patching import find_channel_source

        _validate_formats(formats, _VALID_MASK_FORMATS)

        patch_dir = Path(patch_dir) if patch_dir is not None else self._czi.output_dir / "patches"
        mask_dir = Path(mask_dir) if mask_dir is not None else self._czi.output_dir / "masks"
        mask_dir.mkdir(parents=True, exist_ok=True)

        missing_count = 0
        if coords is not None:
            source_files = []
            for y, x in coords:
                candidate = find_channel_source(patch_dir, y, x, source_channel)
                if candidate is not None:
                    source_files.append(candidate)
                else:
                    missing_count += 1
                    logger.warning(
                        "No extracted patch found for mask request (%d, %d), channel %d, in %s",
                        y, x, source_channel, patch_dir,
                    )
        else:
            source_files = list(patch_dir.glob(f"patch_y*_x*_pol{source_channel}.npy"))

        total = len(source_files)

        workers = resolve_max_workers(max_workers)
        worker_fn = partial(_process_patch_worker, mask_dir=mask_dir, formats=formats)

        ok_count = 0
        failed_count = 0
        report = make_reporter("masking", total, verbose=verbose, on_progress=on_progress)

        with multiprocessing.Pool(workers) as pool:
            for i, success in enumerate(pool.imap_unordered(worker_fn, source_files), 1):
                if success:
                    ok_count += 1
                else:
                    failed_count += 1
                report(i, f"mask {i}/{total}")

        if failed_count:
            logger.warning("%d/%d mask(s) failed during generation.", failed_count, total)
        if missing_count:
            logger.warning("%d requested coordinate(s) had no extracted patch to mask.", missing_count)

        return {"total": total, "ok": ok_count, "failed": failed_count, "missing": missing_count}
