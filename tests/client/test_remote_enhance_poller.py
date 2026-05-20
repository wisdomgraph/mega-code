"""Poller tests — invariant: zero ``/result`` calls during polling.

The polling invariant (design doc §8.11) is what lets the SKILL.md prompt
the user about install location only once: the result fetch happens
exactly once, at the boundary between non-terminal and terminal. If the
poller calls ``/result`` mid-flight, the gateway emits 409 ``not_terminal``
which the canonical client never tolerates.

Drives ``poller.run`` with a fake clock + a stubbed ``GatewayClient`` so
no real time elapses and the test is deterministic.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

from mega_code.client.remote_enhance.poller import (
    PollTimeout,
    is_terminal,
    progress_line,
    run,
)


class _FakeClock:
    """Deterministic monotonic clock + sleep accumulator."""

    def __init__(self) -> None:
        self.now = 0.0
        self.slept: list[float] = []

    def time(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += seconds


def _client_with_responses(responses: list[dict[str, Any]]) -> MagicMock:
    """Stub ``GatewayClient`` returning ``responses`` from ``get_job`` in order.

    ``get_result`` is *also* stubbed (with ``MagicMock``) so the test can
    assert ``get_result.call_count == 0`` even though the poller never calls
    it — proving the invariant by contradiction-friendly assertion.
    """
    client = MagicMock()
    client.get_job.side_effect = responses
    client.get_result.return_value = {}  # never reached
    return client


# ---------------------------------------------------------------------------
# Progress-line rendering — design doc §"Queued state derivation" matrix
# ---------------------------------------------------------------------------


def test_progress_line_queued_with_phase_public_none():
    line = progress_line({"status": "queued", "phase_public": None})
    assert line.startswith("queued — waiting for worker")


def test_progress_line_running_with_iteration_count():
    line = progress_line(
        {
            "status": "running",
            "phase_public": "iterating",
            "current_iteration": 2,
            "total_iterations": 3,
        }
    )
    assert line.startswith("iterating [2/3]")


def test_progress_line_running_unknown_phase_falls_back_to_status():
    line = progress_line({"status": "running", "phase_public": "future_phase"})
    # Forward-compat: log warning, render status only.
    assert line.startswith("running")


def test_progress_line_terminal_uses_status():
    assert progress_line({"status": "succeeded"}).startswith("succeeded")


def test_is_terminal_recognises_all_seven_terminal_statuses():
    for s in (
        "succeeded",
        "failed",
        "rejected",
        "cancelled",
        "quarantined",
        "enhancement_blocked",
        "revoked",
    ):
        assert is_terminal({"status": s}), f"{s} should be terminal"
    for s in ("queued", "running", "processing"):
        assert not is_terminal({"status": s}), f"{s} should not be terminal"


# ---------------------------------------------------------------------------
# Polling invariant — zero /result calls during polling
# ---------------------------------------------------------------------------


def test_run_issues_zero_result_calls_until_terminal():
    """3 non-terminal polls then one succeeded — get_result never called."""
    clock = _FakeClock()
    client = _client_with_responses(
        [
            {"status": "queued", "phase_public": None},
            {"status": "running", "phase_public": "intake"},
            {
                "status": "running",
                "phase_public": "iterating",
                "current_iteration": 1,
                "total_iterations": 3,
            },
            {"status": "succeeded"},
        ]
    )
    progress: list[str] = []

    result = run(
        client=client,
        job_id="job-1",
        poll_timeout_s=600,
        on_progress=progress.append,
        sleep=clock.sleep,
        now=clock.time,
    )

    assert result.status == "succeeded"
    assert client.get_job.call_count == 4
    assert client.get_result.call_count == 0  # the invariant
    # Three sleeps between four polls (none after terminal).
    assert len(clock.slept) == 3
    # Each sleep is in the [8, 12]s jitter window.
    for s in clock.slept:
        assert 8.0 <= s <= 12.0


def test_run_dedupes_progress_lines():
    """Same status across two polls → only one progress callback."""
    clock = _FakeClock()
    client = _client_with_responses(
        [
            {
                "status": "running",
                "phase_public": "iterating",
                "current_iteration": 1,
                "total_iterations": 3,
            },
            {
                "status": "running",
                "phase_public": "iterating",
                "current_iteration": 1,
                "total_iterations": 3,
            },
            {"status": "succeeded"},
        ]
    )
    progress: list[str] = []

    run(
        client=client,
        job_id="job-1",
        poll_timeout_s=600,
        on_progress=progress.append,
        sleep=clock.sleep,
        now=clock.time,
    )

    # Two distinct lines: "iterating [1/3]" and "succeeded".
    assert len(progress) == 2
    assert progress[0].startswith("iterating [1/3]")
    assert progress[1].startswith("succeeded")


def test_run_raises_poll_timeout_on_deadline():
    clock = _FakeClock()
    # Endless non-terminal responses.
    client = MagicMock()
    client.get_job.return_value = {"status": "running", "phase_public": "iterating"}

    with pytest.raises(PollTimeout) as exc_info:
        run(
            client=client,
            job_id="job-1",
            poll_timeout_s=30,  # exceeded after ~3 sleeps
            sleep=clock.sleep,
            now=clock.time,
        )
    assert exc_info.value.job_id == "job-1"
    assert exc_info.value.elapsed_s == 30
    # No /result call even on timeout.
    client.get_result.assert_not_called()


def test_run_with_zero_timeout_polls_indefinitely_until_terminal():
    clock = _FakeClock()
    client = _client_with_responses(
        [{"status": "running", "phase_public": "iterating"}] * 100 + [{"status": "succeeded"}]
    )

    result = run(
        client=client,
        job_id="job-1",
        poll_timeout_s=0,  # ∞
        sleep=clock.sleep,
        now=clock.time,
    )
    assert result.status == "succeeded"
    assert client.get_job.call_count == 101  # 100 non-terminal + 1 terminal
