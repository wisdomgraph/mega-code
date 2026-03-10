"""Cross-module integration tests for the Codex pipeline (Phase 6)."""

import os
import shutil
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

from mega_code.client.api.codex_sync import sync_codex_trajectories
from mega_code.client.api.protocol import UploadResult
from mega_code.client.history.loader import load_sessions_from_project
from mega_code.client.history.sources.codex import CodexSource
from mega_code.client.history.sources.codex import CodexSource as _RealCodexSource

FIXTURES_DIR = Path(__file__).parent.parent / "fixtures" / "codex"


class TestParserToLoaderIntegration:
    """Cycle 4: Parsed Codex session flows through the loader."""

    def test_parser_to_loader_integration(self, codex_base):
        """Golden session parsed by CodexSource flows through loader."""
        shutil.copy2(FIXTURES_DIR / "golden_session.jsonl", codex_base / "s1.jsonl")

        # Verify CodexSource parses independently
        source = CodexSource(base_path=codex_base)
        sessions = list(source.list_sessions())
        assert len(sessions) >= 1

        # Verify it flows through the loader
        with (
            patch("mega_code.client.history.loader.MegaCodeSource") as MockMegaSource,
            patch(
                "mega_code.client.history.sources.codex.CodexSource",
                return_value=CodexSource(base_path=codex_base),
            ),
        ):
            mock_source = MockMegaSource.return_value
            mock_source.iter_sessions_from_path.return_value = iter([])

            result = load_sessions_from_project(
                project_path=Path("/home/user/projects/test-project"),
                include_codex=True,
            )

        codex_sessions = [s for s in result if s.metadata.source == "codex_cli"]
        assert len(codex_sessions) == 1
        assert len(codex_sessions[0].messages) > 0


class TestParserToSyncIntegration:
    """Cycle 5: Parsed session flows through sync with correct upload payload."""

    def test_parser_to_sync_integration(self, tmp_path, monkeypatch):
        """Parsed session flows through sync with correct upload payload."""
        codex_base = tmp_path / ".codex" / "sessions"
        codex_base.mkdir(parents=True)
        shutil.copy2(FIXTURES_DIR / "golden_session.jsonl", codex_base / "s1.jsonl")

        project_dir = tmp_path / "project-data"
        project_dir.mkdir()

        client = MagicMock()
        client.upload_trajectory.return_value = UploadResult(
            status="accepted",
            session_id="test",
            message="ok",
        )

        monkeypatch.setattr(
            "mega_code.client.history.sources.codex.CodexSource",
            lambda *a, **kw: _RealCodexSource(base_path=codex_base),
        )

        result = sync_codex_trajectories(
            project_dir=project_dir,
            client=client,
            project_id="test-project",
            project_path="/home/user/projects/test-project",
        )

        assert result == 1
        assert client.upload_trajectory.call_count == 1
        # Verify payload has actual turn data
        call_kwargs = client.upload_trajectory.call_args
        turn_set = call_kwargs.kwargs.get("turn_set") or call_kwargs[1].get("turn_set")
        assert turn_set is not None
        assert len(turn_set.turns) > 0


class TestCliContractHelp:
    """Cycle 6: CLI --help includes Codex flags."""

    def test_cli_help_includes_include_codex(self):
        script = Path(__file__).parent.parent.parent / "scripts" / "run_pipeline_async.py"
        result = subprocess.run(
            [sys.executable, str(script), "--help"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert result.returncode == 0
        assert "--include-codex" in result.stdout

    def test_cli_help_includes_include_all(self):
        script = Path(__file__).parent.parent.parent / "scripts" / "run_pipeline_async.py"
        result = subprocess.run(
            [sys.executable, str(script), "--help"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert result.returncode == 0
        assert "--include-all" in result.stdout


class TestCliContractPayload:
    """Cycle 7: CLI accepts Codex flags without argparse error."""

    def test_include_codex_accepted_by_argparse(self):
        """--include-codex is accepted without argparse error."""
        script = Path(__file__).parent.parent.parent / "scripts" / "run_pipeline_async.py"
        result = subprocess.run(
            [sys.executable, str(script), "--include-codex", "--env-debug"],
            capture_output=True,
            text=True,
            timeout=30,
            env={**os.environ, "CLAUDE_PROJECT_DIR": "/tmp/fake"},
        )
        assert result.returncode == 0

    def test_include_all_accepted_by_argparse(self):
        """--include-all is accepted without argparse error."""
        script = Path(__file__).parent.parent.parent / "scripts" / "run_pipeline_async.py"
        result = subprocess.run(
            [sys.executable, str(script), "--include-all", "--env-debug"],
            capture_output=True,
            text=True,
            timeout=30,
            env={**os.environ, "CLAUDE_PROJECT_DIR": "/tmp/fake"},
        )
        assert result.returncode == 0
