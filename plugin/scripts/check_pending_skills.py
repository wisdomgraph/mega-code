#!/usr/bin/env python3
"""
Check for pending skills and strategies; output highlighted notification for Claude Code.

This script is called via UserPromptSubmit hook to inject context about pending items.
It scans ~/.local/mega-code/data/pending-{skills,strategies}/ directories.

Output format follows Claude Code hooks reference:
https://code.claude.com/docs/en/hooks

For UserPromptSubmit, we use hookSpecificOutput with:
- hookEventName: "UserPromptSubmit"
- additionalContext: string added to Claude's context
"""

import json
import sys
from pathlib import Path

# Add project root to path for imports
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

import dotenv  # noqa: E402

# 1. Stable credential store (~/.local/mega-code/.env) — always loaded first
_stable_env = Path.home() / ".local" / "mega-code" / ".env"
if _stable_env.exists():
    dotenv.load_dotenv(_stable_env, override=False)

# 2. Repo root .env — dev overlay (lower priority than stable credentials)
_env_path = project_root / ".env"
if _env_path.exists():
    dotenv.load_dotenv(_env_path, override=False)

from mega_code.client.pending import (  # noqa: E402
    format_review_notification,
    get_pending_skills,
    get_pending_strategies,
)
from mega_code.client.utils.tracing import get_tracer, setup_tracing  # noqa: E402

setup_tracing(service_name="mega-code-client")
_tracer = get_tracer(__name__)


def main():
    if "--env-debug" in sys.argv:
        from mega_code.client.utils.env import print_env_debug

        print("check_pending_skills.py env:", file=sys.stderr)
        print_env_debug()
        sys.exit(0)

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
