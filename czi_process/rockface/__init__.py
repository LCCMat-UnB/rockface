"""
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
"""

__version__ = "0.1.0"

from .identity import slide_id
from .config import set_max_workers
from .masks import Mask
from .patching import Patching
from .czi import CZI

__all__ = ["CZI", "Patching", "Mask", "slide_id", "set_max_workers", "__version__"]
