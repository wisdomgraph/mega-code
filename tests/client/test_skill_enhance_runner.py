"""Direct CLI tests for mega_code.client.skill_enhance_runner."""

from __future__ import annotations

import json
import sys
from unittest.mock import patch

import pytest

from mega_code.client import skill_enhance_runner


def test_main_exits_when_test_cases_file_missing(tmp_path, capsys):
    skill_path = tmp_path / "SKILL.md"
    skill_path.write_text("# Skill", encoding="utf-8")

    with patch.object(
        sys,
        "argv",
        [
            "skill_enhance_runner",
            "--test-cases",
            str(tmp_path / "missing.json"),
            "--skill-md",
            str(skill_path),
        ],
    ):
        with pytest.raises(SystemExit) as exc:
            skill_enhance_runner.main()

    assert exc.value.code == 1
    assert "Test cases file not found" in capsys.readouterr().err


def test_main_exits_when_skill_md_missing(tmp_path, capsys):
    test_cases_path = tmp_path / "test-cases.json"
    test_cases_path.write_text(json.dumps({"cases": [{"task": "T"}]}), encoding="utf-8")

    with patch.object(
        sys,
        "argv",
        [
            "skill_enhance_runner",
            "--test-cases",
            str(test_cases_path),
            "--skill-md",
            str(tmp_path / "missing-skill"),
        ],
    ):
        with pytest.raises(SystemExit) as exc:
            skill_enhance_runner.main()

    assert exc.value.code == 1
    assert "SKILL.md not found" in capsys.readouterr().err


def test_main_writes_output_file_from_skill_directory(tmp_path, capsys):
    test_cases_path = tmp_path / "test-cases.json"
    test_cases_path.write_text(
        json.dumps({"cases": [{"task": "T1", "expectations": ["E1"]}]}),
        encoding="utf-8",
    )
    skill_dir = tmp_path / "my-skill"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text("# Skill", encoding="utf-8")
    output_path = tmp_path / "ab-results.json"

    fake_results = [
        {
            "task": "T1",
            "expectations": ["E1"],
            "with_skill_output": "with",
            "baseline_output": "base",
            "with_skill_model": "gpt-5.4",
            "baseline_model": "gpt-5.4",
            "with_skill_tokens": 10,
            "baseline_tokens": 12,
        }
    ]

    async def _fake_run_ab(test_cases, skill_md, agent=None):
        assert test_cases == [{"task": "T1", "expectations": ["E1"]}]
        assert skill_md == "# Skill"
        assert agent is None
        return fake_results

    with (
        patch.object(
            sys,
            "argv",
            [
                "skill_enhance_runner",
                "--test-cases",
                str(test_cases_path),
                "--skill-md",
                str(skill_dir),
                "--output",
                str(output_path),
            ],
        ),
        patch(
            "mega_code.client.skill_enhance_runner._run_ab", side_effect=_fake_run_ab
        ) as mock_run_ab,
    ):
        skill_enhance_runner.main()

    written = json.loads(output_path.read_text(encoding="utf-8"))
    assert written == fake_results
    assert "Results written to" in capsys.readouterr().err
    assert mock_run_ab.called
