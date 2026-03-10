"""Codex CLI trajectory sync: upload Codex sessions to remote server.

Follows the same pattern as sync.py for MEGA-Code sessions.
Maintains a codex-sync-ledger.json per project directory.

Ledger location:
    {project_dir}/codex-sync-ledger.json
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from mega_code.client.api.protocol import MegaCodeBaseClient, UploadResult
from mega_code.client.api.sync import _load_ledger, _save_ledger
from mega_code.client.models import TurnSet

logger = logging.getLogger(__name__)


def _load_codex_session_as_turnset(
    session_file: Path,
    session_id: str,
) -> TurnSet | None:
    """Load a Codex session file as a TurnSet for upload.

    Args:
        session_file: Path to the Codex JSONL session file.
        session_id: Codex session ID.

    Returns:
        TurnSet if the session has turns, None otherwise.
    """
    from mega_code.client.filters import filter_metadata, filter_turns
    from mega_code.client.history.sources.codex import CodexSource
    from mega_code.client.turns import extract_turns

    source = CodexSource()
    try:
        entries = source._load_jsonl_entries(session_file)
        if not entries:
            return None
        session = source._load_session_from_entries(entries, session_file)
    except Exception:
        logger.debug("Cannot load Codex session %s from %s", session_id, session_file)
        return None

    turns, metadata = extract_turns(session)
    if not turns:
        return None

    # Filter sensitive data before upload
    project_dir = metadata.project_path
    turns = filter_turns(turns, project_dir=project_dir)
    metadata = filter_metadata(metadata, project_dir=project_dir)

    return TurnSet(
        session_id=session_id,
        session_dir=session_file.parent,
        turns=turns,
        metadata=metadata,
    )


def sync_codex_trajectories(
    project_dir: Path,
    client: MegaCodeBaseClient,
    project_id: str,
    project_path: str,
) -> int:
    """Upload Codex CLI sessions matching the project path to the server.

    Compares discovered Codex session files against codex-sync-ledger.json.
    Uploads any sessions not yet in the ledger or with changed mtime.

    Args:
        project_dir: Local mega-code project data folder
            (e.g. ~/.local/mega-code/projects/mega-code_b39e0992/).
        client: Authenticated client (typically MegaCodeRemote).
        project_id: Project identifier for the server.
        project_path: Actual project cwd for Codex filtering.

    Returns:
        Number of newly uploaded sessions.
    """
    from mega_code.client.history.sources.codex import CodexSource

    ledger_path = project_dir / "codex-sync-ledger.json"
    ledger = _load_ledger(ledger_path)

    # Discover Codex sessions matching this project path
    codex_source = CodexSource()
    matching_entries = list(
        codex_source.iter_sessions_by_project_paths([project_path])
    )

    if not matching_entries:
        logger.debug("No Codex sessions found for project path: %s", project_path)
        return 0

    sessions_ledger = ledger.get("sessions", {})
    uploaded = 0

    for entry in matching_entries:
        session_file_str = entry.get("session_file_path")
        if not session_file_str:
            continue

        session_file = Path(session_file_str)
        if not session_file.exists():
            continue

        codex_session_id = entry.get("payload", {}).get("id", "")
        if not codex_session_id:
            continue

        # Check ledger: skip if file path + mtime unchanged
        file_mtime = session_file.stat().st_mtime
        existing = sessions_ledger.get(session_file_str)
        if existing and existing.get("file_mtime") == file_mtime:
            logger.debug("Skipping unchanged Codex session: %s", codex_session_id)
            continue

        # Load and convert to TurnSet
        turn_set = _load_codex_session_as_turnset(session_file, codex_session_id)
        if not turn_set or not turn_set.turns:
            logger.debug("Skipping empty Codex session: %s", codex_session_id)
            continue

        # Upload
        result: UploadResult = client.upload_trajectory(
            turn_set=turn_set,
            project_id=project_id,
        )
        logger.info("Uploaded Codex session %s: %s", codex_session_id, result.message)

        # Update ledger
        sessions_ledger[session_file_str] = {
            "codex_session_id": codex_session_id,
            "uploaded_at": datetime.now(timezone.utc).isoformat(),
            "turn_count": len(turn_set.turns),
            "file_mtime": file_mtime,
        }
        uploaded += 1

    # Save updated ledger
    ledger["sessions"] = sessions_ledger
    _save_ledger(ledger_path, ledger)

    logger.info(
        "Codex sync complete: %d new, %d existing",
        uploaded,
        len(sessions_ledger) - uploaded,
    )
    return uploaded
