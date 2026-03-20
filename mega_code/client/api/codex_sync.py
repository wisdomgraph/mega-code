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
    ledger_dir: Path | None = None,
) -> int:
    """Upload Codex CLI sessions matching the project path to the server."""
    import json as _json

    from mega_code.client.history.sources.codex import CodexSource
    from mega_code.client.utils.path_utils import normalize_path, should_include_session
    from mega_code.client.utils.tracing import get_tracer

    tracer = get_tracer(__name__)
    span_ctx = tracer.start_as_current_span("sync.discover_codex_sessions")
    span = span_ctx.__enter__()
    span.set_attribute("sync.project_dir", str(project_dir))
    span.set_attribute("sync.project_id", project_id)
    span.set_attribute("sync.project_path", project_path)

    logger.info("Codex sync: matching sessions against project_path=%s", project_path)

    # Inline session scanning for per-session tracing
    codex_source = CodexSource()
    normalized_targets = {normalize_path(project_path)}
    matching_entries: list[dict] = []
    total_scanned = 0
    excluded_count = 0
    cwd_breakdown: dict[str, dict[str, int]] = {}

    for jsonl_file in codex_source._iter_session_files():
        total_scanned += 1
        try:
            entries = codex_source._load_jsonl_entries(jsonl_file)
            if not entries:
                span.add_event(
                    "session.filter_decision",
                    {
                        "session.id": "",
                        "session.cwd": "",
                        "session.file": str(jsonl_file),
                        "session.included": False,
                        "session.exclude_reason": "no_entries",
                    },
                )
                excluded_count += 1
                continue

            session_meta = next((e for e in entries if e.get("type") == "session_meta"), None)
            if not session_meta:
                span.add_event(
                    "session.filter_decision",
                    {
                        "session.id": "",
                        "session.cwd": "",
                        "session.file": str(jsonl_file),
                        "session.included": False,
                        "session.exclude_reason": "no_session_meta",
                    },
                )
                excluded_count += 1
                continue

            payload = session_meta.get("payload", {})
            session_cwd = payload.get("cwd")
            session_id = payload.get("id", "")

            if not session_cwd:
                span.add_event(
                    "session.filter_decision",
                    {
                        "session.id": session_id,
                        "session.cwd": "",
                        "session.file": str(jsonl_file),
                        "session.included": False,
                        "session.exclude_reason": "no_cwd",
                    },
                )
                excluded_count += 1
                continue

            # Track cwd breakdown
            if session_cwd not in cwd_breakdown:
                cwd_breakdown[session_cwd] = {"count": 0, "included": 0}
            cwd_breakdown[session_cwd]["count"] += 1

            if should_include_session(session_cwd, normalized_targets):
                cwd_breakdown[session_cwd]["included"] += 1
                matching_entries.append(
                    {
                        "payload": payload,
                        "session_file_path": str(jsonl_file),
                    }
                )
                span.add_event(
                    "session.filter_decision",
                    {
                        "session.id": session_id,
                        "session.cwd": session_cwd,
                        "session.file": str(jsonl_file),
                        "session.included": True,
                        "session.exclude_reason": "",
                    },
                )
            else:
                excluded_count += 1
                span.add_event(
                    "session.filter_decision",
                    {
                        "session.id": session_id,
                        "session.cwd": session_cwd,
                        "session.file": str(jsonl_file),
                        "session.included": False,
                        "session.exclude_reason": "path_mismatch",
                    },
                )
        except Exception as e:
            logger.debug("Failed to process session file %s: %s", jsonl_file, e)
            excluded_count += 1

    # Set summary attributes
    all_cwds = list(cwd_breakdown.keys())
    span.set_attribute("sync.codex_total_scanned", total_scanned)
    span.set_attribute("sync.codex_matched_count", len(matching_entries))
    span.set_attribute("sync.codex_excluded_count", excluded_count)
    span.set_attribute("sync.codex_cwd_breakdown", _json.dumps(cwd_breakdown))
    span.set_attribute("sync.codex_target_path", project_path)
    span.set_attribute("sync.codex_all_cwds", ",".join(all_cwds[:20]))

    logger.info(
        "Codex sync: %d session(s) matched project_path=%s", len(matching_entries), project_path
    )

    if not matching_entries:
        span_ctx.__exit__(None, None, None)
        logger.info(
            "Codex sync: 0 matches — total sessions scanned: %d, cwds: %s",
            total_scanned,
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

    actual_ledger_dir = ledger_dir or project_dir
    return _upload_sessions(
        ledger_path=actual_ledger_dir / "codex-sync-ledger.json",
        ledger_key="sessions",
        sessions=sessions,
        client=client,
        project_id=project_id,
        label="Codex ",
        needs_resync=_needs_resync,
        extra_entry=_extra_entry,
    )
