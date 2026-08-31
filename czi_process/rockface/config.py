"""Session-wide default settings for rockface.

Currently holds a single knob: the default worker-process count used
by every parallelizable task (:meth:`rockface.patching.Patching.run`,
:meth:`rockface.masks.Mask.run`, and :func:`rockface.pipeline.run_pipeline`).

Example:
    >>> import rockface
    >>> rockface.set_max_workers(4)
    >>> czi = rockface.CZI("sample.czi")
    >>> czi.patching.run()   # uses 4 workers, no need to pass max_workers=4 again
    >>> czi.mask.run()       # also uses 4 workers
    >>> czi.patching.run(max_workers=8)  # explicit per-call value still wins

Precedence, resolved by :func:`resolve_max_workers`: an explicit
``max_workers=`` argument to a call always wins; otherwise the value
set here via :func:`set_max_workers` is used; otherwise the library
falls back to ``min(16, multiprocessing.cpu_count())``, same as
before this setting existed.
"""

import multiprocessing
from typing import Optional

#: Session-wide default worker count. ``None`` means "no global
#: override set" -- callers fall back to ``min(16, cpu_count())``.
#: Set via :func:`set_max_workers`, not by assigning this directly
#: (that also works since it's just a module attribute, but the
#: function name documents intent at call sites).
max_workers: Optional[int] = None


def set_max_workers(value: Optional[int]) -> None:
    """Set the session-wide default worker-process count.

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
    """
    global max_workers
    if value is not None and (not isinstance(value, int) or value < 1):
        raise ValueError(f"max_workers must be a positive integer or None, got {value!r}.")
    max_workers = value


def resolve_max_workers(explicit: Optional[int]) -> int:
    """Resolve the worker count to actually use for one call.

    Precedence: ``explicit`` (a call's own ``max_workers=`` argument)
    > the global default set via :func:`set_max_workers` > ``min(16,
    multiprocessing.cpu_count())``.

    Args:
        explicit: The ``max_workers`` value passed to this particular
            call, if any.

    Returns:
        The worker process count to use, always >= 1.
    """
    if explicit is not None:
        return explicit
    if max_workers is not None:
        return max_workers
    return min(16, multiprocessing.cpu_count())
