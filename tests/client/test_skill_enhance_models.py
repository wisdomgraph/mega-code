# tests/client/test_skill_enhance_models.py
"""Tests for skill_enhance_models — verdict logic, aggregation, and threshold clamping."""

from __future__ import annotations

import pytest

from mega_code.client.skill_enhance_models import (
    EVAL_LIFT_THRESHOLD,
    EVAL_SAVINGS_THRESHOLD,
    aggregate_eval_result,
    compute_verdict,
)


@pytest.fixture(autouse=True)
def _default_thresholds(monkeypatch):
    """Pin thresholds to defaults so tests pass regardless of env vars."""
    monkeypatch.setattr("mega_code.client.skill_enhance_models.EVAL_LIFT_THRESHOLD", 0.05)
    monkeypatch.setattr("mega_code.client.skill_enhance_models.EVAL_SAVINGS_THRESHOLD", 0.2)


# ---------------------------------------------------------------------------
# compute_verdict
# ---------------------------------------------------------------------------


class TestComputeVerdict:
    def test_high_performance_is_beneficial(self):
        assert compute_verdict(0.10, 0.0) == "BENEFICIAL"

    def test_zero_performance_high_savings_is_beneficial(self):
        assert compute_verdict(0.0, 0.25) == "BENEFICIAL"

    def test_negative_performance_high_savings_is_not_beneficial(self):
        assert compute_verdict(-0.01, 0.90) == "NOT_BENEFICIAL"

    def test_at_lift_threshold_is_not_beneficial(self):
        """Exactly at EVAL_LIFT_THRESHOLD (0.05) should NOT be beneficial (> not >=)."""
        assert compute_verdict(0.05, 0.0) == "NOT_BENEFICIAL"

    def test_just_above_lift_threshold_is_beneficial(self):
        assert compute_verdict(0.051, 0.0) == "BENEFICIAL"

    def test_at_savings_threshold_is_not_beneficial(self):
        """Exactly at EVAL_SAVINGS_THRESHOLD (0.2) should NOT be beneficial (> not >=)."""
        assert compute_verdict(0.0, 0.2) == "NOT_BENEFICIAL"

    def test_just_above_savings_threshold_is_beneficial(self):
        assert compute_verdict(0.0, 0.201) == "BENEFICIAL"

    def test_both_zero_is_not_beneficial(self):
        assert compute_verdict(0.0, 0.0) == "NOT_BENEFICIAL"


# ---------------------------------------------------------------------------
# Threshold clamping
# ---------------------------------------------------------------------------


class TestThresholdClamping:
    def test_thresholds_are_clamped_to_unit_range(self):
        assert 0.0 <= EVAL_LIFT_THRESHOLD <= 1.0
        assert 0.0 <= EVAL_SAVINGS_THRESHOLD <= 1.0


# ---------------------------------------------------------------------------
# aggregate_eval_result — strict zip
# ---------------------------------------------------------------------------


class TestAggregateEvalResult:
    def _make_test_case(self, task: str = "Write a function") -> dict:
        return {"task": task, "expectations": [{"text": "Uses correct syntax"}]}

    def _make_ab_output(self) -> dict:
        return {
            "with_skill_output": "def foo(): pass",
            "baseline_output": "def foo(): pass",
            "with_skill_tokens": 10,
            "baseline_tokens": 12,
        }

    def _make_grading(self) -> dict:
        return {
            "with_skill_gradings": [
                {"expectation": "Uses correct syntax", "passed": True, "evidence": "yes"}
            ],
            "baseline_gradings": [
                {"expectation": "Uses correct syntax", "passed": False, "evidence": "no"}
            ],
        }

    def test_valid_aggregation(self):
        result = aggregate_eval_result(
            skill_name="test-skill",
            model="host-agent",
            test_cases=[self._make_test_case()],
            ab_outputs=[self._make_ab_output()],
            gradings=[self._make_grading()],
        )
        assert result.skill_name == "test-skill"
        assert len(result.test_results) == 1

    def test_mismatched_lengths_raises_value_error(self):
        with pytest.raises(ValueError):
            aggregate_eval_result(
                skill_name="test-skill",
                model="host-agent",
                test_cases=[self._make_test_case(), self._make_test_case()],
                ab_outputs=[self._make_ab_output()],
                gradings=[self._make_grading()],
            )
