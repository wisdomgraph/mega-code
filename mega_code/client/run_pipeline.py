"""Async pipeline runner for /mega-code:run command.

Imports only from mega_code.client.* — no pipeline dependencies.

The client abstraction (MegaCodeLocal / MegaCodeRemote) handles mode-specific
details; this script only deals with:
1. CLI argument parsing and project path resolution
2. Creating a client, triggering the pipeline, polling for completion
3. Saving outputs to local pending folders
4. Formatting the JSON notification for the Claude Code hook

Usage:
    python -m mega_code.client.run_pipeline
    python -m mega_code.client.run_pipeline --project
    python -m mega_code.client.run_pipeline --project @mega-code
    python -m mega_code.client.run_pipeline --model gemini-3-flash
    python -m mega_code.client.run_pipeline --project --include-claude
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
from pathlib import Path

from mega_code.client.cli import get_env_path, load_env_file

logger = logging.getLogger(__name__)


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
        help="Specific session ID to process (overrides CLAUDE_SESSION_ID)",
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
        "--include-claude",
        action="store_true",
        help="Include related Claude Code sessions when loading from project (default: False)",
    )
    parser.add_argument(
        "--include-codex",
        action="store_true",
        help="Include related Codex CLI sessions when loading from project (default: False)",
    )
    parser.add_argument(
        "--include-all",
        action="store_true",
        help="Include sessions from all sources (Claude + Codex + future integrations)",
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

    # Setup tracing
    from mega_code.client.utils.tracing import get_tracer, setup_tracing

    setup_tracing(service_name="mega-code-client")
    tracer = get_tracer(__name__)

    # Imports (deferred to avoid import cost when --env-debug is used)
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
    include_claude = args.include_claude or args.include_all
    include_codex = args.include_codex or args.include_all

    # Get environment variables
    session_id = args.session_id or os.environ.get("CLAUDE_SESSION_ID")
    project_dir_env = Path(os.environ.get("CLAUDE_PROJECT_DIR", ".")).resolve()
    storage = args.storage or os.environ.get("MEGA_CODE_PIPELINE_STORAGE", "local")

    # Determine execution mode
    mode = resolve_mode(args.mode)
    logger.info(f"Execution mode: {mode}")

    with tracer.start_as_current_span("run_pipeline") as span:
        span.set_attribute("pipeline.mode", mode)
        span.set_attribute("pipeline.model", model_name)
        span.set_attribute("pipeline.session_id", session_id or "")
        span.set_attribute("pipeline.storage", storage)

        try:
            # Resolve project directory
            if args.project:
                mega_code_project_dir = resolve_project_path(args.project)
            else:
                mega_code_project_dir = get_project_sessions_dir(str(project_dir_env))

            project_id = mega_code_project_dir.name
            span.set_attribute("pipeline.project_dir", str(mega_code_project_dir))

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

            # Build trigger kwargs
            trigger_kwargs: dict = {
                "project_id": project_id,
                "steps": args.steps,
                "force": args.force,
                "limit": args.limit,
                "concurrency": args.concurrency,
                "include_claude": include_claude,
                "include_codex": include_codex,
                "model": model_name,
            }

            if resolved_session_id:
                trigger_kwargs["session_id"] = resolved_session_id
            elif resolved_project_path:
                trigger_kwargs["project_path"] = resolved_project_path

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

            # Trigger pipeline
            logger.info("Triggering pipeline via client...")
            trigger_result = await client.trigger_pipeline_run(**trigger_kwargs)
            run_id = trigger_result.run_id
            logger.info(f"Pipeline triggered: run_id={run_id}, status={trigger_result.status}")

            # Poll for completion
            status = await poll_pipeline_status(client, run_id, timeout=poll_timeout)

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

            # Format and output notification
            notification = format_pipeline_notification(result)

            # Output JSON for Claude Code hook
            output = {"additionalContext": notification.strip()}
            print(json.dumps(output))

        except Exception as e:
            span.record_exception(e)
            logger.exception("Pipeline failed")
            notification = format_error_notification(str(e))
            output = {"additionalContext": notification.strip()}
            print(json.dumps(output))
            sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
