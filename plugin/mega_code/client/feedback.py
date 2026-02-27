"""Feedback collection and storage for generated skills and strategies.

This module handles:
1. Archiving pending items (instead of deleting) after user review
2. Storing structured user feedback alongside archived items
3. Loading feedback data for upload and analysis

Feedback lifecycle:
  /mega-code:run → pipeline generates → user reviews/installs → items archived
  /mega-code:feedback → discover archived items → ask user questions → save feedback

Storage layout (project-scoped):
  ~/.local/mega-code/data/feedback/
  └── {project_id}/
      └── {run_id}/
          ├── manifest.json        # What was in this run (skills + strategies)
          ├── skills/
          │   └── {name}/          # Archived skill folder (SKILL.md, metadata.json, etc.)
          ├── strategies/
          │   └── {name}.md        # Archived strategy file
          ├── lessons/
          │   └── {slug}.md        # Lessons saved directly here by save_outputs_to_pending()
          └── feedback.json        # User feedback (written by /mega-code:feedback)
"""

import json
import logging
import shutil
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Literal

from mega_code.client.models import FeedbackItem
from mega_code.client.pending import (
    FEEDBACK_DIR,
    PendingSkillInfo,
    PendingStrategyInfo,
)

logger = logging.getLogger(__name__)


def _validate_path_component(value: str, name: str) -> bool:
    """Validate that a string is safe for use as a path component (no traversal)."""
    if not value or "/" in value or "\\" in value or ".." in value:
        logger.warning(f"Invalid {name} containing path separators: {value!r}")
        return False
    return True


# =============================================================================
# Data Models
# =============================================================================


# FeedbackItem is imported from mega_code.client.models (Pydantic BaseModel).
# Re-exported here for backwards compatibility.


@dataclass
class RunFeedback:
    """Feedback for an entire pipeline run."""

    run_id: str
    project_id: str = ""
    items: list[FeedbackItem] = field(default_factory=list)
    overall_quality: Literal["excellent", "good", "mixed", "poor"] | None = None
    additional_comments: str | None = None
    feedback_at: str | None = None  # ISO format datetime


@dataclass
class ArchivedRun:
    """Metadata about an archived pipeline run."""

    run_id: str
    archived_at: str = ""  # ISO format
    project_id: str = ""
    skills: list[dict] = field(default_factory=list)  # Serialized PendingSkillInfo
    strategies: list[dict] = field(default_factory=list)  # Serialized PendingStrategyInfo
    actions: dict[str, str] = field(default_factory=dict)  # item_name -> action_taken
    has_feedback: bool = False


# =============================================================================
# Archive Operations (replaces delete)
# =============================================================================


def archive_pending_items(
    run_id: str,
    project_id: str,
    installed_skills: list[PendingSkillInfo] | None = None,
    installed_strategies: list[PendingStrategyInfo] | None = None,
    skipped_skills: list[PendingSkillInfo] | None = None,
    skipped_strategies: list[PendingStrategyInfo] | None = None,
) -> str | None:
    """Archive pending items to feedback directory instead of deleting them.

    Moves all pending items (both installed and skipped) into a project-scoped
    archive folder so they can be referenced during feedback collection.
    Lessons are saved directly to the run folder by save_outputs_to_pending()
    and do not require a separate archive step.

    Storage: feedback/{project_id}/{run_id}/

    Args:
        run_id: Pipeline run ID (UUID from pipeline).
        project_id: Project identifier (e.g. "my-project_a1b2c3d4").
        installed_skills: Skills that were installed by the user.
        installed_strategies: Strategies that were installed by the user.
        skipped_skills: Skills that the user chose not to install.
        skipped_strategies: Strategies that the user chose not to install.

    Returns:
        Run ID of the archive, or None if nothing to archive.
    """
    installed_skills = installed_skills or []
    installed_strategies = installed_strategies or []
    skipped_skills = skipped_skills or []
    skipped_strategies = skipped_strategies or []

    all_skills = installed_skills + skipped_skills
    all_strategies = installed_strategies + skipped_strategies

    if not all_skills and not all_strategies:
        return None

    archive_dir = FEEDBACK_DIR / project_id / run_id
    archive_skills_dir = archive_dir / "skills"
    archive_strategies_dir = archive_dir / "strategies"

    try:
        # Only need child dirs; parents=True creates archive_dir implicitly
        archive_skills_dir.mkdir(parents=True, exist_ok=True)
        archive_strategies_dir.mkdir(parents=True, exist_ok=True)

        # Archive skills (move from pending to archive)
        for skill in all_skills:
            src = Path(skill.path)
            if src.exists() and src.is_dir():
                dst = archive_skills_dir / skill.name
                shutil.copytree(src, dst, dirs_exist_ok=True)
                shutil.rmtree(src)
                logger.info(f"Archived skill: {skill.name} -> {dst}")

        # Archive strategies (move from pending to archive)
        for strategy in all_strategies:
            dst = archive_strategies_dir / f"{strategy.name}.md"
            if _move_file(Path(strategy.path), dst):
                logger.info(f"Archived strategy: {strategy.name} -> {dst}")

        # Build actions map
        actions = {s.name: "installed" for s in installed_skills + installed_strategies}
        actions |= {s.name: "skipped" for s in skipped_skills + skipped_strategies}

        # Write manifest
        manifest = ArchivedRun(
            run_id=run_id,
            project_id=project_id,
            archived_at=datetime.now().isoformat(),
            skills=[
                {
                    "name": s.name,
                    "description": s.description,
                    "path": str(archive_skills_dir / s.name),
                }
                for s in all_skills
            ],
            strategies=[
                {
                    "name": s.name,
                    "description": s.description,
                    "path": str(archive_strategies_dir / f"{s.name}.md"),
                }
                for s in all_strategies
            ],
            actions=actions,
        )
        _save_manifest(archive_dir, manifest)

        logger.info(
            f"Archived {len(all_skills)} skills, "
            f"{len(all_strategies)} strategies -> {archive_dir}"
        )
        return run_id

    except OSError as e:
        logger.error(f"Failed to archive pending items: {e}")
        return None


def _move_file(src: Path, dst: Path) -> bool:
    """Copy src to dst then delete src. Returns True if src existed."""
    if not (src.exists() and src.is_file()):
        return False
    shutil.copy2(src, dst)
    src.unlink()
    return True


def _save_manifest(archive_dir: Path, manifest: ArchivedRun) -> None:
    """Save archive manifest to manifest.json."""
    (archive_dir / "manifest.json").write_text(
        json.dumps(asdict(manifest), indent=2), encoding="utf-8"
    )


# =============================================================================
# Feedback Operations
# =============================================================================


def save_feedback(run_id: str, project_id: str, feedback: RunFeedback) -> bool:
    """Save user feedback for an archived run.

    Args:
        run_id: The run ID (UUID) of the archived run.
        project_id: The project identifier.
        feedback: The feedback data to save.

    Returns:
        True if saved successfully.
    """
    archive_dir = FEEDBACK_DIR / project_id / run_id
    if not archive_dir.exists():
        logger.error(f"Archive directory not found: {archive_dir}")
        return False

    feedback.feedback_at = datetime.now().isoformat()

    # RunFeedback is a dataclass but items are Pydantic models,
    # so we serialize manually instead of using dataclasses.asdict().
    feedback_data = {
        "run_id": feedback.run_id,
        "project_id": feedback.project_id,
        "items": [item.model_dump() for item in feedback.items],
        "overall_quality": feedback.overall_quality,
        "additional_comments": feedback.additional_comments,
        "feedback_at": feedback.feedback_at,
    }

    feedback_path = archive_dir / "feedback.json"
    feedback_path.write_text(json.dumps(feedback_data, indent=2), encoding="utf-8")

    # Update manifest to mark has_feedback
    manifest_path = archive_dir / "manifest.json"
    if manifest_path.exists():
        manifest_data = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest_data["has_feedback"] = True
        manifest_path.write_text(json.dumps(manifest_data, indent=2), encoding="utf-8")

    logger.info(f"Saved feedback for run {run_id}: {len(feedback.items)} items")
    return True


def load_feedback(run_id: str, project_id: str) -> RunFeedback | None:
    """Load feedback for a specific run.

    Args:
        run_id: The run ID to load feedback for.
        project_id: The project identifier.

    Returns:
        RunFeedback if found, None otherwise.
    """
    feedback_path = FEEDBACK_DIR / project_id / run_id / "feedback.json"
    if not feedback_path.exists():
        return None

    data = json.loads(feedback_path.read_text(encoding="utf-8"))
    items = [FeedbackItem.model_validate(item) for item in data.get("items", [])]
    return RunFeedback(
        run_id=data["run_id"],
        project_id=data.get("project_id", ""),
        items=items,
        overall_quality=data.get("overall_quality"),
        additional_comments=data.get("additional_comments"),
        feedback_at=data.get("feedback_at"),
    )


def load_manifest(run_id: str, project_id: str) -> ArchivedRun | None:
    """Load the manifest for an archived run.

    Args:
        run_id: The run ID to load manifest for.
        project_id: The project identifier.

    Returns:
        ArchivedRun if found, None otherwise.
    """
    manifest_path = FEEDBACK_DIR / project_id / run_id / "manifest.json"
    if not manifest_path.exists():
        return None

    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    return ArchivedRun(
        run_id=data["run_id"],
        project_id=data.get("project_id", ""),
        archived_at=data["archived_at"],
        skills=data.get("skills", []),
        strategies=data.get("strategies", []),
        actions=data.get("actions", {}),
        has_feedback=data.get("has_feedback", False),
    )


# =============================================================================
# Discovery (for /mega-code:feedback)
# =============================================================================


def get_runs_for_project(project_id: str, limit: int = 10) -> list[ArchivedRun]:
    """Get archived runs for a specific project.

    Args:
        project_id: The project identifier.
        limit: Maximum number of runs to return.

    Returns:
        List of ArchivedRun objects for the project, sorted most recent first.
    """
    project_dir = FEEDBACK_DIR / project_id
    if not project_dir.exists():
        return []

    runs = []
    for run_dir in project_dir.iterdir():
        if not run_dir.is_dir():
            continue
        manifest = load_manifest(run_id=run_dir.name, project_id=project_id)
        if manifest:
            runs.append(manifest)

    runs.sort(key=lambda r: r.archived_at, reverse=True)
    return runs[:limit]


def get_recent_runs(limit: int = 5) -> list[ArchivedRun]:
    """Get recent archived runs across all projects, most recent first.

    Args:
        limit: Maximum number of runs to return.

    Returns:
        List of ArchivedRun objects sorted by most recent first.
    """
    if not FEEDBACK_DIR.exists():
        return []

    runs = []
    # Iterate over project subdirs, then run subdirs within each
    for project_dir in sorted(FEEDBACK_DIR.iterdir(), reverse=True):
        if not project_dir.is_dir():
            continue
        project_id = project_dir.name
        for run_dir in sorted(project_dir.iterdir(), reverse=True):
            if not run_dir.is_dir():
                continue
            manifest = load_manifest(run_id=run_dir.name, project_id=project_id)
            if manifest:
                runs.append(manifest)

    # Sort by archived_at descending, then limit
    runs.sort(key=lambda r: r.archived_at, reverse=True)
    return runs[:limit]


def get_runs_without_feedback(limit: int = 5) -> list[ArchivedRun]:
    """Get archived runs that don't have feedback yet.

    Args:
        limit: Maximum number of runs to return.

    Returns:
        List of ArchivedRun objects that haven't been reviewed yet.
    """
    runs = get_recent_runs(limit=limit * 2)  # Fetch more to filter
    return [r for r in runs if not r.has_feedback][:limit]


def get_feedback_dir() -> Path:
    """Get the feedback directory path.

    Returns:
        Path to ~/.local/mega-code/data/feedback/
    """
    return FEEDBACK_DIR


def get_all_feedback_files(run_id: str | None = None) -> list[Path]:
    """Get all feedback JSON files, optionally filtered by run_id.

    Args:
        run_id: If provided, only return feedback for this run (across all projects).

    Returns:
        List of paths to feedback.json files.
    """
    if not FEEDBACK_DIR.exists():
        return []

    if run_id:
        if not _validate_path_component(run_id, "run_id"):
            return []
        # Search across all project dirs for this run_id
        results = []
        for project_dir in FEEDBACK_DIR.iterdir():
            if not project_dir.is_dir():
                continue
            feedback_path = project_dir / run_id / "feedback.json"
            if feedback_path.exists():
                results.append(feedback_path)
        return results

    return sorted(FEEDBACK_DIR.rglob("feedback.json"), reverse=True)


# =============================================================================
# Notification Formatting (for inline feedback after install)
# =============================================================================


def format_feedback_prompt(run_id: str, project_id: str = "") -> str:
    """Format a feedback prompt to show after skill installation.

    This is appended to the review notification after STEP 5 (cleanup),
    inviting the user to provide feedback.

    Args:
        run_id: The archive run ID for reference.
        project_id: The project identifier for the feedback CLI.

    Returns:
        Formatted string prompting for feedback.
    """
    pid = project_id or "<PROJECT_ID>"
    return f"""\

══════════════════════════════════════════════════════════════════════
STEP 6: COLLECT FEEDBACK (use AskUserQuestion tool)
══════════════════════════════════════════════════════════════════════

After installation, ask the user for feedback on the generated items.
Archive run ID: {run_id}

MUST use AskUserQuestion with these questions:

Question 1 - "How useful were the generated items?" (header: "Quality")
  Options:
     - Excellent - all items were relevant and useful
     - Good - most items were useful
     - Mixed - some good, some irrelevant
     - Poor - most items missed the mark

Question 2 - "What could be improved?" (header: "Improve", multiSelect: true)
  Options:
     - More specific trigger conditions
     - Better examples in skills
     - More concise content
     - Better scope (too broad/narrow)

Question 3 - "Any additional comments?" (header: "Comments")
  Options:
     - No additional comments
     - (User can type via "Other")

After collecting answers, save feedback by running:

```bash
MEGA_DIR=$(cat ~/.local/mega-code/plugin-root 2>/dev/null || echo ~/.claude/mega-code)
[ -f "${{HOME}}/.local/mega-code/.env" ] && set -a && . "${{HOME}}/.local/mega-code/.env" && set +a
cd "$MEGA_DIR" && set -a && . ./.env && set +a && \\
  uv run python -m mega_code.client.feedback_cli \\
  --run-id {run_id} \\
  --project {pid} \\
  --overall-quality <quality> \\
  --comments "<text>"
```

Items in this run will have their per-item feedback auto-populated from
the install/skip actions recorded in the manifest.

⚡ ASK THE USER NOW ⚡"""
