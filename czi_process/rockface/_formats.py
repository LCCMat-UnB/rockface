"""
_formats.py
==================================================================
Small internal helper for validating a ``formats`` argument against a
module's set of supported output formats. Shared by
:mod:`rockface.patching` and :mod:`rockface.masks`, each of which has
its own valid-format set (``_VALID_PATCH_FORMATS`` /
``_VALID_MASK_FORMATS``). Not part of the public API.
"""

from typing import Iterable, Optional, Sequence


def validate_formats(
    formats: Optional[Sequence[str]],
    valid_formats: Iterable[str],
    param_name: str = "formats",
) -> None:
    """Raise a clear error for an unknown format, before any work starts.

    Fails fast at the call site (``Patching.run``, ``Mask.run``, ...)
    rather than deep inside a multiprocessing worker, where a bad value
    would otherwise only surface as a per-patch/per-mask error dict.

    Args:
        formats: The formats requested by the caller, or ``None``
            (meaning "use the default" -- always valid, a no-op here).
        valid_formats: The set of formats the caller's module supports.
        param_name: Name used in the error message.

    Raises:
        ValueError: If ``formats`` contains anything not in ``valid_formats``.
    """
    if formats is None:
        return
    invalid = sorted(set(formats) - set(valid_formats))
    if invalid:
        raise ValueError(f"Unknown {param_name}: {invalid}. Valid options: {sorted(valid_formats)}.")
