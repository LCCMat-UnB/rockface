"""
czi.py
==================================================================
The :class:`CZI` class -- RockFace's entry point for a single Zeiss CZI
petrographic slide -- plus the module-level helper functions it wraps.

Opening a :class:`CZI` resolves a stable slide identity, opens the file,
and reads its physical/structural overview (bounding box, pixel scale,
available polarization channels). It also carries the low-level image
I/O and color-space helpers shared by patch extraction and mask
generation: atomic file writes (.npy/.npz/.png/.jpg), color-order
conversion, and multi-polarization synthesis.

Module-level functions vs. instance methods
---------------------------------------------
Every ``CZI`` method in this file is a thin wrapper around a
module-level function of the same behavior (``_atomic_write``,
``pixel_sizes_from_handle``, ``convert_array_to_rgb``, etc.). This is
deliberate: the multiprocessing workers in ``patching.py`` and
``masks.py`` run in separate processes and cannot use a bound method of
a ``CZI`` instance (the instance holds an open, unpicklable native file
handle -- see those modules' docstrings), so they call these same
module-level functions directly instead of duplicating the logic. The
``CZI`` methods remain the documented public API; the module-level
functions are the shared implementation.

Color convention
-----------------
Zeiss CZI RGB files most commonly store pixels as ``Bgr24`` -- channel
data arrives in **BGR** order. :meth:`CZI.convert_to_rgb` reads the
``PixelType`` field from the slide's own metadata to convert correctly
whenever that field is present and recognized, and falls back to the
BGR assumption (today's safe default) otherwise -- see
:func:`convert_array_to_rgb` for the exact fallback rules.

Example:
    >>> import rockface
    >>> czi = rockface.CZI("sample.czi", output_base="./output_project")
    >>> czi.id
    'a3f9c1e2b7d4'
    >>> czi.size()
    (2.2e-07, 2.2e-07)
    >>> czi.save_metadata()
    PosixPath('output_project/a3f9c1e2b7d4/image_metadata.json')
"""

import json
import os
import warnings
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple, Union

import numpy as np
from aicspylibczi import CziFile
from PIL import Image

from . import __version__
from .identity import slide_id
from .masks import Mask
from .patching import Patching

# PixelType values known to already be RGB-ordered (no channel reversal needed).
_RGB_PIXEL_TYPES = {"Rgb24", "Rgb48", "Rgb24Planar"}
# PixelType values known to be BGR-ordered (channel reversal needed -- today's default).
_BGR_PIXEL_TYPES = {"Bgr24", "Bgr48"}


# ==================================================================
# MODULE-LEVEL HELPERS
# (picklable-safe: operate only on plain data / an explicit CZI handle,
#  never on a CZI/Patching/Mask instance -- see module docstring)
# ==================================================================

def metadata_root_from_handle(handle: CziFile) -> Optional[Any]:
    """Return a CZI handle's metadata XML root element.

    The underlying library may expose metadata already parsed as an XML
    element, or as a raw string; this normalizes both cases.

    Args:
        handle: An opened ``aicspylibczi.CziFile``.

    Returns:
        The root ``xml.etree.ElementTree.Element``, or ``None`` if
        metadata is unavailable.
    """
    metadata = handle.meta
    if metadata is None:
        return None
    if isinstance(metadata, str):
        return ET.fromstring(metadata)
    return metadata


def pixel_sizes_from_handle(handle: CziFile) -> Tuple[Optional[float], Optional[float]]:
    """Read the physical pixel size (X and Y) from a CZI handle's metadata, in meters.

    Walks every ``<Distance>`` node in the metadata XML looking for the
    ``X`` and ``Y`` axes and reads the numeric value in each node's
    ``<Value>`` child.

    Args:
        handle: An opened ``aicspylibczi.CziFile``.

    Returns:
        A ``(pixel_size_x_meters, pixel_size_y_meters)`` tuple. Either
        element is ``None`` when that axis could not be determined.
    """
    root = metadata_root_from_handle(handle)
    pixel_size_x: Optional[float] = None
    pixel_size_y: Optional[float] = None

    if root is None:
        return pixel_size_x, pixel_size_y

    for distance in root.findall(".//Distance"):
        axis = distance.get("Id")
        value_element = distance.find("Value")
        if value_element is None or value_element.text is None:
            continue
        try:
            value = float(value_element.text)
        except ValueError:
            continue
        if axis == "X":
            pixel_size_x = value
        elif axis == "Y":
            pixel_size_y = value

    return pixel_size_x, pixel_size_y


def available_channels_from_handle(handle: CziFile) -> List[int]:
    """Discover the channel (dimension ``C``) indices present in a CZI handle.

    In polarized-light acquisitions, each polarizer angle is typically
    stored as its own channel. This inspects the CZI dimension shape
    rather than assuming a fixed channel count.

    Args:
        handle: An opened ``aicspylibczi.CziFile``.

    Returns:
        A list of channel indices, e.g. ``[0, 1, 2, 3, 4, 5]``. Falls
        back to ``[0]`` if channel introspection fails.
    """
    try:
        dims_shape = handle.get_dims_shape()
        for block in dims_shape:
            if "C" in block:
                start, end = block["C"]
                return list(range(start, end))
    except Exception as exc:  # pragma: no cover - defensive, library-dependent
        warnings.warn(f"Could not read channel dimension (C) from CZI metadata: {exc}")
    return [0]


def pixel_format_from_handle(handle: CziFile) -> str:
    """Read the ``PixelType`` field from a CZI handle's metadata.

    Note:
        The exact metadata location used here
        (``Information/Image/PixelType``) and the value strings checked
        against it follow the documented Zeiss CZI XML schema
        convention, but have not been verified against a real ``.czi``
        file in this environment (no sample file or ``aicspylibczi``
        install was available while writing this). Before relying on
        this for a non-BGR slide, confirm by dumping
        ``ET.tostring(metadata_root_from_handle(handle))`` for a real
        lab file and checking the actual field path/value.

    Args:
        handle: An opened ``aicspylibczi.CziFile``.

    Returns:
        The raw ``PixelType`` text (e.g. ``"Bgr24"``), or ``"unknown"``
        if the field is missing, empty, or metadata is unavailable.
    """
    root = metadata_root_from_handle(handle)
    if root is None:
        return "unknown"
    node = root.find(".//Information/Image/PixelType")
    if node is None or not node.text:
        return "unknown"
    return node.text.strip()


def convert_array_to_rgb(array: np.ndarray, pixel_format: str = "unknown") -> np.ndarray:
    """Convert a raw CZI channel array to RGB order, ``uint8``.

    Decides whether channel order needs reversing based on
    ``pixel_format`` (typically obtained via
    :func:`pixel_format_from_handle`):

    - Recognized as RGB (``Rgb24``, ``Rgb48``, ...): left as-is.
    - Recognized as BGR (``Bgr24``, ``Bgr48``): reversed (today's
      historical default behavior).
    - Missing or unrecognized (``"unknown"`` or anything else): falls
      back to reversing (assume BGR), with a warning for the
      unrecognized (non-"unknown") case -- this matches the original,
      pre-metadata-aware behavior exactly, so nothing regresses when
      metadata is unavailable.

    Grayscale arrays (anything that isn't ``HxWx3``) are never
    reordered, regardless of ``pixel_format``.

    Args:
        array: Image array in the slide's native channel order (``HxWx3``
            color, or ``HxW`` grayscale).
        pixel_format: The slide's ``PixelType`` metadata value, or
            ``"unknown"`` if unavailable.

    Returns:
        A contiguous ``uint8`` array in RGB order, ready for
        ``PIL.Image.fromarray``.
    """
    is_color = array.ndim == 3 and array.shape[-1] == 3

    if is_color:
        if pixel_format in _RGB_PIXEL_TYPES:
            reverse_channels = False
        elif pixel_format in _BGR_PIXEL_TYPES:
            reverse_channels = True
        else:
            if pixel_format != "unknown":
                warnings.warn(f"Unrecognized PixelType '{pixel_format}', assuming BGR.")
            reverse_channels = True  # safe fallback: today's original default
    else:
        reverse_channels = False

    if reverse_channels:
        array_rgb = np.ascontiguousarray(array[..., ::-1])
    else:
        array_rgb = np.ascontiguousarray(array)

    if array_rgb.dtype != np.uint8:
        array_rgb = np.clip(array_rgb, 0, 255).astype(np.uint8, copy=False)
    return array_rgb


def synthesize_channel_stack(polarized_stack: List[np.ndarray], mode: str = "mean") -> np.ndarray:
    """Combine a stack of polarization channels into one composite image.

    In polarized-light microscopy, combining several polarizer
    rotations approximates an unpolarized ("normal") view of the thin
    section.

    Args:
        polarized_stack: A list of same-shape arrays, one per
            polarization channel.
        mode: Combination strategy -- ``"mean"`` (per-pixel average,
            default) or ``"max"`` (per-pixel maximum, emphasizing
            bright responses).

    Returns:
        A ``uint8`` array in the same channel order as the input
        (typically BGR, matching the raw CZI read).

    Raises:
        ValueError: If ``polarized_stack`` is empty, or its arrays do
            not all share the same shape.
    """
    if not polarized_stack:
        raise ValueError("polarized_stack is empty -- nothing to synthesize.")

    base_shape = polarized_stack[0].shape
    if any(layer.shape != base_shape for layer in polarized_stack):
        raise ValueError("All polarization channels must share the same shape.")

    stack = np.stack([layer.astype(np.float32) for layer in polarized_stack], axis=0)
    combined = stack.max(axis=0) if mode == "max" else stack.mean(axis=0)
    return np.clip(combined, 0, 255).astype(np.uint8)


def _atomic_write(output_path: Union[str, Path], write_fn: Callable[[Path], None]) -> Path:
    """Write a file atomically: write to a temp path, then ``os.replace``.

    Shared by every save helper below so a process killed mid-write
    never leaves a corrupted/partial file at ``output_path``.

    Args:
        output_path: Final destination path.
        write_fn: Called with the temporary path; must write the file's
            full contents there.

    Returns:
        ``output_path``, as a ``Path``.
    """
    output_path = Path(output_path)
    tmp_path = output_path.with_name(output_path.name + ".tmp")
    try:
        write_fn(tmp_path)
        os.replace(tmp_path, output_path)
    finally:
        if tmp_path.exists():
            try:
                tmp_path.unlink()
            except OSError:
                pass
    return output_path


def save_npy_array(array: np.ndarray, output_path: Union[str, Path]) -> Path:
    """Save an array as ``.npy``, atomically. See :meth:`CZI.save_npy`.

    Note:
        ``numpy.save``/``numpy.savez*`` silently append their own
        extension (``.npy``/``.npz``) when given a bare path that
        doesn't already end with it -- which would corrupt the atomic
        tmp-file rename below (the ``.tmp`` suffix means the path never
        ends in ``.npy``/``.npz``). Both savers are therefore called
        with an explicit open file handle, not a path, to bypass that
        auto-suffixing.
    """
    def _write(tmp_path: Path) -> None:
        with open(tmp_path, "wb") as file:
            np.save(file, array)

    return _atomic_write(output_path, _write)


def save_npz_array(array: np.ndarray, output_path: Union[str, Path], compressed: bool = True) -> Path:
    """Save an array as ``.npz``, atomically. See :meth:`CZI.save_npz`.

    See :func:`save_npy_array`'s note: writes through an explicit open
    file handle so numpy does not auto-append ``.npz`` to the temp path.
    """
    saver = np.savez_compressed if compressed else np.savez

    def _write(tmp_path: Path) -> None:
        with open(tmp_path, "wb") as file:
            saver(file, array)

    return _atomic_write(output_path, _write)


def load_array_for_npz(source: Union[np.ndarray, str, Path]) -> np.ndarray:
    """Resolve ``save_npz``'s dual input: an array as-is, or a ``.npy`` path to load.

    Args:
        source: Either a ``numpy.ndarray`` directly, or a path
            (``str``/``Path``) to an existing ``.npy`` file.

    Returns:
        The resolved ``numpy.ndarray``.

    Raises:
        FileNotFoundError: If ``source`` is a path that does not exist.
        ValueError: If ``source`` is a path without a ``.npy`` suffix.
    """
    if isinstance(source, (str, Path)):
        source_path = Path(source)
        if source_path.suffix != ".npy":
            raise ValueError(f"Expected a .npy file, got: {source_path}")
        if not source_path.exists():
            raise FileNotFoundError(f"No such .npy file: {source_path}")
        return np.load(source_path)
    return source


def save_image_file(
    image: Image.Image,
    output_path: Union[str, Path],
    lossless: bool = True,
    jpeg_quality: int = 95,
) -> Path:
    """Save a PIL image as PNG or JPEG, atomically. See :meth:`CZI.save_image`."""
    def _write(tmp_path: Path) -> None:
        if lossless:
            image.save(tmp_path, format="PNG")
        else:
            image.save(tmp_path, format="JPEG", quality=jpeg_quality, subsampling=0)

    return _atomic_write(output_path, _write)


# ==================================================================
# CZI
# ==================================================================

class CZI:
    """Represents one Zeiss CZI microscope slide and its metadata.

    Opens the file, computes a stable slide identity (see
    :mod:`rockface.identity`), reads the slide's physical/structural
    overview, and exposes the metadata and image-IO helpers used by the
    :attr:`patching` and :attr:`mask` sub-objects.

    Attributes:
        path (Path): Resolved path to the source .czi file.
        id (str): Stable, deterministic slide identifier (see
            :func:`rockface.identity.slide_id`).
        output_dir (Path): Directory where this slide's metadata,
            patches, and masks are written -- ``output_base / self.id``.
        overview (dict): Structural/physical summary of the slide --
            ``bbox`` (mosaic bounding box), ``pixel_size_x_meters``,
            ``pixel_size_y_meters``, ``dims`` (dimension order string),
            and ``channels`` (available channel indices).
        patching (Patching): Patch extraction and reassembly. See
            :class:`rockface.patching.Patching`.
        mask (Mask): Pore-mask generation. See :class:`rockface.masks.Mask`.

    Example:
        >>> import rockface
        >>> czi = rockface.CZI("sample.czi")
        >>> czi.patching.run()
        >>> czi.mask.run()
    """

    def __init__(self, path: Union[str, Path], output_base: Union[str, Path] = "./output_project") -> None:
        """Open a CZI slide and prepare its output location.

        Args:
            path: Path to the .czi file.
            output_base: Root directory under which this slide's own
                output directory (named after its :attr:`id`) is created.

        Raises:
            FileNotFoundError: If ``path`` does not exist.
        """
        self.path = Path(path).resolve()
        if not self.path.exists():
            raise FileNotFoundError(f"CZI file not found: {self.path}")

        # Computed from path/size/mtime -- cheap, and fails fast before
        # the (comparatively expensive) native CZI file open below.
        self.id: str = slide_id(self.path)

        self.output_dir: Path = Path(output_base) / self.id
        self.output_dir.mkdir(parents=True, exist_ok=True)

        # The native library handle. Kept private: it cannot be pickled
        # across multiprocessing worker processes, which is why patch
        # and mask workers re-open their own handle instead of sharing
        # this one (see patching.py / masks.py module docstrings).
        self._handle = CziFile(str(self.path))

        # Sub-objects are attached before reading the overview, since
        # read_overview() delegates back through self.patching.
        self.patching = Patching(self)
        self.mask = Mask(self)

        self.overview: Dict[str, Any] = self.patching.read_overview()

    def __repr__(self) -> str:
        return f"CZI(path={self.path.name!r}, id={self.id!r})"

    # ------------------------------------------------------------
    # METADATA
    # ------------------------------------------------------------

    def _metadata_root(self) -> Optional[Any]:
        """Return this slide's metadata XML root element.

        Private: only used internally by :meth:`size` and
        :meth:`convert_to_rgb` (via :meth:`_pixel_format`), which both
        read different fields from the same metadata tree. Formerly the
        standalone public function ``get_metadata_root`` in
        ``image_functions.py`` -- folded in here since it was only ever
        used to feed these two lookups.
        """
        return metadata_root_from_handle(self._handle)

    def size(self) -> Tuple[Optional[float], Optional[float]]:
        """Read the physical pixel size (X and Y) from slide metadata, in meters.

        Formerly ``extract_pixel_sizes_from_metadata`` in
        ``image_functions.py``.

        Returns:
            A ``(pixel_size_x_meters, pixel_size_y_meters)`` tuple.
            Either element is ``None`` when that axis could not be
            determined (missing metadata, missing node, or a
            non-numeric value).
        """
        return pixel_sizes_from_handle(self._handle)

    def get_available_channels(self) -> List[int]:
        """Discover the channel (dimension ``C``) indices present in this slide.

        Formerly defined in ``patching_engine.py``; moved here since it
        is fundamentally a property of the slide's own structure, not
        of any particular patching run.

        Returns:
            A list of channel indices, e.g. ``[0, 1, 2, 3, 4, 5]``.
            Falls back to ``[0]`` if channel introspection fails.
        """
        return available_channels_from_handle(self._handle)

    def _pixel_format(self) -> str:
        """Read this slide's ``PixelType`` metadata field.

        Private helper for :meth:`convert_to_rgb`. See
        :func:`pixel_format_from_handle` for the full caveats around
        this field's reliability.
        """
        return pixel_format_from_handle(self._handle)

    def save_metadata(self) -> Path:
        """Save this slide's image metadata as JSON.

        Records the slide's identity (:attr:`id` + original filename),
        structural dimensions, and physical pixel scale. Does not
        include anything about patch generation -- see
        :meth:`save_patching_data` for that half, kept in a separate
        file so image identity and a particular patching run can be
        looked up and reasoned about independently while still
        cross-referencing each other through ``slide_id`` and
        ``source_file``. Formerly half of ``save_image_metadata_from_overview``
        in ``image_functions.py``.

        Returns:
            Path to the written ``image_metadata.json`` file, inside
            :attr:`output_dir`.
        """
        bbox = self.overview["bbox"]
        metadata = {
            "slide_id": self.id,
            "source_file": self.path.name,
            "image_structure": {
                "full_width_px": bbox["w"],
                "full_height_px": bbox["h"],
                "global_x_origin": bbox["x"],
                "global_y_origin": bbox["y"],
                "dimensions_order": self.overview.get("dims", ""),
            },
            "physical_scaling": {
                "pixel_size_x_meters": self.overview["pixel_size_x_meters"],
                "pixel_size_y_meters": self.overview["pixel_size_y_meters"],
                "unit": "meters",
            },
            "channels_available": self.overview.get("channels", []),
            "analysis_info": {
                "project": "RockFace",
                "institution": "LCCMat / Petrobras",
                "engine_version": __version__,
            },
        }

        output_path = self.output_dir / "image_metadata.json"
        with open(output_path, "w", encoding="utf-8") as file:
            json.dump(metadata, file, indent=4, ensure_ascii=False)
        return output_path

    def save_patching_data(
        self,
        patch_coords: List[Tuple[int, int]],
        patch_size: int,
        stride: int,
        polarization_channels: Optional[List[int]] = None,
        normal_mode: str = "mean",
        full_grid_total: Optional[int] = None,
    ) -> Path:
        """Save this patching run's parameters as JSON.

        The patching-process half of what used to be a single combined
        metadata file (``save_image_metadata_from_overview`` in
        ``image_functions.py``). Kept as a separate method from
        :meth:`save_metadata`: it can only be called once a patch grid
        exists (see :meth:`rockface.patching.Patching.get_patches`),
        while image metadata is available immediately after opening the
        slide.

        Args:
            patch_coords: The patch coordinates actually used for this
                run -- either the full grid from ``Patching.get_patches``,
                or an explicit subset (see ``Patching.run(coords=...)``).
            patch_size: Patch side length, in pixels.
            stride: Step between patches, in pixels.
            polarization_channels: Channel indices used for this run,
                recorded for reference.
            normal_mode: How the "normal" (unpolarized composite) image
                was synthesized for this run -- ``"mean"`` or ``"max"``.
            full_grid_total: The size of the slide's full patch grid at
                this ``patch_size``/``stride`` (i.e.
                ``len(Patching.get_patches(patch_size, stride))``), when
                known. Pass this whenever ``patch_coords`` might be a
                partial subset, so the saved metadata honestly records
                whether this run covered the whole slide instead of
                silently implying full coverage. ``None`` (default)
                means "not tracked" -- ``is_partial`` is then always
                recorded as ``False``.

        Returns:
            Path to the written ``patching_metadata.json`` file, inside
            :attr:`output_dir`.
        """
        channels = polarization_channels if polarization_channels is not None else []
        is_partial = full_grid_total is not None and len(patch_coords) < full_grid_total
        metadata = {
            "slide_id": self.id,
            "source_file": self.path.name,
            "patch_generation": {
                "patch_size_px": patch_size,
                "stride_px": stride,
                "overlap_px": patch_size - stride,
                "total_patches": len(patch_coords),
                "full_grid_total": full_grid_total,
                "is_partial": is_partial,
            },
            "polarization": {
                "channels": channels,
                "count": len(channels),
                "normal_mode": normal_mode,
            },
        }

        output_path = self.output_dir / "patching_metadata.json"
        with open(output_path, "w", encoding="utf-8") as file:
            json.dump(metadata, file, indent=4, ensure_ascii=False)
        return output_path

    # ------------------------------------------------------------
    # ATOMIC I/O
    # ------------------------------------------------------------

    def save_npy(self, array: np.ndarray, output_path: Union[str, Path]) -> Path:
        """Save an array as ``.npy``, atomically.

        Formerly ``atomic_save_npy`` in ``image_functions.py``. Writes
        to a temporary file first and only then performs an atomic
        ``os.replace`` to the final path, so a process killed mid-write
        never leaves a corrupted/partial file at ``output_path``.

        Args:
            array: The array to persist.
            output_path: Destination ``.npy`` path.

        Returns:
            ``output_path``, as a ``Path``.
        """
        return save_npy_array(array, output_path)

    def save_npz(
        self,
        source: Union[np.ndarray, str, Path],
        output_path: Union[str, Path],
        compressed: bool = True,
    ) -> Path:
        """Save an array as ``.npz``, atomically.

        New helper (there was no ``.npz`` saver in the original
        codebase). Accepts either an in-memory array, or the path to an
        existing ``.npy`` file to load first -- so a patch already
        written by :meth:`save_npy` during extraction can be converted
        to ``.npz`` afterwards without re-reading the CZI file.

        Args:
            source: Either a ``numpy.ndarray`` directly, or a path
                (``str``/``Path``) to an existing ``.npy`` file.
            output_path: Destination ``.npz`` path.
            compressed: If ``True`` (default), uses
                ``numpy.savez_compressed``; if ``False``, uses
                ``numpy.savez`` (faster to write, larger on disk).

        Returns:
            ``output_path``, as a ``Path``.

        Raises:
            FileNotFoundError: If ``source`` is a path that does not exist.
            ValueError: If ``source`` is a path without a ``.npy`` suffix
                (guards against pointing this at the wrong file type and
                getting a confusing error from ``numpy.load`` instead).
        """
        array = load_array_for_npz(source)
        return save_npz_array(array, output_path, compressed=compressed)

    def save_image(
        self,
        image: Image.Image,
        output_path: Union[str, Path],
        lossless: bool = True,
        jpeg_quality: int = 95,
    ) -> Path:
        """Save a PIL image as PNG or JPEG, atomically.

        Formerly ``atomic_save_image`` in ``image_functions.py``. Same
        write-to-temp-then-replace pattern as :meth:`save_npy`.

        Args:
            image: The PIL image to persist.
            output_path: Destination path.
            lossless: If ``True`` (default), saves as PNG; if ``False``,
                saves as JPEG at ``jpeg_quality``.
            jpeg_quality: JPEG quality (0-100), used only when
                ``lossless`` is ``False``.

        Returns:
            ``output_path``, as a ``Path``.
        """
        return save_image_file(image, output_path, lossless=lossless, jpeg_quality=jpeg_quality)

    # ------------------------------------------------------------
    # COLOR SPACE
    # ------------------------------------------------------------

    def convert_to_rgb(self, array: np.ndarray) -> np.ndarray:
        """Convert a raw CZI channel array to RGB order, ``uint8``.

        Formerly ``convert_bgr_to_rgb_uint8`` in ``image_functions.py``,
        which unconditionally assumed BGR input. This now reads the
        slide's own ``PixelType`` metadata to decide whether channel
        order actually needs reversing -- see :func:`convert_array_to_rgb`
        for the exact rules and fallback behavior.

        Args:
            array: Image array in the slide's native channel order
                (``HxWx3`` color, or ``HxW`` grayscale).

        Returns:
            A contiguous ``uint8`` array in RGB order, ready for
            ``PIL.Image.fromarray``.
        """
        return convert_array_to_rgb(array, pixel_format=self._pixel_format())

    def synthesize_channels(self, polarized_stack: List[np.ndarray], mode: str = "mean") -> np.ndarray:
        """Combine a stack of polarization channels into one composite image.

        Formerly ``synthesize_normal_image`` in ``image_functions.py``.

        Args:
            polarized_stack: A list of same-shape arrays, one per
                polarization channel.
            mode: Combination strategy -- ``"mean"`` (per-pixel average,
                default) or ``"max"`` (per-pixel maximum, emphasizing
                bright responses).

        Returns:
            A ``uint8`` array in the same channel order as the input
            (typically BGR, matching the raw CZI read).

        Raises:
            ValueError: If ``polarized_stack`` is empty, or its arrays
                do not all share the same shape.
        """
        return synthesize_channel_stack(polarized_stack, mode=mode)

    # ------------------------------------------------------------
    # PIPELINE CONVENIENCE
    # ------------------------------------------------------------

    def run(self, **kwargs) -> dict:
        """Run the full RockFace pipeline for this slide, end to end.

        Thin convenience wrapper around
        :func:`rockface.pipeline.run_pipeline` -- see that function for
        the full stage list, parameters, and return value. Kept as a
        separate module-level function rather than inlined here because
        it orchestrates across :attr:`patching` and :attr:`mask`
        together, not just this object's own state.

        Example:
            >>> czi = rockface.CZI("sample.czi")
            >>> summary = czi.run(patch_size=4096, stride=3800)
        """
        from . import pipeline  # local import: avoids a circular import at module load time

        return pipeline.run_pipeline(self, **kwargs)
