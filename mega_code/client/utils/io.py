"""File I/O utilities for the client package."""

from pathlib import Path


def atomic_write(target: Path, content: str, *, encoding: str = "utf-8") -> None:
    """Write content to a file atomically via a temp file.

    Writes to a .tmp sibling, then renames to the target path.
    If the write fails, the temp file is cleaned up and the
    original exception is re-raised.

    Args:
        target: Destination file path.
        content: String content to write.
        encoding: File encoding (default utf-8).
    """
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(".tmp")
    try:
        tmp.write_text(content, encoding=encoding)
        tmp.rename(target)
    except OSError:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        raise
