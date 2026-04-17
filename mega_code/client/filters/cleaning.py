"""Remove MEGA-Code wrapper turns from session data.

Anchor-based segmentation: find explicit MEGA-Code anchors, expand locally
through nearby low-information turns that are part of the same wrapper flow,
and stop when the conversation returns to non-MEGA task work.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

from mega_code.client.models import Turn
from mega_code.client.utils.tracing import traced

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Explicit MEGA-Code anchors
# ---------------------------------------------------------------------------

USER_TRIGGER_PATTERNS = [
    re.compile(r"<command-name>/?mega-code:", re.I),
    re.compile(r"<command-message>mega-code:", re.I),
    re.compile(r"(?<!\S)\$mega-code-[a-z0-9-]+(?:\s|$)", re.I),
    re.compile(r"<skill>\s*.*?<name>mega-code-[^<]+</name>", re.I | re.S),
]

SKILL_INJECTION_PATTERNS = [
    re.compile(r"Base directory for this skill:.*mega-code", re.I | re.S),
    re.compile(r"<path>.*mega-code-[^<]+/SKILL\.md</path>", re.I),
    re.compile(r"plugins/.*/mega-code/", re.I),
]

MEGA_PATH_PATTERNS = [
    re.compile(r"\.local/share/mega-code(?:/|$)", re.I),
    re.compile(r"\.claude/plugins/(?:cache|marketplaces)/.*/mega-code(?:/|$)", re.I),
    re.compile(r"\.agents/skills/mega-code-[^/\s]+", re.I),
    re.compile(r"\.agents/rules/mega-code/", re.I),
]

# Shared patterns used in both command and content matching
_MEGA_SHARED_PATTERNS = [
    re.compile(r"\bMEGA_[A-Z]"),
    re.compile(r"\bmega_code\b", re.I),
    re.compile(r"\bmegacode-eureka\b", re.I),
]

MEGA_COMMAND_PATTERNS = [
    *_MEGA_SHARED_PATTERNS,
    re.compile(r"\bpython\s+-m\s+mega_code\.", re.I),
    re.compile(r"\buv run --directory .*mega-code", re.I),
    re.compile(r"\brun_pipeline_async\.py\b", re.I),
]

MEGA_CONTENT_PATTERNS = [
    *_MEGA_SHARED_PATTERNS,
    re.compile(r"mega-code:strategies:(?:start|end)", re.I),
    re.compile(r"http://localhost:3117", re.I),
    re.compile(r"\bviewer\.pid\b", re.I),
    re.compile(r"\bMEGACODE\b"),
    re.compile(r"\bpending skills\b", re.I),
    re.compile(r"\bpending strategies\b", re.I),
    re.compile(r"\bstored on the server\b", re.I),
    re.compile(r"\barchived\b.*\bitems\b", re.I),
    re.compile(r"\bplugin-root\b", re.I),
    re.compile(r"\barchive_pending_items\b", re.I),
]

# ---------------------------------------------------------------------------
# Anchor detection
# ---------------------------------------------------------------------------


def _has_user_trigger(turn: Turn) -> bool:
    if turn.role != "user":
        return False
    return any(p.search(turn.content) for p in USER_TRIGGER_PATTERNS)


def _has_skill_injection(turn: Turn) -> bool:
    if turn.role != "user":
        return False
    return any(p.search(turn.content) for p in SKILL_INJECTION_PATTERNS)


def _has_mega_command(turn: Turn) -> bool:
    cmd = turn.command
    if not cmd:
        return False
    return any(p.search(cmd) for p in MEGA_COMMAND_PATTERNS)


def _has_mega_content(turn: Turn) -> bool:
    content = turn.content
    if not content:
        return False
    return any(p.search(content) for p in MEGA_CONTENT_PATTERNS)


def _has_mega_path(turn: Turn) -> bool:
    """Check command, content, and tool_target for MEGA path patterns (single pass)."""
    for text in (turn.command, turn.content, turn.tool_target):
        if text and any(p.search(text) for p in MEGA_PATH_PATTERNS):
            return True
    return False


def _is_mega_anchor(turn: Turn) -> bool:
    return (
        _has_user_trigger(turn)
        or _has_skill_injection(turn)
        or _has_mega_command(turn)
        or _has_mega_content(turn)
        or _has_mega_path(turn)
    )


# ---------------------------------------------------------------------------
# Local expansion around anchors
# ---------------------------------------------------------------------------

MAX_NEIGHBOR_STEPS = 4

META_TOOL_NAMES = {
    "AskUserQuestion",
    "ToolSearch",
    "request_user_input",
    "write_stdin",
}

NEAR_ANCHOR_ASSISTANT_PATTERNS = [
    re.compile(r"mega-code-[a-z0-9-]+", re.I),
    re.compile(r"\boauth flow\b", re.I),
    re.compile(r"\bavailable skills\b", re.I),
]


def _is_interrupt_like(turn: Turn) -> bool:
    content = turn.content.strip()
    return not content or content.startswith(
        ("[Request interrupted", "<local-command-caveat>", "<local-command-stdout>")
    )


def _is_meta_tool_turn(turn: Turn) -> bool:
    return (turn.tool_name or "") in META_TOOL_NAMES


def _is_near_anchor_assistant(turn: Turn) -> bool:
    if turn.role != "assistant":
        return False
    content = turn.content.strip()
    if not content:
        return False
    return any(p.search(content) for p in NEAR_ANCHOR_ASSISTANT_PATTERNS)


def _is_absorbable_neighbor(turn: Turn, is_anchor: bool) -> bool:
    return (
        is_anchor
        or _is_interrupt_like(turn)
        or _is_meta_tool_turn(turn)
        or _is_near_anchor_assistant(turn)
    )


def _segment_mega_blocks(turns: list[Turn]) -> list[bool]:
    """Return mask of turns that belong to explicit MEGA-Code blocks."""
    n = len(turns)
    is_anchor = [_is_mega_anchor(t) for t in turns]
    marked = [False] * n

    for idx in range(n):
        if not is_anchor[idx]:
            continue
        marked[idx] = True

        steps = 0
        j = idx - 1
        while (
            j >= 0
            and steps < MAX_NEIGHBOR_STEPS
            and _is_absorbable_neighbor(turns[j], is_anchor[j])
        ):
            marked[j] = True
            j -= 1
            steps += 1

        steps = 0
        j = idx + 1
        while (
            j < n and steps < MAX_NEIGHBOR_STEPS and _is_absorbable_neighbor(turns[j], is_anchor[j])
        ):
            marked[j] = True
            j += 1
            steps += 1

    return marked


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


@dataclass
class CleaningResult:
    """Result of mega-code turn cleaning."""

    kept: list[Turn]
    removed: list[Turn] = field(default_factory=list)


@traced("filter.clean_mega_code_turns")
def clean_mega_code_turns(turns: list[Turn]) -> CleaningResult:
    """Remove MEGA-Code self-referential turns and reindex.

    Args:
        turns: List of Turn objects from a session.

    Returns:
        CleaningResult with kept (reindexed) and removed turns.
    """
    if not turns:
        return CleaningResult(kept=turns)

    marked = _segment_mega_blocks(turns)
    removed_count = sum(marked)

    if removed_count == 0:
        return CleaningResult(kept=turns)

    kept = [turn for turn, remove in zip(turns, marked) if not remove]
    removed = [turn for turn, remove in zip(turns, marked) if remove]

    # Reindex turn_ids (Turn is frozen, so we need to reconstruct)
    reindexed = [turn.model_copy(update={"turn_id": i}) for i, turn in enumerate(kept)]

    logger.info(
        "Cleaned %d mega-code self-referential turn(s), %d turns remaining",
        removed_count,
        len(reindexed),
    )

    return CleaningResult(kept=reindexed, removed=removed)


def save_cleaning_debug(
    original: list[Turn],
    result: CleaningResult,
    session_dir: Path,
) -> None:
    """Save original and removed turns to session dir for debugging.

    Best-effort: failures are logged but never raised.

    Args:
        original: Turns before cleaning.
        result: CleaningResult from clean_mega_code_turns.
        session_dir: Session directory to write into.
    """
    if not result.removed:
        return

    try:
        session_dir.mkdir(parents=True, exist_ok=True)

        original_path = session_dir / "turns-original.jsonl"
        with open(original_path, "w", encoding="utf-8") as f:
            for turn in original:
                f.write(json.dumps(turn.model_dump(mode="json"), default=str) + "\n")

        removed_path = session_dir / "turns-removed.jsonl"
        with open(removed_path, "w", encoding="utf-8") as f:
            for turn in result.removed:
                f.write(json.dumps(turn.model_dump(mode="json"), default=str) + "\n")

        logger.debug(
            "Saved cleaning debug: %d original, %d removed → %s",
            len(original),
            len(result.removed),
            session_dir,
        )
    except (OSError, TypeError, ValueError):
        logger.warning("Failed to save cleaning debug files", exc_info=True)
