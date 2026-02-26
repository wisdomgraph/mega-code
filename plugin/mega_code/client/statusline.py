#!/usr/bin/env python3
"""MEGA-Code status line renderer.

This script renders a multi-line status line showing session statistics,
tool activity, cost information, and strategy stats.

Usage:
    uv run python statusline.py

Input (stdin):
    JSON with context_window, model, cwd, transcript_path, etc.

Output (stdout):
    Multi-line status with ANSI colors
"""

import json
import sys
from typing import Any

from mega_code.client.schema import SessionStats
from mega_code.client.stats import load_stats, find_current_session, lookup_project_folder


# ANSI color codes
class Colors:
    RESET = "\033[0m"
    BOLD = "\033[1m"
    DIM = "\033[2m"

    # Foreground colors
    RED = "\033[31m"
    GREEN = "\033[32m"
    YELLOW = "\033[33m"
    BLUE = "\033[34m"
    MAGENTA = "\033[35m"
    CYAN = "\033[36m"
    WHITE = "\033[37m"

    # Bright foreground
    BRIGHT_RED = "\033[91m"
    BRIGHT_GREEN = "\033[92m"
    BRIGHT_YELLOW = "\033[93m"
    BRIGHT_CYAN = "\033[96m"


def colored(text: str, color: str) -> str:
    """Wrap text with color codes."""
    return f"{color}{text}{Colors.RESET}"


def dim(text: str) -> str:
    """Dim the text."""
    return f"{Colors.DIM}{text}{Colors.RESET}"


def read_stdin() -> dict[str, Any]:
    """Read JSON input from stdin."""
    if sys.stdin.isatty():
        return {}
    try:
        return json.load(sys.stdin)
    except json.JSONDecodeError:
        return {}


def get_context_percent(stdin_data: dict[str, Any]) -> int:
    """Calculate context window usage percentage."""
    context_window = stdin_data.get("context_window", {})
    size = context_window.get("context_window_size", 0)
    usage = context_window.get("current_usage", {})

    if not size:
        return 0

    total_tokens = (
        usage.get("input_tokens", 0)
        + usage.get("cache_creation_input_tokens", 0)
        + usage.get("cache_read_input_tokens", 0)
    )

    # Add buffer for autocompact threshold
    AUTOCOMPACT_BUFFER = 40000
    return min(100, round(((total_tokens + AUTOCOMPACT_BUFFER) / size) * 100))


def get_context_color(percent: int) -> str:
    """Get color based on context usage percentage."""
    if percent >= 80:
        return Colors.BRIGHT_RED
    elif percent >= 60:
        return Colors.BRIGHT_YELLOW
    elif percent >= 40:
        return Colors.YELLOW
    else:
        return Colors.BRIGHT_GREEN


def render_progress_bar(percent: int, width: int = 10) -> str:
    """Render a progress bar with color."""
    filled = int(width * percent / 100)
    empty = width - filled

    color = get_context_color(percent)
    bar = "█" * filled + "░" * empty

    return f"{color}{bar}{Colors.RESET}"


def format_tokens(n: int) -> str:
    """Format token count for display."""
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    elif n >= 1_000:
        return f"{n / 1_000:.0f}k"
    return str(n)


def format_duration(ms: int) -> str:
    """Format duration in milliseconds to human-readable string."""
    if ms < 60_000:
        return "<1m"

    mins = ms // 60_000
    if mins < 60:
        return f"{mins}m"

    hours = mins // 60
    remaining_mins = mins % 60
    return f"{hours}h {remaining_mins}m"


def render_session_line(stdin_data: dict[str, Any], stats: SessionStats | None) -> str:
    """Render the first line: model, context, counts, duration."""
    parts = []

    # Model name
    model = stdin_data.get("model", {})
    model_name = model.get("display_name", model.get("id", "Unknown"))
    parts.append(colored(f"[{model_name}]", Colors.CYAN))

    # Context bar and percentage
    percent = get_context_percent(stdin_data)
    bar = render_progress_bar(percent)
    color = get_context_color(percent)
    parts.append(f"{bar} {color}{percent}%{Colors.RESET}")

    # Stats if available
    if stats:
        parts.append(f"📊 {stats.counts.user_prompts} prompts")
        parts.append(f"🔧 {stats.counts.tool_calls} tools")

        # Duration
        if stats.timing.total_duration_ms > 0:
            duration = format_duration(stats.timing.total_duration_ms)
            parts.append(f"⏱️ {duration}")

    return " | ".join(parts)


def render_tools_line(stats: SessionStats | None) -> str | None:
    """Render the second line: tool activity."""
    if not stats or stats.counts.tool_calls == 0:
        return None

    parts = []

    # Sort tools by count, take top 4
    sorted_tools = sorted(
        stats.counts.tool_calls_by_type.items(),
        key=lambda x: x[1],
        reverse=True,
    )[:4]

    for tool_name, count in sorted_tools:
        parts.append(f"{colored('✓', Colors.GREEN)} {tool_name} {dim(f'×{count}')}")

    if stats.counts.errors > 0:
        parts.append(f"{colored('✗', Colors.RED)} {stats.counts.errors} errors")

    return " | ".join(parts) if parts else None


def render_cost_line(stats: SessionStats | None) -> str | None:
    """Render the third line: cost and tokens with MEGA indicator."""
    if not stats:
        return None

    parts = []

    # Cost
    cost = stats.cost.estimated_usd
    parts.append(f"💰 ${cost:.2f}")

    # Tokens
    if stats.tokens.total_input > 0:
        parts.append(f"📥 {format_tokens(stats.tokens.total_input)} in")
    if stats.tokens.total_output > 0:
        parts.append(f"📤 {format_tokens(stats.tokens.total_output)} out")
    if stats.tokens.total_cache_read > 0:
        parts.append(f"🔄 {format_tokens(stats.tokens.total_cache_read)} cached")

    # MEGA indicator
    parts.append(colored("🏷️ MEGA", Colors.MAGENTA))

    return " | ".join(parts) if parts else None


def main():
    """Main entry point for the statusline."""
    stdin_data = read_stdin()

    if not stdin_data:
        print(colored("[mega-code] Initializing...", Colors.DIM))
        return

    # Get project context from cwd
    cwd = stdin_data.get("cwd", "")

    # Find current session (scoped to project if cwd provided)
    session_id = stdin_data.get("session_id")
    if not session_id:
        session_id = find_current_session(cwd if cwd else None)

    # Load stats with project context
    project_dir = cwd if cwd and lookup_project_folder(cwd) else None
    stats = load_stats(session_id, project_dir) if session_id else None

    # Render lines
    lines = []

    # Line 1: Session info (always shown)
    session_line = render_session_line(stdin_data, stats)
    lines.append(session_line)

    # Line 2: Tool activity (if any tools used)
    tools_line = render_tools_line(stats)
    if tools_line:
        lines.append(tools_line)

    # Line 3: Cost and tokens (if any data)
    cost_line = render_cost_line(stats)
    if cost_line:
        lines.append(cost_line)

    # Output each line (replace spaces with non-breaking spaces)
    for line in lines:
        output = f"{Colors.RESET}{line.replace(' ', chr(0x00A0))}"
        print(output)


if __name__ == "__main__":
    main()
