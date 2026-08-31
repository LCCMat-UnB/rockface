"""
_progress.py
==================================================================
Small internal helper shared by :meth:`Patching.run` and
:meth:`Mask.run` for progress reporting. Not part of the public API.

Turns a processing loop's per-item completions into two things at
once: a rate-limited percentage line printed to stderr when
``verbose=True``, and a full-resolution pass-through to a
caller-supplied ``on_progress`` callback -- the two are independent,
so a caller can use either, both, or neither.
"""

import sys
from typing import Callable, Optional


def make_reporter(
    stage: str,
    total: int,
    verbose: bool = False,
    on_progress: Optional[Callable[[str, float, str], None]] = None,
) -> Callable[[int, str], None]:
    """Build a ``report(done, message)`` closure for a progress loop.

    Args:
        stage: Stage name passed through to ``on_progress`` (e.g.
            ``"patching"``, ``"masking"``).
        total: Total number of items expected to complete. If ``0``,
            progress is reported as complete (``1.0``) immediately to
            avoid a division by zero.
        verbose: If ``True``, prints one line per whole percentage
            point reached (plus always on the final item) to stderr --
            this keeps output readable even for runs with thousands of
            items, rather than printing on every single completion.
        on_progress: Optional callback ``on_progress(stage, progress,
            message)``, called at full resolution (every item)
            regardless of ``verbose``, with ``progress`` in
            ``[0.0, 1.0]``.

    Returns:
        A function ``report(done, message)`` to call after each item
        completes, where ``done`` is the number of items completed so far.
    """
    last_percent = {"value": -1}

    def report(done: int, message: str) -> None:
        progress = done / total if total else 1.0
        if verbose:
            percent = int(progress * 100)
            if percent != last_percent["value"] or done >= total:
                print(f"[{stage}] {percent:3d}%  {message}", file=sys.stderr)
                last_percent["value"] = percent
        if on_progress is not None:
            on_progress(stage, progress, message)

    return report
