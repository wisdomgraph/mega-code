#!/usr/bin/env python3
"""Check for pending skills and strategies; output highlighted notification for Claude Code.

This script is called via UserPromptSubmit hook to inject context about pending items.
It scans ~/.local/mega-code/data/pending-{skills,strategies}/ directories.

Output format follows Claude Code hooks reference:
https://code.claude.com/docs/en/hooks

For UserPromptSubmit, we use hookSpecificOutput with:
- hookEventName: "UserPromptSubmit"
- additionalContext: string added to Claude's context
"""

import json
import os
import sys
from pathlib import Path

import dotenv

from mega_code.client.pending import (
    format_review_notification,
    get_pending_skills,
    get_pending_strategies,
)
from mega_code.client.utils.tracing import get_tracer, setup_tracing


def _load_env():
    """Load credentials from the stable data-root .env, then overlay plugin .env.

    Search order (same as collector.py):
    1. ~/.local/mega-code/.env  — stable credential store (always loaded first)
    2. CLAUDE_PLUGIN_ROOT/.env  — versioned plugin dir
    3. Repo root .env           — dev mode fallback
    """
    # 1. Stable credential store (survives plugin updates)
    stable_env = Path.home() / ".local" / "mega-code" / ".env"
    if stable_env.exists():
        dotenv.load_dotenv(stable_env, override=False)

    # 2. Versioned plugin dir (may add non-secret config on top)
    plugin_root = os.environ.get("CLAUDE_PLUGIN_ROOT")
    if plugin_root:
        plugin_env = Path(plugin_root) / ".env"
        if plugin_env.exists():
            dotenv.load_dotenv(plugin_env, override=False)
        return  # marketplace install — skip dev fallback

    # 3. Dev mode: repo root is three parents up from mega_code/client/check_pending.py
    repo_root = Path(__file__).resolve().parent.parent.parent
    dev_env = repo_root / ".env"
    if dev_env.exists():
        dotenv.load_dotenv(dev_env, override=False)


def main():
    _load_env()

    if "--env-debug" in sys.argv:
        from mega_code.client.utils.env import print_env_debug

        print("check_pending.py env:", file=sys.stderr)
        print_env_debug()
        sys.exit(0)

    setup_tracing(service_name="mega-code-client")
    _tracer = get_tracer(__name__)

    with _tracer.start_as_current_span("check_pending_skills") as span:
        # Read hook input from stdin (Claude Code passes JSON context)
        # Currently unused but reserved for future features (e.g., filtering by user prompt)
        try:
            _ = json.loads(sys.stdin.read()) if not sys.stdin.isatty() else {}
        except json.JSONDecodeError:
            pass  # Allow empty/invalid JSON input for hook testing

        skills = get_pending_skills()
        strategies = get_pending_strategies()

        span.set_attribute("check_pending.skills_count", len(skills))
        span.set_attribute("check_pending.strategies_count", len(strategies))

        if not skills and not strategies:
            # No pending items - exit cleanly with no output
            sys.exit(0)

        # Format highlighted notification using shared formatter
        notification = format_review_notification(skills, strategies)

        # Output using hookSpecificOutput format
        result = {
            "hookSpecificOutput": {
                "hookEventName": "UserPromptSubmit",
                "additionalContext": notification.strip(),
            }
        }

        print(json.dumps(result))
        sys.exit(0)


if __name__ == "__main__":
    main()
