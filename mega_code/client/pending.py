"""Pending skills, strategies, and lessons management for client-side operations.

This module handles local file operations for pending pipeline outputs:
- Save pipeline outputs (from both local and remote runs) to pending folders
- Scan pending directories for review
- Clear and delete pending items
- Format review notifications for Claude Code

Pending data is user-generated content stored at user level, consistent across
both local and remote installation modes.

Directories (under data_dir()/data/):
- pending-skills/{name}/ - for skills (SKILL.md + metadata)
- pending-strategies/{name}.md - for strategies (modular rules)
- feedback/{project_id}/{run_id}/lessons/{slug}.md - for lessons
"""

from __future__ import annotations

import asyncio
import json
import logging
import shutil
import string
import time

import httpx
import yaml
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from mega_code.client.dirs import data_dir as _data_dir

if TYPE_CHECKING:
    from mega_code.client.api.protocol import MegaCodeBaseClient, PipelineStatusResult

logger = logging.getLogger(__name__)

_CONFIG_PATH = Path(__file__).parent / "config.yaml"


def _load_config() -> dict:
    """Load client config from config.yaml."""
    with open(_CONFIG_PATH) as f:
        return yaml.safe_load(f)


# Pending directories under user data (resolved via dirs.data_dir()).
_MEGA_CODE_DATA_DIR = _data_dir() / "data"
PENDING_SKILLS_DIR = _MEGA_CODE_DATA_DIR / "pending-skills"
PENDING_STRATEGIES_DIR = _MEGA_CODE_DATA_DIR / "pending-strategies"
FEEDBACK_DIR = _MEGA_CODE_DATA_DIR / "feedback"
DEDUP_METADATA_PATH = _MEGA_CODE_DATA_DIR / "dedup_metadata.json"

# Maximum length for description truncation
MAX_DESCRIPTION_LENGTH = 100


def _truncate(text: str, max_len: int = MAX_DESCRIPTION_LENGTH) -> str:
    """Truncate text to max_len, adding ellipsis if needed."""
    return text[:max_len] + "..." if len(text) > max_len else text


def save_dedup_metadata(
    skill_metadata: dict[str, dict], strategy_metadata: dict[str, dict]
) -> None:
    """Save dedup metadata (signal_strength, embedding) for pending items.

    Written by the pipeline after dedup filtering; read by archive_pending_items()
    so that manifests include signal_strength and embedding for cross-run dedup.
    """
    DEDUP_METADATA_PATH.parent.mkdir(parents=True, exist_ok=True)
    DEDUP_METADATA_PATH.write_text(
        json.dumps(
            {"skill_metadata": skill_metadata, "strategy_metadata": strategy_metadata}
        ),
        encoding="utf-8",
    )


def load_dedup_metadata() -> tuple[dict[str, dict], dict[str, dict]]:
    """Load and consume dedup metadata written by the pipeline.

    Returns (skill_metadata, strategy_metadata). Deletes the file after reading
    so it is only consumed once.
    """
    try:
        data = json.loads(DEDUP_METADATA_PATH.read_text(encoding="utf-8"))
        DEDUP_METADATA_PATH.unlink(missing_ok=True)
        return data.get("skill_metadata", {}), data.get("strategy_metadata", {})
    except (json.JSONDecodeError, OSError):
        return {}, {}


@dataclass
class PendingSkillInfo:
    """Information about a pending skill."""

    name: str
    description: str
    path: str
    domains: list[str] = field(default_factory=list)
    validation_passed: bool = True
    author: str = ""
    version: str = ""
    tags: list[str] = field(default_factory=list)


@dataclass
class PendingStrategyInfo:
    """Information about a pending strategy."""

    name: str
    description: str
    path: str
    category: str | None = None
    author: str = ""
    version: str = ""
    tags: list[str] = field(default_factory=list)


@dataclass
class PendingLessonInfo:
    """Information about a saved lesson learned document."""

    slug: str
    title: str
    path: str
    author: str = ""
    version: str = ""
    tags: list[str] = field(default_factory=list)


@dataclass
class PendingResult:
    """Result from saving outputs to pending folders."""

    skills: list[PendingSkillInfo] = field(default_factory=list)
    strategies: list[PendingStrategyInfo] = field(default_factory=list)
    lessons: list[PendingLessonInfo] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    run_id: str = ""
    project_id: str = ""

    @property
    def skill_count(self) -> int:
        return len(self.skills)

    @property
    def strategy_count(self) -> int:
        return len(self.strategies)

    @property
    def lesson_count(self) -> int:
        return len(self.lessons)

    @property
    def total_count(self) -> int:
        return self.skill_count + self.strategy_count + self.lesson_count

    def has_outputs(self) -> bool:
        return self.total_count > 0


from mega_code.client.skill_utils import (
    sanitize_name,
    ensure_skill_frontmatter,
)  # noqa: E402

# Backwards-compatible aliases for internal callers
_sanitize_name = sanitize_name
_ensure_skill_frontmatter = ensure_skill_frontmatter


# =============================================================================
# Save pipeline outputs to pending folders
# =============================================================================


def save_outputs_to_pending(
    status: PipelineStatusResult,
    project_id: str = "",
    run_id: str = "",
) -> PendingResult:
    """Save pipeline outputs to local pending folders.

    Works for both local and remote pipeline results. Takes a
    PipelineStatusResult and writes pending skills/strategies to
    the standard pending directories.

    Args:
        status: PipelineStatusResult with outputs.
        project_id: Project identifier (overrides status.project_id if given).
        run_id: Pipeline run identifier (overrides status.run_id if given).

    Returns:
        PendingResult with local file paths.
    """
    resolved_run_id = run_id or getattr(status, "run_id", "")
    resolved_project_id = project_id or getattr(status, "project_id", "")

    result = PendingResult(run_id=resolved_run_id, project_id=resolved_project_id)

    if not status.outputs:
        return result

    # Save pending skills
    for skill_data in status.outputs.pending_skills or []:
        skill_name = _sanitize_name(skill_data.skill_name)
        skill_dir = PENDING_SKILLS_DIR / skill_name
        skill_dir.mkdir(parents=True, exist_ok=True)

        # Ensure SKILL.md has required YAML frontmatter before writing
        skill_md_content = _ensure_skill_frontmatter(skill_data.skill_md, skill_name)
        (skill_dir / "SKILL.md").write_text(skill_md_content, encoding="utf-8")

        # Write remaining files as-is
        (skill_dir / "injection.json").write_text(
            skill_data.injection_rules, encoding="utf-8"
        )
        (skill_dir / "evidence.json").write_text(skill_data.evidence, encoding="utf-8")
        (skill_dir / "metadata.json").write_text(skill_data.metadata, encoding="utf-8")

        result.skills.append(
            PendingSkillInfo(
                name=skill_name,
                description=extract_skill_description(skill_md_content),
                path=str(skill_dir),
            )
        )

    # Save pending strategies
    for strat in status.outputs.pending_strategies or []:
        strat_name = _sanitize_name(strat.strategy_name)
        PENDING_STRATEGIES_DIR.mkdir(parents=True, exist_ok=True)
        path = PENDING_STRATEGIES_DIR / f"{strat_name}.md"
        path.write_text(strat.content, encoding="utf-8")
        result.strategies.append(
            PendingStrategyInfo(
                name=strat_name,
                description=_truncate(_extract_heading(strat.content)),
                path=str(path),
                category=strat.category,
            )
        )

    # Save lessons to feedback/{project_id}/{run_id}/lessons/{slug}.md
    lessons_to_save = [
        ls for ls in (status.outputs.pending_lessons or []) if ls.rendered_md
    ]
    if lessons_to_save:
        lessons_dir = (
            FEEDBACK_DIR
            / (resolved_project_id or "unknown")
            / (resolved_run_id or "unknown")
            / "lessons"
        )
        lessons_dir.mkdir(parents=True, exist_ok=True)
        for lesson in lessons_to_save:
            lesson_path = lessons_dir / f"{_sanitize_name(lesson.slug)}.md"
            if lesson_path.exists():
                logger.warning("Overwriting existing lesson file: %s", lesson_path)
            lesson_path.write_text(lesson.rendered_md, encoding="utf-8")
            result.lessons.append(
                PendingLessonInfo(
                    slug=lesson.slug, title=lesson.title, path=str(lesson_path)
                )
            )

    return result


# =============================================================================
# Scan pending directories
# =============================================================================


def _parse_yaml_frontmatter(content: str) -> dict[str, str | list[str]]:
    """Parse YAML frontmatter from markdown content into a flat dict.

    Only handles simple key: value pairs and tags lists.
    Returns empty dict if no frontmatter found.
    """
    if not content.strip().startswith("---"):
        return {}

    result: dict[str, str | list[str]] = {}
    lines = content.split("\n")
    current_list_key: str | None = None
    current_list: list[str] = []

    for line in lines[1:]:  # skip opening ---
        if line.strip() == "---":
            if current_list_key:
                result[current_list_key] = current_list
            break

        # List item (e.g. "  - python")
        stripped = line.strip()
        if stripped.startswith("- ") and current_list_key:
            current_list.append(stripped[2:].strip())
            continue

        # New key
        if ":" in line and not line.startswith(" ") and not line.startswith("\t"):
            # Save previous list if any
            if current_list_key:
                result[current_list_key] = current_list
                current_list_key = None
                current_list = []

            key, _, val = line.partition(":")
            key = key.strip()
            val = val.strip().strip('"').strip("'")
            if not val:
                # Might be a list or multi-line value
                current_list_key = key
                current_list = []
            else:
                result[key] = val
        elif current_list_key and not stripped:
            # Empty line ends list
            result[current_list_key] = current_list
            current_list_key = None
            current_list = []

    return result


def get_pending_skills() -> list[PendingSkillInfo]:
    """Scan pending-skills directory and return list of pending skills."""
    pending_dir = PENDING_SKILLS_DIR
    skills = []

    if not pending_dir.exists():
        return skills

    for skill_dir in pending_dir.iterdir():
        if not skill_dir.is_dir():
            continue

        skill_md = skill_dir / "SKILL.md"
        if not skill_md.exists():
            continue

        # Read metadata if available
        metadata_file = skill_dir / "metadata.json"
        metadata = {}
        if metadata_file.exists():
            try:
                metadata = json.loads(metadata_file.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                pass

        # Extract description and frontmatter fields from SKILL.md (single read)
        skill_content = skill_md.read_text(encoding="utf-8")
        fm = _parse_yaml_frontmatter(skill_content)
        description = (
            str(fm.get("description", ""))
            if fm.get("description")
            else _extract_first_paragraph(skill_content)
        )
        fm_tags = fm.get("tags", [])

        skills.append(
            PendingSkillInfo(
                name=skill_dir.name,
                description=description,
                path=str(skill_dir),
                domains=metadata.get("workflow", {}).get("domains", []),
                validation_passed=metadata.get("validation_passed", True),
                author=str(fm.get("author", "")),
                version=str(fm.get("version", "")),
                tags=fm_tags if isinstance(fm_tags, list) else [],
            )
        )

    return skills


def _extract_first_paragraph(content: str) -> str:
    """Extract first non-heading paragraph from markdown, skipping frontmatter."""
    in_frontmatter = False
    for i, line in enumerate(content.strip().split("\n")):
        if i == 0 and line.strip() == "---":
            in_frontmatter = True
            continue
        if in_frontmatter:
            if line.strip() == "---":
                in_frontmatter = False
            continue
        stripped = line.strip()
        if stripped and not stripped.startswith("#"):
            return _truncate(stripped)
    return "No description available"


def _extract_heading(content: str) -> str:
    """Extract text from the first # heading in markdown content, or empty string."""
    for line in content.split("\n"):
        if line.startswith("# "):
            return line[2:].strip()
    return ""


def get_pending_strategies() -> list[PendingStrategyInfo]:
    """Scan pending-strategies directory and return list of pending strategies."""
    if not PENDING_STRATEGIES_DIR.exists():
        return []

    strategies = []
    for strategy_file in PENDING_STRATEGIES_DIR.glob("*.md"):
        content = strategy_file.read_text(encoding="utf-8")
        fm = _parse_yaml_frontmatter(content)
        fm_tags = fm.get("tags", [])

        # Use heading if present, otherwise first non-empty content line
        description = _extract_heading(content)
        if not description:
            for line in content.split("\n"):
                stripped = line.strip()
                if (
                    stripped
                    and not stripped.startswith("---")
                    and not stripped.startswith("#")
                ):
                    if not stripped.startswith("category:"):
                        description = stripped
                        break

        strategies.append(
            PendingStrategyInfo(
                name=strategy_file.stem,
                description=_truncate(description),
                path=str(strategy_file),
                category=str(fm.get("category", "")) or None,
                author=str(fm.get("author", "")),
                version=str(fm.get("version", "")),
                tags=fm_tags if isinstance(fm_tags, list) else [],
            )
        )

    return strategies


def extract_skill_description(content: str, fallback: str = "") -> str:
    """Extract description from SKILL.md content string.

    Looks for a YAML frontmatter ``description`` field first, then falls back
    to the first non-heading paragraph line.

    Args:
        content: Raw markdown text of the skill file.
        fallback: Value to return when no description is found.

    Returns:
        Extracted (and truncated) description, or *fallback*.
    """
    lines = content.strip().split("\n")

    in_frontmatter = False
    in_multiline_desc = False
    desc_lines: list[str] = []
    for i, line in enumerate(lines):
        if i == 0 and line.strip() == "---":
            in_frontmatter = True
            continue
        if in_frontmatter:
            if line.strip() == "---":
                in_frontmatter = False
                if desc_lines:
                    return _truncate(" ".join(desc_lines))
                continue
            if line.startswith("description:"):
                val = line.split(":", 1)[1].strip().strip('"').strip("'")
                if val in ("|", ">", "|+", "|-", ">+", ">-", ""):
                    in_multiline_desc = True
                    continue
                return _truncate(val)
            if in_multiline_desc:
                if line.startswith("  ") or line.startswith("\t"):
                    desc_lines.append(line.strip())
                    continue
                else:
                    in_multiline_desc = False
            continue

        stripped = line.strip()
        if stripped and not stripped.startswith("#"):
            return _truncate(stripped)

    return fallback or "No description available"


def _extract_description_from_skill(skill_md: Path) -> str:
    """Extract description from a SKILL.md file path."""
    return extract_skill_description(skill_md.read_text(encoding="utf-8"))


# =============================================================================
# Delete operations
# =============================================================================


def clear_pending(skills: bool = True, strategies: bool = True) -> int:
    """Clear all pending files.

    Args:
        skills: If True, clear pending skills.
        strategies: If True, clear pending strategies.

    Returns:
        Number of items cleared.
    """
    cleared = 0

    if skills:
        if PENDING_SKILLS_DIR.exists():
            for item in PENDING_SKILLS_DIR.iterdir():
                if item.is_dir():
                    shutil.rmtree(item)
                    cleared += 1
                elif item.is_file():
                    item.unlink()
                    cleared += 1

    if strategies:
        if PENDING_STRATEGIES_DIR.exists():
            for item in PENDING_STRATEGIES_DIR.glob("*.md"):
                item.unlink()
                cleared += 1

    logger.info(f"Cleared {cleared} pending items")
    return cleared


def delete_pending_item(path: Path) -> bool:
    """Delete a single pending item (skill directory or strategy file).

    Only allows deletion within PENDING_SKILLS_DIR or PENDING_STRATEGIES_DIR
    to prevent accidental deletion of files outside the allowed directories.

    Args:
        path: Path to the pending item.

    Returns:
        True if deleted successfully, False otherwise.
    """
    resolved = path.resolve()
    if not (
        resolved.is_relative_to(PENDING_SKILLS_DIR.resolve())
        or resolved.is_relative_to(PENDING_STRATEGIES_DIR.resolve())
    ):
        logger.error(f"Refusing to delete path outside pending directories: {resolved}")
        return False

    try:
        if resolved.is_dir():
            shutil.rmtree(resolved)
        elif resolved.is_file():
            resolved.unlink()
        else:
            return False
        logger.info(f"Deleted pending item: {resolved}")
        return True
    except OSError as e:
        logger.error(f"Failed to delete pending item {resolved}: {e}")
        return False


# =============================================================================
# Pipeline status polling
# =============================================================================


async def poll_pipeline_status(
    client: MegaCodeBaseClient,
    run_id: str,
    poll_interval: float = 10.0,
    timeout: float | None = 1200.0,
    max_retries: int = 5,
) -> PipelineStatusResult:
    """Poll client.get_pipeline_status() until completed/failed or timeout.

    For MegaCodeLocal: returns immediately (status is already 'completed').
    For MegaCodeRemote: polls server every poll_interval seconds with retry
    on transient HTTP errors (502/503/504) and network failures.

    Args:
        client: MegaCodeBaseClient implementation.
        run_id: Pipeline run identifier.
        poll_interval: Seconds between polls (default: 10s).
        timeout: Maximum seconds to wait. None means wait indefinitely
            until the pipeline completes or fails (default: 1200s = 20 min).
        max_retries: Max consecutive retries on transient errors before
            raising. Uses exponential backoff capped at 120s (default: 5).

    Returns:
        PipelineStatusResult with final status.

    Raises:
        TimeoutError: If pipeline doesn't finish within timeout (when set).
        httpx.HTTPStatusError: On non-retryable HTTP errors or after
            max_retries consecutive transient errors.
        httpx.NetworkError: After max_retries consecutive network failures.
    """
    start = time.monotonic()
    last_phase = ""
    consecutive_errors = 0

    def _retry_wait(label: str) -> float | None:
        """Increment error counter, log warning, return wait seconds or None if budget exhausted."""
        nonlocal consecutive_errors
        if consecutive_errors >= max_retries:
            return None
        consecutive_errors += 1
        wait = min(poll_interval * (2**consecutive_errors), 120.0)
        logger.warning(
            "  %s (attempt %d/%d), retrying in %.0fs...",
            label,
            consecutive_errors,
            max_retries,
            wait,
        )
        return wait

    while True:
        try:
            status = await asyncio.to_thread(client.get_pipeline_status, run_id=run_id)
            consecutive_errors = 0  # reset on success

        except httpx.HTTPStatusError as exc:
            code = exc.response.status_code
            if code in (502, 503, 504):
                wait = _retry_wait(f"Status poll got HTTP {code}")
                if wait is not None:
                    await asyncio.sleep(wait)
                    continue
            raise  # non-retryable status or max retries exhausted

        except (httpx.NetworkError, httpx.TimeoutException) as exc:
            wait = _retry_wait(f"Network error ({type(exc).__name__})")
            if wait is not None:
                await asyncio.sleep(wait)
                continue
            raise

        if status.status in ("completed", "failed"):
            return status

        # Log progress on phase change
        if status.progress:
            phase = status.progress.get("current_phase", "")
            processed = status.progress.get("sessions_processed", 0)
            total = status.progress.get("sessions_total", 0)
            if phase and phase != last_phase:
                logger.info("  Progress: %s (%d/%d)", phase, processed, total)
                last_phase = phase

        # Sleep before next poll; respect remaining timeout to avoid overshoot
        if timeout is not None:
            elapsed = time.monotonic() - start
            remaining = timeout - elapsed
            if remaining <= 0:
                raise TimeoutError(
                    f"Pipeline timed out after {timeout:.0f}s (run_id={run_id})"
                )
            await asyncio.sleep(min(poll_interval, remaining))
        else:
            await asyncio.sleep(poll_interval)


# =============================================================================
# Notification Formatting
# =============================================================================


def _get_skill_name(s) -> str:
    """Get skill name from PendingSkillInfo (dataclass) or PendingSkillData (Pydantic)."""
    return getattr(s, "name", None) or getattr(s, "skill_name", "")


def _get_skill_description(s) -> str:
    """Get skill description from dataclass or Pydantic model."""
    return getattr(s, "description", "") or ""


def _get_skill_path(s) -> str:
    """Get skill path from dataclass or Pydantic model."""
    return getattr(s, "path", "") or ""


def _get_skill_validation_passed(s) -> bool:
    """Get validation status from dataclass or Pydantic model."""
    return getattr(s, "validation_passed", True)


def _get_strategy_name(s) -> str:
    """Get strategy name from PendingStrategyInfo or PendingStrategyData."""
    return getattr(s, "name", None) or getattr(s, "strategy_name", "")


def _get_strategy_description(s) -> str:
    """Get strategy description from dataclass or Pydantic model."""
    # PendingStrategyInfo uses 'description', PendingStrategyData uses 'content'
    return getattr(s, "description", "") or getattr(s, "content", "") or ""


def _get_strategy_path(s) -> str:
    """Get strategy path from dataclass or Pydantic model."""
    return getattr(s, "path", "") or ""


def _get_strategy_category(s) -> str | None:
    """Get strategy category from dataclass or Pydantic model."""
    return getattr(s, "category", None)


def _format_skills_section(skills: list) -> str:
    """Format skills list for notification display.

    Accepts both PendingSkillInfo (dataclass) and PendingSkillData (Pydantic).
    """
    if not skills:
        return "  (none)"
    lines = []
    for i, s in enumerate(skills, 1):
        status = "\u2713" if _get_skill_validation_passed(s) else "\u26a0"
        name = _get_skill_name(s)
        lines.append(f"  {i}. {status} **{name}**")
        desc = _get_skill_description(s)
        if desc:
            lines.append(f"       {desc}")
        path = _get_skill_path(s)
        if path:
            lines.append(f"       \U0001f4c1 `{path}`")
    return "\n".join(lines)


def _format_strategies_section(strategies: list) -> str:
    """Format strategies list for notification display.

    Accepts both PendingStrategyInfo (dataclass) and PendingStrategyData (Pydantic).
    """
    if not strategies:
        return "  (none)"
    lines = []
    for i, s in enumerate(strategies, 1):
        category = _get_strategy_category(s)
        name = _get_strategy_name(s)
        category_tag = f" [{category}]" if category else ""
        lines.append(f"  {i}. **{name}**{category_tag}")
        desc = _get_strategy_description(s)
        if desc:
            lines.append(f"       {desc}")
        path = _get_strategy_path(s)
        if path:
            lines.append(f"       \U0001f4c1 `{path}`")
    return "\n".join(lines)


def _format_lessons_section(lessons: list) -> str:
    """Format lessons list for notification display.

    Accepts both PendingLessonInfo (dataclass) and PendingLessonData (Pydantic).
    """
    if not lessons:
        return ""
    lines = []
    for i, lesson in enumerate(lessons, 1):
        title = getattr(lesson, "title", "") or getattr(lesson, "slug", "")
        lines.append(f"  {i}. \U0001f4d6 **{title}**")
        path = getattr(lesson, "path", "")
        if path:
            lines.append(f"       \U0001f4c1 `{path}`")
    return "\n".join(lines)


def _get_workflow_template() -> str:
    """Load the workflow template from config.yaml."""
    config = _load_config()
    return config["review_notification"]["workflow_template"].rstrip("\n")


def get_no_outputs_notification() -> str:
    """Load the no-outputs notification from config.yaml."""
    config = _load_config()
    return config["no_outputs_notification"].rstrip("\n")


def format_error_notification(error: str) -> str:
    """Format an error notification from config.yaml template."""
    config = _load_config()
    template = config["error_notification"].rstrip("\n")
    return string.Template(template).safe_substitute(error=error)


def format_pipeline_notification(result: PendingResult) -> str:
    """Format notification after pipeline completion.

    Delegates to format_review_notification() for the review workflow,
    with a pipeline-specific header and preamble.
    """
    if not result.has_outputs():
        return get_no_outputs_notification()

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


def format_review_notification(
    skills: list,
    strategies: list,
    *,
    lessons: list | None = None,
    header: str = "PENDING ITEM(S) READY FOR REVIEW",
    preamble: str = "",
    errors: list[str] | None = None,
    run_id: str = "",
    project_id: str = "",
) -> str:
    """Format a visually highlighted notification for Claude with pending items.

    This is the single source of truth for the review notification format.
    Used by both check_pending_skills.py (hook) and run_pipeline_async.py (post-pipeline).

    Accepts both dataclass types (PendingSkillInfo/PendingStrategyInfo) and
    Pydantic models (PendingSkillData/PendingStrategyData) via duck-typing.

    Args:
        skills: List of pending skills to display.
        strategies: List of pending strategies to display.
        lessons: Optional list of lessons to display.
        header: Title shown in the top banner box.
        preamble: Optional text shown between the banner and the item lists.
        errors: Optional list of warning messages to display.
        run_id: Pipeline run UUID for archive commands.
        project_id: Project identifier for archive commands.

    Returns:
        Formatted notification string.
    """
    lessons = lessons or []
    total_count = len(skills) + len(strategies) + len(lessons)
    skills_section = _format_skills_section(skills)
    strategies_section = _format_strategies_section(strategies)

    errors_section = ""
    if errors:
        warning_lines = "\n".join(f"  \u2022 {e}" for e in errors)
        errors_section = f"\n\u26a0\ufe0f  WARNINGS:\n{warning_lines}\n"

    preamble_section = f"\n{preamble}\n" if preamble else ""

    workflow = string.Template(_get_workflow_template()).safe_substitute(
        run_id=run_id or "<RUN_ID>",
        project_id=project_id or "<PROJECT_ID>",
    )

    lessons_block = ""
    if lessons:
        lessons_section = _format_lessons_section(lessons)
        lessons_block = f"""
\U0001f4d6 LESSONS LEARNED ({len(lessons)}):
{lessons_section}
"""

    return f"""
\u2554\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2557
\u2551  \U0001f3af MEGA-CODE: {total_count} {header:<54}\u2551
\u2560\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2563
\u2551  IMPORTANT: Review these items BEFORE responding to the user!     \u2551
\u255a\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u2550\u255d
{preamble_section}
\U0001f4e6 PENDING SKILLS ({len(skills)}):
{skills_section}

\U0001f4cb PENDING STRATEGIES ({len(strategies)}):
{strategies_section}
{lessons_block}{errors_section}
{workflow}"""


# =========================================================================
# CLI entry point
# =========================================================================


def main() -> int:
    """CLI entry point for pending item operations.

    Subcommands:
      review   Print the review notification with workflow instructions.
    """
    import argparse

    parser = argparse.ArgumentParser(
        prog="mega-code-pending",
        description="Manage pending skills, strategies, and lessons",
    )
    sub = parser.add_subparsers(dest="command")

    review_parser = sub.add_parser(
        "review",
        help="Print review notification with workflow instructions",
    )
    review_parser.add_argument("--run-id", default="", help="Pipeline run UUID")
    review_parser.add_argument("--project-id", default="", help="Project identifier")

    args = parser.parse_args()

    if args.command == "review":
        skills = get_pending_skills()
        strategies = get_pending_strategies()
        print(
            format_review_notification(
                skills,
                strategies,
                run_id=args.run_id,
                project_id=args.project_id,
            )
        )
        return 0

    parser.print_help()
    return 1


if __name__ == "__main__":
    import sys

    sys.exit(main())
