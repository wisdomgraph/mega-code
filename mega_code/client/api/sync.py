"""Trajectory sync: upload local sessions to remote server before pipeline run.

Maintains a sync-ledger.json per project directory that tracks which sessions
have been uploaded. Before triggering a remote pipeline run, the caller invokes
sync_trajectories() to ensure all local sessions are on the server.

Ledger location:
    ~/.local/share/mega-code/projects/{project_id}/sync-ledger.json
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from mega_code.client.api.protocol import MegaCodeBaseClient, UploadResult
from mega_code.client.models import TurnSet
from mega_code.client.utils.tracing import traced

if TYPE_CHECKING:
    from mega_code.client.history.models import Session

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


def _session_to_turnset(
    session: Session,
    session_dir: Path = Path(""),
) -> TurnSet | None:
    """Extract turns from a Session, apply filters, return TurnSet."""
    from mega_code.client.filters import (
        clean_mega_code_turns,
        filter_metadata,
        filter_turns,
        save_cleaning_debug,
    )
    from mega_code.client.turns import extract_turns

    turns, metadata = extract_turns(session)
    if not turns:
        return None

    # Clean mega-code self-referential turns, then anonymize before writing
    # debug files to disk (SecretMasker + PathAnonymizer must run first to
    # avoid persisting raw API keys and absolute paths under ~/.local/share).
    cleaning = clean_mega_code_turns(turns)
    if not cleaning.kept:
        return None

    project_dir = metadata.project_path
    masked_original = filter_turns(turns, project_dir=project_dir)
    masked_removed = filter_turns(cleaning.removed, project_dir=project_dir)
    masked_kept = filter_turns(cleaning.kept, project_dir=project_dir)
    from mega_code.client.filters.cleaning import CleaningResult

    save_cleaning_debug(
        masked_original,
        CleaningResult(kept=masked_kept, removed=masked_removed),
        session_dir,
    )
    turns = masked_kept
    metadata = filter_metadata(metadata, project_dir=project_dir)

    return TurnSet(
        session_id=session.metadata.session_id,
        session_dir=session_dir,
        turns=turns,
        metadata=metadata,
    )


def _upload_sessions(
    *,
    ledger_path: Path,
    ledger_key: str,
    sessions: list[tuple[str, Callable[[], TurnSet | None]]],
    client: MegaCodeBaseClient,
    project_id: str,
    label: str = "",
    needs_resync: Callable[[str, dict], bool] | None = None,
    extra_entry: Callable[[str], dict] | None = None,
) -> int:
    """Upload sessions not yet in the ledger and persist updated ledger.

    Args:
        ledger_path: Path to sync-ledger.json.
        ledger_key: Key in the ledger dict ("sessions" or "claude_sessions").
        sessions: List of (session_id, loader_callable) pairs.
        client: Authenticated client.
        project_id: Project identifier for the server.
        label: Label for log messages (e.g. "" or "Claude ").
        needs_resync: Optional callback(session_id, existing_entry) -> bool.
            For sessions already in the ledger, returns True if they should
            be re-uploaded (e.g. file mtime changed). Default None = never resync.
        extra_entry: Optional callback(session_id) -> dict of extra fields
            to merge into each ledger entry. Default None = no extra fields.

    Returns:
        Number of newly uploaded sessions.
    """
    ledger = _load_ledger(ledger_path)
    synced = ledger.get(ledger_key, {})

    to_upload: list[tuple[str, Callable[[], TurnSet | None]]] = []
    for sid, loader in sessions:
        existing = synced.get(sid)
        if existing is None or (needs_resync is not None and needs_resync(sid, existing)):
            to_upload.append((sid, loader))

    if not to_upload:
        logger.info("All %d %ssessions already synced", len(synced), label)
        return 0

    logger.info(
        "Syncing %d new %ssessions (%d already synced)",
        len(to_upload),
        label,
        len(synced),
    )

    uploaded = 0
    for session_id, loader in to_upload:
        turn_set = loader()
        if not turn_set or not turn_set.turns:
            logger.debug("Skipping empty %ssession: %s", label, session_id)
            continue

        result: UploadResult = client.upload_trajectory(
            turn_set=turn_set,
            project_id=project_id,
        )
        logger.info("Uploaded %s%s: %s", label, session_id, result.message)

        entry = {
            "uploaded_at": datetime.now(UTC).isoformat(),
            "turn_count": len(turn_set.turns),
        }
        if extra_entry is not None:
            entry.update(extra_entry(session_id))
        ledger.setdefault(ledger_key, {})[session_id] = entry
        uploaded += 1

    # Save updated ledger
    from mega_code.client.api.remote import MegaCodeRemote

    if isinstance(client, MegaCodeRemote):
        ledger["server_url"] = client.server_url
    _save_ledger(ledger_path, ledger)

    logger.info("%sSync complete: %d new, %d existing", label, uploaded, len(synced))
    return uploaded


@traced("client.sync_trajectories")
def sync_trajectories(
    project_dir: Path,
    client: MegaCodeBaseClient,
    project_id: str,
) -> int:
    """Ensure all local sessions are uploaded to the server.

    Args:
        project_dir: Local mega-code project data folder.
        client: Authenticated client (typically MegaCodeRemote).
        project_id: Project identifier for the server.

    Returns:
        Number of newly uploaded sessions.
    """
    from mega_code.client.history.loader import DataLoader
    from mega_code.client.history.sources.mega_code import MegaCodeSource

    local_sessions = [d.name for d in project_dir.iterdir() if d.is_dir() and _is_uuid(d.name)]

    def _make_loader(session_dir: Path, session_id: str) -> Callable[[], TurnSet | None]:
        def _load() -> TurnSet | None:
            source = MegaCodeSource()
            try:
                loader = DataLoader()
                loader.register_source(source)
                session = loader.load_from("mega_code", session_id)
            except (KeyError, FileNotFoundError):
                logger.debug("Cannot load session %s from %s", session_id, session_dir)
                return None
            return _session_to_turnset(session, session_dir)

        return _load

    sessions = [(sid, _make_loader(project_dir / sid, sid)) for sid in local_sessions]

    return _upload_sessions(
        ledger_path=project_dir / "sync-ledger.json",
        ledger_key="sessions",
        sessions=sessions,
        client=client,
        project_id=project_id,
    )


@traced("client.sync_claude_trajectories")
def sync_claude_trajectories(
    project_dir: Path,
    client: MegaCodeBaseClient,
    project_id: str,
) -> int:
    """Upload matching Claude Code native sessions as trajectories.

    Args:
        project_dir: Local mega-code project data folder.
        client: Authenticated client (typically MegaCodeRemote).
        project_id: Project identifier for the server.

    Returns:
        Number of newly uploaded Claude sessions.
    """
    from mega_code.client.history.loader import load_sessions_from_project

    all_sessions = load_sessions_from_project(
        project_dir,
        include_claude=True,
        include_codex=False,
    )

    claude_sessions = [s for s in all_sessions if s.metadata.source == "claude_native"]
    if not claude_sessions:
        logger.info("No Claude native sessions found for project")
        return 0

    sessions = [
        (s.metadata.session_id, lambda s=s: _session_to_turnset(s)) for s in claude_sessions
    ]

    return _upload_sessions(
        ledger_path=project_dir / "sync-ledger.json",
        ledger_key="claude_sessions",
        sessions=sessions,
        client=client,
        project_id=project_id,
        label="Claude ",
    )
