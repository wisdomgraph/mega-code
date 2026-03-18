"""Async pipeline runner for /mega-code:run command.

Imports only from mega_code.client.* — no pipeline dependencies.

The client abstraction (MegaCodeLocal / MegaCodeRemote) handles mode-specific
details; this script only deals with:
1. CLI argument parsing and project path resolution
2. Creating a client, triggering the pipeline, polling for completion
3. Saving outputs to local pending folders
4. Formatting the JSON notification for the Claude Code hook

Exit codes:
    0 — success
    1 — fatal error (auth, network, unknown)
    2 — conflict (pipeline already running for this project)
    3 — server timeout (pipeline exceeded max runtime)

Usage:
    python -m mega_code.client.run_pipeline
    python -m mega_code.client.run_pipeline --project
    python -m mega_code.client.run_pipeline --project @mega-code
    python -m mega_code.client.run_pipeline --model gemini-3-flash
    python -m mega_code.client.run_pipeline --project --include-codex
    python -m mega_code.client.run_pipeline --poll-existing <run_id> --project <project_id>
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import re
import sys
from pathlib import Path

from mega_code.client.cli import get_env_path, load_env_file

logger = logging.getLogger(__name__)


def _parse_conflict_run_id(detail: str) -> str | None:
    """Extract run_id from 409 detail string (e.g. 'run_id=abc-123')."""
    match = re.search(r"run_id=([a-f0-9-]+)", detail)
    return match.group(1) if match else None


def _load_env() -> None:
    """Load environment variables from stable + repo .env files."""
    # 1. Stable credential store — always loaded first
    from mega_code.client.dirs import data_dir

    stable_env = data_dir() / ".env"
    if stable_env.exists():
        for key, value in load_env_file(stable_env).items():
            os.environ.setdefault(key, value)

    # 2. Plugin root .env — dev overlay (lower priority than stable credentials)
    env_path = get_env_path()
    if env_path.exists():
        for key, value in load_env_file(env_path).items():
            os.environ.setdefault(key, value)


def parse_args() -> argparse.Namespace:
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        description="Run MEGA-Code pipeline and save to pending folders",
        epilog="""
Project argument formats:
  @mega-code           Name prefix (with @ for Claude Code autocomplete)
  mega-code            Name prefix (without @)
  mega-code_b39e0992   Exact folder name
  /path/to/project     Filesystem path
""",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--project",
        nargs="?",
        const="",
        default=None,
        metavar="PROJECT",
        help=(
            "Run on project sessions. Without value: current project. "
            "With value: @name, folder_name, or /path/to/project"
        ),
    )
    parser.add_argument(
        "--session-id",
        type=str,
        help="Specific session ID to process (overrides MEGA_CODE_SESSION_ID)",
    )
    parser.add_argument(
        "--model",
        type=str,
        default=None,
        help="LLM model for pipeline (default: server picks). e.g. gemini-3-flash, gpt-5-mini",
    )
    parser.add_argument(
        "--mode",
        choices=["local", "remote"],
        default=None,
        help="Execution mode. Default: auto-detect from MEGA_CODE_CLIENT_MODE.",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Enable debug logging",
    )
    parser.add_argument(
        "--storage",
        type=str,
        default=None,
        help=(
            "Storage backend for pipeline ('local' or 'postgres'). "
            "Default: MEGA_CODE_PIPELINE_STORAGE env var or 'local'."
        ),
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Force re-run even if cached results exist",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Max sessions to process",
    )
    parser.add_argument(
        "--steps",
        nargs="+",
        default=None,
        metavar="STEP",
        help="Pipeline steps to run (space-separated). Default: all steps.",
    )
    parser.add_argument(
        "--concurrency",
        type=int,
        default=4,
        help="Max concurrent operations (default: 4)",
    )
    parser.add_argument(
        "--include-codex",
        action="store_true",
        help="Include related Codex CLI sessions when loading from project (default: False)",
    )
    parser.add_argument(
        "--poll-timeout",
        type=int,
        default=None,
        metavar="SECONDS",
        help=(
            "Max seconds to wait for pipeline completion (default: 1200 = 20 min). "
            "Pass 0 to wait indefinitely until the pipeline finishes. "
            "Also reads MEGA_CODE_POLL_TIMEOUT env var."
        ),
    )
    parser.add_argument(
        "--poll-existing",
        type=str,
        default=None,
        metavar="RUN_ID",
        help="Poll an already-running pipeline by run_id (skip trigger)",
    )
    parser.add_argument(
        "--env-debug",
        action="store_true",
        help="Print key environment variables and exit",
    )
    return parser.parse_args()


async def main():
    """Main entry point for pipeline runner."""
    _load_env()
    args = parse_args()

    if args.env_debug:
        from mega_code.client.utils.env import print_env_debug

        print("run_pipeline env:", file=sys.stderr)
        print_env_debug()
        sys.exit(0)

    # Configure logging
    logging.basicConfig(
        level=logging.DEBUG if args.debug else logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )

    # Setup tracing (opt-in via MEGA_CODE_TRACING env var)
    from mega_code.client.utils.tracing import get_tracer, setup_tracing

    session_id = args.session_id or os.environ.get("MEGA_CODE_SESSION_ID")
    setup_tracing(service_name="mega-code-client", session_id=session_id)
    tracer = get_tracer(__name__)

    # Imports (deferred to avoid import cost when --env-debug is used)
    import httpx

    from mega_code.client.api import create_client, resolve_mode
    from mega_code.client.pending import (
        PendingResult,
        format_error_notification,
        format_pipeline_notification,
        poll_pipeline_status,
        save_outputs_to_pending,
    )
    from mega_code.client.stats import (
        get_project_sessions_dir,
        resolve_project_path,
    )

    model_name = args.model  # None → server picks best model from user's BYOK keys
    if model_name:
        logger.info(f"Using model: {model_name}")
    else:
        logger.info("Model not specified — server will select based on configured LLM keys")

    # Resolve include flags
    include_codex = args.include_codex

    # Agent identity (set by SKILL.md setup block)
    agent = os.environ.get("MEGA_CODE_AGENT", "")

    # Get environment variables
    # session_id already resolved above for tracing setup
    # MEGA_CODE_PROJECT_DIR is set by skills.
    project_dir_env = Path(os.environ.get("MEGA_CODE_PROJECT_DIR", ".")).resolve()
    storage = args.storage or os.environ.get("MEGA_CODE_PIPELINE_STORAGE", "local")

    # Determine execution mode
    mode = resolve_mode(args.mode)
    logger.info(f"Execution mode: {mode}")

    with tracer.start_as_current_span("run_pipeline") as span:
        # --- Environment snapshot ---
        span.set_attribute("env.MEGA_CODE_PROJECT_DIR", os.environ.get("MEGA_CODE_PROJECT_DIR", ""))
        span.set_attribute("env.MEGA_CODE_SESSION_ID", os.environ.get("MEGA_CODE_SESSION_ID", ""))
        span.set_attribute("env.MEGA_CODE_CLIENT_MODE", os.environ.get("MEGA_CODE_CLIENT_MODE", ""))
        span.set_attribute("env.MEGA_CODE_API_KEY_SET", bool(os.environ.get("MEGA_CODE_API_KEY")))
        span.set_attribute("env.MEGA_CODE_SERVER_URL", os.environ.get("MEGA_CODE_SERVER_URL", ""))
        span.set_attribute(
            "env.MEGA_CODE_PIPELINE_STORAGE", os.environ.get("MEGA_CODE_PIPELINE_STORAGE", "")
        )
        span.set_attribute(
            "env.MEGA_CODE_POLL_TIMEOUT", os.environ.get("MEGA_CODE_POLL_TIMEOUT", "")
        )

        # --- CLI args ---
        span.set_attribute("args.project", str(args.project) if args.project is not None else "")
        span.set_attribute("args.session_id", args.session_id or "")
        span.set_attribute("args.model", args.model or "")
        span.set_attribute("args.mode", args.mode or "auto")
        span.set_attribute("args.storage", args.storage or "")
        span.set_attribute("args.force", args.force)
        span.set_attribute("args.limit", args.limit or 0)
        span.set_attribute("args.steps", ",".join(args.steps) if args.steps else "")
        span.set_attribute("args.concurrency", args.concurrency)
        span.set_attribute("args.include_codex", include_codex)
        span.set_attribute("args.agent", agent)
        span.set_attribute("args.poll_existing", args.poll_existing or "")

        # --- Resolved values ---
        span.set_attribute("pipeline.mode", mode)
        span.set_attribute("pipeline.model", model_name or "")
        span.set_attribute("pipeline.session_id", session_id or "")
        span.set_attribute("pipeline.storage", storage)
        span.set_attribute("pipeline.project_dir_env", str(project_dir_env))

        try:
            # Resolve project directory
            if args.project:
                mega_code_project_dir = resolve_project_path(args.project)
            else:
                mega_code_project_dir = get_project_sessions_dir(str(project_dir_env))

            project_id = mega_code_project_dir.name
            span.set_attribute("pipeline.project_dir", str(mega_code_project_dir))
            span.set_attribute("pipeline.project_id", project_id)

            # Determine session_id or project_path for trigger
            resolved_session_id: str | None = None
            resolved_project_path: Path | None = None

            if args.session_id:
                resolved_session_id = args.session_id
            elif session_id and args.project is None:
                resolved_session_id = session_id
            else:
                resolved_project_path = mega_code_project_dir

            # --- Client protocol: create → trigger → poll → save ---

            # Create client (auto-detects local vs remote based on mode)
            client_kwargs: dict = {}
            if mode == "local":
                client_kwargs["backend"] = storage
                client_kwargs["project_id"] = project_id
            client = create_client(mode=mode, **client_kwargs)
            logger.info(f"Client: {type(client).__name__} (mode={mode})")
            span.set_attribute("pipeline.client_type", type(client).__name__)
            _server = getattr(client, "server_url", None)
            if _server:
                span.set_attribute("pipeline.server_url", _server)

            # Build trigger kwargs
            trigger_kwargs: dict = {
                "project_id": project_id,
                "steps": args.steps,
                "force": args.force,
                "limit": args.limit,
                "concurrency": args.concurrency,
                "include_codex": include_codex,
                "model": model_name,
                "agent": agent,
            }

            if include_codex:
                trigger_kwargs["project_cwd"] = str(project_dir_env)

            if resolved_session_id:
                trigger_kwargs["session_id"] = resolved_session_id
            elif resolved_project_path:
                trigger_kwargs["project_path"] = resolved_project_path

            # Record full trigger payload as span attributes
            span.set_attribute("trigger.project_id", project_id)
            span.set_attribute("trigger.session_id", resolved_session_id or "")
            span.set_attribute("trigger.project_path", str(resolved_project_path or ""))
            span.set_attribute("trigger.force", args.force)
            span.set_attribute("trigger.limit", args.limit or 0)
            span.set_attribute("trigger.concurrency", args.concurrency)
            span.set_attribute("trigger.steps", ",".join(args.steps) if args.steps else "all")
            span.set_attribute("trigger.model", model_name or "server-default")
            span.set_attribute("trigger.include_codex", include_codex)
            if include_codex:
                span.set_attribute("trigger.project_cwd", str(project_dir_env))

            # Validate and resolve poll timeout before triggering (fail fast)
            if args.poll_timeout is not None and args.poll_timeout < 0:
                raise ValueError(f"--poll-timeout must be >= 0, got {args.poll_timeout}")
            if args.poll_timeout is not None:
                _raw = args.poll_timeout
            else:
                env_val = os.environ.get("MEGA_CODE_POLL_TIMEOUT", "1200")
                try:
                    _raw = int(env_val)
                except ValueError:
                    logger.warning(
                        "Invalid MEGA_CODE_POLL_TIMEOUT=%r (must be integer); using default 1200",
                        env_val,
                    )
                    _raw = 1200
            poll_timeout: float | None = None if _raw == 0 else float(_raw)
            if poll_timeout is None:
                logger.info("Poll timeout: indefinite (waiting until pipeline completes)")
            else:
                logger.info(f"Poll timeout: {poll_timeout:.0f}s ({poll_timeout / 60:.0f} min)")

            # Trigger or poll existing pipeline
            if args.poll_existing:
                run_id = args.poll_existing
                _server = getattr(client, "server_url", None)
                logger.info(f"Polling existing pipeline: run_id={run_id}")
                if _server:
                    logger.info(f"Polling URL: {_server}/api/megacode/v1/pipeline/status/{run_id}")
            else:
                logger.info("Triggering pipeline via client...")
                _server = getattr(client, "server_url", None)
                if _server:
                    logger.info(f"POST {_server}/api/megacode/v1/pipeline/run")
                trigger_result = await client.trigger_pipeline_run(**trigger_kwargs)
                run_id = trigger_result.run_id
                logger.info(f"Pipeline triggered: run_id={run_id}, status={trigger_result.status}")
                span.set_attribute("trigger.response.run_id", run_id)
                span.set_attribute("trigger.response.status", trigger_result.status)
                span.set_attribute(
                    "trigger.response.message", getattr(trigger_result, "message", "")
                )
                if _server:
                    logger.info(f"Polling URL: {_server}/api/megacode/v1/pipeline/status/{run_id}")

            # Poll for completion
            span.set_attribute("poll.timeout_seconds", poll_timeout or 0)
            span.set_attribute("poll.run_id", run_id)
            status = await poll_pipeline_status(client, run_id, timeout=poll_timeout)
            span.set_attribute("poll.final_status", status.status)
            span.set_attribute("poll.error", status.error or "")
            if status.started_at:
                span.set_attribute("poll.server_started_at", status.started_at)
            if status.completed_at:
                span.set_attribute("poll.server_completed_at", status.completed_at)
            if status.progress:
                span.set_attribute(
                    "poll.sessions_processed", status.progress.get("sessions_processed", 0)
                )
                span.set_attribute("poll.sessions_total", status.progress.get("sessions_total", 0))
                span.set_attribute("poll.current_phase", status.progress.get("current_phase", ""))

            # Check for server-side timeout — exit code 3 tells the run skill
            # to prompt the user with retry/leave options. The JSON on stdout
            # provides run_id and error details the skill needs for its prompt.
            if status.status == "timeout":
                timeout_info = {
                    "additionalContext": (
                        f"The pipeline timed out on the server ({status.error}).\n"
                        "You can start a new run with /mega-code:run."
                    ),
                    "timeout": {
                        "run_id": run_id,
                        "project_id": project_id,
                        "error": status.error,
                    },
                }
                print(json.dumps(timeout_info))
                sys.exit(3)

            if status.status == "failed":
                error_msg = status.error or "Unknown error"
                logger.error(f"Pipeline failed: {error_msg}")
                result = PendingResult(
                    run_id=run_id,
                    project_id=project_id,
                    errors=[error_msg],
                )
            else:
                # Save outputs to pending folders
                result = save_outputs_to_pending(status, project_id=project_id, run_id=run_id)

            span.set_attribute("pipeline.skills_count", result.skill_count)
            span.set_attribute("pipeline.strategies_count", result.strategy_count)
            span.set_attribute("pipeline.lessons_count", result.lesson_count)
            span.set_attribute("pipeline.total_outputs", result.total_count)
            span.set_attribute("pipeline.has_outputs", result.has_outputs())
            span.set_attribute("pipeline.run_id", result.run_id)
            # Record individual skill/strategy names for easy filtering
            if result.skills:
                span.set_attribute("output.skill_names", ",".join(s.name for s in result.skills))
                span.set_attribute("output.skill_paths", ",".join(s.path for s in result.skills))
            if result.strategies:
                span.set_attribute(
                    "output.strategy_names", ",".join(s.name for s in result.strategies)
                )
            if result.lessons:
                span.set_attribute(
                    "output.lesson_slugs", ",".join(ls.slug for ls in result.lessons)
                )
            if result.errors:
                span.set_attribute("output.errors", ",".join(result.errors))

            # Format and output notification
            notification = format_pipeline_notification(result)

            # JSON on stdout serves two consumers:
            # 1. The run skill (SKILL.md) parses it for run_id/project_id
            #    and uses exit code 0 to enter the post-pipeline workflow.
            # 2. Claude Code hooks use "additionalContext" to inject context.
            output = {"additionalContext": notification.strip()}
            print(json.dumps(output))

        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 409:
                # Exit code 2 tells the run skill to prompt the user with
                # stop/wait/leave options. The JSON on stdout provides the
                # run_id and project_id the skill needs to act on the choice.
                detail = exc.response.text
                conflict_run_id = _parse_conflict_run_id(detail)
                conflict_info = {
                    "additionalContext": (
                        f"A pipeline is already running for this project"
                        f" (run_id: {conflict_run_id or 'unknown'}).\n"
                        "Use /mega-code:stop to stop it, or wait for it to finish."
                    ),
                    "conflict": {
                        "run_id": conflict_run_id,
                        "project_id": project_id,
                        "detail": detail,
                    },
                }
                print(json.dumps(conflict_info))
                sys.exit(2)
            span.record_exception(exc)
            logger.exception("Pipeline failed")
            notification = format_error_notification(str(exc))
            output = {"additionalContext": notification.strip()}
            print(json.dumps(output))
            sys.exit(1)

        except Exception as e:
            span.record_exception(e)
            logger.exception("Pipeline failed")
            notification = format_error_notification(str(e))
            output = {"additionalContext": notification.strip()}
            print(json.dumps(output))
            sys.exit(1)

        finally:
            pass

    # Export after root span closes — all spans are in the file
    try:
        from mega_code.client.utils.ndjson_tracing import export_traces
        from mega_code.client.utils.tracing import get_span_writer

        export_traces(writer=get_span_writer())
    except Exception:
        pass


if __name__ == "__main__":
    asyncio.run(main())
