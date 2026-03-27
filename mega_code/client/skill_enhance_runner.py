# mega_code/client/skill_enhance_runner.py
"""Run A/B tests for skill evaluation using the host agent CLI.

Called by the SKILL.md orchestrator (Phase 3).  Reads test-case JSON
produced by the host agent, runs isolated completions for each test case
(with-skill vs baseline) via ``host_llm.complete()``, and writes the
A/B output JSON to stdout.

Usage::

    python -m mega_code.client.skill_enhance_runner \\
        --test-cases /tmp/mega-eval-tests.json \\
        --skill-md /path/to/SKILL.md
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from pathlib import Path

logger = logging.getLogger(__name__)


async def _run_ab(test_cases: list[dict], skill_md: str, agent: str | None = None) -> list[dict]:
    """Run A/B completions for every test case.

    For each test case two isolated completions are made via the host
    agent CLI:

    * **with-skill** — the skill content is passed as ``system_prompt``
    * **baseline** — no system prompt (plain task only)

    Args:
        test_cases: List of test case dicts with ``task`` and ``expectations``.
        skill_md: Full SKILL.md content to use as system prompt for with-skill arm.
        agent: Force a specific agent CLI (``"claude"`` or ``"codex"``).
            When ``None``, auto-detected.

    Returns a list of dicts, one per test case.
    """
    from mega_code.client.host_llm import complete

    async def _one(tc: dict) -> dict:
        task = tc["task"]
        outcomes = await asyncio.gather(
            complete(prompt=task, system_prompt=skill_md, agent=agent),
            complete(prompt=task, agent=agent),
            return_exceptions=True,
        )
        for outcome in outcomes:
            if isinstance(outcome, BaseException):
                raise outcome
        with_result, baseline_result = outcomes
        return {
            "task": task,
            "expectations": tc.get("expectations", []),
            "with_skill_output": with_result.text,
            "baseline_output": baseline_result.text,
            "with_skill_model": with_result.model,
            "baseline_model": baseline_result.model,
            "with_skill_tokens": with_result.output_tokens,
            "baseline_tokens": baseline_result.output_tokens,
        }

    outcomes = await asyncio.gather(*[_one(tc) for tc in test_cases], return_exceptions=True)
    for outcome in outcomes:
        if isinstance(outcome, BaseException):
            raise outcome
    return list(outcomes)


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="mega_code.client.skill_enhance_runner",
        description="Run A/B tests for skill eval using the host agent CLI.",
    )
    parser.add_argument(
        "--test-cases",
        required=True,
        help="Path to JSON file with test cases (EvalTestSuite format).",
    )
    parser.add_argument(
        "--skill-md",
        required=True,
        help="Path to the SKILL.md file being evaluated.",
    )
    parser.add_argument(
        "--agent",
        default=None,
        choices=["claude", "codex"],
        help="Force a specific agent CLI for A/B completions (auto-detected if omitted).",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="Write JSON results to this file instead of stdout (avoids uv warning pollution).",
    )
    parser.add_argument("--debug", action="store_true", help="Enable debug logging.")
    args = parser.parse_args()

    if args.debug:
        logging.basicConfig(
            level=logging.DEBUG, format="%(asctime)s %(name)s %(levelname)s %(message)s"
        )
    else:
        logging.basicConfig(level=logging.WARNING)

    # Load inputs
    test_cases_path = Path(args.test_cases)
    if not test_cases_path.exists():
        print(f"Test cases file not found: {test_cases_path}", file=sys.stderr)
        sys.exit(1)

    test_data = json.loads(test_cases_path.read_text(encoding="utf-8"))
    # Accept both {"cases": [...]} (EvalTestSuite) and bare list
    cases = test_data.get("cases", test_data) if isinstance(test_data, dict) else test_data

    skill_md_path = Path(args.skill_md)
    if skill_md_path.is_dir():
        skill_md_path = skill_md_path / "SKILL.md"
    if not skill_md_path.exists():
        print(f"SKILL.md not found: {skill_md_path}", file=sys.stderr)
        sys.exit(1)
    skill_md = skill_md_path.read_text(encoding="utf-8")

    # Run A/B
    ab_results = asyncio.run(_run_ab(cases, skill_md, agent=args.agent))

    # Output JSON — write to file if --output is given (avoids uv stderr
    # pollution when the SKILL.md orchestrator captures output), else stdout.
    result_json = json.dumps(ab_results, indent=2, ensure_ascii=False)
    if args.output:
        Path(args.output).write_text(result_json, encoding="utf-8")
        print(f"Results written to {args.output}", file=sys.stderr)
    else:
        sys.stdout.write(result_json + "\n")
        sys.stdout.flush()


if __name__ == "__main__":
    main()
