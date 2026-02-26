"""Statistics aggregator for MEGA-Code sessions (client edition).

Handles session file I/O: directories, stats, metadata, events.
"""

import hashlib
import json
import os
import re
from pathlib import Path

from mega_code.client.schema import (
    SessionStats,
    CollectorSessionMetadata,
    estimate_cost,
    utcnow_iso,
)
from mega_code.client.utils.io import atomic_write

# Default data directory
DEFAULT_DATA_DIR = Path.home() / ".local" / "mega-code"


def get_data_dir() -> Path:
    """Get the data directory, creating it if necessary."""
    data_dir = Path(os.environ.get("MEGA_CODE_DATA_DIR", DEFAULT_DATA_DIR))
    data_dir.mkdir(parents=True, exist_ok=True)
    return data_dir


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


def get_session_dir(session_id: str, project_dir: str | None = None) -> Path:
    """Get or create directory for a specific session.

    Args:
        session_id: The session identifier
        project_dir: The project directory (required for new sessions)

    Returns:
        Path to the session directory
    """
    if project_dir:
        session_dir = get_project_sessions_dir(project_dir) / session_id
    else:
        # Search for session in all project directories
        session_dir = find_session_dir(session_id)
        if session_dir is None:
            raise ValueError(f"Session {session_id} not found and no project_dir provided")
    session_dir.mkdir(parents=True, exist_ok=True)
    return session_dir


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


def load_stats(session_id: str, project_dir: str | None = None) -> SessionStats | None:
    """Load session statistics from file.

    Args:
        session_id: The session identifier
        project_dir: The project directory (optional, will search if not provided)

    Returns:
        SessionStats if found, None otherwise
    """
    try:
        session_dir = get_session_dir(session_id, project_dir)
    except ValueError:
        return None

    stats_file = session_dir / "stats.json"

    if not stats_file.exists():
        return None

    try:
        with open(stats_file, "r", encoding="utf-8") as f:
            data = json.load(f)
            return SessionStats.from_dict(data)
    except (json.JSONDecodeError, OSError):
        return None


def save_stats(
    stats: SessionStats,
    project_dir: str | None = None,
    model: str | None = None,
) -> None:
    """Save session statistics to file atomically.

    Args:
        stats: The SessionStats to save
        project_dir: The project directory (optional, will search if not provided)
        model: Model identifier for cost estimation (e.g. "claude-sonnet-4-20250514")
    """
    session_dir = get_session_dir(stats.session_id, project_dir)
    stats_file = session_dir / "stats.json"

    # Update timestamp
    stats.updated_at = utcnow_iso()

    # Update cost estimate using model-specific pricing
    stats.cost.estimated_usd = estimate_cost(stats.tokens, model=model)

    # Write atomically (atomic_write creates parent dirs)
    atomic_write(stats_file, stats.to_json())


def load_metadata(
    session_id: str, project_dir: str | None = None
) -> CollectorSessionMetadata | None:
    """Load session metadata from file.

    Args:
        session_id: The session identifier
        project_dir: The project directory (optional, will search if not provided)

    Returns:
        CollectorSessionMetadata if found, None otherwise
    """
    try:
        session_dir = get_session_dir(session_id, project_dir)
    except ValueError:
        return None

    metadata_file = session_dir / "metadata.json"

    if not metadata_file.exists():
        return None

    try:
        with open(metadata_file, "r", encoding="utf-8") as f:
            data = json.load(f)
            return CollectorSessionMetadata.from_dict(data)
    except (json.JSONDecodeError, OSError):
        return None


def save_metadata(metadata: CollectorSessionMetadata) -> None:
    """Save session metadata to file.

    Args:
        metadata: The CollectorSessionMetadata to save (uses metadata.project_dir)
    """
    session_dir = get_session_dir(metadata.session_id, metadata.project_dir)
    metadata_file = session_dir / "metadata.json"
    atomic_write(metadata_file, metadata.to_json())


def initialize_session(
    session_id: str,
    project_dir: str,
) -> tuple[SessionStats, CollectorSessionMetadata]:
    """Initialize a new session with empty files.

    Args:
        session_id: Unique session identifier
        project_dir: Working directory for the session

    Returns:
        Tuple of (SessionStats, CollectorSessionMetadata)
    """
    now = utcnow_iso()

    # Create metadata (this will register the project and create directories)
    metadata = CollectorSessionMetadata(
        session_id=session_id,
        project_dir=project_dir,
        started_at=now,
    )
    save_metadata(metadata)

    # Create stats
    stats = SessionStats.create(session_id, now)
    save_stats(stats, project_dir)

    # Create empty events.jsonl
    session_dir = get_session_dir(session_id, project_dir)
    (session_dir / "events.jsonl").touch()

    return stats, metadata


def finalize_session(
    session_id: str,
    end_reason: str | None = None,
) -> None:
    """Finalize a session by updating metadata with end time."""
    metadata = load_metadata(session_id)
    if metadata:
        metadata.ended_at = utcnow_iso()
        metadata.end_reason = end_reason
        save_metadata(metadata)


def find_current_session(project_dir: str | None = None) -> str | None:
    """Find the most recently updated session.

    Args:
        project_dir: If provided, only search within this project's sessions

    Returns:
        Session ID of the most recent session, or None if no sessions exist
    """
    if project_dir:
        # Search within specific project
        folder_name = lookup_project_folder(project_dir)
        if not folder_name:
            return None
        search_dirs = [get_projects_dir() / folder_name]
    else:
        # Search all projects
        projects_dir = get_projects_dir()
        if not projects_dir.exists():
            return None
        search_dirs = [d for d in projects_dir.iterdir() if d.is_dir()]

    # Find session with most recent stats.json modification time
    latest_session = None
    latest_mtime = 0.0

    for project_folder in search_dirs:
        if not project_folder.exists():
            continue
        for session_dir in project_folder.iterdir():
            if not session_dir.is_dir():
                continue

            stats_file = session_dir / "stats.json"
            if stats_file.exists():
                mtime = stats_file.stat().st_mtime
                if mtime > latest_mtime:
                    latest_mtime = mtime
                    latest_session = session_dir.name

    return latest_session
