"""Job-status poller with phase_public progress rendering.

Issues exactly **zero** ``GET /result`` calls while non-terminal, and
exactly one once terminal. Progress lines are rendered from
``phase_public`` only (never the internal ``phase`` string, which is
operator-only). ``phase_public=None`` is the explicit "queued, awaiting
worker" signal.
"""

from __future__ import annotations

import logging
import random
import time
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict

from mega_code.client.remote_enhance.client import GatewayClient
from mega_code.client.utils.tracing import set_span_attributes, traced

logger = logging.getLogger(__name__)

# Mirrors the gateway's terminal status set. Non-terminal: ``queued``,
# ``processing``. Any new status added upstream must land here in the same
# release; otherwise the poller will spin past it (treating it as
# non-terminal) or short-circuit on an unknown terminal.
_TERMINAL_STATUSES: frozenset[str] = frozenset(
    {
        "succeeded",
        "failed",
        "rejected",
        "cancelled",
        "quarantined",
        "enhancement_blocked",
        "revoked",
    }
)
_KNOWN_PHASES_PUBLIC: frozenset[str] = frozenset(
    {"intake", "evaluation_setup", "iterating", "publishing"}
)

# Poll cadence: 10s nominal with ±2s jitter to avoid synchronised thundering
# herds when many clients poll the same backend.
_BASE_POLL_INTERVAL_S = 10.0
_POLL_JITTER_S = 2.0


class PollResult(BaseModel):
    """What the poller returns once a terminal status is observed.

    The caller still needs to invoke ``client.get_result(job_id)`` exactly
    once after this — the poller deliberately does not embed the result
    fetch so the polling-invariant unit test can assert "zero `/result`
    calls during polling, exactly one after."
    """

    model_config = ConfigDict(frozen=True)

    job_id: str
    status: str
    last_job_detail: dict[str, Any]


class PollTimeout(Exception):
    """Raised when ``--poll-timeout`` elapses without a terminal status."""

    def __init__(self, *, job_id: str, elapsed_s: float):
        super().__init__(f"poll timeout after {elapsed_s:.0f}s for job {job_id}")
        self.job_id = job_id
        self.elapsed_s = elapsed_s


def _format_age(started_at: str | None) -> str:
    if not started_at:
        return ""
    try:
        ts = datetime.fromisoformat(started_at.replace("Z", "+00:00"))
    except ValueError:
        return ""
    age_s = (datetime.now(UTC) - ts).total_seconds()
    if age_s < 0:
        return ""
    return f" [{int(age_s)}s]"


def progress_line(detail: dict[str, Any]) -> str:
    """Render one human-readable progress line for the host LLM tee'd log.

    Logic:

      | status   | phase_public                   | rendering                          |
      |----------|--------------------------------|------------------------------------|
      | queued   | None                           | "queued — waiting for worker [age]"|
      | running  | intake / evaluation_setup /    |                                    |
      |          | iterating / publishing         | "<phase> [<i>/<t>] [age]"          |
      | running  | unknown non-null               | "running (<status>) [age]"         |
      | terminal | (any)                          | "<status> [age]"                   |
    """
    status = str(detail.get("status", ""))
    phase_public = detail.get("phase_public")
    age = _format_age(detail.get("phase_started_at") or detail.get("created_at"))

    if status == "queued" and phase_public is None:
        return f"queued — waiting for worker{age}"
    if status in _TERMINAL_STATUSES:
        return f"{status}{age}"
    if phase_public in _KNOWN_PHASES_PUBLIC:
        cur = detail.get("current_iteration")
        tot = detail.get("total_iterations")
        if cur is not None and tot is not None:
            return f"{phase_public} [{cur}/{tot}]{age}"
        return f"{phase_public}{age}"
    if phase_public is not None:
        logger.warning("unrecognised phase_public=%r — host needs an enum bump", phase_public)
        return f"{status}{age}"
    return f"{status}{age}"


def is_terminal(detail: dict[str, Any]) -> bool:
    return str(detail.get("status", "")) in _TERMINAL_STATUSES


@traced("client.remote_enhance.poller.run")
def run(
    *,
    client: GatewayClient,
    job_id: str,
    poll_timeout_s: float,
    on_progress: Callable[[str], None] | None = None,
    sleep: Callable[[float], None] = time.sleep,
    now: Callable[[], float] = time.monotonic,
) -> PollResult:
    """Poll ``GET /jobs/{id}`` until terminal, with ±2s jitter on the 10s cadence.

    The poller's invariant is that it issues **zero** ``GET /result`` calls
    — those happen exactly once after this returns. ``poll_timeout_s == 0``
    means wait indefinitely (used by integration tests via fake clocks).

    ``sleep`` and ``now`` are injected so tests can drive the loop with a
    fake clock without touching real time.
    """
    set_span_attributes(job_id=str(job_id), poll_timeout_s=poll_timeout_s)
    deadline = None if poll_timeout_s <= 0 else now() + poll_timeout_s
    last_line: str | None = None
    poll_count = 0

    while True:
        detail = client.get_job(job_id)
        poll_count += 1
        line = progress_line(detail)
        if on_progress is not None and line != last_line:
            on_progress(line)
            last_line = line
        if is_terminal(detail):
            set_span_attributes(
                terminal_status=str(detail["status"]),
                poll_count=poll_count,
            )
            return PollResult(
                job_id=str(job_id),
                status=str(detail["status"]),
                last_job_detail=detail,
            )
        if deadline is not None and now() >= deadline:
            raise PollTimeout(job_id=str(job_id), elapsed_s=poll_timeout_s)
        # Wait the next polling interval. Jitter is signed so the average
        # cadence stays at the nominal 10s.
        wait = _BASE_POLL_INTERVAL_S + random.uniform(-_POLL_JITTER_S, _POLL_JITTER_S)
        sleep(wait)
