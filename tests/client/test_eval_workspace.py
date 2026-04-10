# tests/client/test_eval_workspace.py
"""Tests for eval_workspace iteration directory management."""

from __future__ import annotations

import pytest

from mega_code.client.eval_workspace import (
    create_iteration_dir,
    get_latest_iteration,
    load_artifact,
    load_previous_iteration,
    resolve_workspace_skill_name,
    save_artifact,
    save_text_artifact,
    workspace_root,
)


@pytest.fixture()
def mock_data_dir(tmp_path, monkeypatch):
    """Point data_dir() to a temp directory."""
    monkeypatch.setenv("MEGA_CODE_DATA_DIR", str(tmp_path))
    return tmp_path


class TestWorkspaceRoot:
    def test_returns_expected_path(self, mock_data_dir):
        root = workspace_root("my-skill")
        assert root == mock_data_dir / "data" / "skill-enhance" / "my-skill"

    def test_handles_kebab_case_names(self, mock_data_dir):
        root = workspace_root("fastapi-pydantic-metadata-persistence")
        assert root.name == "fastapi-pydantic-metadata-persistence"


class TestCreateIterationDir:
    def test_first_iteration(self, mock_data_dir):
        path, num = create_iteration_dir("test-skill")
        assert num == 1
        assert path.name == "iteration-1"
        assert path.exists()

    def test_increments_iteration(self, mock_data_dir):
        path1, num1 = create_iteration_dir("test-skill")
        path2, num2 = create_iteration_dir("test-skill")
        assert num1 == 1
        assert num2 == 2
        assert path2.name == "iteration-2"

    def test_skips_non_iteration_dirs(self, mock_data_dir):
        root = workspace_root("test-skill")
        root.mkdir(parents=True, exist_ok=True)
        (root / "other-dir").mkdir()
        (root / "iteration-3").mkdir()

        path, num = create_iteration_dir("test-skill")
        assert num == 4
        assert path.name == "iteration-4"

    def test_handles_empty_workspace(self, mock_data_dir):
        root = workspace_root("test-skill")
        root.mkdir(parents=True, exist_ok=True)

        path, num = create_iteration_dir("test-skill")
        assert num == 1

    def test_uses_canonical_name_from_skill_path(self, mock_data_dir, tmp_path):
        skill_dir = tmp_path / "skills" / "legacy-folder-name"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(
            """\
---
name: canonical-skill-name
description: Test
---

# Skill
""",
            encoding="utf-8",
        )

        path, num = create_iteration_dir("legacy-folder-name", str(skill_dir / "SKILL.md"))
        assert num == 1
        assert (
            path
            == mock_data_dir / "data" / "skill-enhance" / "canonical-skill-name" / "iteration-1"
        )


class TestResolveWorkspaceSkillName:
    def test_prefers_frontmatter_name_from_skill_path(self, tmp_path):
        skill_dir = tmp_path / "skills" / "legacy-folder-name"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(
            """\
---
name: canonical-skill-name
description: Test
---

# Skill
""",
            encoding="utf-8",
        )

        assert (
            resolve_workspace_skill_name("legacy-folder-name", str(skill_dir / "SKILL.md"))
            == "canonical-skill-name"
        )

    def test_falls_back_to_argument_when_skill_path_missing(self):
        assert (
            resolve_workspace_skill_name("Legacy Folder Name", "/does/not/exist/SKILL.md")
            == "legacy-folder-name"
        )


class TestSaveAndLoadArtifact:
    def test_save_and_load_roundtrip(self, mock_data_dir):
        iter_dir, _ = create_iteration_dir("test-skill")
        data = {"cases": [{"task": "test", "expectations": []}]}

        saved_path = save_artifact(iter_dir, "test-cases.json", data)
        assert saved_path.exists()
        assert saved_path.name == "test-cases.json"

        loaded = load_artifact(iter_dir, "test-cases.json")
        assert loaded == data

    def test_save_adds_json_extension(self, mock_data_dir):
        iter_dir, _ = create_iteration_dir("test-skill")
        path = save_artifact(iter_dir, "test-cases", {"key": "value"})
        assert path.name == "test-cases.json"

    def test_load_nonexistent_returns_none(self, mock_data_dir):
        iter_dir, _ = create_iteration_dir("test-skill")
        result = load_artifact(iter_dir, "nonexistent.json")
        assert result is None


class TestLoadPreviousIteration:
    def test_returns_none_for_first_iteration(self, mock_data_dir):
        result = load_previous_iteration("test-skill", 1)
        assert result is None

    def test_loads_previous_eval_full(self, mock_data_dir):
        iter_dir, _ = create_iteration_dir("test-skill")
        eval_data = {"skill_name": "test", "test_cases": []}
        save_artifact(iter_dir, "eval-full.json", eval_data)

        result = load_previous_iteration("test-skill", 2)
        assert result == eval_data

    def test_returns_none_when_no_previous_data(self, mock_data_dir):
        create_iteration_dir("test-skill")
        result = load_previous_iteration("test-skill", 2)
        assert result is None


class TestGetLatestIteration:
    def test_returns_zero_when_no_workspace(self, mock_data_dir):
        assert get_latest_iteration("nonexistent-skill") == 0

    def test_returns_highest_iteration(self, mock_data_dir):
        create_iteration_dir("test-skill")
        create_iteration_dir("test-skill")
        create_iteration_dir("test-skill")
        assert get_latest_iteration("test-skill") == 3


class TestSaveTextArtifact:
    def test_saves_text_content(self, mock_data_dir):
        iter_dir, _ = create_iteration_dir("test-skill")
        content = "# Enhanced Skill\n\nSome content here."
        path = save_text_artifact(iter_dir, "enhanced-skill.md", content)
        assert path.exists()
        assert path.read_text(encoding="utf-8") == content

    def test_saves_html_content(self, mock_data_dir):
        iter_dir, _ = create_iteration_dir("test-skill")
        html = "<html><body>Review</body></html>"
        path = save_text_artifact(iter_dir, "review.html", html)
        assert path.name == "review.html"
        assert path.read_text(encoding="utf-8") == html
