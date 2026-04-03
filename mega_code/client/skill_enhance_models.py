# mega_code/client/skill_enhance_models.py
"""Pydantic models and verdict logic for skill-enhance evaluation.

Canonical location — previously lived in ``mega_code.pipeline.skill_enhance``
but moved here because the module is pure computation with zero pipeline
dependencies and must be available in the OSS distribution.
"""

from __future__ import annotations

import logging
import os
from typing import Literal

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

# Thresholds — same as existing pipeline eval (generator.py)
EVAL_LIFT_THRESHOLD = max(
    0.0, min(1.0, float(os.getenv("MEGA_CODE_BEHAVIORAL_LIFT_THRESHOLD", "0.05")))
)
EVAL_SAVINGS_THRESHOLD = max(
    0.0, min(1.0, float(os.getenv("MEGA_CODE_BEHAVIORAL_SAVINGS_THRESHOLD", "0.2")))
)


# =============================================================================
# Pydantic Models
# =============================================================================


class EvalExpectation(BaseModel):
    """A natural language assertion about expected output quality."""

    text: str = Field(description="e.g., 'The response uses conventional commit format'")


class EvalTestCase(BaseModel):
    """A single evaluation test case with natural language expectations."""

    task: str = Field(description="Realistic coding task prompt")
    expectations: list[EvalExpectation] = Field(description="3-4 natural language assertions")


class EvalTestSuite(BaseModel):
    """Container for generated test cases."""

    cases: list[EvalTestCase]


class EvalGrading(BaseModel):
    """Grading result for a single expectation."""

    expectation: str
    passed: bool
    evidence: str = Field(description="One-sentence evidence quote from the output")


class EvalGradingResult(BaseModel):
    """Container for grading results of a single output."""

    gradings: list[EvalGrading]


class EvalTestResult(BaseModel):
    """Result of grading a single test case."""

    test_case: EvalTestCase
    with_skill_gradings: list[EvalGrading]
    baseline_gradings: list[EvalGrading]
    with_skill_score: float = Field(description="Fraction of expectations met")
    baseline_score: float
    with_skill_tokens: int = Field(default=0, description="Completion tokens with skill")
    baseline_tokens: int = Field(default=0, description="Completion tokens without skill")


class EvalResult(BaseModel):
    """Aggregated evaluation result."""

    skill_name: str
    model: str
    test_results: list[EvalTestResult]
    with_skill_avg: float
    baseline_avg: float
    performance_increase: float = Field(description="with_skill_avg - baseline_avg (as %)")
    token_savings: float = Field(description="1 - (with_tokens / baseline_tokens) (as %)")
    with_skill_total_tokens: int
    baseline_total_tokens: int
    verdict: Literal["BENEFICIAL", "NOT_BENEFICIAL"] = Field(
        description="BENEFICIAL or NOT_BENEFICIAL"
    )


# =============================================================================
# Verdict Computation
# =============================================================================


def compute_verdict(
    performance_increase: float, token_savings: float
) -> Literal["BENEFICIAL", "NOT_BENEFICIAL"]:
    """Compute eval verdict using same thresholds as pipeline eval.

    BENEFICIAL if:
    - performance_increase > 5%, OR
    - performance_increase >= 0% AND token_savings > 20%
    """
    if performance_increase > EVAL_LIFT_THRESHOLD:
        return "BENEFICIAL"
    if performance_increase >= 0 and token_savings > EVAL_SAVINGS_THRESHOLD:
        return "BENEFICIAL"
    return "NOT_BENEFICIAL"


# =============================================================================
# Aggregation
# =============================================================================


def aggregate_eval_result(
    skill_name: str,
    model: str,
    test_cases: list[dict],
    ab_outputs: list[dict],
    gradings: list[dict],
) -> EvalResult:
    """Aggregate pre-computed eval data into an :class:`EvalResult`.

    This function is called after the host agent has generated test cases,
    the A/B runner has produced outputs, and the host agent has graded them.

    Args:
        skill_name: Name of the skill being evaluated.
        model: Model identifier (from host agent metadata or ``"host-agent"``).
        test_cases: List of test case dicts with ``task`` and ``expectations``.
        ab_outputs: List of A/B output dicts with ``with_skill_output`` and
            ``baseline_output`` (one per test case, same order as *test_cases*).
        gradings: List of grading dicts, one per test case, each containing
            ``with_skill_gradings`` and ``baseline_gradings`` lists.

    Returns:
        Fully populated :class:`EvalResult`.
    """
    test_results: list[EvalTestResult] = []

    for tc_dict, ab, grading in zip(test_cases, ab_outputs, gradings, strict=True):
        expectations = [
            EvalExpectation(text=e["text"] if isinstance(e, dict) else e)
            for e in tc_dict.get("expectations", [])
        ]
        tc = EvalTestCase(task=tc_dict["task"], expectations=expectations)

        with_gradings = [EvalGrading(**g) for g in grading.get("with_skill_gradings", [])]
        baseline_gradings = [EvalGrading(**g) for g in grading.get("baseline_gradings", [])]

        total_exp = len(expectations)

        # Truncate extra gradings (LLM may return duplicates)
        if len(with_gradings) > total_exp:
            with_gradings = with_gradings[:total_exp]
        if len(baseline_gradings) > total_exp:
            baseline_gradings = baseline_gradings[:total_exp]

        with_score = sum(1 for g in with_gradings if g.passed) / total_exp if total_exp else 0.0
        baseline_score = (
            sum(1 for g in baseline_gradings if g.passed) / total_exp if total_exp else 0.0
        )

        # Token counts: estimate from text length if not provided
        with_tokens = ab.get("with_skill_tokens", len(ab.get("with_skill_output", "")))
        baseline_tokens = ab.get("baseline_tokens", len(ab.get("baseline_output", "")))

        test_results.append(
            EvalTestResult(
                test_case=tc,
                with_skill_gradings=with_gradings,
                baseline_gradings=baseline_gradings,
                with_skill_score=with_score,
                baseline_score=baseline_score,
                with_skill_tokens=with_tokens,
                baseline_tokens=baseline_tokens,
            )
        )

    total = len(test_results)
    with_avg = sum(r.with_skill_score for r in test_results) / total if total else 0.0
    baseline_avg = sum(r.baseline_score for r in test_results) / total if total else 0.0
    performance_increase = with_avg - baseline_avg

    with_total_tokens = sum(r.with_skill_tokens for r in test_results)
    baseline_total_tokens = sum(r.baseline_tokens for r in test_results)
    token_savings = (
        1 - (with_total_tokens / baseline_total_tokens) if baseline_total_tokens > 0 else 0.0
    )

    verdict = compute_verdict(performance_increase, token_savings)

    logger.info(
        "Eval aggregated for '%s': perf=%+.1f%%, savings=%+.1f%%, verdict=%s",
        skill_name,
        performance_increase * 100,
        token_savings * 100,
        verdict,
    )

    return EvalResult(
        skill_name=skill_name,
        model=model,
        test_results=test_results,
        with_skill_avg=with_avg,
        baseline_avg=baseline_avg,
        performance_increase=performance_increase,
        token_savings=token_savings,
        with_skill_total_tokens=with_total_tokens,
        baseline_total_tokens=baseline_total_tokens,
        verdict=verdict,
    )
