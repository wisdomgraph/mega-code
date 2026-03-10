"""Trajectory sync: upload local sessions to remote server before pipeline run.

Maintains a sync-ledger.json per project directory that tracks which sessions
have been uploaded. Before triggering a remote pipeline run, the caller invokes
sync_trajectories() to ensure all local sessions are on the server.

Ledger location:
    ~/.local/mega-code/projects/{project_id}/sync-ledger.json
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path

from mega_code.client.api.protocol import MegaCodeBaseClient, UploadResult
from mega_code.client.models import TurnSet
from mega_code.client.utils.tracing import traced

logger = logging.getLogger(__name__)

_UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")


def _is_uuid(name: str) -> bool:
    """Check if a directory name looks like a UUID session ID."""
    return bool(_UUID_RE.match(name))


def _load_ledger(ledger_path: Path) -> dict:
    """Load sync-ledger.json, returning empty dict if missing."""
    if not ledger_path.exists():
        return {}
    try:
        return json.loads(ledger_path.read_text())
    except (json.JSONDecodeError, OSError):
        logger.warning("Corrupt sync-ledger.json, starting fresh: %s", ledger_path)
        return {}


def _save_ledger(ledger_path: Path, ledger: dict) -> None:
    """Save sync-ledger.json atomically via temp file."""
    from mega_code.client.utils.io import atomic_write

    atomic_write(ledger_path, json.dumps(ledger, indent=2))


def _load_local_session_as_turnset(
    session_dir: Path,
    session_id: str,
) -> TurnSet | None:
    """Load a local session directory as a TurnSet for upload.

    Reads events.jsonl via DataLoader/MegaCodeSource, then extracts
    turns using the existing extract_turns() function.

    Args:
        session_dir: Path to the session directory containing events.jsonl.
        session_id: Session UUID.

    Returns:
        TurnSet if the session has turns, None otherwise.
    """
    from mega_code.client.filters import filter_metadata, filter_turns
    from mega_code.client.history.loader import DataLoader
    from mega_code.client.history.sources.mega_code import MegaCodeSource
    from mega_code.client.turns import extract_turns

    source = MegaCodeSource()
    try:
        loader = DataLoader()
        loader.register_source(source)
        session = loader.load_from("mega_code", session_id)
    except (KeyError, FileNotFoundError):
        logger.debug("Cannot load session %s from %s", session_id, session_dir)
        return None

    turns, metadata = extract_turns(session)
    if not turns:
        return None

    # Filter sensitive data before upload (use actual project path, not data storage path)
    project_dir = metadata.project_path
    turns = filter_turns(turns, project_dir=project_dir)
    metadata = filter_metadata(metadata, project_dir=project_dir)

    return TurnSet(
        session_id=session_id,
        session_dir=session_dir,
        turns=turns,
        metadata=metadata,
    )


def _load_claude_session_as_turnset(
    session: "Session",
) -> TurnSet | None:
    """Convert a Claude native Session into a TurnSet for upload.

    Args:
        session: Session loaded from ClaudeNativeSource.

    Returns:
        TurnSet if the session has turns, None otherwise.
    """
    from mega_code.client.filters import filter_metadata, filter_turns
    from mega_code.client.turns import extract_turns

    turns, metadata = extract_turns(session)
    if not turns:
        return None

    project_dir = metadata.project_path
    turns = filter_turns(turns, project_dir=project_dir)
    metadata = filter_metadata(metadata, project_dir=project_dir)

    return TurnSet(
        session_id=session.metadata.session_id,
        session_dir=Path(""),
        turns=turns,
        metadata=metadata,
    )


@traced("client.sync_trajectories")
def sync_trajectories(
    project_dir: Path,
    client: MegaCodeBaseClient,
    project_id: str,
) -> int:
    """Ensure all local sessions are uploaded to the server.

    Compares local session dirs against sync-ledger.json.
    Uploads any sessions not yet in the ledger.

    Args:
        project_dir: Local mega-code project data folder
            (e.g. ~/.local/mega-code/projects/mega-code_b39e0992/).
        client: Authenticated client (typically MegaCodeRemote).
        project_id: Project identifier for the server.

    Returns:
        Number of newly uploaded sessions.
    """
    ledger_path = project_dir / "sync-ledger.json"
    ledger = _load_ledger(ledger_path)

    # Discover all local session dirs (UUID-named subdirectories)
    local_sessions = [d.name for d in project_dir.iterdir() if d.is_dir() and _is_uuid(d.name)]

    # Find sessions not yet in ledger
    synced = set(ledger.get("sessions", {}).keys())
    unsynced = [sid for sid in local_sessions if sid not in synced]

    if not unsynced:
        logger.info("All %d sessions already synced", len(synced))
        return 0

    logger.info(
        "Syncing %d new sessions (%d already synced)",
        len(unsynced),
        len(synced),
    )

    uploaded = 0
    for session_id in unsynced:
        session_dir = project_dir / session_id
        turn_set = _load_local_session_as_turnset(session_dir, session_id)
        if not turn_set or not turn_set.turns:
            logger.debug("Skipping empty session: %s", session_id)
            continue

        result: UploadResult = client.upload_trajectory(
            turn_set=turn_set,
            project_id=project_id,
        )
        logger.info("Uploaded %s: %s", session_id, result.message)

        # Update ledger
        ledger.setdefault("sessions", {})[session_id] = {
            "uploaded_at": datetime.now(timezone.utc).isoformat(),
            "turn_count": len(turn_set.turns),
        }
        uploaded += 1

    # Save updated ledger atomically
    from mega_code.client.api.remote import MegaCodeRemote

    if isinstance(client, MegaCodeRemote):
        ledger["server_url"] = client.server_url
    _save_ledger(ledger_path, ledger)

    logger.info("Sync complete: %d new, %d existing", uploaded, len(synced))
    return uploaded


@traced("client.sync_claude_trajectories")
def sync_claude_trajectories(
    project_dir: Path,
    client: MegaCodeBaseClient,
    project_id: str,
) -> int:
    """Upload matching Claude Code native sessions as trajectories.

    Finds Claude native sessions whose projectPath matches the project paths
    found in local MEGA-Code sessions, converts them to TurnSets, and uploads
    to the server. Tracks uploads in sync-ledger.json under a
    "claude_sessions" key to avoid re-uploading.

    Args:
        project_dir: Local mega-code project data folder
            (e.g. ~/.local/mega-code/projects/mega-code_b39e0992/).
        client: Authenticated client (typically MegaCodeRemote).
        project_id: Project identifier for the server.

    Returns:
        Number of newly uploaded Claude sessions.
    """
    from mega_code.client.history.loader import load_sessions_from_project

    # Load merged sessions (include_claude=True gets us the Claude sessions)
    all_sessions = load_sessions_from_project(
        project_dir, include_claude=True, include_codex=False,
    )

    # Filter to only Claude native sessions
    claude_sessions = [
        s for s in all_sessions if s.metadata.source == "claude_native"
    ]
    if not claude_sessions:
        logger.info("No Claude native sessions found for project")
        return 0

    # Load ledger and check which Claude sessions are already synced
    ledger_path = project_dir / "sync-ledger.json"
    ledger = _load_ledger(ledger_path)
    synced = set(ledger.get("claude_sessions", {}).keys())

    unsynced = [s for s in claude_sessions if s.metadata.session_id not in synced]
    if not unsynced:
        logger.info("All %d Claude sessions already synced", len(synced))
        return 0

    logger.info(
        "Syncing %d new Claude sessions (%d already synced)",
        len(unsynced),
        len(synced),
    )

    uploaded = 0
    for session in unsynced:
        session_id = session.metadata.session_id
        try:
            turn_set = _load_claude_session_as_turnset(session)
            if not turn_set or not turn_set.turns:
                logger.debug("Skipping empty Claude session: %s", session_id)
                continue

            result: UploadResult = client.upload_trajectory(
                turn_set=turn_set,
                project_id=project_id,
            )
            logger.info("Uploaded Claude session %s: %s", session_id, result.message)

            ledger.setdefault("claude_sessions", {})[session_id] = {
                "uploaded_at": datetime.now(timezone.utc).isoformat(),
                "turn_count": len(turn_set.turns),
            }
            uploaded += 1
        except Exception as e:
            logger.warning("Failed to sync Claude session %s: %s", session_id, e)
            continue

    # Save updated ledger
    from mega_code.client.api.remote import MegaCodeRemote

    if isinstance(client, MegaCodeRemote):
        ledger["server_url"] = client.server_url
    _save_ledger(ledger_path, ledger)

    logger.info("Claude sync complete: %d new, %d existing", uploaded, len(synced))
    return uploaded
