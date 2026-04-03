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
import os
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
    from mega_code.client.utils.tracing import get_tracer

    tracer = get_tracer(__name__)
    # Capture parent span id before entering gather — the global span stack
    # is not async-safe, so child spans set parent explicitly.
    parent_span_id = tracer.current_span_id or ""

    async def _one(tc: dict, index: int) -> dict:
        task = tc["task"]
        span = tracer.start_span(f"skill_enhance_runner.test_case_{index}")
        span.parent_span_id = parent_span_id
        with span:
            span.set_attribute("task_preview", task[:100])
            span.set_attribute("task_full", task)
            span.set_attribute("expectation_count", len(tc.get("expectations", [])))
            span.set_attribute("expectations_json", json.dumps(tc.get("expectations", [])))
            span.set_attribute("agent", agent or "auto")

            # Run with-skill and baseline completions concurrently
            with_skill_span = tracer.start_span(
                f"skill_enhance_runner.test_case_{index}.with_skill"
            )
            with_skill_span.parent_span_id = span.span_id
            baseline_span = tracer.start_span(f"skill_enhance_runner.test_case_{index}.baseline")
            baseline_span.parent_span_id = span.span_id

            async def _run_with_skill():
                with with_skill_span as s:
                    s.set_attribute("arm", "with_skill")
                    s.set_attribute("has_system_prompt", True)
                    result = await complete(prompt=task, system_prompt=skill_md, agent=agent)
                    s.set_attribute("output_tokens", result.output_tokens)
                    s.set_attribute("model", result.model)
                    s.set_attribute("output_preview", result.text[:500])
                    return result

            async def _run_baseline():
                with baseline_span as s:
                    s.set_attribute("arm", "baseline")
                    s.set_attribute("has_system_prompt", False)
                    result = await complete(prompt=task, agent=agent)
                    s.set_attribute("output_tokens", result.output_tokens)
                    s.set_attribute("model", result.model)
                    s.set_attribute("output_preview", result.text[:500])
                    return result

            outcomes = await asyncio.gather(
                _run_with_skill(),
                _run_baseline(),
                return_exceptions=True,
            )
            for i, outcome in enumerate(outcomes):
                arm_name = "with_skill" if i == 0 else "baseline"
                if isinstance(outcome, BaseException):
                    span.set_attribute(f"{arm_name}_failed", True)
                    span.set_attribute(f"{arm_name}_error_type", type(outcome).__name__)
                    span.set_attribute(f"{arm_name}_error", str(outcome)[:1000])
                    span.record_exception(outcome)
                    raise outcome
            # After the loop, all outcomes are CompletionResult (exceptions were raised)
            assert not isinstance(outcomes[0], BaseException)
            assert not isinstance(outcomes[1], BaseException)
            with_result, baseline_result = outcomes[0], outcomes[1]
            span.set_attribute("with_skill_tokens", with_result.output_tokens)
            span.set_attribute("baseline_tokens", baseline_result.output_tokens)
            span.set_attribute("with_skill_model", with_result.model)
            span.set_attribute("baseline_model", baseline_result.model)
            span.set_attribute(
                "token_delta", with_result.output_tokens - baseline_result.output_tokens
            )
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

    results = await asyncio.gather(
        *[_one(tc, i) for i, tc in enumerate(test_cases)], return_exceptions=True
    )
    checked: list[dict] = []
    for r in results:
        if isinstance(r, BaseException):
            raise r
        checked.append(r)
    return checked


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

    # Setup tracing
    from mega_code.client.utils.tracing import get_span_writer, get_tracer, setup_tracing

    session_id = os.environ.get("MEGA_CODE_SESSION_ID") or os.environ.get("CLAUDE_SESSION_ID")
    setup_tracing(service_name="mega-code-skill-enhance-runner", session_id=session_id)
    tracer = get_tracer(__name__)

    try:
        with tracer.start_as_current_span("skill_enhance_runner") as root_span:
            root_span.set_attribute("args.test_cases", str(args.test_cases))
            root_span.set_attribute("args.skill_md", str(args.skill_md))
            root_span.set_attribute("args.agent", args.agent or "auto")

            # Load inputs
            test_cases_path = Path(args.test_cases)
            if not test_cases_path.exists():
                print(f"Test cases file not found: {test_cases_path}", file=sys.stderr)
                sys.exit(1)

            test_data = json.loads(test_cases_path.read_text(encoding="utf-8"))
            # Accept both {"cases": [...]} (EvalTestSuite) and bare list
            cases = test_data.get("cases", test_data) if isinstance(test_data, dict) else test_data
            root_span.set_attribute("test_case_count", len(cases))

            skill_md_path = Path(args.skill_md)
            if skill_md_path.is_dir():
                skill_md_path = skill_md_path / "SKILL.md"
            if not skill_md_path.exists():
                print(f"SKILL.md not found: {skill_md_path}", file=sys.stderr)
                sys.exit(1)
            skill_md = skill_md_path.read_text(encoding="utf-8")

            # Run A/B
            ab_results = asyncio.run(_run_ab(cases, skill_md, agent=args.agent))
            root_span.set_attribute("result_count", len(ab_results))

            total_with_tokens = sum(r.get("with_skill_tokens", 0) for r in ab_results)
            total_baseline_tokens = sum(r.get("baseline_tokens", 0) for r in ab_results)
            root_span.set_attribute("total_with_skill_tokens", total_with_tokens)
            root_span.set_attribute("total_baseline_tokens", total_baseline_tokens)

            # Output JSON — write to file if --output is given (avoids uv stderr
            # pollution when the SKILL.md orchestrator captures output), else stdout.
            result_json = json.dumps(ab_results, indent=2, ensure_ascii=False)
            if args.output:
                Path(args.output).write_text(result_json, encoding="utf-8")
                print(f"Results written to {args.output}", file=sys.stderr)
            else:
                sys.stdout.write(result_json + "\n")
                sys.stdout.flush()
    except Exception as exc:
        root_span.record_exception(exc)  # pyright: ignore[reportPossiblyUnboundVariable]
        raise
    finally:
        from mega_code.client.utils.ndjson_tracing import export_traces

        export_traces(writer=get_span_writer())


if __name__ == "__main__":
    main()
