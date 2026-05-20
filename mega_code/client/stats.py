"""Project mapping and directory helpers for MEGA-Code sessions.

Owns the canonical mapping from project paths to data-dir folder names
and provides the anchor directory used by the sync ledgers
(``claude-sync-ledger.json`` / ``codex-sync-ledger.json``).
"""

import hashlib
import json
import re
from pathlib import Path

from mega_code.client.dirs import data_dir as get_data_dir
from mega_code.client.utils.io import atomic_write

# =============================================================================
# Project Mapping Functions
# =============================================================================


def get_mapping_file() -> Path:
    """Get the path to mapping.json."""
    return get_data_dir() / "mapping.json"


def load_mapping() -> dict[str, str]:
    """Load mapping.json, return empty dict if not exists.

    Returns:
        Dict mapping folder_name -> project_dir
    """
    mapping_file = get_mapping_file()
    if not mapping_file.exists():
        return {}
    try:
        return json.loads(mapping_file.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def save_mapping(mapping: dict[str, str]) -> None:
    """Atomically save mapping.json."""
    atomic_write(get_mapping_file(), json.dumps(mapping, indent=2, ensure_ascii=False))


def get_project_folder_name(project_dir: str) -> str:
    """Generate readable folder name: <basename>_<hash8>.

    Examples:
        /Users/foo/mega-code → mega-code_a1b2c3d4
        /Users/foo/my project → my_project_b2c3d4e5
        /tmp → tmp_c3d4e5f6

    Args:
        project_dir: The project directory path

    Returns:
        Folder name in format <sanitized_basename>_<hash8>
    """
    normalized = str(Path(project_dir).resolve())
    basename = Path(normalized).name or "root"
    # Sanitize basename: keep alphanumeric, dash, underscore
    safe_name = re.sub(r"[^a-zA-Z0-9_-]", "_", basename)
    # Truncate long names
    safe_name = safe_name[:32]
    # Remove leading/trailing underscores
    safe_name = safe_name.strip("_") or "project"
    hash_suffix = hashlib.sha256(normalized.encode()).hexdigest()[:8]
    return f"{safe_name}_{hash_suffix}"


def register_project(project_dir: str) -> str:
    """Register project in mapping if not exists, return folder name.

    Args:
        project_dir: The project directory path

    Returns:
        The folder name for this project
    """
    folder_name = get_project_folder_name(project_dir)
    mapping = load_mapping()
    if folder_name not in mapping:
        mapping[folder_name] = str(Path(project_dir).resolve())
        save_mapping(mapping)
    return folder_name


def lookup_project_folder(project_dir: str) -> str | None:
    """Find folder name for project_dir from mapping.

    Args:
        project_dir: The project directory path

    Returns:
        Folder name if found, None otherwise
    """
    folder_name = get_project_folder_name(project_dir)
    mapping = load_mapping()
    return folder_name if folder_name in mapping else None


# =============================================================================
# Session Directory Functions
# =============================================================================


def get_projects_dir() -> Path:
    """Get the projects directory."""
    projects_dir = get_data_dir() / "projects"
    projects_dir.mkdir(parents=True, exist_ok=True)
    return projects_dir


def get_project_sessions_dir(project_dir: str) -> Path:
    """Get project-scoped sessions directory, registering project if needed.

    Args:
        project_dir: The project directory path

    Returns:
        Path to the project's sessions directory
    """
    folder_name = register_project(project_dir)
    project_sessions_dir = get_projects_dir() / folder_name
    project_sessions_dir.mkdir(parents=True, exist_ok=True)
    return project_sessions_dir


def find_session_dir(session_id: str) -> Path | None:
    """Find session directory by searching all projects.

    Args:
        session_id: The session identifier

    Returns:
        Path to session directory if found, None otherwise
    """
    projects_dir = get_projects_dir()
    if not projects_dir.exists():
        return None

    for project_folder in projects_dir.iterdir():
        if not project_folder.is_dir():
            continue
        session_dir = project_folder / session_id
        if session_dir.exists():
            return session_dir

    return None


def resolve_project_path(project_arg: str) -> Path:
    """Resolve a project argument to a mega-code data folder path.

    Supports three input formats:
    1. @prefix or name prefix: fuzzy match against mapping.json keys
       e.g. '@mega-code' or 'mega-code' -> ~/.local/share/mega-code/projects/mega-code_b39e0992/
    2. Folder name with hash: direct lookup
       e.g. 'mega-code_b39e0992' -> ~/.local/share/mega-code/projects/mega-code_b39e0992/
    3. Absolute/relative path: resolve via get_project_sessions_dir()
       e.g. '/Users/foo/my-project' -> ~/.local/share/mega-code/projects/my-project_a1b2c3d4/

    Args:
        project_arg: Project identifier (with optional @ prefix).

    Returns:
        Path to the mega-code project data folder.

    Raises:
        ValueError: If the project cannot be resolved.
    """
    import logging

    _logger = logging.getLogger(__name__)

    # Strip @ prefix if present (Claude Code autocomplete adds this)
    arg = project_arg.lstrip("@").strip()

    if not arg:
        raise ValueError("Empty project argument")

    projects_dir = get_projects_dir()
    mapping = load_mapping()

    # Strategy 1: Exact folder name match (e.g. 'mega-code_b39e0992')
    candidate = projects_dir / arg
    if candidate.is_dir():
        _logger.info(f"Resolved project by exact folder name: {arg}")
        return candidate

    # Strategy 2: Prefix match against mapping keys (e.g. 'mega-code')
    matches = [
        folder_name
        for folder_name in mapping
        if folder_name.startswith(arg) and (projects_dir / folder_name).is_dir()
    ]
    if len(matches) == 1:
        _logger.info(f"Resolved project by prefix '{arg}' -> {matches[0]}")
        return projects_dir / matches[0]
    if len(matches) > 1:
        match_list = ", ".join(matches)
        raise ValueError(
            f"Ambiguous project prefix '{arg}' matches: {match_list}. "
            f"Use a more specific name or the full folder name."
        )

    # Strategy 3: Treat as filesystem path, resolve via stats
    path = Path(arg).expanduser().resolve()
    if path.is_dir():
        _logger.info(f"Resolved project by path: {path}")
        return get_project_sessions_dir(str(path))

    raise ValueError(
        f"Cannot resolve project '{project_arg}'. "
        f"Use: @<name-prefix>, <folder_name>, or /path/to/project"
    )
