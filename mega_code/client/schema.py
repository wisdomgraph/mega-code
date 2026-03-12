"""Schema definitions for MEGA-Code data collection (client edition).

Client-side version with no LLM dependencies.
estimate_cost() returns 0.0 — cost computation is handled server-side.
A server-side installation may override this function with accurate pricing.
"""

from __future__ import annotations

__all__ = [
    "CollectorSessionMetadata",
    "SessionCost",
    "SessionCounts",
    "SessionStats",
    "SessionTiming",
    "SessionTokens",
    "estimate_cost",
    "utcnow",
    "utcnow_iso",
]

import dataclasses
import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any


def utcnow() -> datetime:
    """Get current UTC time (timezone-aware then stripped for consistency)."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def utcnow_iso() -> str:
    """Get current UTC time as ISO 8601 string with Z suffix."""
    return utcnow().isoformat() + "Z"


@dataclass
class CollectorSessionMetadata:
    """Metadata written to disk by the collector (metadata.json).

    Not to be confused with ``models.SessionMetadata`` (pipeline TurnSet metadata)
    or ``history.models.HistorySessionMetadata`` (multi-source history loader).
    """

    session_id: str
    project_dir: str
    started_at: str
    ended_at: str | None = None
    end_reason: str | None = None
    model_id: str | None = None
    version: str | None = None
    git_branch: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return asdict(self)

    def to_json(self) -> str:
        """Serialize to JSON string."""
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=2)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CollectorSessionMetadata:
        """Create from dictionary."""
        valid_keys = {f.name for f in dataclasses.fields(cls)}
        return cls(**{k: v for k, v in data.items() if k in valid_keys})


@dataclass
class SessionCounts:
    """Counts for a session."""

    user_prompts: int = 0
    assistant_responses: int = 0
    tool_calls: int = 0
    tool_calls_by_type: dict[str, int] = field(default_factory=dict)
    errors: int = 0

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return asdict(self)


@dataclass
class SessionTiming:
    """Timing information for a session."""

    total_duration_ms: int = 0
    avg_response_time_ms: float = 0.0
    last_response_time_ms: int = 0
    response_times: list[int] = field(default_factory=list)

    def add_response_time(self, ms: int) -> None:
        """Add a response time and update average."""
        self.response_times.append(ms)
        self.last_response_time_ms = ms
        self.avg_response_time_ms = sum(self.response_times) / len(self.response_times)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary (excluding response_times list)."""
        return {
            "total_duration_ms": self.total_duration_ms,
            "avg_response_time_ms": round(self.avg_response_time_ms, 2),
            "last_response_time_ms": self.last_response_time_ms,
        }


@dataclass
class SessionTokens:
    """Token usage for a session."""

    total_input: int = 0
    total_output: int = 0
    total_cache_read: int = 0
    total_cache_create: int = 0

    def to_dict(self) -> dict[str, int]:
        """Convert to dictionary."""
        return asdict(self)


@dataclass
class SessionCost:
    """Cost estimate for a session."""

    estimated_usd: float = 0.0

    def to_dict(self) -> dict[str, float]:
        """Convert to dictionary."""
        return {"estimated_usd": round(self.estimated_usd, 4)}


@dataclass
class SessionStats:
    """Complete statistics for a session."""

    session_id: str
    started_at: str
    updated_at: str
    counts: SessionCounts = field(default_factory=SessionCounts)
    timing: SessionTiming = field(default_factory=SessionTiming)
    tokens: SessionTokens = field(default_factory=SessionTokens)
    cost: SessionCost = field(default_factory=SessionCost)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "session_id": self.session_id,
            "started_at": self.started_at,
            "updated_at": self.updated_at,
            "counts": self.counts.to_dict(),
            "timing": self.timing.to_dict(),
            "tokens": self.tokens.to_dict(),
            "cost": self.cost.to_dict(),
        }

    def to_json(self) -> str:
        """Serialize to JSON string."""
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=2)

    @classmethod
    def create(cls, session_id: str, started_at: str | None = None) -> SessionStats:
        """Create new session stats."""
        now = utcnow_iso()
        return cls(
            session_id=session_id,
            started_at=started_at or now,
            updated_at=now,
        )

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SessionStats:
        """Create from dictionary."""
        stats = cls(
            session_id=data.get("session_id", ""),
            started_at=data.get("started_at", ""),
            updated_at=data.get("updated_at", ""),
        )

        if "counts" in data:
            counts = data["counts"]
            stats.counts = SessionCounts(
                user_prompts=counts.get("user_prompts", 0),
                assistant_responses=counts.get("assistant_responses", 0),
                tool_calls=counts.get("tool_calls", 0),
                tool_calls_by_type=counts.get("tool_calls_by_type", {}),
                errors=counts.get("errors", 0),
            )

        if "timing" in data:
            timing = data["timing"]
            stats.timing = SessionTiming(
                total_duration_ms=timing.get("total_duration_ms", 0),
                avg_response_time_ms=timing.get("avg_response_time_ms", 0.0),
                last_response_time_ms=timing.get("last_response_time_ms", 0),
            )

        if "tokens" in data:
            tokens = data["tokens"]
            stats.tokens = SessionTokens(
                total_input=tokens.get("total_input", 0),
                total_output=tokens.get("total_output", 0),
                total_cache_read=tokens.get("total_cache_read", 0),
                total_cache_create=tokens.get("total_cache_create", 0),
            )

        if "cost" in data:
            stats.cost = SessionCost(estimated_usd=data["cost"].get("estimated_usd", 0.0))

        return stats


def estimate_cost(tokens: SessionTokens, model: str | None = None) -> float:
    """Return 0.0 — client does not compute cost.

    Server computes accurate cost on upload.
    A server-side installation may override this function with litellm-based pricing.

    Args:
        tokens: Token usage counts for the session.
        model: Model identifier (unused in client — kept for API compatibility).

    Returns:
        0.0 always.
    """
    return 0.0
