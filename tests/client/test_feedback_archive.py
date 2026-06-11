"""Tests for archive manifest shape (A5).

Pins the per-skill dict shape produced by `archive_pending_items` so the MQ1
`skill_slug` addition (A5) is a tracked extension rather than an accidental
drift. Baseline test captures the pre-A5 state; the dedicated `skill_slug`
test pins the new field once A5 lands.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from mega_code.client.feedback import archive_pending_items
from mega_code.client.pending import PendingSkillInfo


@pytest.fixture
def isolated_feedback_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect feedback storage to a tmp dir so the test doesn't touch user state."""
    feedback_dir = tmp_path / "feedback"
    monkeypatch.setattr("mega_code.client.feedback.FEEDBACK_DIR", feedback_dir)
    return feedback_dir


def _make_pending_skill(tmp_path: Path, name: str) -> PendingSkillInfo:
    """Create a pending skill directory on disk + the matching PendingSkillInfo."""
    skill_dir = tmp_path / "pending" / name
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(f"---\nname: {name}\n---\nbody\n")
    return PendingSkillInfo(
        name=name,
        description="A skill.",
        path=str(skill_dir),
        author="alice",
        version="1.0.0",
        tags=["py"],
    )


def _read_manifest(feedback_dir: Path, project_id: str, run_id: str) -> dict:
    manifest_path = feedback_dir / project_id / run_id / "manifest.json"
    return json.loads(manifest_path.read_text())


class TestArchiveManifestShape:
    """Per-skill entry shape in manifest.json — single source of truth."""

    PROJECT_ID = "proj-123"
    RUN_ID = "run-abc"

    def _archive(self, tmp_path: Path, name: str = "my-skill") -> dict:
        skill = _make_pending_skill(tmp_path, name)
        archive_pending_items(
            run_id=self.RUN_ID,
            project_id=self.PROJECT_ID,
            installed_skills=[skill],
            skill_metadata={name: {"signal_strength": 0.9}},
        )
        return self

    def test_per_skill_entry_contains_baseline_keys(
        self, tmp_path: Path, isolated_feedback_dir: Path
    ):
        skill = _make_pending_skill(tmp_path, "my-skill")
        archive_pending_items(
            run_id=self.RUN_ID,
            project_id=self.PROJECT_ID,
            installed_skills=[skill],
            skill_metadata={"my-skill": {"signal_strength": 0.9}},
        )
        manifest = _read_manifest(isolated_feedback_dir, self.PROJECT_ID, self.RUN_ID)
        assert len(manifest["skills"]) == 1
        entry = manifest["skills"][0]

        assert entry["name"] == "my-skill"
        assert entry["description"] == "A skill."
        assert entry["author"] == "alice"
        assert entry["version"] == "1.0.0"
        assert entry["tags"] == ["py"]
        assert entry["metadata"] == {"signal_strength": 0.9}
        assert entry["path"].endswith("/feedback/proj-123/run-abc/skills/my-skill")

    def test_archive_top_level_keys(self, tmp_path: Path, isolated_feedback_dir: Path):
        skill = _make_pending_skill(tmp_path, "my-skill")
        archive_pending_items(
            run_id=self.RUN_ID,
            project_id=self.PROJECT_ID,
            installed_skills=[skill],
        )
        manifest = _read_manifest(isolated_feedback_dir, self.PROJECT_ID, self.RUN_ID)
        assert set(manifest.keys()) == {
            "run_id",
            "archived_at",
            "project_id",
            "skills",
            "strategies",
            "lessons",
            "actions",
        }
        assert manifest["run_id"] == self.RUN_ID
        assert manifest["project_id"] == self.PROJECT_ID
        assert manifest["actions"] == {"my-skill": "installed"}

    # -- MQ1: skill_slug field ----------------------------------------------

    def test_per_skill_entry_includes_skill_slug(self, tmp_path: Path, isolated_feedback_dir: Path):
        skill = _make_pending_skill(tmp_path, "my-skill")
        archive_pending_items(
            run_id=self.RUN_ID,
            project_id=self.PROJECT_ID,
            installed_skills=[skill],
        )
        entry = _read_manifest(isolated_feedback_dir, self.PROJECT_ID, self.RUN_ID)["skills"][0]
        # name preserved for display, slug derived from it.
        assert entry["name"] == "my-skill"
        assert entry["skill_slug"] == "my-skill"

    def test_skill_slug_diverges_from_name_for_mixed_case(
        self, tmp_path: Path, isolated_feedback_dir: Path
    ):
        skill = _make_pending_skill(tmp_path, "My Mixed Case Name")
        archive_pending_items(
            run_id=self.RUN_ID,
            project_id=self.PROJECT_ID,
            installed_skills=[skill],
        )
        entry = _read_manifest(isolated_feedback_dir, self.PROJECT_ID, self.RUN_ID)["skills"][0]
        # canonical_skill_name(...) lowercases + kebab-cases — divergence captured.
        assert entry["name"] == "My Mixed Case Name"
        assert entry["skill_slug"] == "my-mixed-case-name"

    def test_skill_slug_falls_back_to_unnamed_for_unicode_only(
        self, tmp_path: Path, isolated_feedback_dir: Path
    ):
        """MQ1 rationale case: Unicode-only names collapse to 'unnamed' via sanitize_name."""
        skill = _make_pending_skill(tmp_path, "스킬")
        archive_pending_items(
            run_id=self.RUN_ID,
            project_id=self.PROJECT_ID,
            installed_skills=[skill],
        )
        entry = _read_manifest(isolated_feedback_dir, self.PROJECT_ID, self.RUN_ID)["skills"][0]
        assert entry["name"] == "스킬"
        assert entry["skill_slug"] == "unnamed"
