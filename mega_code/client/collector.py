#!/usr/bin/env python3
"""MEGA-Code data collector hook handler.

This script is invoked by Claude Code hooks to collect interaction data.
It reads hook event data from stdin and processes it accordingly.

Usage:
    uv run python collector.py --event <EventName>

Events:
    SessionStart - Initialize new session
    SessionEnd - Finalize session
    UserPromptSubmit - Capture user prompt (with MEGA tagging)
    Stop - Append transcript entries and update stats
"""

import argparse
import json
import logging
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

import dotenv
import httpx

from mega_code.client.api import create_client
from mega_code.client.filters import filter_metadata, filter_turns
from mega_code.client.history.sources.mega_code import MegaCodeSource
from mega_code.client.models import TurnSet
from mega_code.client.turns import extract_turns
from mega_code.client.schema import CollectorSessionMetadata, SessionStats, estimate_cost, utcnow
from mega_code.client.stats import (
    initialize_session,
    finalize_session,
    load_stats,
    save_stats,
    load_metadata,
    save_metadata,
    get_session_dir,
    get_project_folder_name,
)
from mega_code.client.utils.tracing import get_tracer, setup_tracing

logger = logging.getLogger(__name__)

# Required env vars per client mode.
# Each entry: (VAR_NAME, error_hint_or_None).
# None means the var has a default and won't produce a warning.
_MODE_REQUIREMENTS: dict[str, list[tuple[str, str | None]]] = {
    "remote": [
        (
            "MEGA_CODE_API_KEY",
            "Required for server authentication. Run: mega-code configure --api-key <key>",
        ),
        ("MEGA_CODE_SERVER_URL", None),  # has a built-in default
    ],
}


def _validate_env_config() -> list[str]:
    """Check required env vars for the current client mode.

    Returns a list of human-readable warning strings (empty if all OK).
    """
    mode = os.environ.get("MEGA_CODE_CLIENT_MODE", "local")
    requirements = _MODE_REQUIREMENTS.get(mode, [])
    warnings: list[str] = []
    for var, hint in requirements:
        if not os.environ.get(var):
            msg = f"MEGA_CODE_CLIENT_MODE={mode} but {var} is not set."
            if hint:
                msg += f" {hint}"
            warnings.append(msg)
    return warnings


def read_stdin() -> dict[str, Any]:
    """Read JSON input from stdin."""
    try:
        return json.load(sys.stdin)
    except json.JSONDecodeError:
        return {}


def output_json(data: dict[str, Any]) -> None:
    """Output JSON response to stdout."""
    print(json.dumps(data, ensure_ascii=False))


def handle_session_start(input_data: dict[str, Any]) -> dict[str, Any]:
    """Handle SessionStart event - initialize new session."""
    session_id = input_data.get("session_id", "")
    cwd = input_data.get("cwd", "")

    if not session_id:
        return {}

    # Use current directory as fallback if cwd not provided
    if not cwd:
        cwd = str(Path.cwd())

    # Initialize session files (metadata.json, stats.json, events.jsonl)
    initialize_session(session_id=session_id, project_dir=cwd)

    # Warn early if env config is incomplete for the chosen client mode
    warnings = _validate_env_config()
    if warnings:
        return {"additionalContext": "\n".join(warnings)}
    return {}


def _load_session_context(
    session_id: str,
) -> tuple[CollectorSessionMetadata | None, SessionStats | None, str | None]:
    """Load metadata, stats, and project_dir for a session."""
    metadata = load_metadata(session_id)
    project_dir = metadata.project_dir if metadata else None
    stats = load_stats(session_id, project_dir)
    return metadata, stats, project_dir


def handle_session_end(input_data: dict[str, Any]) -> dict[str, Any]:
    """Handle SessionEnd event - capture final events and finalize session."""
    session_id = input_data.get("session_id", "")
    reason = input_data.get("reason", "other")

    if not session_id:
        return {}

    # First, capture any remaining transcript entries (in case Stop hook didn't fire)
    # This ensures we don't lose events when sessions are closed abruptly
    handle_stop(input_data)

    metadata, stats, project_dir = _load_session_context(session_id)

    # Calculate and save total duration
    if stats:
        if metadata and metadata.started_at:
            try:
                start = datetime.fromisoformat(metadata.started_at.rstrip("Z"))
                end = utcnow()
                stats.timing.total_duration_ms = int((end - start).total_seconds() * 1000)
                save_stats(stats, project_dir, model=metadata.model_id)
            except (ValueError, TypeError):
                pass

    # Finalize metadata with end time and reason
    finalize_session(session_id, reason)

    # Upload trajectory via client (best-effort)
    _upload_trajectory(session_id, project_dir)

    return {}


def handle_user_prompt_submit(input_data: dict[str, Any]) -> dict[str, Any]:
    """Handle UserPromptSubmit event - increment prompt count, return MEGA tag."""
    session_id = input_data.get("session_id", "")

    if not session_id:
        return {}

    metadata, stats, project_dir = _load_session_context(session_id)
    if stats:
        stats.counts.user_prompts += 1
        save_stats(stats, project_dir, model=metadata.model_id if metadata else None)

    # Return empty hook output (no tagging)
    return {}


def handle_stop(input_data: dict[str, Any]) -> dict[str, Any]:
    """Handle Stop event - append new transcript entries to events.jsonl."""
    session_id = input_data.get("session_id", "")
    transcript_path = input_data.get("transcript_path", "")

    if not session_id:
        return {}

    metadata, stats, project_dir = _load_session_context(session_id)
    if not stats:
        return {}

    # Track timing for this response
    response_start = utcnow()

    # Append raw transcript entries to events.jsonl
    if transcript_path and Path(transcript_path).exists():
        session_dir = get_session_dir(session_id, project_dir)
        events_file = session_dir / "events.jsonl"

        # Track last processed line using marker file
        marker_file = session_dir / ".transcript_offset"
        last_offset = 0
        if marker_file.exists():
            try:
                last_offset = int(marker_file.read_text().strip())
            except (ValueError, OSError):
                last_offset = 0

        # Read new transcript entries
        new_entries = []
        current_line = 0
        with open(transcript_path, "r", encoding="utf-8") as f:
            for line in f:
                current_line += 1
                if current_line <= last_offset:
                    continue
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                    new_entries.append(entry)
                except json.JSONDecodeError:
                    continue

        # Append new entries and update stats
        if new_entries:
            with open(events_file, "a", encoding="utf-8") as f:
                for entry in new_entries:
                    f.write(json.dumps(entry, ensure_ascii=False) + "\n")

            # Update marker
            marker_file.write_text(str(current_line))

            # Update stats from entries
            for entry in new_entries:
                message = entry.get("message", {})
                role = message.get("role", "")

                if role == "assistant":
                    stats.counts.assistant_responses += 1

                    # Token usage
                    usage = message.get("usage", {})
                    if usage:
                        stats.tokens.total_input += usage.get("input_tokens", 0)
                        stats.tokens.total_output += usage.get("output_tokens", 0)
                        stats.tokens.total_cache_read += usage.get("cache_read_input_tokens", 0)
                        stats.tokens.total_cache_create += usage.get(
                            "cache_creation_input_tokens", 0
                        )

                    # Tool calls
                    content = message.get("content", [])
                    if isinstance(content, list):
                        for block in content:
                            if isinstance(block, dict) and block.get("type") == "tool_use":
                                tool_name = block.get("name", "unknown")
                                stats.counts.tool_calls += 1
                                stats.counts.tool_calls_by_type[tool_name] = (
                                    stats.counts.tool_calls_by_type.get(tool_name, 0) + 1
                                )

                elif role == "user":
                    # Count tool errors
                    content = message.get("content", [])
                    if isinstance(content, list):
                        for block in content:
                            if isinstance(block, dict) and block.get("type") == "tool_result":
                                if block.get("is_error", False):
                                    stats.counts.errors += 1

            # Update metadata from transcript entries
            if metadata:
                for entry in new_entries:
                    if not metadata.model_id:
                        msg = entry.get("message", {})
                        if msg.get("model"):
                            metadata.model_id = msg.get("model")
                    if not metadata.version and entry.get("version"):
                        metadata.version = entry.get("version")
                    if not metadata.git_branch and entry.get("gitBranch"):
                        metadata.git_branch = entry.get("gitBranch")
                save_metadata(metadata)

            # Update cost (use model-specific pricing when available)
            model_id = metadata.model_id if metadata else None
            stats.cost.estimated_usd = estimate_cost(stats.tokens, model=model_id)

    # Record response time
    response_end = utcnow()
    response_time_ms = int((response_end - response_start).total_seconds() * 1000)
    stats.timing.add_response_time(response_time_ms)

    model_for_save = metadata.model_id if metadata else None
    save_stats(stats, project_dir, model=model_for_save)
    return {}


def _get_client():
    """Get a MEGA-Code client instance.

    Reads MEGA_CODE_CLIENT_MODE env var (default: "local").

    Returns:
        A MegaCodeBaseClient implementation.
    """
    return create_client(mode=os.environ.get("MEGA_CODE_CLIENT_MODE"))


def _upload_trajectory(session_id: str, project_dir: str | None) -> None:
    """Convert events.jsonl to TurnSet and upload via client.

    Best-effort: failures are logged but do not block session end.

    Args:
        session_id: The session ID to upload.
        project_dir: The project directory path (for project_id derivation).
    """
    try:
        client = _get_client()

        session_dir = get_session_dir(session_id, project_dir)
        events_file = session_dir / "events.jsonl"
        if not events_file.exists():
            return

        source = MegaCodeSource()
        meta = source._load_metadata(session_dir)
        if not meta:
            return

        session = source._load_session_from_dir(
            session_dir,
            project_dir or "",
            meta,
        )

        # Extract turns using existing infrastructure
        turns, turn_metadata = extract_turns(session)
        if not turns:
            return

        # Filter sensitive data before upload
        turns = filter_turns(turns, project_dir=project_dir)
        turn_metadata = filter_metadata(turn_metadata, project_dir=project_dir)

        # Build TurnSet
        turn_set = TurnSet(
            session_id=session_id,
            session_dir=session_dir,
            turns=turns,
            metadata=turn_metadata,
        )

        # Derive project_id from project_dir
        project_id = get_project_folder_name(project_dir) if project_dir else "unknown"

        result = client.upload_trajectory(turn_set=turn_set, project_id=project_id)
        logger.info(
            "Uploaded trajectory: session=%s project=%s status=%s",
            session_id,
            project_id,
            result.status,
        )

    except (httpx.HTTPError, OSError, ValueError, KeyError):
        # Best-effort: log but don't fail session end
        logger.warning(
            "Failed to upload trajectory for session %s",
            session_id,
            exc_info=True,
        )


def _load_env():
    """Load credentials from the stable data-root .env, then overlay plugin .env.

    Credentials are stored in ~/.local/share/mega-code/.env (a fixed, version-independent
    path that survives plugin updates).  The versioned plugin .env may still hold
    non-secret overrides (e.g. MEGA_CODE_SERVER_URL for dev/staging).

    Search order:
    1. ~/.local/share/mega-code/.env  — stable credential store (always loaded first)
    2. CLAUDE_PLUGIN_ROOT/.env       — versioned plugin dir (loaded after, so it can
       override non-secret config without clobbering credentials)
    3. Repo root .env                — dev mode fallback
    """
    # 1. Stable credential store (survives plugin updates)
    from mega_code.client.dirs import data_dir

    stable_env = data_dir() / ".env"
    if stable_env.exists():
        dotenv.load_dotenv(stable_env, override=False)  # don't override existing env vars

    # 2. Versioned plugin dir (may add non-secret config on top)
    plugin_root = os.environ.get("CLAUDE_PLUGIN_ROOT")
    if plugin_root:
        plugin_env = Path(plugin_root) / ".env"
        if plugin_env.exists():
            dotenv.load_dotenv(plugin_env, override=False)
        return  # we're in a marketplace install — skip dev fallback

    # 3. Dev mode: repo root is three parents up from mega_code/client/collector.py
    repo_root = Path(__file__).resolve().parent.parent.parent
    dev_env = repo_root / ".env"
    if dev_env.exists():
        dotenv.load_dotenv(dev_env, override=False)


def main():
    """Main entry point for the collector."""
    _load_env()

    # Configure logging so warnings/errors are visible on stderr
    logging.basicConfig(
        level=logging.WARNING,
        format="%(name)s:%(levelname)s: %(message)s",
        stream=sys.stderr,
    )

    parser = argparse.ArgumentParser(
        description="MEGA-Code data collector",
        epilog="Environment check: pass --env-debug to print loaded env vars.",
    )
    parser.add_argument(
        "--event",
        required="--env-debug" not in sys.argv,
        choices=["SessionStart", "SessionEnd", "UserPromptSubmit", "Stop"],
        help="The hook event type",
    )
    parser.add_argument(
        "--env-debug",
        action="store_true",
        help="Print key environment variables and exit",
    )
    args = parser.parse_args()

    if args.env_debug:
        from mega_code.client.utils.env import print_env_debug

        print("collector.py env:", file=sys.stderr)
        print_env_debug()
        sys.exit(0)

    # Setup tracing (after dotenv so OTEL_EXPORTER_OTLP_ENDPOINT is available)
    setup_tracing(service_name="mega-code-client")
    tracer = get_tracer(__name__)

    # Read input from stdin
    input_data = read_stdin()

    # Route to handler
    handlers = {
        "SessionStart": handle_session_start,
        "SessionEnd": handle_session_end,
        "UserPromptSubmit": handle_user_prompt_submit,
        "Stop": handle_stop,
    }

    handler = handlers.get(args.event)
    if handler:
        span_name = f"collector.{args.event}"
        with tracer.start_as_current_span(span_name) as span:
            session_id = input_data.get("session_id", "")
            span.set_attribute("collector.event", args.event)
            span.set_attribute("collector.session_id", session_id)
            result = handler(input_data)
            if result:
                output_json(result)


if __name__ == "__main__":
    main()
