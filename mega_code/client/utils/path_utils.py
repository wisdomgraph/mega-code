"""Path matching utilities for session enrichment.

Pure utility functions with no enterprise dependencies.
"""

import logging
from pathlib import Path

logger = logging.getLogger(__name__)


def normalize_path(path: str | Path) -> Path:
    """Normalize path for comparison.

    Resolves symlinks, relative paths, and converts to lowercase
    for case-insensitive matching.

    Args:
        path: Path to normalize (string or Path object)

    Returns:
        Normalized Path object (lowercase, resolved)

    Example:
        >>> normalize_path("/Users/Foo/Project")
        PosixPath('/users/foo/project')
    """
    return Path(str(Path(path).resolve()).lower())


def should_include_session(
    claude_project_path: str | Path,
    mega_code_project_paths: set[Path],
    exact_match: bool = False,
) -> bool:
    """Check if Claude session should be included based on project path.

    Args:
        claude_project_path: Project path from Claude session
        mega_code_project_paths: Set of normalized MEGA-Code project paths
        exact_match: If False, subdirectories are also included (default).
                    If True, only exact matches are included.

    Returns:
        True if session should be included

    Example:
        >>> mega_paths = {normalize_path("/Users/foo/project")}
        >>> should_include_session("/Users/foo/project", mega_paths)
        True
        >>> should_include_session("/Users/foo/project/backend", mega_paths)
        True
        >>> should_include_session("/Users/foo/project/backend", mega_paths, exact_match=True)
        False
    """
    if not mega_code_project_paths:
        return False

    normalized = normalize_path(claude_project_path)

    for mega_path in mega_code_project_paths:
        # Exact match
        if normalized == mega_path:
            return True

        # Subdirectory match (only if exact_match is False)
        if not exact_match:
            try:
                if normalized.is_relative_to(mega_path):
                    return True
            except (ValueError, TypeError):
                # is_relative_to raises ValueError if paths are on different drives (Windows)
                # or TypeError for invalid inputs - continue to next path
                pass

    return False
