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
    """Upload sessions not yet in the ledger and persist updated ledger."""
    from mega_code.client.utils.tracing import get_tracer

    tracer = get_tracer(__name__)
    with tracer.start_as_current_span(
        f"sync.upload_sessions.{label.strip() or 'mega_code'}"
    ) as span:
        span.set_attribute("sync.label", label.strip() or "mega_code")
        span.set_attribute("sync.ledger_path", str(ledger_path))
        span.set_attribute("sync.ledger_key", ledger_key)
        span.set_attribute("sync.project_id", project_id)
        span.set_attribute("sync.total_discovered", len(sessions))

        ledger = _load_ledger(ledger_path)
        synced = ledger.get(ledger_key, {})
        span.set_attribute("sync.already_synced", len(synced))

        to_upload: list[tuple[str, Callable[[], TurnSet | None]]] = []
        for sid, loader in sessions:
            existing = synced.get(sid)
            if existing is None or (needs_resync is not None and needs_resync(sid, existing)):
                to_upload.append((sid, loader))

        span.set_attribute("sync.to_upload", len(to_upload))
        span.set_attribute("sync.skipped_already_synced", len(sessions) - len(to_upload))

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
        skipped_empty = 0
        for session_id, loader in to_upload:
            turn_set = loader()
            if not turn_set or not turn_set.turns:
                logger.debug("Skipping empty %ssession: %s", label, session_id)
                skipped_empty += 1
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

        span.set_attribute("sync.uploaded", uploaded)
        span.set_attribute("sync.skipped_empty", skipped_empty)
        logger.info("%sSync complete: %d new, %d existing", label, uploaded, len(synced))
        return uploaded


@traced("client.sync_trajectories")
def sync_trajectories(
    project_dir: Path,
    client: MegaCodeBaseClient,
    project_id: str,
) -> int:
    """Ensure all local sessions are uploaded to the server."""
    from mega_code.client.history.loader import DataLoader
    from mega_code.client.history.sources.mega_code import MegaCodeSource
    from mega_code.client.utils.tracing import get_tracer

    tracer = get_tracer(__name__)
    with tracer.start_as_current_span("sync.discover_mega_code_sessions") as span:
        span.set_attribute("sync.project_dir", str(project_dir))
        span.set_attribute("sync.project_id", project_id)

        local_sessions = [d.name for d in project_dir.iterdir() if d.is_dir() and _is_uuid(d.name)]
        span.set_attribute("sync.local_session_count", len(local_sessions))
        span.set_attribute("sync.local_session_ids", ",".join(local_sessions[:50]))

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
    """Upload matching Claude Code native sessions as trajectories."""
    from mega_code.client.history.loader import load_sessions_from_project
    from mega_code.client.utils.tracing import get_tracer

    tracer = get_tracer(__name__)
    with tracer.start_as_current_span("sync.discover_claude_sessions") as span:
        span.set_attribute("sync.project_dir", str(project_dir))
        span.set_attribute("sync.project_id", project_id)

        all_sessions = load_sessions_from_project(
            project_dir,
            include_claude=True,
            include_codex=False,
        )
        span.set_attribute("sync.all_sessions_found", len(all_sessions))

        claude_sessions = [s for s in all_sessions if s.metadata.source == "claude_native"]
        span.set_attribute("sync.claude_session_count", len(claude_sessions))
        if claude_sessions:
            span.set_attribute(
                "sync.claude_session_ids",
                ",".join(s.metadata.session_id for s in claude_sessions[:50]),
            )

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
