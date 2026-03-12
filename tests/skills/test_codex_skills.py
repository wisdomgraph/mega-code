"""Phase 4: Codex skill adaptation tests."""

import os
import subprocess
import time
from pathlib import Path

import pytest
import yaml

CODEX_SKILLS_DIR = Path(__file__).parent.parent.parent / "codex-skills"
SKILL_NAMES = [
    "mega-code-login",
    "mega-code-run",
    "mega-code-status",
    "mega-code-profile",
    "mega-code-help",
]

FORBIDDEN_STRINGS = [
    "CLAUDE_PLUGIN_ROOT",
    "CLAUDE_PROJECT_DIR",
    "/mega-code:",
    "/plugin marketplace",
    ".claude/skills/",
    ".claude/rules/",
]


# ── Cycle 1 ───────────────────────────────────────────────────────────


def test_all_five_skills_exist():
    for name in SKILL_NAMES:
        path = CODEX_SKILLS_DIR / name / "SKILL.md"
        assert path.is_file(), f"Missing: {path}"


# ── Cycle 2 ───────────────────────────────────────────────────────────


@pytest.mark.parametrize("skill_name", SKILL_NAMES)
def test_codex_skill_frontmatter_valid(skill_name):
    path = CODEX_SKILLS_DIR / skill_name / "SKILL.md"
    content = path.read_text()
    parts = content.split("---", 2)
    assert len(parts) >= 3, "Missing YAML frontmatter"
    fm = yaml.safe_load(parts[1])
    assert "description" in fm
    assert len(fm["description"]) > 0
    for forbidden in ("allowed-tools", "argument-hint", "disable-model-invocation"):
        assert forbidden not in fm


# ── Cycle 3 ───────────────────────────────────────────────────────────


@pytest.mark.parametrize("skill_name", SKILL_NAMES)
def test_no_claude_code_references(skill_name):
    path = CODEX_SKILLS_DIR / skill_name / "SKILL.md"
    content = path.read_text()
    for forbidden in FORBIDDEN_STRINGS:
        assert forbidden not in content, f"Found Claude ref: {forbidden}"


# ── Cycle 4 ───────────────────────────────────────────────────────────


@pytest.mark.parametrize("skill_name", SKILL_NAMES)
def test_codex_invocation_syntax(skill_name):
    path = CODEX_SKILLS_DIR / skill_name / "SKILL.md"
    content = path.read_text()
    assert "/mega-code:" not in content


def test_help_skill_uses_dollar_prefix():
    content = (CODEX_SKILLS_DIR / "mega-code-help" / "SKILL.md").read_text()
    assert "$mega-code-login" in content
    assert "$mega-code-run" in content
    assert "$mega-code-status" in content


# ── Cycle 5 ───────────────────────────────────────────────────────────


def test_run_skill_includes_codex_flag():
    content = (CODEX_SKILLS_DIR / "mega-code-run" / "SKILL.md").read_text()
    assert "--include-codex" in content
    # Flag should appear in the bash command template
    lines = content.split("\n")
    cmd_lines = [line for line in lines if "run_pipeline_async.py" in line]
    assert any("--include-codex" in line for line in cmd_lines)


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
    assert (data_dir / "plugin-root").read_text().strip() == str(mega_dir)
    assert (data_dir / "codex-initialized").is_file()


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
    assert (data_dir / "codex-initialized").is_file()

    # Second run — should be fast
    start = time.monotonic()
    r = subprocess.run(
        ["bash", str(script), str(mega_dir)], env=env, timeout=5, capture_output=True
    )
    assert r.returncode == 0
    assert time.monotonic() - start < 2.0
