"""Claude Code native trajectory sync: upload Claude sessions to remote server.

Follows the same pattern as codex_sync.py for Codex CLI sessions.
Maintains a claude-sync-ledger.json per project directory.

Ledger location:
    {project_dir}/claude-sync-ledger.json
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path

from mega_code.client.api.protocol import MegaCodeBaseClient
from mega_code.client.api.sync import _session_to_turnset, _upload_sessions
from mega_code.client.models import TurnSet

logger = logging.getLogger(__name__)


def sync_claude_trajectories(
    project_dir: Path,
    client: MegaCodeBaseClient,
    project_id: str,
    project_path: str,
    ledger_dir: Path | None = None,
) -> int:
    """Upload Claude native sessions matching the project path to the server.

    Scans ``~/.claude/projects/<dir>/<uuid>.jsonl`` directly (no dependency
    on the legacy hook-written mega-code mirror) and filters by the
    ``cwd``/``projectPath`` recorded in each session.

    Args:
        project_dir: Local mega-code project anchor folder. Used as the
            default ledger location.
        client: Authenticated client (typically MegaCodeRemote).
        project_id: Project identifier for the server.
        project_path: Real working directory to match against session cwd.
        ledger_dir: Override directory for the ledger file.

    Returns:
        Number of newly uploaded Claude sessions.
    """
    from mega_code.client.history.sources.claude_native import ClaudeNativeSource
    from mega_code.client.utils.tracing import get_tracer

    tracer = get_tracer(__name__)
    claude_source = ClaudeNativeSource()
    mtime_map: dict[str, float] = {}
    sessions: list[tuple[str, Callable[[], TurnSet | None]]] = []

    with tracer.start_as_current_span("sync.discover_claude_sessions") as span:
        span.set_attribute("sync.project_dir", str(project_dir))
        span.set_attribute("sync.project_id", project_id)
        span.set_attribute("sync.project_path", project_path)

        logger.info("Claude sync: matching sessions against project_path=%s", project_path)

        matching_entries = list(claude_source.iter_sessions_by_project_paths([project_path]))

        span.set_attribute("sync.claude_matched_count", len(matching_entries))
        logger.info(
            "Claude sync: %d session(s) matched project_path=%s",
            len(matching_entries),
            project_path,
        )

        if not matching_entries:
            return 0

        for entry in matching_entries:
            session_id = entry.get("sessionId")
            full_path = entry.get("fullPath")
            if not session_id or not full_path:
                continue

            session_file = Path(full_path)
            try:
                mtime_map[session_id] = session_file.stat().st_mtime
            except FileNotFoundError:
                continue

            def _make_loader(
                e: dict = entry,
                sf: Path = session_file,
                sid: str = session_id,
            ) -> TurnSet | None:
                try:
                    session = claude_source.load_session_from_entry(e, sf.parent)
                except (OSError, ValueError, KeyError) as exc:
                    logger.warning("Cannot load Claude session %s from %s: %s", sid, sf, exc)
                    return None
                turn_set = _session_to_turnset(session, project_dir / sid)
                if turn_set is None:
                    logger.debug("Claude session %s: TurnSet is None (0 turns)", sid)
                else:
                    logger.debug("Claude session %s: %d turn(s)", sid, len(turn_set.turns))
                return turn_set

            sessions.append((session_id, _make_loader))

        span.set_attribute("sync.claude_sessions_to_process", len(sessions))

    def _needs_resync(sid: str, existing: dict) -> bool:
        return existing.get("file_mtime") != mtime_map.get(sid)

    def _extra_entry(sid: str) -> dict:
        return {"file_mtime": mtime_map[sid]}

    actual_ledger_dir = ledger_dir or project_dir
    return _upload_sessions(
        ledger_path=actual_ledger_dir / "claude-sync-ledger.json",
        ledger_key="sessions",
        sessions=sessions,
        client=client,
        project_id=project_id,
        label="Claude ",
        needs_resync=_needs_resync,
        extra_entry=_extra_entry,
    )


def sync_single_claude_session(
    session_id: str,
    project_dir: Path,
    client: MegaCodeBaseClient,
    project_id: str,
    project_path: str,
    ledger_dir: Path | None = None,
) -> int:
    """Upload one specific Claude session — the no-flag wisdom-gen path.

    Differs from ``sync_claude_trajectories`` in that it uploads only the
    requested ``session_id`` (the current/active transcript), leaving all
    other matching sessions for the project alone. The shared
    ``claude-sync-ledger.json`` and ``_upload_sessions`` machinery are
    reused so mtime/skipped bookkeeping stays consistent across both
    entry points.

    Args:
        session_id: Target Claude session UUID.
        project_dir: Local mega-code project anchor folder.
        client: Authenticated client (typically MegaCodeRemote).
        project_id: Project identifier for the server.
        project_path: Real working directory used to locate the session
            transcript via ``iter_sessions_by_project_paths``.
        ledger_dir: Override directory for the ledger file.

    Returns:
        1 if the session was uploaded, 0 if it was not found, empty, or
        already up-to-date in the ledger.
    """
    from mega_code.client.history.sources.claude_native import ClaudeNativeSource

    claude_source = ClaudeNativeSource()
    target_entry: dict | None = None
    for entry in claude_source.iter_sessions_by_project_paths([project_path]):
        if entry.get("sessionId") == session_id:
            target_entry = entry
            break

    if target_entry is None:
        logger.warning(
            "Claude single-session sync: session %s not found under project_path=%s",
            session_id,
            project_path,
        )
        return 0

    full_path = target_entry.get("fullPath")
    if not full_path:
        logger.warning("Claude single-session sync: entry for %s has no fullPath", session_id)
        return 0

    session_file = Path(full_path)
    try:
        mtime = session_file.stat().st_mtime
    except FileNotFoundError:
        logger.warning("Claude single-session sync: %s does not exist on disk", session_file)
        return 0

    def _load() -> TurnSet | None:
        try:
            session = claude_source.load_session_from_entry(target_entry, session_file.parent)
        except (OSError, ValueError, KeyError) as exc:
            logger.warning(
                "Cannot load Claude session %s from %s: %s", session_id, session_file, exc
            )
            return None
        return _session_to_turnset(session, project_dir / session_id)

    def _needs_resync(_sid: str, existing: dict) -> bool:
        return existing.get("file_mtime") != mtime

    def _extra_entry(_sid: str) -> dict:
        return {"file_mtime": mtime}

    actual_ledger_dir = ledger_dir or project_dir
    return _upload_sessions(
        ledger_path=actual_ledger_dir / "claude-sync-ledger.json",
        ledger_key="sessions",
        sessions=[(session_id, _load)],
        client=client,
        project_id=project_id,
        label="Claude ",
        needs_resync=_needs_resync,
        extra_entry=_extra_entry,
    )
