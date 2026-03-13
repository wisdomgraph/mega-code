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
    """Upload Codex CLI sessions matching the project path to the server.

    Compares discovered Codex session files against codex-sync-ledger.json.
    Uploads any sessions not yet in the ledger or with changed mtime.

    Args:
        project_dir: Local mega-code project data folder
            (e.g. ~/.local/share/mega-code/projects/mega-code_b39e0992/).
        client: Authenticated client (typically MegaCodeRemote).
        project_id: Project identifier for the server.
        project_path: Actual project cwd for Codex filtering.

    Returns:
        Number of newly uploaded sessions.
    """
    from mega_code.client.history.sources.codex import CodexSource

    # Discover Codex sessions matching this project path
    codex_source = CodexSource()
    matching_entries = list(codex_source.iter_sessions_by_project_paths([project_path]))

    if not matching_entries:
        logger.debug("No Codex sessions found for project path: %s", project_path)
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
                    return None
                session = source._load_session_from_entries(entries, sf)
            except Exception:
                logger.debug("Cannot load Codex session %s from %s", sid, sf)
                return None
            return _session_to_turnset(session, sf.parent)

        sessions.append((codex_session_id, _make_loader))

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
