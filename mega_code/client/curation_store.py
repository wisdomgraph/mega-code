"""Curation store — persist curate results for later resumption.

Curations are saved as JSON files organized by lifecycle status:
  {data_dir}/curations/pending/    — curated but not yet executed
  {data_dir}/curations/running/    — currently executing
  {data_dir}/curations/completed/  — finished (executed or feedback submitted)

Status transitions move the file between directories.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from mega_code.client.api.protocol import WisdomCurateResult
from mega_code.client.dirs import data_dir
from mega_code.client.utils.tracing import get_current_span, traced

logger = logging.getLogger(__name__)

CurationStatus = Literal["pending", "running", "completed"]


class SavedCuration(BaseModel):
    """Persisted curation — only fields needed for resume.

    Excludes skills (presigned URLs expire) and wisdoms (IDs not useful
    at resume time). The curation markdown + installed skills on disk
    are sufficient to resume the workflow.
    """

    session_id: str = Field(description="Session identifier for linking feedback")
    query: str = Field(description="Original user query")
    curation: str = Field(description="Markdown curation document with step-by-step workflow")
    token_count: int = Field(default=0, description="Total LLM tokens consumed")
    cost_usd: float = Field(default=0.0, description="Total LLM cost in USD")
    created_at: str = Field(description="ISO timestamp when curation was saved")
    status: CurationStatus = Field(
        default="pending", description="Lifecycle status: pending → running → completed"
    )


_SESSION_ID_RE = re.compile(r"^[a-zA-Z0-9_\-]+$")


def _validate_session_id(session_id: str) -> None:
    """Validate session_id to prevent path traversal."""
    if not _SESSION_ID_RE.match(session_id):
        raise ValueError(f"Invalid session_id: {session_id!r}")


def _curations_dir(status: CurationStatus) -> Path:
    """Status-specific curation subdirectory."""
    d = data_dir() / "curations" / status
    d.mkdir(parents=True, exist_ok=True)
    return d


@traced("curation_store.save_curation")
def save_curation(result: WisdomCurateResult, status: CurationStatus = "pending") -> Path:
    """Save a curate result to the status subdirectory.

    Only persists fields needed for resume (session_id, query, curation,
    token_count, cost_usd). Excludes skills (URLs expire) and wisdoms.

    Returns the path to the saved JSON file.
    """
    span = get_current_span()
    span.set_attribute("curation.session_id", result.session_id)
    span.set_attribute("curation.status", status)
    span.set_attribute("curation.token_count", result.token_count)
    span.set_attribute("curation.cost_usd", result.cost_usd)
    saved = SavedCuration(
        session_id=result.session_id,
        query=result.query,
        curation=result.curation,
        token_count=result.token_count,
        cost_usd=result.cost_usd,
        created_at=datetime.now(UTC).isoformat(),
        status=status,
    )
    _validate_session_id(saved.session_id)
    path = _curations_dir(status) / f"{saved.session_id}.json"
    path.write_text(saved.model_dump_json(indent=2), encoding="utf-8")
    span.set_attribute("curation.saved_path", str(path))
    logger.info("Saved curation %s → %s", saved.session_id, path)
    return path


def get_curation(session_id: str) -> SavedCuration | None:
    """Load a saved curation by session ID (searches all status dirs)."""
    _validate_session_id(session_id)
    for status in ("pending", "running", "completed"):
        path = _curations_dir(status) / f"{session_id}.json"
        if path.exists():
            return SavedCuration.model_validate_json(path.read_text(encoding="utf-8"))
    return None


def list_curations(status: CurationStatus | None = None) -> list[SavedCuration]:
    """List saved curations, optionally filtered by status. Newest first."""
    statuses: list[CurationStatus] = [status] if status else ["pending", "running", "completed"]
    results: list[SavedCuration] = []
    for s in statuses:
        d = _curations_dir(s)
        for path in d.glob("*.json"):
            try:
                results.append(SavedCuration.model_validate_json(path.read_text(encoding="utf-8")))
            except (json.JSONDecodeError, ValueError):
                logger.debug("Skipping corrupt curation file: %s", path)
    return sorted(results, key=lambda c: c.created_at, reverse=True)


@traced("curation_store.update_curation_status")
def update_curation_status(session_id: str, new_status: CurationStatus) -> None:
    """Move curation file from current status dir to new status dir."""
    span = get_current_span()
    span.set_attribute("curation.session_id", session_id)
    span.set_attribute("curation.new_status", new_status)
    _validate_session_id(session_id)
    for status in ("pending", "running", "completed"):
        src = _curations_dir(status) / f"{session_id}.json"
        if src.exists():
            span.set_attribute("curation.old_status", status)
            if status == new_status:
                return  # already in correct dir
            # Update status in file content and move atomically
            saved = SavedCuration.model_validate_json(src.read_text(encoding="utf-8"))
            saved.status = new_status
            dest = _curations_dir(new_status) / f"{session_id}.json"
            dest.write_text(saved.model_dump_json(indent=2), encoding="utf-8")
            src.unlink(missing_ok=True)
            logger.info("Curation %s: %s → %s", session_id, status, new_status)
            return
    span.add_event("curation.not_found", {"session_id": session_id})
    logger.warning("Curation %s not found in any status directory", session_id)
