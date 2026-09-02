# RockFace API Reference

Generated via Python's `inspect` module against the installed package -- always reflects actual source, never hand-maintained prose. For task-oriented recipes instead of a full parameter listing, see [`USAGE.md`](./USAGE.md).

## Overview

```python
RockFace: petrographic thin-section image processing toolkit
(LCCMat / Petrobras).

Turns Zeiss CZI polarized-light microscope scans of rock thin sections
into tiled patches and pore-space masks, for porosity analysis and ML
dataset preparation. Usable from Python or from the terminal (see the
``rockface`` command installed alongside this package).

Typical usage:
    >>> import rockface
    >>> rockface.set_max_workers(4)         # optional: session-wide default for all Pool-based calls
    >>> czi = rockface.CZI("sample.czi")
    >>> czi.patching.run()                  # full patch extraction
    >>> czi.mask.run()                      # pore mask generation for all patches
    >>> mosaic = czi.patching.restitch(stride=3800)  # reassemble into a full image

Or the whole pipeline in one call:
    >>> czi = rockface.CZI("sample.czi")
    >>> summary = czi.run(patch_size=4096, stride=3800)

Package layout:
    - ``rockface.czi`` -- the ``CZI`` class: slide identity, metadata,
      and low-level image I/O / color-space helpers.
    - ``rockface.patching`` -- the ``Patching`` class (``czi.patching``):
      patch grid computation, parallel extraction, and restitching.
    - ``rockface.masks`` -- the ``Mask`` class (``czi.mask``): pore-space
      segmentation and overlay generation.
    - ``rockface.pipeline`` -- end-to-end orchestration (``run_pipeline``)
      and the ``rockface`` command-line entry point.
    - ``rockface.identity`` -- stable slide identifier hashing.
    - ``rockface.config`` -- session-wide defaults (currently just
      ``max_workers``), set via ``rockface.set_max_workers()``.
```

## Installation

```bash
pip install -e .
```

## class `rockface.czi.CZI`

```
Represents one Zeiss CZI microscope slide and its metadata.

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
```

### Constructor

```python
__init__(self, path: Union[str, pathlib.Path], output_base: Union[str, pathlib.Path] = './output_project') -> None
```

```
Open a CZI slide and prepare its output location.

Args:
    path: Path to the .czi file.
    output_base: Root directory under which this slide's own
        output directory (named after its :attr:`id`) is created.

Raises:
    FileNotFoundError: If ``path`` does not exist.
```

### `CZI.size`

```python
size(self) -> Tuple[Optional[float], Optional[float]]
```

```
Read the physical pixel size (X and Y) from slide metadata, in meters.

Formerly ``extract_pixel_sizes_from_metadata`` in
``image_functions.py``.

Returns:
    A ``(pixel_size_x_meters, pixel_size_y_meters)`` tuple.
    Either element is ``None`` when that axis could not be
    determined (missing metadata, missing node, or a
    non-numeric value).
```

### `CZI.get_available_channels`

```python
get_available_channels(self) -> List[int]
```

```
Discover the channel (dimension ``C``) indices present in this slide.

Formerly defined in ``patching_engine.py``; moved here since it
is fundamentally a property of the slide's own structure, not
of any particular patching run.

Returns:
    A list of channel indices, e.g. ``[0, 1, 2, 3, 4, 5]``.
    Falls back to ``[0]`` if channel introspection fails.
```

### `CZI.save_metadata`

```python
save_metadata(self) -> pathlib.Path
```

```
Save this slide's image metadata as JSON.

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
```

### `CZI.save_patching_data`

```python
save_patching_data(self, patch_coords: List[Tuple[int, int]], patch_size: int, stride: int, polarization_channels: Optional[List[int]] = None, full_grid_total: Optional[int] = None) -> pathlib.Path
```

```
Save this patching run's parameters as JSON.

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
        recorded for reference. Each is saved as its own raw
        channel image (``patch_y{y}_x{x}_pol{c}.*``) -- no
        composite "normal" image is synthesized; channel ``0``
        is the slide's own unpolarized/normal view (see
        :meth:`rockface.masks.Mask.run`'s ``source_channel``).
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
```

### `CZI.save_npy`

```python
save_npy(self, array: numpy.ndarray, output_path: Union[str, pathlib.Path]) -> pathlib.Path
```

```
Save an array as ``.npy``, atomically.

Formerly ``atomic_save_npy`` in ``image_functions.py``. Writes
to a temporary file first and only then performs an atomic
``os.replace`` to the final path, so a process killed mid-write
never leaves a corrupted/partial file at ``output_path``.

Args:
    array: The array to persist.
    output_path: Destination ``.npy`` path.

Returns:
    ``output_path``, as a ``Path``.
```

### `CZI.save_npz`

```python
save_npz(self, source: Union[numpy.ndarray, str, pathlib.Path], output_path: Union[str, pathlib.Path], compressed: bool = True) -> pathlib.Path
```

```
Save an array as ``.npz``, atomically.

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
```

### `CZI.save_image`

```python
save_image(self, image: PIL.Image.Image, output_path: Union[str, pathlib.Path], lossless: bool = True, jpeg_quality: int = 95) -> pathlib.Path
```

```
Save a PIL image as PNG or JPEG, atomically.

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
```

### `CZI.convert_to_rgb`

```python
convert_to_rgb(self, array: numpy.ndarray) -> numpy.ndarray
```

```
Convert a raw CZI channel array to RGB order, ``uint8``.

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
```

### `CZI.run`

```python
run(self, **kwargs) -> dict
```

```
Run the full RockFace pipeline for this slide, end to end.

Thin convenience wrapper around
:func:`rockface.pipeline.run_pipeline` -- see that function for
the full stage list, parameters, and return value. Kept as a
separate module-level function rather than inlined here because
it orchestrates across :attr:`patching` and :attr:`mask`
together, not just this object's own state.

Example:
    >>> czi = rockface.CZI("sample.czi")
    >>> summary = czi.run(patch_size=4096, stride=3800)
```

## class `rockface.patching.Patching`

```
Cuts a CZI slide into overlapping patches and reassembles them.

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
```

### Constructor

```python
__init__(self, czi: 'CZI') -> None
```

```
Initialize self.  See help(type(self)) for accurate signature.
```

### `Patching.read_overview`

```python
read_overview(self) -> Dict[str, Any]
```

```
Read the slide's mosaic overview: bounding box, scale, channels.

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
```

### `Patching.get_patches`

```python
get_patches(self, patch_size: int, stride: int, image_shape: Optional[Tuple[int, int]] = None) -> List[Tuple[int, int]]
```

```
Generate deterministic patch coordinates, as ``(y, x)`` pairs.

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
```

### `Patching.process_single_patch`

```python
process_single_patch(self, coord: Tuple[int, int], patch_size: int = 4096, polarization_channels: Optional[List[int]] = None, output_dir: Union[str, pathlib.Path, NoneType] = None, formats: Optional[List[str]] = None) -> Dict[str, Any]
```

```
Extract a single patch, synchronously, without a multiprocessing Pool.

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
```

### `Patching.run`

```python
run(self, coords: Optional[List[Tuple[int, int]]] = None, patch_size: int = 4096, stride: int = 3800, polarization_channels: Optional[List[int]] = None, formats: Optional[List[str]] = None, max_workers: Optional[int] = None, on_progress: Optional[Callable[[str, float, str], NoneType]] = None, verbose: bool = False) -> Dict[str, Any]
```

```
Extract patches of the slide, in parallel.

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
```

### `Patching.generate_manifest`

```python
generate_manifest(self, patch_dir: Union[str, pathlib.Path], mask_dir: Union[str, pathlib.Path], output_path: Union[str, pathlib.Path], url_base: Optional[str] = None, normal_channel: int = 0) -> int
```

```
Write a manifest describing every extracted patch and its mask.

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
```

### `Patching.restitch`

```python
restitch(self, patch_dir: Union[str, pathlib.Path, NoneType] = None, stride: Optional[int] = None, channel: str = 'pol0') -> numpy.ndarray
```

```
Reassemble extracted patches into a single full-resolution mosaic.

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
```

### `Patching.restitch_with_mask_overlay`

```python
restitch_with_mask_overlay(self, patch_dir: Union[str, pathlib.Path, NoneType] = None, mask_dir: Union[str, pathlib.Path, NoneType] = None, stride: Optional[int] = None, source_channel: int = 0, color: Tuple[int, int, int] = (0, 255, 0), filled_mode: bool = True, thickness: int = 2) -> numpy.ndarray
```

```
Reassemble the full slide with its pore mask drawn on top.

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
```

## class `rockface.masks.Mask`

```
Generates pore-space segmentation masks from normal-light patches.

Accessed via ``CZI.mask``. Pore detection assumes blue/cyan epoxy
resin impregnation under polarized light (standard LCCMat
thin-section preparation).

Example:
    >>> czi = rockface.CZI("sample.czi")
    >>> czi.patching.run()
    >>> result = czi.mask.run()
```

### Constructor

```python
__init__(self, czi: 'CZI') -> None
```

```
Initialize self.  See help(type(self)) for accurate signature.
```

### `Mask.generate_pore_mask`

```python
generate_pore_mask(self, bgr_array: numpy.ndarray) -> numpy.ndarray
```

```
Segment resin-impregnated pore space (blue/cyan) in a BGR image.

Formerly ``generate_pore_mask`` in ``image_functions.py``.

Args:
    bgr_array: Patch image in **BGR** order (``HxWx3``, ``uint8``).

Returns:
    A binary ``uint8`` mask (``0`` or ``255``), where ``255``
    marks pore space.
```

### `Mask.generate_transparent_overlay`

```python
generate_transparent_overlay(self, mask: numpy.ndarray, color: Tuple[int, int, int] = (0, 255, 0), filled_mode: bool = False, thickness: int = 2) -> numpy.ndarray
```

```
Build a transparent RGBA overlay from a binary mask.

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
```

### `Mask.overlay_on_patch`

```python
overlay_on_patch(self, channel_array: numpy.ndarray, mask: Optional[numpy.ndarray] = None, color: Tuple[int, int, int] = (0, 255, 0), filled_mode: bool = False, thickness: int = 2, pixel_format: str = 'unknown') -> numpy.ndarray
```

```
Composite a pore mask directly on top of one patch's image.

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
```

### `Mask.process_patch`

```python
process_patch(self, source_npy_path: Union[str, pathlib.Path], mask_dir: Union[str, pathlib.Path], formats: Optional[List[str]] = None) -> bool
```

```
Generate and save the pore mask for a single patch, synchronously.

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
```

### `Mask.run`

```python
run(self, coords: Optional[List[Tuple[int, int]]] = None, patch_dir: Union[str, pathlib.Path, NoneType] = None, mask_dir: Union[str, pathlib.Path, NoneType] = None, source_channel: int = 0, formats: Optional[List[str]] = None, max_workers: Optional[int] = None, on_progress: Optional[Callable[[str, float, str], NoneType]] = None, verbose: bool = False) -> Dict[str, Any]
```

```
Generate pore masks for extracted patches, in parallel.

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
```

## Module-level functions

### `rockface.pipeline.run_pipeline`

```python
run_pipeline(czi: 'CZI', coords: Optional[List[Tuple[int, int]]] = None, patch_size: int = 4096, stride: int = 3800, polarization_channels: Optional[List[int]] = None, mask_source_channel: int = 0, patch_formats: Optional[List[str]] = None, mask_formats: Optional[List[str]] = None, max_workers: Optional[int] = None, on_progress: Optional[Callable[[str, float, str], NoneType]] = None, verbose: bool = False, restitch_channels: Optional[List[str]] = None, write_manifest: bool = True, include_petrophysical: bool = False) -> Dict[str, Any]
```

```
Run the full RockFace pipeline for one CZI slide, end to end.

Stages:
    1. Save image metadata (``CZI.save_metadata``).
    2. Compute the patch grid and save patching metadata
       (``Patching.get_patches``, ``CZI.save_patching_data``).
    3. Extract patches: every requested polarization channel, as
       its own raw image, for either the full grid or an explicit
       ``coords`` subset (``Patching.run``). No composite "normal"
       image is synthesized -- channel 0 is the slide's own
       unpolarized/normal view. Saving each patch happens as an
       inherent part of this stage -- there is no separate "save
       patching" step in this pipeline.
    4. Generate a pore mask for every patch just extracted, from
       ``mask_source_channel`` only (``Mask.run``) -- the same
       ``coords`` subset is passed through, so a partial run masks
       exactly what it patched, not whatever else happens to
       already be on disk.
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
        channel available on the slide (typically ``0`` through
        ``6``). Each is saved as its own raw image -- no composite
        is synthesized.
    mask_source_channel: Which polarization channel to segment
        masks from, relayed to ``Mask.run(source_channel=...)``.
        Defaults to ``0``, the slide's own unpolarized/normal view
        -- a fixed rule of the segmentation method, not a free
        choice (see ``Mask.run``'s docstring).
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
        e.g. ``["pol0"]`` or ``["pol0", "pol3"]``. ``None``
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
```

### `rockface.pipeline.cli_main`

```python
cli_main(argv: Optional[List[str]] = None) -> int
```

```
Command-line entry point for RockFace (``rockface`` console script).

Example:
    .. code-block:: console

        $ rockface path/to/sample.czi --patch-size 4096 --stride 3800 --output ./output_project

Args:
    argv: Argument list to parse. Defaults to ``sys.argv[1:]``.

Returns:
    Process exit code: ``0`` on success, ``1`` on failure.
```

### `rockface.identity.slide_id`

```python
slide_id(path: Union[str, pathlib.Path], length: int = 12) -> str
```

```
Compute a stable, deterministic identifier for a CZI slide file.

The ID is derived from the file's resolved absolute path, size, and
modification time -- not its full content. CZI mosaics can be many
gigabytes, so hashing the full content every time a slide is opened
would be slow; path + size + mtime is enough to make the ID stable
across repeated runs on the same, unmodified file, while still
changing if the file at that path is later replaced with different
content (its size or modification time will differ).

Args:
    path: Path to the .czi file.
    length: Number of hexadecimal characters to keep from the
        SHA-256 digest. Defaults to 12 (48 bits), which is ample
        for any realistic single-project slide collection -- the
        chance of two slides colliding stays negligible well
        beyond millions of files.

Returns:
    A lowercase hexadecimal string, e.g. ``"a3f9c1e2b7d4"``.

Raises:
    FileNotFoundError: If ``path`` does not point to an existing file.

Example:
    >>> slide_id("sample.czi")
    'a3f9c1e2b7d4'
```

### `rockface.config.set_max_workers`

```python
set_max_workers(value: Optional[int]) -> None
```

```
Set the session-wide default worker-process count.

After calling this, every :meth:`~rockface.patching.Patching.run`,
:meth:`~rockface.masks.Mask.run`, and
:func:`~rockface.pipeline.run_pipeline` call that does not pass its
own ``max_workers=`` argument will use ``value``.

Args:
    value: Worker process count to use by default, or ``None`` to
        clear the override and go back to the
        ``min(16, multiprocessing.cpu_count())`` default.

Raises:
    ValueError: If ``value`` is given and is not a positive integer.
```

### `rockface.config.resolve_max_workers`

```python
resolve_max_workers(explicit: Optional[int]) -> int
```

```
Resolve the worker count to actually use for one call.

Precedence: ``explicit`` (a call's own ``max_workers=`` argument)
> the global default set via :func:`set_max_workers` > ``min(16,
multiprocessing.cpu_count())``.

Args:
    explicit: The ``max_workers`` value passed to this particular
        call, if any.

Returns:
    The worker process count to use, always >= 1.
```

### `rockface.patching.find_channel_source`

```python
find_channel_source(patch_dir: Union[str, pathlib.Path], y: int, x: int, channel: int) -> Optional[pathlib.Path]
```

```
Locate one patch coordinate's raw file for a specific polarization channel.

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
```

## Command-line interface

```console
$ rockface --help

usage: rockface [-h] [--output OUTPUT] [--patch-size PATCH_SIZE]
                [--stride STRIDE] [--mask-source-channel MASK_SOURCE_CHANNEL]
                [--max-workers MAX_WORKERS] [--restitch RESTITCH]
                [--coords-file COORDS_FILE] [--patch-formats PATCH_FORMATS]
                [--mask-formats MASK_FORMATS] [--verbose]
                czi_path

Process a Zeiss CZI petrographic thin-section slide: extract patches and
generate pore-space masks.

positional arguments:
  czi_path              Path to the .czi slide file.

options:
  -h, --help            show this help message and exit
  --output OUTPUT       Output base directory (default: ./output_project).
  --patch-size PATCH_SIZE
                        Patch side length, in pixels (default: 4096).
  --stride STRIDE       Step between patches, in pixels (default: 3800).
  --mask-source-channel MASK_SOURCE_CHANNEL
                        Polarization channel to segment pore masks from
                        (default: 0, the slide's own unpolarized/normal view).
                        Fixed rule of the segmentation method -- change only
                        if your slide's unpolarized channel is genuinely
                        indexed differently.
  --max-workers MAX_WORKERS
                        Worker process count (default: min(16, cpu_count())).
  --restitch RESTITCH   Comma-separated channel names to restitch into full
                        mosaics, e.g. 'pol0' or 'pol0,pol3'. Omit to skip
                        restitching.
  --coords-file COORDS_FILE
                        Path to a JSON file containing a list of [y, x] patch
                        coordinates to process, e.g. '[[0, 0], [0, 3800]]',
                        instead of the full grid. Applies to both patch
                        extraction and mask generation.
  --patch-formats PATCH_FORMATS
                        Comma-separated formats to save per patch: any of
                        npy,npz,png,jpg (default: npy,png). Restitching
                        (--restitch) needs npy or npz.
  --mask-formats MASK_FORMATS
                        Comma-separated formats to save per mask: any of
                        npy,npz,png,jpg (default: png).
  --verbose             Print percentage progress for each stage, and enable
                        INFO-level logging.

```

## Advanced: module-level helper functions

These back the multiprocessing workers and internal machinery -- not part of the stable public API, but documented here for anyone extending the package.

### `rockface.czi`

- `rockface.czi.available_channels_from_handle(handle: aicspylibczi.CziFile.CziFile) -> List[int]` -- Discover the channel (dimension ``C``) indices present in a CZI handle.
- `rockface.czi.convert_array_to_rgb(array: numpy.ndarray, pixel_format: str = 'unknown') -> numpy.ndarray` -- Convert a raw CZI channel array to RGB order, ``uint8``.
- `rockface.czi.load_array_for_npz(source: Union[numpy.ndarray, str, pathlib.Path]) -> numpy.ndarray` -- Resolve ``save_npz``'s dual input: an array as-is, or a ``.npy`` path to load.
- `rockface.czi.metadata_root_from_handle(handle: aicspylibczi.CziFile.CziFile) -> Optional[Any]` -- Return a CZI handle's metadata XML root element.
- `rockface.czi.pixel_format_from_handle(handle: aicspylibczi.CziFile.CziFile) -> str` -- Read the ``PixelType`` field from a CZI handle's metadata.
- `rockface.czi.pixel_sizes_from_handle(handle: aicspylibczi.CziFile.CziFile) -> Tuple[Optional[float], Optional[float]]` -- Read the physical pixel size (X and Y) from a CZI handle's metadata, in meters.
- `rockface.czi.save_image_file(image: PIL.Image.Image, output_path: Union[str, pathlib.Path], lossless: bool = True, jpeg_quality: int = 95) -> pathlib.Path` -- Save a PIL image as PNG or JPEG, atomically. See :meth:`CZI.save_image`.
- `rockface.czi.save_npy_array(array: numpy.ndarray, output_path: Union[str, pathlib.Path]) -> pathlib.Path` -- Save an array as ``.npy``, atomically. See :meth:`CZI.save_npy`.
- `rockface.czi.save_npz_array(array: numpy.ndarray, output_path: Union[str, pathlib.Path], compressed: bool = True) -> pathlib.Path` -- Save an array as ``.npz``, atomically. See :meth:`CZI.save_npz`.

### `rockface.masks`

- `rockface.masks.composite_overlay_on_image(base_rgb: numpy.ndarray, overlay_rgba: numpy.ndarray) -> numpy.ndarray` -- Alpha-composite a transparent mask overlay on top of an RGB image.
- `rockface.masks.generate_pore_mask_array(bgr_array: numpy.ndarray) -> numpy.ndarray` -- Segment resin-impregnated pore space (blue/cyan) in a BGR image.
- `rockface.masks.generate_transparent_overlay_array(mask: numpy.ndarray, color: Tuple[int, int, int] = (0, 255, 0), filled_mode: bool = False, thickness: int = 2) -> numpy.ndarray` -- Build a transparent RGBA overlay from a binary mask.
