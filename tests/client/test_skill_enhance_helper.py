"""Tests for mega_code.client.skill_enhance_helper — skill discovery and enhanced skill persistence."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import yaml

from mega_code.client import skill_enhance_helper as helper_module
from mega_code.client.pending import PendingSkillInfo
from mega_code.client.skill_enhance_helper import (
    _apply_cli_project_dir,
    _current_project_id,
    _is_mega_code_skill,
    _repair_selected_skill_path,
    _resolve_current_project_dir,
    _scan_archived_skills,
    _scan_project_installed_skills,
    accept_enhanced_skill,
    list_skills,
    resolve_skill,
    store_enhanced_skill_on_server,
)
from mega_code.client.stats import get_project_folder_name

# =============================================================================
# _is_mega_code_skill
# =============================================================================


class TestIsMegaCodeSkill:
    def test_default_author(self):
        assert _is_mega_code_skill("co-authored by www.megacode.ai")

    def test_case_insensitive(self):
        assert _is_mega_code_skill("Co-Authored by www.MegaCode.AI")

    def test_non_mega_code(self):
        assert not _is_mega_code_skill("some other author")

    def test_empty(self):
        assert not _is_mega_code_skill("")


def test_apply_cli_project_dir_sets_real_project_dir(tmp_path, monkeypatch):
    monkeypatch.delenv("CODEX_PROJECT_DIR", raising=False)
    project_dir = tmp_path / "project"
    project_dir.mkdir()

    _apply_cli_project_dir(str(project_dir))

    assert Path(os.environ["CODEX_PROJECT_DIR"]) == project_dir.resolve()


def test_apply_cli_project_dir_ignores_plugin_marketplace_path(tmp_path, monkeypatch):
    monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
    plugin_dir = tmp_path / ".claude" / "plugins" / "marketplaces" / "mind-ai-mega-code"
    plugin_dir.mkdir(parents=True)

    _apply_cli_project_dir(str(plugin_dir))

    assert "CLAUDE_PROJECT_DIR" not in os.environ


# =============================================================================
# list_skills
# =============================================================================


def test_list_skills_filters_by_author(tmp_path):
    """Only skills with megacode.ai author should be returned."""
    mega_skill_dir = tmp_path / "pending-skills" / "mega-skill"
    mega_skill_dir.mkdir(parents=True)
    (mega_skill_dir / "SKILL.md").write_text(
        '---\nname: mega-skill\ndescription: test\nauthor: "co-authored by www.megacode.ai"\n---\n# Skill',
        encoding="utf-8",
    )

    other_skill_dir = tmp_path / "pending-skills" / "other-skill"
    other_skill_dir.mkdir(parents=True)
    (other_skill_dir / "SKILL.md").write_text(
        "---\nname: other-skill\ndescription: test\nauthor: custom-author\n---\n# Skill",
        encoding="utf-8",
    )

    with patch("mega_code.client.skill_enhance_helper.get_pending_skills") as mock_pending:
        mock_pending.return_value = [
            PendingSkillInfo(
                name="mega-skill",
                description="test",
                path=str(mega_skill_dir),
                author="co-authored by www.megacode.ai",
            ),
            PendingSkillInfo(
                name="other-skill",
                description="test",
                path=str(other_skill_dir),
                author="custom-author",
            ),
        ]
        with (
            patch(
                "mega_code.client.skill_enhance_helper._scan_project_installed_skills",
                return_value=[],
            ),
            patch(
                "mega_code.client.skill_enhance_helper._scan_user_installed_skills",
                return_value=[],
            ),
            patch("mega_code.client.skill_enhance_helper._scan_archived_skills", return_value=[]),
        ):
            skills = list_skills()

    assert len(skills) == 1
    assert skills[0]["name"] == "mega-skill"


def test_list_skills_uses_correct_priority_order():
    """Project skills win over user, pending, and archived duplicates."""
    with (
        patch(
            "mega_code.client.skill_enhance_helper._scan_project_installed_skills",
            return_value=[
                {
                    "name": "shared-skill",
                    "description": "test",
                    "state": "installed",
                    "path": "/tmp/project-shared",
                },
                {
                    "name": "project-only",
                    "description": "test",
                    "state": "installed",
                    "path": "/tmp/project-only",
                },
            ],
        ),
        patch(
            "mega_code.client.skill_enhance_helper._scan_user_installed_skills",
            return_value=[
                {
                    "name": "shared-skill",
                    "description": "test",
                    "state": "installed",
                    "path": "/tmp/user-shared",
                },
                {
                    "name": "user-only",
                    "description": "test",
                    "state": "installed",
                    "path": "/tmp/user-only",
                },
            ],
        ),
        patch(
            "mega_code.client.skill_enhance_helper.get_pending_skills",
            return_value=[
                PendingSkillInfo(
                    name="shared-skill",
                    description="test",
                    path="/tmp/pending-shared",
                    author="co-authored by www.megacode.ai",
                ),
                PendingSkillInfo(
                    name="pending-only",
                    description="test",
                    path="/tmp/pending-only",
                    author="co-authored by www.megacode.ai",
                ),
            ],
        ),
        patch(
            "mega_code.client.skill_enhance_helper._scan_archived_skills",
            return_value=[
                {
                    "name": "shared-skill",
                    "description": "test",
                    "state": "archived",
                    "path": "/tmp/archived-shared",
                },
                {
                    "name": "archived-only",
                    "description": "test",
                    "state": "archived",
                    "path": "/tmp/archived-only",
                },
            ],
        ),
    ):
        skills = list_skills()

    assert [s["name"] for s in skills] == [
        "shared-skill",
        "project-only",
        "user-only",
        "pending-only",
        "archived-only",
    ]
    assert skills[0]["path"] == "/tmp/project-shared"


def test_list_skills_stops_after_five_unique_skills():
    """Lower-priority locations are skipped once the cap is met."""
    project_skills = [
        {
            "name": f"project-skill-{i}",
            "description": "test",
            "state": "installed",
            "path": f"/tmp/project-{i}",
        }
        for i in range(5)
    ]

    with (
        patch(
            "mega_code.client.skill_enhance_helper._scan_project_installed_skills",
            return_value=project_skills,
        ),
        patch("mega_code.client.skill_enhance_helper._scan_user_installed_skills") as mock_user,
        patch("mega_code.client.skill_enhance_helper.get_pending_skills") as mock_pending,
        patch("mega_code.client.skill_enhance_helper._scan_archived_skills") as mock_archived,
    ):
        skills = list_skills()

    assert len(skills) == 5
    assert [s["name"] for s in skills] == [f"project-skill-{i}" for i in range(5)]
    mock_user.assert_not_called()
    mock_pending.assert_not_called()
    mock_archived.assert_not_called()


def test_scan_project_installed_skills_uses_claude_project_dir_and_nested_metadata_author(
    tmp_path, monkeypatch
):
    """Project-installed skill scanning should use CLAUDE_PROJECT_DIR and metadata.author."""
    project_dir = tmp_path / "real-project"
    plugin_dir = tmp_path / "plugin-dir"
    plugin_dir.mkdir()
    skills_dir = project_dir / ".agents" / "skills" / "nested-author-skill"
    skills_dir.mkdir(parents=True)
    (skills_dir / "SKILL.md").write_text(
        """---
name: nested-author-skill
description: nested author test
metadata:
  author: "co-authored by www.megacode.ai"
---
# Skill
""",
        encoding="utf-8",
    )

    monkeypatch.setenv("CODEX_PROJECT_DIR", str(project_dir))
    monkeypatch.chdir(plugin_dir)

    skills = _scan_project_installed_skills()

    assert [s["name"] for s in skills] == ["nested-author-skill"]
    assert skills[0]["path"].endswith("/real-project/.agents/skills/nested-author-skill/SKILL.md")


def test_resolve_current_project_dir_prefers_session_mapping_over_plugin_cache(
    tmp_path, monkeypatch
):
    """Plugin-cache CODEX_PROJECT_DIR should not override session-backed project mapping."""
    session_id = "session-123"
    real_project = tmp_path / "real-project"
    real_project.mkdir()
    project_id = get_project_folder_name(str(real_project))
    plugin_cache_dir = (
        tmp_path
        / ".claude"
        / "plugins"
        / "cache"
        / "mind-ai-mega-code"
        / "mega-code"
        / "1.0.3-beta"
    )
    fake_session_dir = tmp_path / "projects" / project_id / session_id

    plugin_cache_dir.mkdir(parents=True)
    fake_session_dir.mkdir(parents=True)

    monkeypatch.setenv("CODEX_THREAD_ID", session_id)
    monkeypatch.setenv("CODEX_PROJECT_DIR", str(plugin_cache_dir))
    monkeypatch.chdir(plugin_cache_dir)

    with (
        patch(
            "mega_code.client.skill_enhance_helper.find_session_dir",
            return_value=fake_session_dir,
        ),
        patch(
            "mega_code.client.skill_enhance_helper.load_mapping",
            return_value={project_id: str(real_project)},
        ),
    ):
        assert _resolve_current_project_dir() == real_project.resolve()
        assert _current_project_id() == project_id


def test_list_skills_uses_session_mapped_project_when_claude_project_dir_is_plugin_cache(
    tmp_path, monkeypatch
):
    """Project-installed discovery should use the real project, not the plugin cache."""
    session_id = "session-123"
    project_id = "real-project_abcd1234"
    real_project = tmp_path / "real-project"
    plugin_cache_dir = (
        tmp_path
        / ".claude"
        / "plugins"
        / "cache"
        / "mind-ai-mega-code"
        / "mega-code"
        / "1.0.3-beta"
    )
    fake_session_dir = tmp_path / "projects" / project_id / session_id
    skill_dir = real_project / ".agents" / "skills" / "session-mapped-skill"

    skill_dir.mkdir(parents=True)
    plugin_cache_dir.mkdir(parents=True)
    fake_session_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        """---
name: session-mapped-skill
description: session mapped project skill
metadata:
  author: "co-authored by www.megacode.ai"
---
# Skill
""",
        encoding="utf-8",
    )

    monkeypatch.setenv("CODEX_THREAD_ID", session_id)
    monkeypatch.setenv("CODEX_PROJECT_DIR", str(plugin_cache_dir))
    monkeypatch.chdir(plugin_cache_dir)

    with (
        patch(
            "mega_code.client.skill_enhance_helper.find_session_dir",
            return_value=fake_session_dir,
        ),
        patch(
            "mega_code.client.skill_enhance_helper.load_mapping",
            return_value={project_id: str(real_project)},
        ),
        patch("mega_code.client.skill_enhance_helper._scan_user_installed_skills", return_value=[]),
        patch("mega_code.client.skill_enhance_helper.get_pending_skills", return_value=[]),
        patch("mega_code.client.skill_enhance_helper._scan_archived_skills", return_value=[]),
    ):
        skills = list_skills()

    assert [s["name"] for s in skills] == ["session-mapped-skill"]
    assert skills[0]["path"] == str(skill_dir / "SKILL.md")


def test_scan_archived_skills_uses_current_project_and_recency_order(tmp_path, monkeypatch):
    """Archived skills come from the current project only, most recent run first."""
    current_project = "project-alpha"
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path / current_project))

    feedback_dir = tmp_path / "feedback"
    older_skill = feedback_dir / current_project / "run-1" / "skills" / "older-skill"
    newer_skill = feedback_dir / current_project / "run-2" / "skills" / "newer-skill"
    other_project_skill = feedback_dir / "project-beta" / "run-9" / "skills" / "other-skill"

    for skill_dir, name in [
        (older_skill, "older-skill"),
        (newer_skill, "newer-skill"),
        (other_project_skill, "other-skill"),
    ]:
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(
            f'---\nname: {name}\ndescription: test\nauthor: "co-authored by www.megacode.ai"\n---\n# Skill',
            encoding="utf-8",
        )

    (feedback_dir / current_project / "run-1" / "manifest.json").write_text(
        '{"archived_at": "2026-03-20T10:00:00", "project_id": "project-alpha", "run_id": "run-1"}',
        encoding="utf-8",
    )
    (feedback_dir / current_project / "run-2" / "manifest.json").write_text(
        '{"archived_at": "2026-03-22T10:00:00", "project_id": "project-alpha", "run_id": "run-2"}',
        encoding="utf-8",
    )
    (feedback_dir / "project-beta" / "run-9" / "manifest.json").write_text(
        '{"archived_at": "2026-03-25T10:00:00", "project_id": "project-beta", "run_id": "run-9"}',
        encoding="utf-8",
    )

    with patch("mega_code.client.skill_enhance_helper.FEEDBACK_DIR", feedback_dir):
        skills = _scan_archived_skills(project_id=current_project)

    assert [s["name"] for s in skills] == ["newer-skill", "older-skill"]


def test_list_skills_uses_session_project_id_for_archived_skills_from_plugin_context(
    tmp_path, monkeypatch
):
    """Archived discovery should use the session project id, not one derived from plugin cache."""
    session_id = "session-123"
    real_project = tmp_path / "mega-code-evals"
    real_project.mkdir()
    project_id = get_project_folder_name(str(real_project))
    plugin_cache_dir = (
        tmp_path
        / ".claude"
        / "plugins"
        / "cache"
        / "mind-ai-mega-code"
        / "mega-code"
        / "1.0.3-beta"
    )
    fake_session_dir = tmp_path / "projects" / project_id / session_id
    archived_skill_dir = tmp_path / "feedback" / project_id / "run-1" / "skills" / "archived-skill"

    plugin_cache_dir.mkdir(parents=True)
    fake_session_dir.mkdir(parents=True)
    archived_skill_dir.mkdir(parents=True)
    (archived_skill_dir / "SKILL.md").write_text(
        """---
name: archived-skill
description: archived skill
author: "co-authored by www.megacode.ai"
---
# Skill
""",
        encoding="utf-8",
    )
    (tmp_path / "feedback" / project_id / "run-1" / "manifest.json").write_text(
        json.dumps(
            {
                "archived_at": "2026-03-30T10:00:00",
                "project_id": project_id,
                "run_id": "run-1",
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setenv("CODEX_THREAD_ID", session_id)
    monkeypatch.setenv("CODEX_PROJECT_DIR", str(plugin_cache_dir))
    monkeypatch.chdir(plugin_cache_dir)

    with (
        patch(
            "mega_code.client.skill_enhance_helper.find_session_dir",
            return_value=fake_session_dir,
        ),
        patch(
            "mega_code.client.skill_enhance_helper.load_mapping",
            return_value={project_id: str(real_project)},
        ),
        patch(
            "mega_code.client.skill_enhance_helper._scan_project_installed_skills", return_value=[]
        ),
        patch("mega_code.client.skill_enhance_helper._scan_user_installed_skills", return_value=[]),
        patch("mega_code.client.skill_enhance_helper.get_pending_skills", return_value=[]),
        patch("mega_code.client.skill_enhance_helper.FEEDBACK_DIR", tmp_path / "feedback"),
    ):
        skills = list_skills()

    assert [s["name"] for s in skills] == ["archived-skill"]
    assert skills[0]["state"] == "archived"
    assert skills[0]["path"] == str(archived_skill_dir / "SKILL.md")


def test_list_skills_falls_back_to_all_archived_projects_when_current_project_unresolved():
    """If project resolution fails, archived discovery should fall back to all projects."""
    archived_results = [
        {
            "name": "fallback-archived-skill",
            "description": "test",
            "state": "archived",
            "path": "/tmp/fallback-archived-skill/SKILL.md",
        }
    ]

    with (
        patch(
            "mega_code.client.skill_enhance_helper._scan_project_installed_skills", return_value=[]
        ),
        patch("mega_code.client.skill_enhance_helper._scan_user_installed_skills", return_value=[]),
        patch("mega_code.client.skill_enhance_helper.get_pending_skills", return_value=[]),
        patch("mega_code.client.skill_enhance_helper._current_project_id", return_value=""),
        patch(
            "mega_code.client.skill_enhance_helper._scan_all_archived_skills",
            return_value=archived_results,
        ) as mock_all_archived,
        patch(
            "mega_code.client.skill_enhance_helper._scan_archived_skills"
        ) as mock_project_archived,
    ):
        skills = list_skills()

    assert [s["name"] for s in skills] == ["fallback-archived-skill"]
    mock_all_archived.assert_called_once()
    mock_project_archived.assert_not_called()


def test_resolve_skill_allows_explicit_non_mega_code_skill():
    """Explicit skill resolution should not depend on mega-code-only listing."""
    with (
        patch(
            "mega_code.client.skill_enhance_helper._all_resolvable_skills",
            return_value=[
                {
                    "name": "custom-skill",
                    "description": "test",
                    "state": "installed",
                    "path": "/tmp/custom-skill/SKILL.md",
                }
            ],
        ),
        patch(
            "mega_code.client.skill_enhance_helper._read_skill_md",
            return_value=(
                "custom-skill",
                "---\nauthor: custom\n---\n# Skill",
                "/tmp/custom-skill/SKILL.md",
            ),
        ),
    ):
        result = resolve_skill("custom-skill")

    assert result == (
        "custom-skill",
        "---\nauthor: custom\n---\n# Skill",
        "/tmp/custom-skill/SKILL.md",
    )


def test_resolve_skill_repairs_project_installed_folder_name(tmp_path, monkeypatch):
    project_dir = tmp_path / "project"
    skill_dir = project_dir / ".agents" / "skills" / "legacy-folder-name"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        """\
---
name: canonical-skill-name
description: test
author: "co-authored by www.megacode.ai"
---
# Skill
""",
        encoding="utf-8",
    )

    monkeypatch.setenv("CODEX_PROJECT_DIR", str(project_dir))
    name, _, path = resolve_skill("canonical-skill-name")
    assert name == "canonical-skill-name"
    assert path == str(project_dir / ".agents" / "skills" / "canonical-skill-name" / "SKILL.md")
    assert not skill_dir.exists()


def test_resolve_skill_repairs_user_installed_folder_name(tmp_path):
    user_skills_dir = tmp_path / ".agents" / "skills"
    skill_dir = user_skills_dir / "legacy-folder-name"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        """\
---
name: canonical-skill-name
description: test
author: "co-authored by www.megacode.ai"
---
# Skill
""",
        encoding="utf-8",
    )

    with patch("pathlib.Path.home", return_value=tmp_path):
        name, _, path = resolve_skill("canonical-skill-name")
    assert name == "canonical-skill-name"
    assert path == str(user_skills_dir / "canonical-skill-name" / "SKILL.md")
    assert not skill_dir.exists()


def test_resolve_skill_repairs_pending_folder_name(tmp_path):
    pending_dir = tmp_path / "pending-skills"
    skill_dir = pending_dir / "legacy-folder-name"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        """\
---
name: canonical-skill-name
description: test
metadata:
  author: "co-authored by www.megacode.ai"
---
# Skill
""",
        encoding="utf-8",
    )

    with (
        patch("mega_code.client.skill_enhance_helper.PENDING_SKILLS_DIR", pending_dir),
        patch("mega_code.client.pending.PENDING_SKILLS_DIR", pending_dir),
    ):
        name, _, path = resolve_skill("canonical-skill-name")
    assert name == "canonical-skill-name"
    assert path == str(pending_dir / "canonical-skill-name" / "SKILL.md")
    assert not skill_dir.exists()


def test_resolve_skill_repairs_archived_folder_name(tmp_path, monkeypatch):
    project_dir = tmp_path / "project-a"
    project_dir.mkdir()
    project_id = get_project_folder_name(str(project_dir))
    archived_dir = tmp_path / "feedback" / project_id / "run-1" / "skills" / "legacy-folder-name"
    archived_dir.mkdir(parents=True)
    (archived_dir / "SKILL.md").write_text(
        """\
---
name: canonical-skill-name
description: test
author: "co-authored by www.megacode.ai"
---
# Skill
""",
        encoding="utf-8",
    )
    (tmp_path / "feedback" / project_id / "run-1" / "manifest.json").write_text(
        json.dumps(
            {
                "archived_at": "2026-03-30T10:00:00",
                "project_id": project_id,
                "run_id": "run-1",
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setenv("CODEX_PROJECT_DIR", str(project_dir))
    with patch("mega_code.client.skill_enhance_helper.FEEDBACK_DIR", tmp_path / "feedback"):
        name, _, path = resolve_skill("canonical-skill-name")
    assert name == "canonical-skill-name"
    assert path == str(
        tmp_path
        / "feedback"
        / project_id
        / "run-1"
        / "skills"
        / "canonical-skill-name"
        / "SKILL.md"
    )
    assert not archived_dir.exists()


def test_repair_selected_skill_path_skips_when_canonical_target_exists(tmp_path):
    original_dir = tmp_path / "legacy-folder-name"
    original_dir.mkdir()
    original_path = original_dir / "SKILL.md"
    original_path.write_text(
        """\
---
name: canonical-skill-name
description: test
---
# Skill
""",
        encoding="utf-8",
    )
    target_dir = tmp_path / "canonical-skill-name"
    target_dir.mkdir()
    (target_dir / "SKILL.md").write_text("# Existing", encoding="utf-8")

    name, _, path = _repair_selected_skill_path(
        "legacy-folder-name",
        original_path.read_text(encoding="utf-8"),
        str(original_path),
    )
    assert name == "canonical-skill-name"
    assert path == str(original_path)
    assert original_dir.exists()


# =============================================================================
# accept_enhanced_skill
# =============================================================================

_ORIGINAL_SKILL = """\
---
name: my-test-skill
description: A test skill.
metadata:
  version: "1.0.0"
  author: "co-authored by www.megacode.ai"
---

# My Test Skill

Original content here.
"""

_DRAFT_SKILL = """\
---
name: my-test-skill
description: An improved test skill.
metadata:
  version: "1.0.0"
  author: "co-authored by www.megacode.ai"
---

# My Enhanced Test Skill

Better content here.
"""

_DRAFT_SKILL_WITH_STALE_EVAL_METADATA = """\
---
name: my-test-skill
description: An improved test skill.
metadata:
  version: "1.1.0"
  author: "co-authored by www.megacode.ai"
  eval_version: "1.1.0"
  enhanced_from: "1.0.0"
  roi:
    - model: "gemini-3-flash"
      performance_increase: "0%"
      token_savings: "100%"
---

# My Enhanced Test Skill

Better content here.
"""


class TestAcceptEnhancedSkill:
    def test_backs_up_original(self, tmp_path):
        original = tmp_path / "SKILL.md"
        original.write_text(_ORIGINAL_SKILL, encoding="utf-8")

        draft = tmp_path / "draft-skill.md"
        draft.write_text(_DRAFT_SKILL, encoding="utf-8")

        iter_dir = tmp_path / "iteration-1"
        iter_dir.mkdir()

        accept_enhanced_skill(original, draft, iter_dir, 1)

        backup = iter_dir / "original-skill.md"
        assert backup.exists()
        assert "Original content here" in backup.read_text(encoding="utf-8")

    def test_replaces_original_with_enhanced(self, tmp_path):
        original = tmp_path / "SKILL.md"
        original.write_text(_ORIGINAL_SKILL, encoding="utf-8")

        draft = tmp_path / "draft-skill.md"
        draft.write_text(_DRAFT_SKILL, encoding="utf-8")

        iter_dir = tmp_path / "iteration-1"
        iter_dir.mkdir()

        accept_enhanced_skill(original, draft, iter_dir, 1)

        replaced = original.read_text(encoding="utf-8")
        assert "Better content here" in replaced
        assert "Original content here" not in replaced

    def test_renames_skill_directory_to_match_frontmatter_name(self, tmp_path):
        skill_dir = tmp_path / "very-long-generated-folder-name"
        skill_dir.mkdir()
        original = skill_dir / "SKILL.md"
        original.write_text(_ORIGINAL_SKILL, encoding="utf-8")
        (skill_dir / "metadata.json").write_text("{}", encoding="utf-8")

        draft = tmp_path / "draft-skill.md"
        draft.write_text(_DRAFT_SKILL, encoding="utf-8")

        iter_dir = tmp_path / "iteration-1"
        iter_dir.mkdir()

        with patch("mega_code.client.skill_enhance_helper.PENDING_SKILLS_DIR", tmp_path):
            final_path, _, _ = accept_enhanced_skill(original, draft, iter_dir, 1)

        assert final_path == tmp_path / "my-test-skill" / "SKILL.md"
        assert final_path.exists()
        assert not skill_dir.exists()

    def test_bumps_semantic_version_and_refreshes_generated_at(self, tmp_path):
        original = tmp_path / "SKILL.md"
        original.write_text(_ORIGINAL_SKILL, encoding="utf-8")

        draft = tmp_path / "draft-skill.md"
        draft.write_text(_DRAFT_SKILL, encoding="utf-8")

        iter_dir = tmp_path / "iteration-1"
        iter_dir.mkdir()

        with patch(
            "mega_code.client.skill_enhance_helper.current_timestamp_z",
            return_value="2026-03-26T12:00:00Z",
        ):
            accept_enhanced_skill(original, draft, iter_dir, 1)

        replaced = original.read_text(encoding="utf-8")
        frontmatter = yaml.safe_load(replaced.split("---", 2)[1])
        assert frontmatter["metadata"]["version"] == "1.1.0"
        assert frontmatter["metadata"]["generated_at"] == "2026-03-26T12:00:00Z"
        assert "eval_version" not in frontmatter["metadata"]
        assert "enhanced_from" not in frontmatter["metadata"]

    def test_preserves_plugin_version_and_replaces_stale_eval_roi(self, tmp_path):
        original = tmp_path / "SKILL.md"
        original.write_text(
            """\
---
name: my-test-skill
description: A test skill.
metadata:
  version: "1.0.0"
  author: "co-authored by www.megacode.ai"
  generated_at: "2026-03-20T06:23:15Z"
  roi:
    - model: "gemini-3-flash"
      performance_increase: "0%"
      token_savings: "100%"
---

# My Test Skill

Original content here.
""",
            encoding="utf-8",
        )

        draft = tmp_path / "draft-skill.md"
        draft.write_text(_DRAFT_SKILL_WITH_STALE_EVAL_METADATA, encoding="utf-8")

        iter_dir = tmp_path / "iteration-1"
        iter_dir.mkdir()

        with patch(
            "mega_code.client.skill_enhance_helper.current_timestamp_z",
            return_value="2026-03-26T12:00:00Z",
        ):
            accept_enhanced_skill(
                original,
                draft,
                iter_dir,
                1,
                eval_roi={
                    "model": "claude-opus-4-6",
                    "performance_increase": 0.75,
                    "token_savings": 0.0,
                },
            )

        replaced = original.read_text(encoding="utf-8")
        frontmatter = yaml.safe_load(replaced.split("---", 2)[1])
        assert frontmatter["metadata"]["version"] == "1.1.0"
        assert frontmatter["metadata"]["generated_at"] == "2026-03-26T12:00:00Z"
        assert "eval_version" not in frontmatter["metadata"]
        assert "enhanced_from" not in frontmatter["metadata"]
        assert frontmatter["metadata"]["roi"] == [
            {
                "model": "claude-opus-4-6",
                "performance_increase": "75%",
                "token_savings": "0%",
            }
        ]
        assert "gemini-3-flash" not in replaced

    def test_enhanced_output_normalized_to_new_frontmatter_when_original_is_legacy(self, tmp_path):
        original = tmp_path / "SKILL.md"
        original.write_text(
            """\
---
name: my-test-skill
description: A test skill.
version: "1.0.0"
author: "co-authored by www.megacode.ai"
generated_at: "2026-03-20T06:23:15Z"
roi:
  - model: "gemini-3-flash"
    performance_increase: "0%"
    token_savings: "100%"
---

# My Test Skill
""",
            encoding="utf-8",
        )

        draft = tmp_path / "draft-skill.md"
        draft.write_text(_DRAFT_SKILL, encoding="utf-8")

        iter_dir = tmp_path / "iteration-1"
        iter_dir.mkdir()

        with patch(
            "mega_code.client.skill_enhance_helper.current_timestamp_z",
            return_value="2026-03-26T12:00:00Z",
        ):
            accept_enhanced_skill(original, draft, iter_dir, 1)

        replaced = original.read_text(encoding="utf-8")
        frontmatter = yaml.safe_load(replaced.split("---", 2)[1])
        assert frontmatter["metadata"]["author"] == "co-authored by www.megacode.ai"
        assert frontmatter["metadata"]["version"] == "1.1.0"
        assert frontmatter["metadata"]["generated_at"] == "2026-03-26T12:00:00Z"
        assert "eval_version" not in frontmatter["metadata"]
        assert "enhanced_from" not in frontmatter["metadata"]
        assert "author" not in {k: v for k, v in frontmatter.items() if k != "metadata"}

    def test_author_always_set_to_megacode(self, tmp_path):
        """Author is always overwritten to megacode.ai regardless of original."""
        original = tmp_path / "SKILL.md"
        original.write_text(
            "---\nname: my-test-skill\ndescription: A test skill.\n"
            'metadata:\n  version: "1.0.0"\n  author: "custom-author"\n---\n\nOriginal content.\n',
            encoding="utf-8",
        )
        draft = tmp_path / "draft-skill.md"
        draft.write_text(_DRAFT_SKILL, encoding="utf-8")
        iter_dir = tmp_path / "iteration-1"
        iter_dir.mkdir()

        accept_enhanced_skill(original, draft, iter_dir, 1)

        replaced = original.read_text(encoding="utf-8")
        frontmatter = yaml.safe_load(replaced.split("---", 2)[1])
        assert frontmatter["metadata"]["author"] == "co-authored by www.megacode.ai"

    def test_tags_preserved_from_enhanced_skill(self, tmp_path):
        """When the enhanced skill already has tags, they are preserved."""
        original = tmp_path / "SKILL.md"
        original.write_text(_ORIGINAL_SKILL, encoding="utf-8")
        draft = tmp_path / "draft-skill.md"
        draft.write_text(
            "---\nname: my-test-skill\ndescription: An improved test skill.\n"
            'metadata:\n  version: "1.0.0"\n  author: "co-authored by www.megacode.ai"\n'
            "  tags: [python, testing, automation]\n---\n\nBetter content here.\n",
            encoding="utf-8",
        )
        iter_dir = tmp_path / "iteration-1"
        iter_dir.mkdir()

        accept_enhanced_skill(original, draft, iter_dir, 1)

        replaced = original.read_text(encoding="utf-8")
        frontmatter = yaml.safe_load(replaced.split("---", 2)[1])
        assert frontmatter["metadata"]["tags"] == ["python", "testing", "automation"]

    def test_tags_fallback_to_original_when_enhanced_has_none(self, tmp_path):
        """When enhanced skill has no tags, original skill tags are used."""
        original = tmp_path / "SKILL.md"
        original.write_text(
            "---\nname: my-test-skill\ndescription: A test skill.\n"
            'metadata:\n  version: "1.0.0"\n  author: "co-authored by www.megacode.ai"\n'
            "  tags: [git, devops]\n---\n\nOriginal content.\n",
            encoding="utf-8",
        )
        draft = tmp_path / "draft-skill.md"
        draft.write_text(_DRAFT_SKILL, encoding="utf-8")
        iter_dir = tmp_path / "iteration-1"
        iter_dir.mkdir()

        accept_enhanced_skill(original, draft, iter_dir, 1)

        replaced = original.read_text(encoding="utf-8")
        frontmatter = yaml.safe_load(replaced.split("---", 2)[1])
        assert frontmatter["metadata"]["tags"] == ["git", "devops"]

    def test_updates_iteration_enhanced_artifact_to_match_accepted_frontmatter(self, tmp_path):
        original = tmp_path / "SKILL.md"
        original.write_text(_ORIGINAL_SKILL, encoding="utf-8")

        draft = tmp_path / "draft-skill.md"
        draft.write_text(_DRAFT_SKILL, encoding="utf-8")

        iter_dir = tmp_path / "iteration-1"
        iter_dir.mkdir()

        with patch(
            "mega_code.client.skill_enhance_helper.current_timestamp_z",
            return_value="2026-03-26T12:00:00Z",
        ):
            accept_enhanced_skill(
                original,
                draft,
                iter_dir,
                1,
                eval_roi={
                    "model": "claude-opus-4-6",
                    "performance_increase": 0.81,
                    "token_savings": 0.0,
                },
            )

        artifact_frontmatter = yaml.safe_load(
            (iter_dir / "enhanced-skill.md").read_text(encoding="utf-8").split("---", 2)[1]
        )
        assert artifact_frontmatter["metadata"]["version"] == "1.1.0"
        assert artifact_frontmatter["metadata"]["generated_at"] == "2026-03-26T12:00:00Z"
        assert artifact_frontmatter["metadata"]["roi"] == [
            {
                "model": "claude-opus-4-6",
                "performance_increase": "81%",
                "token_savings": "0%",
            }
        ]

    def test_updates_feedback_skill_metadata_roi_when_present(self, tmp_path):
        feedback_skill_dir = (
            tmp_path / "feedback" / "project-a" / "run-1" / "skills" / "my-test-skill"
        )
        feedback_skill_dir.mkdir(parents=True)
        original = feedback_skill_dir / "SKILL.md"
        original.write_text(_ORIGINAL_SKILL, encoding="utf-8")
        (feedback_skill_dir / "metadata.json").write_text(
            json.dumps(
                {
                    "skill_id": "my-test-skill",
                    "run_id": "run-1",
                    "roi": {
                        "model": "gemini-3-flash",
                        "performance_increase": 0.0,
                        "token_savings": 0.68,
                    },
                }
            ),
            encoding="utf-8",
        )

        draft = tmp_path / "draft-skill.md"
        draft.write_text(_DRAFT_SKILL, encoding="utf-8")

        iter_dir = tmp_path / "iteration-1"
        iter_dir.mkdir()

        with (
            patch("mega_code.client.skill_enhance_helper.FEEDBACK_DIR", tmp_path / "feedback"),
            patch(
                "mega_code.client.skill_enhance_helper.current_timestamp_z",
                return_value="2026-03-26T12:00:00Z",
            ),
        ):
            accept_enhanced_skill(
                original,
                draft,
                iter_dir,
                1,
                eval_roi={
                    "model": "claude-opus-4-6",
                    "performance_increase": 0.81,
                    "token_savings": 0.0,
                    "test_count": 4,
                    "with_skill_avg": 0.81,
                    "baseline_avg": 0.0,
                },
            )

        updated_meta = json.loads(
            (feedback_skill_dir / "metadata.json").read_text(encoding="utf-8")
        )
        assert updated_meta["roi"] == [
            {
                "model": "claude-opus-4-6",
                "performance_increase": "81%",
                "token_savings": "0%",
                "test_count": 4,
                "with_success_rate": 0.81,
                "baseline_success_rate": 0.0,
            }
        ]

    def test_returns_original_path(self, tmp_path):
        original = tmp_path / "SKILL.md"
        original.write_text(_ORIGINAL_SKILL, encoding="utf-8")

        draft = tmp_path / "draft-skill.md"
        draft.write_text(_DRAFT_SKILL, encoding="utf-8")

        iter_dir = tmp_path / "iteration-1"
        iter_dir.mkdir()

        result, old_ver, new_ver = accept_enhanced_skill(original, draft, iter_dir, 1)
        assert result == original
        assert old_ver == "1.0.0"
        assert new_ver == "1.1.0"


# =============================================================================
# store_enhanced_skill_on_server
# =============================================================================


def test_store_enhanced_skips_in_local_mode(monkeypatch, capsys):
    """In local mode, store_enhanced_skill_on_server should not call the server."""
    monkeypatch.setenv("MEGA_CODE_CLIENT_MODE", "local")
    # Should not raise
    store_enhanced_skill_on_server("test-skill", "# content", 1)
    captured = capsys.readouterr()
    assert captured.err == ""  # no longer prints to stderr


def test_store_enhanced_uses_canonical_name(tmp_path, monkeypatch):
    """In remote mode, skill name comes from frontmatter content."""
    monkeypatch.setenv("MEGA_CODE_CLIENT_MODE", "remote")

    mock_client = MagicMock()
    mock_client.enhance_skill.return_value = MagicMock(success=True)

    with (
        patch("mega_code.client.api.create_client", return_value=mock_client),
        patch(
            "mega_code.client.skill_enhance_helper._current_project_id",
            return_value="test-project_abc12345",
        ),
    ):
        store_enhanced_skill_on_server(
            "test-skill",
            """\
---
name: short-skill-name
description: Test
author: "co-authored by www.megacode.ai"
metadata:
  version: "1.1.0"
---

# enhanced content
""",
            1,
        )

    mock_client.enhance_skill.assert_called_once_with(
        skill_name="short-skill-name",
        skill_md="""\
---
name: short-skill-name
description: Test
author: "co-authored by www.megacode.ai"
metadata:
  version: "1.1.0"
---

# enhanced content
""",
        version="1.1.0",
        metadata=None,
        project_id="test-project_abc12345",
        parent_skill_name="test-skill",
    )


def test_accept_enhanced_skill_writes_identity_artifact_before_pending_rename(
    tmp_path, monkeypatch
):
    pending_dir = tmp_path / "pending-skills"
    skill_dir = pending_dir / "legacy-folder-name"
    skill_dir.mkdir(parents=True)
    original = skill_dir / "SKILL.md"
    original.write_text(_ORIGINAL_SKILL, encoding="utf-8")

    draft = tmp_path / "draft-skill.md"
    draft.write_text(
        """\
---
name: canonical-skill-name
description: Enhanced test skill
author: "co-authored by www.megacode.ai"
metadata:
  version: "1.0.0"
---

# Enhanced
""",
        encoding="utf-8",
    )

    iter_dir = tmp_path / "iteration-1"
    iter_dir.mkdir()

    with (
        patch("mega_code.client.skill_enhance_helper.PENDING_SKILLS_DIR", pending_dir),
        patch(
            "mega_code.client.skill_enhance_helper.current_timestamp_z",
            return_value="2026-03-26T12:00:00Z",
        ),
    ):
        result, old_ver, new_ver = accept_enhanced_skill(original, draft, iter_dir, 1)

    identity = json.loads((iter_dir / "skill-identity.json").read_text(encoding="utf-8"))
    assert identity["original_skill_name"] == "legacy-folder-name"
    assert identity["canonical_skill_name"] == "canonical-skill-name"
    assert identity["original_skill_path"] == str(original)
    assert result == skill_dir.with_name("canonical-skill-name") / "SKILL.md"
    assert old_ver == "1.0.0"
    assert new_ver == "1.1.0"


def test_accept_enhanced_skill_renames_feedback_skill_folder(tmp_path):
    feedback_skill_dir = (
        tmp_path / "feedback" / "project-a" / "run-1" / "skills" / "legacy-folder-name"
    )
    feedback_skill_dir.mkdir(parents=True)
    original = feedback_skill_dir / "SKILL.md"
    original.write_text(_ORIGINAL_SKILL, encoding="utf-8")

    draft = tmp_path / "draft-skill.md"
    draft.write_text(
        """\
---
name: canonical-skill-name
description: Enhanced test skill
author: "co-authored by www.megacode.ai"
metadata:
  version: "1.0.0"
---

# Enhanced
""",
        encoding="utf-8",
    )

    iter_dir = tmp_path / "iteration-1"
    iter_dir.mkdir()

    with (
        patch("mega_code.client.skill_enhance_helper.FEEDBACK_DIR", tmp_path / "feedback"),
        patch(
            "mega_code.client.skill_enhance_helper.current_timestamp_z",
            return_value="2026-03-26T12:00:00Z",
        ),
    ):
        result, old_ver, new_ver = accept_enhanced_skill(original, draft, iter_dir, 1)

    renamed_skill_dir = feedback_skill_dir.with_name("canonical-skill-name")
    assert renamed_skill_dir.exists()
    assert not feedback_skill_dir.exists()
    assert result == renamed_skill_dir / "SKILL.md"
    assert old_ver == "1.0.0"
    assert new_ver == "1.1.0"


def test_store_enhanced_sends_correct_api_call(tmp_path, monkeypatch):
    """In remote mode, the enhance_skill API is called with canonical name and version."""
    monkeypatch.setenv("MEGA_CODE_CLIENT_MODE", "remote")
    iteration_dir = tmp_path / "iteration-1"
    iteration_dir.mkdir()

    mock_client = MagicMock()
    mock_client.enhance_skill.return_value = MagicMock(success=True)

    with (
        patch("mega_code.client.api.create_client", return_value=mock_client),
        patch(
            "mega_code.client.skill_enhance_helper._current_project_id",
            return_value="test-project_abc12345",
        ),
    ):
        store_enhanced_skill_on_server(
            "short-skill-name",
            """\
---
name: short-skill-name
description: Test
author: "co-authored by www.megacode.ai"
metadata:
  version: "1.1.0"
---

# enhanced content
""",
            1,
        )

    mock_client.enhance_skill.assert_called_once_with(
        skill_name="short-skill-name",
        skill_md="""\
---
name: short-skill-name
description: Test
author: "co-authored by www.megacode.ai"
metadata:
  version: "1.1.0"
---

# enhanced content
""",
        version="1.1.0",
        metadata=None,
        project_id="test-project_abc12345",
        parent_skill_name="short-skill-name",
    )


def test_store_enhanced_allows_any_author(monkeypatch):
    """Any author's skills can be stored as enhanced skills."""
    monkeypatch.setenv("MEGA_CODE_CLIENT_MODE", "remote")

    mock_client = MagicMock()
    mock_client.enhance_skill.return_value = MagicMock(success=True)

    with patch("mega_code.client.api.create_client", return_value=mock_client):
        store_enhanced_skill_on_server(
            "custom-skill",
            """\
---
name: custom-skill
author: custom-author
metadata:
  version: "1.1.0"
---

# custom content
""",
            1,
        )

    mock_client.enhance_skill.assert_called_once()


def test_accept_skill_cli_prints_success_line(tmp_path, capsys):
    original = tmp_path / "SKILL.md"
    original.write_text(_ORIGINAL_SKILL, encoding="utf-8")

    draft = tmp_path / "draft-skill.md"
    draft.write_text(_DRAFT_SKILL, encoding="utf-8")

    iter_dir = tmp_path / "iteration-1"
    iter_dir.mkdir()

    benchmark = tmp_path / "benchmark.json"
    benchmark.write_text(
        json.dumps(
            {
                "model": "claude-opus-4-6",
                "performance_increase": 0.81,
                "token_savings": 0.28,
                "test_results": [],
                "with_skill_avg": 0.81,
                "baseline_avg": 0.0,
            }
        ),
        encoding="utf-8",
    )

    with (
        patch.object(
            sys,
            "argv",
            [
                "skill_enhance_helper",
                "accept-skill",
                "--skill-path",
                str(original),
                "--draft-path",
                str(draft),
                "--iteration-dir",
                str(iter_dir),
                "--iteration",
                "1",
                "--benchmark",
                str(benchmark),
            ],
        ),
        patch(
            "mega_code.client.skill_enhance_helper.current_timestamp_z",
            return_value="2026-03-26T12:00:00Z",
        ),
    ):
        helper_module.main()

    stdout = capsys.readouterr().out
    assert "SUCCESS: replaced SKILL.md, version 1.0.0 -> 1.1.0" in stdout
    assert str(original) in stdout


def test_store_skill_cli_prefers_iteration_enhanced_artifact(tmp_path, monkeypatch):
    monkeypatch.setenv("MEGA_CODE_CLIENT_MODE", "remote")

    iteration_dir = tmp_path / "iteration-1"
    iteration_dir.mkdir()

    accepted_artifact = iteration_dir / "enhanced-skill.md"
    accepted_artifact.write_text(
        """\
---
name: accepted-skill
description: Accepted artifact
metadata:
  version: "1.1.0"
---

# accepted content
""",
        encoding="utf-8",
    )

    installed_skill = tmp_path / "installed" / "SKILL.md"
    installed_skill.parent.mkdir()
    installed_skill.write_text(
        """\
---
name: installed-skill
description: Installed skill
metadata:
  version: "9.9.9"
---

# installed content
""",
        encoding="utf-8",
    )

    mock_client = MagicMock()
    mock_client.enhance_skill.return_value = MagicMock(success=True)

    with (
        patch.object(
            sys,
            "argv",
            [
                "skill_enhance_helper",
                "store-skill",
                "--skill-name",
                "accepted-skill",
                "--iteration-dir",
                str(iteration_dir),
                "--iteration",
                "1",
                "--skill-path",
                str(installed_skill),
            ],
        ),
        patch("mega_code.client.api.create_client", return_value=mock_client),
    ):
        helper_module.main()

    mock_client.enhance_skill.assert_called_once()
    kwargs = mock_client.enhance_skill.call_args.kwargs
    assert kwargs["skill_name"] == "accepted-skill"
    assert "# accepted content" in kwargs["skill_md"]
    assert "# installed content" not in kwargs["skill_md"]
    assert kwargs["version"] == "1.1.0"


def test_accept_enhanced_skill_logs_clarified_metadata_warning_for_installed_skill(
    tmp_path, caplog
):
    skill_dir = tmp_path / ".claude" / "skills" / "installed-skill"
    skill_dir.mkdir(parents=True)
    original = skill_dir / "SKILL.md"
    original.write_text(_ORIGINAL_SKILL, encoding="utf-8")

    draft = tmp_path / "draft-skill.md"
    draft.write_text(_DRAFT_SKILL, encoding="utf-8")

    iter_dir = tmp_path / "iteration-1"
    iter_dir.mkdir()

    accept_enhanced_skill(
        original,
        draft,
        iter_dir,
        1,
        eval_roi={
            "model": "claude-opus-4-6",
            "performance_increase": 0.81,
            "token_savings": 0.28,
        },
    )

    assert (
        "Skipping metadata.json ROI update — skill dir is outside pending/feedback paths"
        in caplog.text
    )
