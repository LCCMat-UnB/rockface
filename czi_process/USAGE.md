# RockFace -- Usage Guide

Task-oriented recipes for common RockFace workflows. For full parameter
and method reference, see [`API_REFERENCE.md`](./API_REFERENCE.md) --
this document shows *how to accomplish a specific task*, that one
documents *every parameter each method accepts*.

All examples assume:

```python
import rockface

czi = rockface.CZI("sample.czi", output_base="./output_project")
```

Opening a `CZI` computes a stable slide ID (`czi.id`), creates
`czi.output_dir = output_base/{czi.id}/`, and reads the slide's
overview (bounding box, pixel scale, available channels) into
`czi.overview`.

---

## 1. Run a single patch and its mask

Useful for testing the pipeline on one region before committing to a
full slide, or for debugging a specific coordinate.

```python
# Extract one patch at (y=0, x=0)
result = czi.patching.process_single_patch(
    coord=(0, 0),
    patch_size=4096,
    formats=["npy", "png"],
)
print(result)  # {"status": "ok", "channels": [...], "normal_is_redundant": False}

# Generate its mask
mask_ok = czi.mask.process_patch(
    source_npy_path=czi.output_dir / "patches" / "patch_y0_x0_normal.npy",
    mask_dir=czi.output_dir / "masks",
    formats=["npy", "png"],
)
```

**Note:** if the slide (or this run) only has one polarization
channel available, no separate `patch_y0_x0_normal.npy` is written --
see [Section 6](#6-why-does-a-patch-sometimes-have-no-normal-file).
Use `rockface.patching.find_normal_source(patch_dir, y, x)` to resolve
the right source file instead of hardcoding the `_normal` filename:

```python
from rockface.patching import find_normal_source

source = find_normal_source(czi.output_dir / "patches", 0, 0)
mask_ok = czi.mask.process_patch(source, czi.output_dir / "masks", formats=["npy", "png"])
```

### Single patch with its mask overlaid on top

To get one composited image (patch + mask drawn on it), instead of two
separate files:

```python
import numpy as np

normal_array = np.load(source)  # or czi.output_dir / "patches" / "patch_y0_x0_normal.npy"
overlaid = czi.mask.overlay_on_patch(normal_array, pixel_format=czi._pixel_format())

from PIL import Image
czi.save_image(Image.fromarray(overlaid), czi.output_dir / "patches" / "patch_y0_x0_overlay.png")
```

Pass a mask you already computed to avoid re-segmenting:

```python
mask = czi.mask.generate_pore_mask(normal_array)  # expects BGR input
overlaid = czi.mask.overlay_on_patch(normal_array, mask=mask, filled_mode=True)
```

---

## 2. Patching and masking in parallel (whole slide)

The full pipeline, run stage by stage:

```python
czi.save_metadata()

coords = czi.patching.get_patches(patch_size=4096, stride=3800)
czi.save_patching_data(coords, patch_size=4096, stride=3800, full_grid_total=len(coords))

czi.patching.run(patch_size=4096, stride=3800, formats=["npy", "png"], verbose=True)
czi.mask.run(formats=["npy", "png"], verbose=True)
```

Or the same thing in one call:

```python
summary = czi.run(
    patch_size=4096,
    stride=3800,
    patch_formats=["npy", "png"],
    mask_formats=["npy", "png"],
    verbose=True,
)
```

Both `Patching.run()` and `Mask.run()` parallelize internally with
`multiprocessing.Pool` -- "patching and masking in parallel" in the
sense of each stage being internally parallel across patches. Masking
always depends on patches already existing on disk, so the two stages
themselves run one after the other (mask generation reads each
patch's saved `_normal`/`_pol{c}` file) -- `czi.run()` sequences them
correctly for you.

To control how many worker processes are used, either pass
`max_workers=` to a specific call, or set it once for the whole
session (see [Section 7](#7-set-a-global-max_workers-for-every-parallel-call)).

---

## 3. Generate slide metadata

```python
metadata_path = czi.save_metadata()
```

Writes `image_metadata.json` into `czi.output_dir`, with the slide's
ID, original filename, structural dimensions, physical pixel scale,
and available channels. Can be called immediately after opening the
slide -- does not require patching to have run.

---

## 4. Generate the manifest

The manifest lists every patch that has both a normal image and a
mask, with relative (or absolute) file paths -- typically consumed by
a viewer or a downstream ML dataset step.

```python
count = czi.patching.generate_manifest(
    patch_dir=czi.output_dir / "patches",
    mask_dir=czi.output_dir / "masks",
    output_path=czi.output_dir / "manifest.json",
)
print(f"{count} patches indexed")
```

Pass `url_base="http://localhost:8000/results"` to get absolute URLs
instead of relative paths (matches the original pre-refactor
behavior, useful if serving files through `legacy/server.py`).

---

## 5. Reconstruct (restitch) the slide, the mask, and both overlaid

All three read patch/mask files back from disk and reassemble them
into one full-resolution array, using non-overlapping `stride x
stride` windows per patch (no double-counted overlap at patch seams).

### Slide alone (any channel)

```python
normal_mosaic = czi.patching.restitch(stride=3800, channel="normal")
pol0_mosaic = czi.patching.restitch(stride=3800, channel="pol0")
```

Requires patches to have been saved with `formats` including `"npy"`
or `"npz"` (raw arrays) -- `"png"`/`"jpg"`-only patches can't be
restitched, and this is raised as a clear `FileNotFoundError` if you
try.

### Mask alone

There is no dedicated "restitch a mask alone" method (masks use the
same non-overlapping-window logic, but are named `{base}_mask.*`
rather than `{base}_{channel}.*`) -- if you want just the mask mosaic
as its own array, assemble it the same way `restitch_with_mask_overlay`
does internally, or call that method and discard the compositing by
inspecting `mask_dir` directly:

```python
import numpy as np
from pathlib import Path
import re

mask_dir = czi.output_dir / "masks"
bbox = czi.overview["bbox"]
canvas = np.zeros((bbox["h"], bbox["w"]), dtype=np.uint8)
pattern = re.compile(r"patch_y(\d+)_x(\d+)_mask\.npy$")
for path in mask_dir.glob("patch_y*_x*_mask.npy"):
    match = pattern.match(path.name)
    if not match:
        continue
    y, x = int(match.group(1)), int(match.group(2))
    array = np.load(path)
    eff_h, eff_w = min(3800, bbox["h"] - y), min(3800, bbox["w"] - x)
    canvas[y:y + eff_h, x:x + eff_w] = array[:eff_h, :eff_w]
```

(A dedicated `Mask.restitch()` is a natural follow-up if this becomes
a common need on its own -- not implemented now since every real use
case so far wants either the raw patch mosaic or the combined overlay
below.)

### Slide + mask, overlaid together

```python
overlaid = czi.patching.restitch_with_mask_overlay(stride=3800)

from PIL import Image
czi.save_image(Image.fromarray(overlaid), czi.output_dir / "mosaic_with_mask.png")
```

Requires both the normal patches (`npy`/`npz`) and the masks
(`npy`/`npz`, from `czi.mask.run(formats=[...])`) to already be on
disk. Defaults to a **filled** overlay (`filled_mode=True`) rather
than contours-only, since solid fill reads more clearly at full-slide
scale; pass `filled_mode=False` for contours instead.

**Memory note:** restitching a full slide loads the whole mosaic into
RAM -- `restitch_with_mask_overlay` holds two full mosaics (normal +
mask) plus the composited result at once. For very large slides,
restitch one region/channel at a time instead of the whole thing.

---

## 6. Why does a patch sometimes have no `_normal` file?

If a slide (or a given run's `polarization_channels`) has only **one**
polarization channel, synthesizing a "normal" composite from it is a
no-op -- the mean/max of one array is that same array. In that case,
`Patching.run()` no longer writes a redundant `_normal` file that
would just be a byte-for-byte copy of `_pol{c}`; the per-patch result
dict reports this via `"normal_is_redundant": True`.

Every RockFace method that reads a patch's "normal" image (`Mask.run`,
`Mask.process_patch`, `Patching.restitch(channel="normal")`,
`restitch_with_mask_overlay`) already knows how to fall back to that
single `_pol{c}` file automatically -- you never need to special-case
this yourself. If you're calling into patch files directly, use
`rockface.patching.find_normal_source(patch_dir, y, x)` rather than
assuming `patch_y{y}_x{x}_normal.npy` always exists.

---

## 7. Set a global `max_workers` for every parallel call

By default, every parallel call (`Patching.run`, `Mask.run`,
`czi.run`/`run_pipeline`) uses `min(16, os.cpu_count())` workers. To
set a different default once, for the rest of the session:

```python
rockface.set_max_workers(4)

czi.patching.run()   # uses 4 workers
czi.mask.run()       # also uses 4 workers, no need to repeat max_workers=4
```

An explicit `max_workers=` argument on a specific call always
overrides the global default:

```python
czi.patching.run(max_workers=8)  # uses 8, ignoring the global default of 4
```

Call `rockface.set_max_workers(None)` to clear the override and go
back to `min(16, os.cpu_count())`.

---

## 8. Command-line equivalent

Most of the above is also available via the `rockface` command
(installed by `pip install -e .`):

```bash
# Full pipeline, whole slide
rockface sample.czi --output ./output_project --patch-size 4096 --stride 3800 --verbose

# Restitch the normal mosaic afterwards
rockface sample.czi --restitch normal

# Only a specific set of coordinates (patching + masking both restricted to these)
rockface sample.czi --coords-file coords.json

# Choose output formats explicitly
rockface sample.czi --patch-formats npy,png --mask-formats npy,png
```

Where `coords.json` is a JSON list of `[y, x]` pairs:

```json
[[0, 0], [0, 3800], [3800, 0]]
```

The CLI does not expose `restitch_with_mask_overlay` or
`overlay_on_patch` directly (both are compositing conveniences meant
for interactive/API use) -- run `--restitch normal`, then call
`czi.patching.restitch_with_mask_overlay(...)` from Python against the
same `--output` directory.
