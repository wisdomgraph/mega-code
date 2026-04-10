"""Direct CLI tests for mega_code.client.skill_enhance_aggregator."""

from __future__ import annotations

import json
import sys
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from mega_code.client import skill_enhance_aggregator


def test_main_exits_when_eval_data_missing(tmp_path, capsys):
    with patch.object(
        sys,
        "argv",
        [
            "skill_enhance_aggregator",
            "--eval-data",
            str(tmp_path / "missing.json"),
        ],
    ):
        with pytest.raises(SystemExit) as exc:
            skill_enhance_aggregator.main()

    assert exc.value.code == 1
    assert "Eval data file not found" in capsys.readouterr().err


def test_main_exits_on_mismatched_lengths(tmp_path, capsys):
    eval_data_path = tmp_path / "eval-full.json"
    eval_data_path.write_text(
        json.dumps(
            {
                "skill_name": "test-skill",
                "model": "host-agent",
                "test_cases": [{"task": "T1", "expectations": [{"text": "E1"}]}],
                "ab_outputs": [],
                "gradings": [{}],
            }
        ),
        encoding="utf-8",
    )

    with patch.object(
        sys,
        "argv",
        [
            "skill_enhance_aggregator",
            "--eval-data",
            str(eval_data_path),
        ],
    ):
        with pytest.raises(SystemExit) as exc:
            skill_enhance_aggregator.main()

    assert exc.value.code == 1
    assert "Mismatched lengths" in capsys.readouterr().err


def test_main_writes_benchmark_json(tmp_path, capsys):
    eval_data_path = tmp_path / "eval-full.json"
    eval_data_path.write_text(
        json.dumps(
            {
                "skill_name": "test-skill",
                "model": "host-agent",
                "test_cases": [{"task": "T1", "expectations": [{"text": "E1"}]}],
                "ab_outputs": [{"with_skill_output": "with", "baseline_output": "base"}],
                "gradings": [{"with_skill_gradings": [], "baseline_gradings": []}],
            }
        ),
        encoding="utf-8",
    )
    iteration_dir = tmp_path / "iteration-1"

    result_payload = {
        "skill_name": "test-skill",
        "model": "host-agent",
        "test_results": [],
        "performance_increase": 0.25,
        "token_savings": 0.5,
        "verdict": "BENEFICIAL",
    }
    fake_result = SimpleNamespace(model_dump=lambda mode="json": result_payload)

    with (
        patch.object(
            sys,
            "argv",
            [
                "skill_enhance_aggregator",
                "--eval-data",
                str(eval_data_path),
                "--iteration-dir",
                str(iteration_dir),
            ],
        ),
        patch(
            "mega_code.client.skill_enhance_models.aggregate_eval_result",
            return_value=fake_result,
        ) as mock_aggregate,
    ):
        skill_enhance_aggregator.main()

    benchmark_path = iteration_dir / "benchmark.json"
    assert benchmark_path.exists()
    assert json.loads(benchmark_path.read_text(encoding="utf-8")) == result_payload
    stdout = capsys.readouterr().out
    assert "Evaluating skill: test-skill" in stdout
    assert "Benchmark saved to:" in stdout
    assert mock_aggregate.called
