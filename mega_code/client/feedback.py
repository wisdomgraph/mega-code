"""Archive storage for generated skills and strategies.

This module handles archiving pending items (instead of deleting) after user review,
preserving them for cross-run dedup and historical reference.

Storage layout (project-scoped):
  ~/.local/share/mega-code/data/feedback/
  └── {project_id}/
      └── {run_id}/
          ├── manifest.json        # What was in this run (skills + strategies)
          ├── skills/
          │   └── {name}/          # Archived skill folder (SKILL.md, metadata.json, etc.)
          ├── strategies/
          │   └── {name}.md        # Archived strategy file
          └── lessons/
              └── {slug}.md        # Lessons saved directly here by save_outputs_to_pending()
"""

import json
import logging
import shutil
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

from mega_code.client.pending import (
    FEEDBACK_DIR,
    PendingSkillInfo,
    PendingStrategyInfo,
)

logger = logging.getLogger(__name__)


# =============================================================================
# Data Models
# =============================================================================


@dataclass
class ArchivedRun:
    """Metadata about an archived pipeline run."""

    run_id: str
    archived_at: str = ""  # ISO format
    project_id: str = ""
    skills: list[dict] = field(default_factory=list)  # Serialized PendingSkillInfo
    strategies: list[dict] = field(default_factory=list)  # Serialized PendingStrategyInfo
    lessons: list[dict] = field(default_factory=list)  # Serialized PendingLessonInfo
    actions: dict[str, str] = field(default_factory=dict)  # item_name -> action_taken


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
    skill_metadata: dict[str, dict] | None = None,
    strategy_metadata: dict[str, dict] | None = None,
) -> str | None:
    """Archive pending items to feedback directory instead of deleting them.

    Moves all pending items (both installed and skipped) into a project-scoped
    archive folder so they can be referenced for cross-run dedup.
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
        skill_metadata: Optional per-skill metadata for cross-run dedup.
            Maps skill name -> {"signal_strength": float, "embedding": list[float]}.
        strategy_metadata: Optional per-strategy metadata for cross-run dedup.
            Maps strategy name -> {"signal_strength": float, "embedding": list[float]}.

    Returns:
        Run ID of the archive, or None if nothing to archive.
    """
    installed_skills = installed_skills or []
    installed_strategies = installed_strategies or []
    skipped_skills = skipped_skills or []
    skipped_strategies = skipped_strategies or []

    # Auto-load dedup metadata if not explicitly provided
    if skill_metadata is None or strategy_metadata is None:
        from mega_code.client.pending import load_dedup_metadata

        loaded_skill, loaded_strategy = load_dedup_metadata()
        skill_metadata = skill_metadata or loaded_skill
        strategy_metadata = strategy_metadata or loaded_strategy

    skill_metadata = skill_metadata or {}
    strategy_metadata = strategy_metadata or {}

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

        # Collect lesson entries from the archive dir (saved by save_outputs_to_pending)
        lessons_dir = archive_dir / "lessons"
        lesson_entries: list[dict] = []
        if lessons_dir.exists():
            for lesson_file in lessons_dir.glob("*.md"):
                lesson_entries.append(
                    {
                        "slug": lesson_file.stem,
                        "path": str(lesson_file),
                    }
                )

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
                    "author": s.author,
                    "version": s.version,
                    "tags": s.tags,
                    "metadata": skill_metadata.get(s.name, {}),
                }
                for s in all_skills
            ],
            strategies=[
                {
                    "name": s.name,
                    "description": s.description,
                    "path": str(archive_strategies_dir / f"{s.name}.md"),
                    "author": s.author,
                    "version": s.version,
                    "tags": s.tags,
                    "metadata": strategy_metadata.get(s.name, {}),
                }
                for s in all_strategies
            ],
            lessons=lesson_entries,
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
# Discovery
# =============================================================================


def load_manifest(run_id: str, project_id: str) -> ArchivedRun | None:
    """Load the manifest for an archived run."""
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
        lessons=data.get("lessons", []),
        actions=data.get("actions", {}),
    )


def get_runs_for_project(project_id: str, limit: int = 10) -> list[ArchivedRun]:
    """Get archived runs for a specific project, sorted most recent first."""
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
    """Get recent archived runs across all projects, most recent first."""
    if not FEEDBACK_DIR.exists():
        return []

    runs = []
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

    runs.sort(key=lambda r: r.archived_at, reverse=True)
    return runs[:limit]
