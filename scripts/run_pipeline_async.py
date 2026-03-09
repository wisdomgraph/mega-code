#!/usr/bin/env python3
"""Async pipeline runner for /mega-code:run command.

Pure client-only CLI — imports only from mega_code.client.*, no enterprise
pipeline dependencies.  Works in both OSS and enterprise editions.

The client abstraction (MegaCodeLocal / MegaCodeRemote) handles mode-specific
details; this script only deals with:
1. CLI argument parsing and project path resolution
2. Creating a client, triggering the pipeline, polling for completion
3. Saving outputs to local pending folders
4. Formatting the JSON notification for the Claude Code hook

Supports two execution modes:
- local (default in enterprise): Runs pipeline in-process via MegaCodeLocal.
- remote (default in OSS): Triggers server pipeline via MegaCodeRemote, polls.

Usage:
    # Run on current session (uses CLAUDE_SESSION_ID)
    uv run python scripts/run_pipeline_async.py

    # Run on all project sessions (current project)
    uv run python scripts/run_pipeline_async.py --project

    # Run on a specific project (@ prefix, folder name, or path)
    uv run python scripts/run_pipeline_async.py --project @mega-code
    uv run python scripts/run_pipeline_async.py --project mega-code_b39e0992
    uv run python scripts/run_pipeline_async.py --project /path/to/project

    # Run on specific session
    uv run python scripts/run_pipeline_async.py --session-id <uuid>

    # Specify model
    uv run python scripts/run_pipeline_async.py --project --model gemini-3-flash

    # Force remote mode
    uv run python scripts/run_pipeline_async.py --project --mode remote

    # Include Claude Code sessions
    uv run python scripts/run_pipeline_async.py --project --include-claude

    # Include Codex CLI sessions
    uv run python scripts/run_pipeline_async.py --project --include-codex

    # Include all sources (Claude + Codex)
    uv run python scripts/run_pipeline_async.py --project --include-all

Environment Variables:
    CLAUDE_SESSION_ID: Current session ID
    CLAUDE_PROJECT_DIR: Project directory path
    MEGA_CODE_CLIENT_MODE: Execution mode ('local' or 'remote')
    MEGA_CODE_SERVER_URL: Server URL for remote mode
    MEGA_CODE_API_KEY: API key for remote mode
"""

import argparse
import asyncio
import json
import logging
import os
import sys
from pathlib import Path

# Add project root to path for imports
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

# Load environment variables
import dotenv  # noqa: E402

# 1. Stable credential store (~/.local/mega-code/.env) — always loaded first
_stable_env = Path.home() / ".local" / "mega-code" / ".env"
if _stable_env.exists():
    dotenv.load_dotenv(_stable_env, override=False)

# 2. Repo root .env — dev overlay (lower priority than stable credentials)
_env_path = project_root / ".env"
if _env_path.exists():
    dotenv.load_dotenv(_env_path, override=False)

# All imports are from mega_code.client.* — no enterprise pipeline dependencies.
from mega_code.client.api import create_client  # noqa: E402
from mega_code.client.pending import (  # noqa: E402
    PendingResult,
    format_review_notification,
    poll_pipeline_status,
    save_outputs_to_pending,
)
from mega_code.client.stats import (  # noqa: E402
    get_project_sessions_dir,
    get_projects_dir,
    load_mapping,
)
from mega_code.client.utils.tracing import get_tracer, setup_tracing  # noqa: E402

logger = logging.getLogger(__name__)

# No client-side default model — let the server pick based on the user's configured
# BYOK keys (priority: OpenAI > Anthropic > Gemini via resolve_default_model_for_keys).
# Only set a model here when the user explicitly passes --model.
DEFAULT_PIPELINE_MODEL = None

NO_OUTPUTS_NOTIFICATION = """
\u2554\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2557
\u2551  \u26a0\ufe0f  MEGA-CODE: PIPELINE COMPLETE - NO NEW OUTPUTS                 \u2551
\u255a\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u255d

The pipeline completed but did not generate any new skills, strategies, or lessons.
This may happen if:
- The session(s) didn't contain learnable patterns
- Quality gates filtered out low-quality outputs
- No new patterns were detected

Try running on more sessions with: /mega-code:run --project
"""


# =============================================================================
# Helpers
# =============================================================================


def resolve_project_path(project_arg: str) -> Path:
    """Resolve a project argument to a mega-code data folder path.

    Supports three input formats:
    1. @prefix or name prefix: fuzzy match against mapping.json keys
       e.g. '@mega-code' or 'mega-code' -> ~/.local/mega-code/projects/mega-code_b39e0992/
    2. Folder name with hash: direct lookup
       e.g. 'mega-code_b39e0992' -> ~/.local/mega-code/projects/mega-code_b39e0992/
    3. Absolute/relative path: resolve via get_project_sessions_dir()
       e.g. '/Users/foo/my-project' -> ~/.local/mega-code/projects/my-project_a1b2c3d4/

    Args:
        project_arg: Project identifier (with optional @ prefix).

    Returns:
        Path to the mega-code project data folder.

    Raises:
        ValueError: If the project cannot be resolved.
    """
    # Strip @ prefix if present (Claude Code autocomplete adds this)
    arg = project_arg.lstrip("@").strip()

    if not arg:
        raise ValueError("Empty project argument")

    projects_dir = get_projects_dir()
    mapping = load_mapping()

    # Strategy 1: Exact folder name match (e.g. 'mega-code_b39e0992')
    candidate = projects_dir / arg
    if candidate.is_dir():
        logger.info(f"Resolved project by exact folder name: {arg}")
        return candidate

    # Strategy 2: Prefix match against mapping keys (e.g. 'mega-code')
    matches = [
        folder_name
        for folder_name in mapping
        if folder_name.startswith(arg) and (projects_dir / folder_name).is_dir()
    ]
    if len(matches) == 1:
        logger.info(f"Resolved project by prefix '{arg}' -> {matches[0]}")
        return projects_dir / matches[0]
    if len(matches) > 1:
        match_list = ", ".join(matches)
        raise ValueError(
            f"Ambiguous project prefix '{arg}' matches: {match_list}. "
            f"Use a more specific name or the full folder name."
        )

    # Strategy 3: Treat as filesystem path, resolve via stats
    path = Path(arg).expanduser().resolve()
    if path.is_dir():
        logger.info(f"Resolved project by path: {path}")
        return get_project_sessions_dir(str(path))

    raise ValueError(
        f"Cannot resolve project '{project_arg}'. "
        f"Use: @<name-prefix>, <folder_name>, or /path/to/project"
    )


def resolve_mode(args: argparse.Namespace) -> str:
    """Determine execution mode (local or remote).

    Priority:
    1. Explicit --mode argument
    2. MEGA_CODE_CLIENT_MODE env var
    3. Default to 'local'
    """
    if args.mode:
        return args.mode
    env_mode = os.environ.get("MEGA_CODE_CLIENT_MODE")
    if env_mode:
        return env_mode
    return "local"


# =============================================================================
# Notification formatting
# =============================================================================


def format_pipeline_notification(result: PendingResult) -> str:
    """Format notification after pipeline completion.

    Delegates to the shared format_review_notification() for the review workflow,
    with a pipeline-specific header and preamble.
    """
    if not result.has_outputs():
        return NO_OUTPUTS_NOTIFICATION

    return format_review_notification(
        result.skills,
        result.strategies,
        lessons=result.lessons,
        header="ITEM(S) READY - PIPELINE COMPLETE",
        preamble="Pipeline completed successfully! Generated:",
        errors=result.errors or None,
        run_id=result.run_id,
        project_id=result.project_id,
    )


def format_error_notification(error: str) -> str:
    """Format an error notification."""
    return f"""
\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550
\u274c MEGA-CODE: PIPELINE ERROR
\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550

The pipeline encountered an error:

{error}

Please check the logs for more details.
You can try again with: /mega-code:run
"""


# =============================================================================
# CLI
# =============================================================================


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
        help=(
            f"LLM model for pipeline (default: {DEFAULT_PIPELINE_MODEL}). "
            "e.g. gemini-3-flash, gpt-5-mini"
        ),
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
    """Main entry point for pipeline runner.

    Uses the client protocol (create_client → trigger → poll → save) which
    works identically for both local and remote modes:
    - Local mode: MegaCodeLocal runs the pipeline in-process.
    - Remote mode: MegaCodeRemote sends HTTP to the server.

    This script handles:
    1. CLI argument parsing and project path resolution
    2. Creating a client and triggering the pipeline
    3. Polling for completion
    4. Saving outputs to local pending dirs
    5. Formatting the JSON notification for Claude Code hook
    """
    args = parse_args()

    if args.env_debug:
        from mega_code.client.utils.env import print_env_debug

        print("run_pipeline_async.py env:", file=sys.stderr)
        print_env_debug()
        sys.exit(0)

    # Configure logging
    logging.basicConfig(
        level=logging.DEBUG if args.debug else logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )

    # Setup tracing (after dotenv load at module level)
    setup_tracing(service_name="mega-code-client")
    tracer = get_tracer(__name__)

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
    mode = resolve_mode(args)
    logger.info(f"Execution mode: {mode}")

    with tracer.start_as_current_span("run_pipeline_async") as span:
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
