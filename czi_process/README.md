# RockFace

Petrographic thin-section image processing toolkit (LCCMat / Petrobras).

RockFace turns Zeiss CZI polarized-light microscope scans of rock thin
sections into tiled patches and pore-space segmentation masks, for
porosity analysis and ML dataset preparation.

## Install

```bash
pip install -e .
```

## Quick start

```python
import rockface

czi = rockface.CZI("sample.czi", output_base="./output_project")
czi.save_metadata()

coords = czi.patching.get_patches(patch_size=4096, stride=3800)
czi.save_patching_data(coords, patch_size=4096, stride=3800,
                        polarization_channels=czi.overview["channels"])

czi.patching.run(patch_size=4096, stride=3800)  # every channel, each its own raw image
czi.mask.run()  # masks generated from channel 0 (the slide's unpolarized view)

mosaic = czi.patching.restitch(stride=3800)  # channel="pol0" by default
czi.patching.generate_manifest(
    patch_dir=czi.output_dir / "patches",
    mask_dir=czi.output_dir / "masks",
    output_path=czi.output_dir / "manifest.json",
)
```

Or, the whole flow in one call:

```python
import rockface

czi = rockface.CZI("sample.czi")
result = czi.run(patch_size=4096, stride=3800)
```

### Processing a specific subset of patches

Both `czi.patching.run()` and `czi.mask.run()` accept an optional `coords`
list of `(y, x)` pairs, instead of always processing the full grid — useful
for a region of interest, re-running only a few failed patches, or a quick
trial before committing to the whole slide. Masking always operates "per
patch" (it needs a patch's extracted channel-0 image to exist first) and
only ever from channel 0 (`source_channel=0` by default — see
[USAGE.md §6](./USAGE.md#6-channels-and-why-masking-is-always-channel-0)); it
never processes the whole slide as a single unit, and never masks a
polarized channel.

```python
coords = [(0, 0), (0, 3800), (3800, 0)]

czi.patching.run(coords=coords, patch_size=4096, stride=3800)
czi.mask.run(coords=coords)          # masks exactly those 3 patches

# czi.mask.run() with no coords processes every *_pol0.npy already on disk
czi.mask.run()
```

`czi.run(coords=coords, ...)` passes the same subset through to both stages
automatically, and records in `patching_metadata.json` whether the run was
partial (`is_partial`) and how many patches the full grid would have had
(`full_grid_total`) — so a partial run's metadata never silently implies
full coverage. Note that a mosaic built with `czi.patching.restitch()` from
a partial patch set will have zero-filled gaps where patches are missing.

### Choosing output formats

Both `czi.patching.run()` and `czi.mask.run()` accept a `formats` list —
any subset of `{"npy", "npz", "png", "jpg"}`:

```python
# Patches: raw arrays only (skip PNGs entirely)
czi.patching.run(patch_size=4096, stride=3800, formats=["npy"])

# Masks: raw binary mask array + visual overlay, but no PNG-only default
czi.mask.run(formats=["npy", "png"])
```

For patches, `"npy"`/`"npz"` save the raw array (native channel order,
lossless — what `restitch()` and most downstream/ML processing need);
`"png"`/`"jpg"` save an RGB-converted visual copy. Default: `["npy", "png"]`
(matches the original behavior).

For masks, `"npy"`/`"npz"` save the raw binary mask array (0/255); `"png"`
saves the transparent RGBA overlay (used for visual inspection / the UI
mosaic); `"jpg"` flattens the overlay onto a solid black background, since
JPEG has no alpha channel. Default: `["png"]`.

Two implications worth knowing:
- `czi.patching.restitch()` needs a raw format (`"npy"` or `"npz"`) to have
  been saved — if you only saved `"png"`/`"jpg"`, it raises a clear error
  telling you so.
- `legacy/petrophysical_properties.py` reads `{base}_mask.png` specifically —
  if you compute porosity stats via `include_petrophysical=True`, your
  `mask_formats` must include `"png"`.

`czi.run(...)` exposes both as `patch_formats=` / `mask_formats=`, and
validates these two implications up front (raises before any processing
starts, rather than failing midway through a long run):

```python
czi.run(patch_size=4096, stride=3800, patch_formats=["npy"], mask_formats=["npy", "png"])
```

### Progress reporting

Pass `verbose=True` to `czi.patching.run()`, `czi.mask.run()`, or `czi.run()`
to print rate-limited percentage progress to stderr as patches/masks
complete, independent of the `on_progress` callback (both fire if both are given):

```python
czi.patching.run(patch_size=4096, stride=3800, verbose=True)
# [patching]  10%  patch 10/97
# [patching]  20%  patch 20/97
# ...
```

### A global default for `max_workers`

Set a worker-process count once for the whole session, instead of
repeating `max_workers=` on every call:

```python
rockface.set_max_workers(4)

czi.patching.run()   # uses 4 workers
czi.mask.run()       # also uses 4 workers
czi.patching.run(max_workers=8)  # explicit per-call value still wins
```

### Overlaying the mask on the patch/slide image

Two variants: a single patch composited with its mask, or the full
slide reconstruction composited with the full mask reconstruction.

```python
# (a) One patch, mask drawn on top
import numpy as np
channel0_array = np.load(czi.output_dir / "patches" / "patch_y0_x0_pol0.npy")
overlaid_patch = czi.mask.overlay_on_patch(channel0_array, pixel_format=czi._pixel_format())

# (b) Whole slide, mask reconstruction drawn on top
overlaid_slide = czi.patching.restitch_with_mask_overlay(stride=3800)  # source_channel=0 by default
```

See [`USAGE.md`](./USAGE.md) for the full set of task-oriented recipes
(single patch + mask, parallel patching/masking, metadata, manifest,
and all three restitch variants).

### Note: channels and masking

Every polarization channel (typically `0`-`6`) is extracted and saved
as its own raw image (`patch_y{y}_x{x}_pol{c}.*`) — no composite
"normal" image is synthesized. Channel `0` **is** the slide's own
unpolarized/normal view, and pore masks are always segmented from it
alone (never from a polarized channel) — a fixed rule of the
segmentation method, not a configurable choice for regular use. See
[USAGE.md §6](./USAGE.md#6-channels-and-why-masking-is-always-channel-0)
for details.

## Terminal usage

```bash
rockface path/to/sample.czi --patch-size 4096 --stride 3800 --output ./output_project --verbose
```

Process only specific patches from the terminal with `--coords-file`
(a JSON file containing a list of `[y, x]` pairs):

```bash
echo '[[0, 0], [0, 3800], [3800, 0]]' > coords.json
rockface path/to/sample.czi --coords-file coords.json --verbose
```

Choose output formats with `--patch-formats` / `--mask-formats` (comma-separated):

```bash
rockface path/to/sample.czi --patch-formats npy --mask-formats npy,png
```

## Package layout

- `rockface/czi.py` — the `CZI` class: opens a slide, reads/saves metadata,
  and provides low-level image I/O and color-space helpers.
- `rockface/patching.py` — the `Patching` class (`czi.patching`): computes
  the patch grid, extracts patches in parallel, and reassembles
  ("restitches") patches back into a full mosaic.
- `rockface/masks.py` — the `Mask` class (`czi.mask`): generates pore-space
  segmentation masks from extracted patches.
- `rockface/pipeline.py` — end-to-end orchestration (`run_pipeline`) and the
  `rockface` command-line entry point.
- `rockface/identity.py` — stable slide identifier hashing.
- `rockface/config.py` — session-wide defaults (currently `max_workers`),
  set via `rockface.set_max_workers()`.
- `legacy/` — `server.py` (FastAPI UI backend) and
  `petrophysical_properties.py` (porosity statistics), kept for reference.
  Not part of the installable package; not covered by this refactor.

## Further documentation

- [`USAGE.md`](./USAGE.md) — task-oriented recipes (single patch + mask,
  parallel patching/masking, metadata, manifest, restitching).
- [`API_REFERENCE.md`](./API_REFERENCE.md) — full method/parameter reference,
  generated from the installed package's docstrings.
