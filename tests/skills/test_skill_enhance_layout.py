"""Validate the skill-enhance / skill-enhance-hitl directory split.

Locks in the post-rename layout so the unified `/mega-code:skill-enhance`
command (remote default, `--hitl` opt-in) cannot regress:

- `skills/skill-enhance/SKILL.md` exists and is `disable-model-invocation: true`.
- `skills/skill-enhance-hitl/SKILL.md` exists and is both
  `disable-model-invocation: true` and `user-invocable: false`.
- The old `skills/enhance-skill/` directory is gone.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from mega_code.client.skill_utils import parse_frontmatter

REPO_ROOT = Path(__file__).resolve().parents[2]
SKILLS_DIR = REPO_ROOT / "skills"
SKILL_ENHANCE_MD = SKILLS_DIR / "skill-enhance" / "SKILL.md"


def _frontmatter(skill_md: Path) -> dict:
    return parse_frontmatter(skill_md.read_text())


def test_skill_enhance_is_remote_default() -> None:
    assert SKILL_ENHANCE_MD.exists(), f"{SKILL_ENHANCE_MD} not found"
    fm = _frontmatter(SKILL_ENHANCE_MD)
    assert fm.get("disable-model-invocation") is True
    # Description must signal that the default flow is remote and that
    # --hitl is the opt-in for the local human-in-the-loop flow — a
    # regression to local-default would otherwise pass the layout test.
    description = fm.get("description", "")
    assert "remote" in description.lower()
    assert "--hitl" in description


def test_skill_enhance_hitl_is_internal() -> None:
    skill_md = SKILLS_DIR / "skill-enhance-hitl" / "SKILL.md"
    assert skill_md.exists(), f"{skill_md} not found"
    fm = _frontmatter(skill_md)
    assert fm.get("disable-model-invocation") is True
    assert fm.get("user-invocable") is False


def test_old_enhance_skill_dir_removed() -> None:
    assert not (SKILLS_DIR / "enhance-skill").exists()


@pytest.mark.parametrize("flag", ["--hitl", "--HITL", "--Hitl"])
def test_phase0_dispatch_block_documents_case_insensitive_flag(flag: str) -> None:
    text = SKILL_ENHANCE_MD.read_text()
    assert "Phase 0" in text
    # Each casing must appear verbatim in the dispatch documentation so
    # users searching for their exact invocation find a hit.
    assert flag in text
