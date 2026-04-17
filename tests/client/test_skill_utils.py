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
