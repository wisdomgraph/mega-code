"""Tests for ensure_skill_frontmatter email/creator injection."""

from __future__ import annotations

from mega_code.client.skill_utils import ensure_skill_frontmatter, split_frontmatter

_NESTED_METADATA_SKILL = """\
---
name: my-skill
description: A skill.
metadata:
  version: "1.0.0"
  author: "co-authored by www.megacode.ai"
---

# My Skill

Body here.
"""

_FRESH_BODY = """\
# My Skill

Body here.
"""


class TestEnsureSkillFrontmatterEmail:
    """ensure_skill_frontmatter injects email as metadata.creator."""

    def test_nested_metadata_branch_injects_creator(self):
        result = ensure_skill_frontmatter(_NESTED_METADATA_SKILL, "my-skill", email="a@b.com")
        fm, _ = split_frontmatter(result)
        assert fm["metadata"]["creator"] == "a@b.com"

    def test_fresh_build_branch_injects_creator(self):
        result = ensure_skill_frontmatter(_FRESH_BODY, "my-skill", email="a@b.com")
        fm, _ = split_frontmatter(result)
        assert fm["metadata"]["creator"] == "a@b.com"

    def test_no_legacy_metadata_branch_injects_creator(self):
        skill_md = """\
---
name: my-skill
description: A skill.
---

Body.
"""
        result = ensure_skill_frontmatter(skill_md, "my-skill", email="user@example.com")
        fm, _ = split_frontmatter(result)
        assert fm["metadata"]["creator"] == "user@example.com"

    def test_empty_email_omits_creator(self):
        result = ensure_skill_frontmatter(_NESTED_METADATA_SKILL, "my-skill", email="")
        fm, _ = split_frontmatter(result)
        assert "creator" not in fm.get("metadata", {})

    def test_default_email_omits_creator(self):
        result = ensure_skill_frontmatter(_NESTED_METADATA_SKILL, "my-skill")
        fm, _ = split_frontmatter(result)
        assert "creator" not in fm.get("metadata", {})

    def test_idempotent_second_call_is_byte_identical(self):
        first = ensure_skill_frontmatter(_NESTED_METADATA_SKILL, "my-skill", email="a@b.com")
        second = ensure_skill_frontmatter(first, "my-skill", email="a@b.com")
        assert first == second

    def test_existing_creator_not_overwritten(self):
        skill_md = """\
---
name: my-skill
description: A skill.
metadata:
  version: "1.0.0"
  creator: "original@example.com"
---

Body.
"""
        result = ensure_skill_frontmatter(skill_md, "my-skill", email="new@example.com")
        fm, _ = split_frontmatter(result)
        assert fm["metadata"]["creator"] == "original@example.com"

    def test_creator_value_is_yaml_quoted(self):
        result = ensure_skill_frontmatter(
            _NESTED_METADATA_SKILL, "my-skill", email="user@example.com"
        )
        # The @ character should be quoted in the rendered YAML
        assert '"user@example.com"' in result or "'user@example.com'" in result

    def test_legacy_metadata_branch_injects_creator(self):
        skill_md = """\
---
name: my-skill
description: A skill.
author: "co-authored by www.megacode.ai"
version: "1.0.0"
---

Body.
"""
        result = ensure_skill_frontmatter(skill_md, "my-skill", email="legacy@example.com")
        fm, _ = split_frontmatter(result)
        assert fm["metadata"]["creator"] == "legacy@example.com"

    def test_legacy_metadata_branch_preserves_existing_creator(self):
        skill_md = """\
---
name: my-skill
description: A skill.
author: "co-authored by www.megacode.ai"
creator: "original@example.com"
---

Body.
"""
        result = ensure_skill_frontmatter(skill_md, "my-skill", email="new@example.com")
        fm, _ = split_frontmatter(result)
        assert fm["metadata"]["creator"] == "original@example.com"


class TestNormalizeSkillFrontmatterPreservesCreator:
    """normalize_skill_frontmatter (called during enhance) preserves creator."""

    def test_creator_survives_normalize(self):
        from mega_code.client.skill_utils import normalize_skill_frontmatter

        fm = {
            "name": "my-skill",
            "description": "A skill.",
            "metadata": {
                "author": "co-authored by www.megacode.ai",
                "creator": "a@b.com",
                "version": "1.0.0",
            },
        }
        result = normalize_skill_frontmatter(fm)
        assert result["metadata"]["creator"] == "a@b.com"


class TestNormalizePendingSkillAllowedToolsBackfill:
    """normalize_pending_skill_markdown backfills `allowed-tools` (A4)."""

    _SKILL_WITH_FRONTMATTER = "---\nname: my-skill\ndescription: A skill.\n---\n\nBody.\n"
    _SKILL_BODY_ONLY = "# My Skill\n\nBody only, no frontmatter yet.\n"

    def _result(self, **kwargs) -> str:
        from mega_code.client.skill_utils import normalize_pending_skill_markdown

        defaults = {"skill_md": self._SKILL_WITH_FRONTMATTER, "skill_name": "my-skill"}
        defaults.update(kwargs)
        return normalize_pending_skill_markdown(**defaults)

    def _allowed_tools(self, rendered: str) -> object:
        from mega_code.client.skill_utils import split_frontmatter

        fm, _ = split_frontmatter(rendered)
        return fm.get("allowed-tools")

    def test_backfill_from_metadata_string(self):
        result = self._result(
            metadata_json='{"allowed_tools": "Read, Grep, Bash"}',
        )
        assert self._allowed_tools(result) == "Read, Grep, Bash"

    def test_backfill_from_metadata_kebab_key(self):
        result = self._result(
            metadata_json='{"allowed-tools": "Read, Edit"}',
        )
        assert self._allowed_tools(result) == "Read, Edit"

    def test_backfill_from_metadata_list_form(self):
        result = self._result(
            metadata_json='{"allowed_tools": ["Read", "Grep", "Glob"]}',
        )
        assert self._allowed_tools(result) == "Read, Grep, Glob"

    def test_safe_default_when_metadata_absent(self):
        from mega_code.client.skill_utils import DEFAULT_ALLOWED_TOOLS

        result = self._result(metadata_json="{}")
        assert self._allowed_tools(result) == DEFAULT_ALLOWED_TOOLS

    def test_safe_default_when_metadata_empty_string(self):
        from mega_code.client.skill_utils import DEFAULT_ALLOWED_TOOLS

        result = self._result(metadata_json='{"allowed_tools": ""}')
        assert self._allowed_tools(result) == DEFAULT_ALLOWED_TOOLS

    def test_safe_default_when_metadata_empty_list(self):
        from mega_code.client.skill_utils import DEFAULT_ALLOWED_TOOLS

        result = self._result(metadata_json='{"allowed_tools": []}')
        assert self._allowed_tools(result) == DEFAULT_ALLOWED_TOOLS

    def test_idempotent_when_frontmatter_already_has_allowed_tools(self):
        skill_md = (
            '---\nname: my-skill\ndescription: A skill.\nallowed-tools: "Read"\n---\n\nBody.\n'
        )
        # Source metadata tries to override — must be ignored.
        result = self._result(
            skill_md=skill_md,
            metadata_json='{"allowed_tools": "OtherTool"}',
        )
        assert self._allowed_tools(result) == "Read"

    def test_idempotent_on_second_call(self):
        first = self._result(metadata_json='{"allowed_tools": "Read, Edit"}')
        second = self._result(skill_md=first, metadata_json="{}")
        assert self._allowed_tools(second) == "Read, Edit"

    def test_backfill_when_source_has_no_frontmatter(self):
        from mega_code.client.skill_utils import DEFAULT_ALLOWED_TOOLS

        result = self._result(skill_md=self._SKILL_BODY_ONLY, metadata_json="{}")
        assert self._allowed_tools(result) == DEFAULT_ALLOWED_TOOLS

    def test_backfill_when_source_has_no_frontmatter_with_metadata(self):
        result = self._result(
            skill_md=self._SKILL_BODY_ONLY,
            metadata_json='{"allowed_tools": "Read"}',
        )
        assert self._allowed_tools(result) == "Read"

    def test_existing_metadata_block_not_disturbed_by_backfill(self):
        from mega_code.client.skill_utils import split_frontmatter

        skill_md = (
            "---\nname: my-skill\ndescription: A skill.\n"
            'metadata:\n  version: "1.0.0"\n  author: "alice"\n---\n\nBody.\n'
        )
        result = self._result(skill_md=skill_md, metadata_json="{}")
        fm, _ = split_frontmatter(result)
        assert fm["metadata"]["author"] == "alice"
        assert fm["metadata"]["version"] == "1.0.0"
