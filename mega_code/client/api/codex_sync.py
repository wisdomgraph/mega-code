"""Codex CLI trajectory sync: upload Codex sessions to remote server.

Follows the same pattern as sync.py for MEGA-Code sessions.
Maintains a codex-sync-ledger.json per project directory.

Ledger location:
    {project_dir}/codex-sync-ledger.json
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path

from mega_code.client.api.protocol import MegaCodeBaseClient
from mega_code.client.api.sync import _session_to_turnset, _upload_sessions
from mega_code.client.models import TurnSet

logger = logging.getLogger(__name__)


def sync_codex_trajectories(
    project_dir: Path,
    client: MegaCodeBaseClient,
    project_id: str,
    project_path: str,
) -> int:
    """Upload Codex CLI sessions matching the project path to the server."""
    from mega_code.client.history.sources.codex import CodexSource
    from mega_code.client.utils.tracing import get_tracer

    tracer = get_tracer(__name__)
    span_ctx = tracer.start_as_current_span("sync.discover_codex_sessions")
    span = span_ctx.__enter__()
    span.set_attribute("sync.project_dir", str(project_dir))
    span.set_attribute("sync.project_id", project_id)
    span.set_attribute("sync.project_path", project_path)

    logger.info("Codex sync: matching sessions against project_path=%s", project_path)

    # Discover Codex sessions matching this project path
    codex_source = CodexSource()
    matching_entries = list(codex_source.iter_sessions_by_project_paths([project_path]))

    span.set_attribute("sync.codex_matched_count", len(matching_entries))
    logger.info(
        "Codex sync: %d session(s) matched project_path=%s", len(matching_entries), project_path
    )

    if not matching_entries:
        # Log all available cwds for diagnostics
        all_cwds: list[str] = []
        for jsonl_file in codex_source._iter_session_files():
            try:
                entries = codex_source._load_jsonl_entries(jsonl_file)
                meta = next((e for e in entries if e.get("type") == "session_meta"), None)
                if meta:
                    cwd = meta.get("payload", {}).get("cwd", "<missing>")
                    all_cwds.append(cwd)
            except Exception:
                pass
        span.set_attribute("sync.codex_all_cwds", ",".join(all_cwds[:20]))
        span.set_attribute("sync.codex_total_sessions", len(all_cwds))
        span_ctx.__exit__(None, None, None)
        logger.info(
            "Codex sync: 0 matches — total sessions found: %d, cwds: %s",
            len(all_cwds),
            all_cwds[:20],
        )
        return 0

    # Build session list and mtime map
    mtime_map: dict[str, float] = {}
    sessions: list[tuple[str, Callable[[], TurnSet | None]]] = []

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

        mtime_map[codex_session_id] = session_file.stat().st_mtime

        def _make_loader(
            sf: Path = session_file,
            sid: str = codex_session_id,
        ) -> TurnSet | None:
            source = CodexSource()
            try:
                entries = source._load_jsonl_entries(sf)
                if not entries:
                    logger.debug("Codex session %s: no entries in %s", sid, sf)
                    return None
                session = source._load_session_from_entries(entries, sf)
            except Exception:
                logger.debug("Cannot load Codex session %s from %s", sid, sf)
                return None
            turn_set = _session_to_turnset(session, sf.parent)
            if turn_set is None:
                logger.debug("Codex session %s: TurnSet is None (0 turns)", sid)
            else:
                logger.debug("Codex session %s: %d turn(s)", sid, len(turn_set.turns))
            return turn_set

        sessions.append((codex_session_id, _make_loader))

    span.set_attribute("sync.codex_sessions_to_process", len(sessions))
    span_ctx.__exit__(None, None, None)

    def _needs_resync(sid: str, existing: dict) -> bool:
        return existing.get("file_mtime") != mtime_map.get(sid)

    def _extra_entry(sid: str) -> dict:
        return {"file_mtime": mtime_map[sid]}

    return _upload_sessions(
        ledger_path=project_dir / "codex-sync-ledger.json",
        ledger_key="sessions",
        sessions=sessions,
        client=client,
        project_id=project_id,
        label="Codex ",
        needs_resync=_needs_resync,
        extra_entry=_extra_entry,
    )
