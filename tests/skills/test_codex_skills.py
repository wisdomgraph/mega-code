"""Unified skill tests: verify skills/ serves both Claude Code and Codex CLI."""

import os
import subprocess
import time
from pathlib import Path

import pytest
import yaml

SKILLS_DIR = Path(__file__).parent.parent.parent / "skills"
SKILL_NAMES = [
    "login",
    "run",
    "status",
    "profile",
    "help",
]


# ── Cycle 1 ───────────────────────────────────────────────────────────


def test_all_five_skills_exist():
    for name in SKILL_NAMES:
        path = SKILLS_DIR / name / "SKILL.md"
        assert path.is_file(), f"Missing: {path}"


# ── Cycle 2 ───────────────────────────────────────────────────────────


@pytest.mark.parametrize("skill_name", SKILL_NAMES)
def test_unified_skill_frontmatter_valid(skill_name):
    path = SKILLS_DIR / skill_name / "SKILL.md"
    content = path.read_text()
    parts = content.split("---", 2)
    assert len(parts) >= 3, "Missing YAML frontmatter"
    fm = yaml.safe_load(parts[1])
    # Must have both Claude Code and Codex required fields
    assert "name" in fm, f"{skill_name}: missing 'name' (required for Codex)"
    assert "description" in fm, f"{skill_name}: missing 'description'"
    assert len(fm["description"]) > 0
    assert "allowed-tools" in fm, (
        f"{skill_name}: missing 'allowed-tools' (required for Claude Code)"
    )


# ── Cycle 3 ───────────────────────────────────────────────────────────


@pytest.mark.parametrize("skill_name", SKILL_NAMES)
def test_unified_setup_block(skill_name):
    """Skills that call uv run must use the unified MEGA_DIR setup with fallback."""
    path = SKILLS_DIR / skill_name / "SKILL.md"
    content = path.read_text()
    if "uv run" not in content:
        pytest.skip(f"{skill_name} does not call uv run")
    assert "MEGA_CODE_PLUGIN_ROOT" in content, f"{skill_name}: missing MEGA_CODE_PLUGIN_ROOT"
    assert "pkg-breadcrumb" in content, f"{skill_name}: missing pkg-breadcrumb fallback"
    assert "codex-bootstrap.sh" in content, f"{skill_name}: missing codex-bootstrap.sh"


# ── Cycle 4 ───────────────────────────────────────────────────────────


@pytest.mark.parametrize("skill_name", SKILL_NAMES)
def test_module_entry_points(skill_name):
    """Skills should use python -m mega_code.client.* entry points."""
    path = SKILLS_DIR / skill_name / "SKILL.md"
    content = path.read_text()
    if "uv run" in content and "python" in content:
        assert "scripts/run_pipeline_async.py" not in content, (
            f"{skill_name}: should use module entry point, not script"
        )
        assert "scripts/check_pending_skills.py" not in content, (
            f"{skill_name}: should use module entry point, not script"
        )


# ── Cycle 5 ───────────────────────────────────────────────────────────


def test_run_skill_includes_codex_flag():
    content = (SKILLS_DIR / "run" / "SKILL.md").read_text()
    assert "--include-codex" in content
    assert "--include-all" in content


def test_help_skill_shows_both_syntaxes():
    content = (SKILLS_DIR / "help" / "SKILL.md").read_text()
    assert "$mega-code-login" in content
    assert "$mega-code-run" in content
    assert "$mega-code-status" in content
    assert "/mega-code:login" in content
    assert "/mega-code:run" in content


# ── Cycle 6 ───────────────────────────────────────────────────────────


def test_bootstrap_script_exists_and_executable():
    script = Path(__file__).parent.parent.parent / "scripts" / "codex-bootstrap.sh"
    assert script.is_file()
    assert os.access(script, os.X_OK)


# ── Cycle 7 ───────────────────────────────────────────────────────────


def test_bootstrap_creates_artifacts(tmp_path):
    script = Path(__file__).parent.parent.parent / "scripts" / "codex-bootstrap.sh"
    data_dir = tmp_path / "data"
    mega_dir = tmp_path / "mega"
    mega_dir.mkdir()
    (mega_dir / "pyproject.toml").write_text(
        '[project]\nname="t"\nversion="0.1"\nrequires-python=">=3.10"\n'
    )
    env = os.environ.copy()
    env["MEGA_CODE_DATA_DIR"] = str(data_dir)
    env["HOME"] = str(tmp_path / "home")
    (tmp_path / "home").mkdir()

    subprocess.run(["bash", str(script), str(mega_dir)], env=env, timeout=60)

    assert (data_dir / ".env").is_file()
    assert oct((data_dir / ".env").stat().st_mode)[-3:] == "600"
    assert (data_dir / "profile.json").read_text() == "{}"
    assert (data_dir / "pkg-breadcrumb").read_text().strip() == str(mega_dir)


# ── Cycle 8 ───────────────────────────────────────────────────────────


def test_bootstrap_idempotent(tmp_path):
    script = Path(__file__).parent.parent.parent / "scripts" / "codex-bootstrap.sh"
    data_dir = tmp_path / "data"
    mega_dir = tmp_path / "mega"
    mega_dir.mkdir()
    (mega_dir / "pyproject.toml").write_text(
        '[project]\nname="t"\nversion="0.1"\nrequires-python=">=3.10"\n'
    )
    env = os.environ.copy()
    env["MEGA_CODE_DATA_DIR"] = str(data_dir)
    env["HOME"] = str(tmp_path / "home")
    (tmp_path / "home").mkdir()

    # First run
    subprocess.run(["bash", str(script), str(mega_dir)], env=env, timeout=60)
    assert (data_dir / "pkg-breadcrumb").read_text().strip() == str(mega_dir)

    # Second run — should be fast
    start = time.monotonic()
    r = subprocess.run(
        ["bash", str(script), str(mega_dir)], env=env, timeout=5, capture_output=True
    )
    assert r.returncode == 0
    assert time.monotonic() - start < 2.0
