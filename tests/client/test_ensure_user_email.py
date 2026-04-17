"""Tests for mega_code.client.ensure_user_email CLI module."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from mega_code.client.ensure_user_email import (
    _apply_all_pending,
    _apply_to,
    _load_cached,
    _resolve_and_apply,
    _save_cached,
    _set_from_env,
    _try_resolve_from_profile,
    main,
)


@pytest.fixture()
def env_dir(tmp_path, monkeypatch):
    """Redirect get_env_path to a temp .env file."""
    env_path = tmp_path / ".env"
    monkeypatch.setattr("mega_code.client.ensure_user_email.get_env_path", lambda: env_path)
    return env_path


@pytest.fixture()
def pending_dir(tmp_path, monkeypatch):
    """Create a temp pending-skills directory and monkeypatch it."""
    pdir = tmp_path / "pending-skills"
    pdir.mkdir()
    monkeypatch.setattr(
        "mega_code.client.ensure_user_email._iter_pending_skill_files",
        lambda: _real_iter(pdir),
    )
    return pdir


def _real_iter(pdir: Path) -> list[Path]:
    if not pdir.exists():
        return []
    return [d / "SKILL.md" for d in pdir.iterdir() if d.is_dir() and (d / "SKILL.md").is_file()]


def _make_pending_skill(pending_dir: Path, name: str) -> Path:
    """Create a minimal pending skill."""
    skill_dir = pending_dir / name
    skill_dir.mkdir()
    skill_md = skill_dir / "SKILL.md"
    skill_md.write_text(
        f"""\
---
name: {name}
description: A test skill.
metadata:
  version: "1.0.0"
  author: "co-authored by www.megacode.ai"
---

# Test Skill
""",
        encoding="utf-8",
    )
    return skill_md


class TestLoadSaveCached:
    def test_round_trip(self, env_dir):
        _save_cached("test@example.com")
        assert _load_cached() == "test@example.com"

    def test_empty_when_no_file(self, env_dir):
        assert _load_cached() == ""


class TestResolveAndApply:
    def test_empty_pending_dir_returns_zero(self, env_dir, monkeypatch):
        monkeypatch.setattr(
            "mega_code.client.ensure_user_email._iter_pending_skill_files",
            list,
        )
        assert _resolve_and_apply(non_interactive=False) == 0

    def test_cached_email_applies(self, env_dir, pending_dir):
        _save_cached("cached@example.com")
        skill_md = _make_pending_skill(pending_dir, "test-skill")
        assert _resolve_and_apply(non_interactive=False) == 0
        content = skill_md.read_text(encoding="utf-8")
        assert "cached@example.com" in content

    def test_profile_resolves_and_caches(self, env_dir, pending_dir, monkeypatch):
        """When no cached email exists, _try_resolve_from_profile is called and its result is applied."""
        skill_md = _make_pending_skill(pending_dir, "test-skill")

        monkeypatch.setattr(
            "mega_code.client.ensure_user_email._try_resolve_from_profile",
            lambda: "profile@example.com",
        )

        assert _resolve_and_apply(non_interactive=False) == 0
        content = skill_md.read_text(encoding="utf-8")
        assert "profile@example.com" in content

    def test_no_email_returns_exit_2(self, env_dir, pending_dir, monkeypatch):
        _make_pending_skill(pending_dir, "test-skill")
        monkeypatch.setattr(
            "mega_code.client.ensure_user_email._try_resolve_from_profile",
            lambda: "",
        )
        assert _resolve_and_apply(non_interactive=False) == 2

    def test_non_interactive_returns_zero_on_no_email(self, env_dir, pending_dir, monkeypatch):
        skill_md = _make_pending_skill(pending_dir, "test-skill")
        monkeypatch.setattr(
            "mega_code.client.ensure_user_email._try_resolve_from_profile",
            lambda: "",
        )
        assert _resolve_and_apply(non_interactive=True) == 0
        content = skill_md.read_text(encoding="utf-8")
        assert "creator" not in content


class TestTryResolveFromProfile:
    def test_returns_empty_on_exception(self, env_dir, monkeypatch):
        """When the API call raises, return '' and log the error."""
        monkeypatch.setattr(
            "mega_code.client.api.create_client",
            MagicMock(side_effect=RuntimeError("connection refused")),
        )
        assert _try_resolve_from_profile() == ""
        assert _load_cached() == ""


class TestSetFromEnv:
    def test_valid_email(self, env_dir, monkeypatch):
        monkeypatch.setenv("MEGA_CODE_EMAIL_INPUT", "valid@example.com")
        assert _set_from_env() == 0
        assert _load_cached() == "valid@example.com"

    def test_invalid_email_no_at(self, env_dir, monkeypatch):
        monkeypatch.setenv("MEGA_CODE_EMAIL_INPUT", "invalid-email")
        assert _set_from_env() == 1

    def test_invalid_email_whitespace(self, env_dir, monkeypatch):
        monkeypatch.setenv("MEGA_CODE_EMAIL_INPUT", "has space@example.com")
        assert _set_from_env() == 1

    def test_empty_email(self, env_dir, monkeypatch):
        monkeypatch.setenv("MEGA_CODE_EMAIL_INPUT", "")
        assert _set_from_env() == 1


class TestApplyAllPending:
    def test_no_cache_returns_1(self, env_dir, pending_dir):
        _make_pending_skill(pending_dir, "test-skill")
        assert _apply_all_pending() == 1

    def test_with_cache_applies(self, env_dir, pending_dir):
        _save_cached("cached@example.com")
        skill_md = _make_pending_skill(pending_dir, "test-skill")
        assert _apply_all_pending() == 0
        content = skill_md.read_text(encoding="utf-8")
        assert "cached@example.com" in content

    def test_no_pending_skills(self, env_dir, monkeypatch):
        _save_cached("cached@example.com")
        monkeypatch.setattr(
            "mega_code.client.ensure_user_email._iter_pending_skill_files",
            list,
        )
        assert _apply_all_pending() == 0


class TestApplyTo:
    def test_idempotent(self, tmp_path):
        skill_dir = tmp_path / "test-skill"
        skill_dir.mkdir()
        skill_md = skill_dir / "SKILL.md"
        skill_md.write_text(
            """\
---
name: test-skill
description: A skill.
metadata:
  version: "1.0.0"
---

Body.
""",
            encoding="utf-8",
        )
        _apply_to([skill_md], "a@b.com")
        first = skill_md.read_text(encoding="utf-8")
        _apply_to([skill_md], "a@b.com")
        second = skill_md.read_text(encoding="utf-8")
        assert first == second
        assert "a@b.com" in first


class TestMain:
    def test_resolve_and_apply_flag(self, env_dir, monkeypatch):
        monkeypatch.setattr(
            "mega_code.client.ensure_user_email._iter_pending_skill_files",
            list,
        )
        monkeypatch.setattr("sys.argv", ["prog", "--resolve-and-apply"])
        assert main() == 0

    def test_show_flag(self, env_dir, monkeypatch):
        monkeypatch.setattr("sys.argv", ["prog", "--show"])
        assert main() == 0
