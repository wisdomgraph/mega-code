# mega_code/client/skill_enhance_aggregator.py
"""Aggregate skill-enhance results and persist them.

Called by the SKILL.md orchestrator (Phase 7) after the host agent has
generated test cases, the A/B runner has produced outputs, and the host
agent has graded them.

Reads a single JSON file containing:
  - ``test_cases``: list of test case dicts
  - ``ab_outputs``: list of A/B output dicts
  - ``gradings``:   list of grading dicts
  - ``skill_name``: name of the evaluated skill
  - ``model``:      model identifier (from A/B metadata)

Computes verdict, saves results to iteration workspace, and prints a summary.

Usage::

    python -m mega_code.client.skill_enhance_aggregator \\
        --eval-data /path/to/eval-full.json \\
        --skill-path /path/to/SKILL.md \\
        [--iteration-dir /path/to/iteration-N]
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

logger = logging.getLogger(__name__)


def _format_eval_summary(result_dict: dict) -> str:
    """Format eval results as a readable summary for stdout."""
    lines = []
    lines.append(f"Evaluating skill: {result_dict['skill_name']}")
    lines.append(f"Model: {result_dict['model']}")
    lines.append("")

    test_results = result_dict.get("test_results", [])
    if test_results:
        lines.append("| # | Task | With Skill | Baseline | Delta |")
        lines.append("|---|------|------------|----------|-------|")

        for i, tr in enumerate(test_results, 1):
            tc = tr["test_case"]
            task = tc["task"][:30]
            total_exp = len(tc["expectations"])

            with_passed = sum(1 for g in tr["with_skill_gradings"] if g["passed"])
            base_passed = sum(1 for g in tr["baseline_gradings"] if g["passed"])
            with_pct = int(tr["with_skill_score"] * 100)
            base_pct = int(tr["baseline_score"] * 100)
            delta = with_pct - base_pct

            lines.append(
                f"| {i} | {task} | {with_passed}/{total_exp} ({with_pct}%) "
                f"| {base_passed}/{total_exp} ({base_pct}%) | {delta:+d}% |"
            )

    lines.append("")
    lines.append("ROI (Return on Investment):")

    perf = result_dict.get("performance_increase", 0)
    savings = result_dict.get("token_savings", 0)
    verdict = result_dict.get("verdict", "UNKNOWN")

    lines.append(f"  Performance increase: {perf:+.0%}")
    display_savings = max(0, savings)
    lines.append(f"  Token savings:        {display_savings:+.0%}")
    lines.append(f"  Verdict:              {verdict}")

    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="mega_code.client.skill_enhance_aggregator",
        description="Aggregate skill-enhance results and save.",
    )
    parser.add_argument(
        "--eval-data",
        required=True,
        help="Path to JSON file with full eval data (test_cases + ab_outputs + gradings).",
    )
    parser.add_argument(
        "--skill-path",
        required=True,
        help="Path to the SKILL.md file being evaluated.",
    )
    parser.add_argument(
        "--iteration-dir",
        default=None,
        help="Path to iteration directory for saving benchmark artifacts.",
    )
    parser.add_argument("--debug", action="store_true", help="Enable debug logging.")
    args = parser.parse_args()

    if args.debug:
        logging.basicConfig(
            level=logging.DEBUG, format="%(asctime)s %(name)s %(levelname)s %(message)s"
        )
    else:
        logging.basicConfig(level=logging.WARNING)

    # Load eval data
    eval_data_path = Path(args.eval_data)
    if not eval_data_path.exists():
        print(f"Eval data file not found: {eval_data_path}", file=sys.stderr)
        sys.exit(1)

    data = json.loads(eval_data_path.read_text(encoding="utf-8"))

    skill_name = data.get("skill_name", "unknown")
    model = data.get("model", "host-agent")
    test_cases = data.get("test_cases", [])
    ab_outputs = data.get("ab_outputs", [])
    gradings = data.get("gradings", [])

    if not test_cases:
        print("No test cases found in eval data.", file=sys.stderr)
        sys.exit(1)

    if len(test_cases) != len(ab_outputs) or len(test_cases) != len(gradings):
        print(
            f"Mismatched lengths: {len(test_cases)} test_cases, "
            f"{len(ab_outputs)} ab_outputs, {len(gradings)} gradings",
            file=sys.stderr,
        )
        sys.exit(1)

    # Aggregate
    from mega_code.client.skill_enhance_models import aggregate_eval_result

    result = aggregate_eval_result(
        skill_name=skill_name,
        model=model,
        test_cases=test_cases,
        ab_outputs=ab_outputs,
        gradings=gradings,
    )
    result_dict = result.model_dump(mode="json")

    # Format and display
    output = _format_eval_summary(result_dict)
    print(output)

    # Save to iteration directory if provided
    if args.iteration_dir:
        iter_dir = Path(args.iteration_dir)
        iter_dir.mkdir(parents=True, exist_ok=True)

        # Save benchmark summary
        benchmark_path = iter_dir / "benchmark.json"
        benchmark_path.write_text(
            json.dumps(result_dict, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        print(f"\nBenchmark saved to: {benchmark_path}")
    else:
        # Print result as JSON to stdout for the orchestrator to capture
        print(f"\n{json.dumps(result_dict, indent=2)}")


if __name__ == "__main__":
    main()
