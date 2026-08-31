"""
identity.py
==================================================================
Stable identifier for RockFace slides.

Every :class:`~rockface.czi.CZI` instance is identified by a short,
deterministic hash rather than by its filename alone, so that output
directories and metadata files never collide between two slides that
happen to share a name (a common situation across labs, batches, or
re-scans), while still keeping the original filename recorded
alongside the hash for human reference wherever the ID is used.
"""

import hashlib
from pathlib import Path
from typing import Union


def slide_id(path: Union[str, Path], length: int = 12) -> str:
    """Compute a stable, deterministic identifier for a CZI slide file.

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
    """
    resolved = Path(path).resolve()
    stat = resolved.stat()  # raises FileNotFoundError if missing -- fail fast
    payload = f"{resolved}:{stat.st_size}:{stat.st_mtime_ns}".encode("utf-8")
    return hashlib.sha256(payload).hexdigest()[:length]
