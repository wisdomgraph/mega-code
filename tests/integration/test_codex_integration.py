"""Cross-module integration tests for the Codex pipeline (Phase 6)."""

import asyncio
import shutil
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

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


class TestRunPipelineCodexSync:
    """Integration: run_pipeline main() → trigger_pipeline_run → sync_codex_trajectories → upload."""

    def test_full_codex_pipeline_flow(self, tmp_path, monkeypatch):
        """Exercise the complete path from trigger_pipeline_run through codex sync.

        Verifies that when include_codex=True and a matching Codex session exists,
        the trajectory is uploaded with non-empty turns.
        """
        # Set up Codex sessions directory with golden fixture
        codex_base = tmp_path / ".codex" / "sessions"
        codex_base.mkdir(parents=True)
        shutil.copy2(FIXTURES_DIR / "golden_session.jsonl", codex_base / "s1.jsonl")

        # Set up mega-code project data directory
        project_dir = tmp_path / "project-data"
        project_dir.mkdir()

        # Build a mock MegaCodeRemote that records upload calls
        mock_client = MagicMock()
        mock_client.upload_trajectory.return_value = UploadResult(
            status="accepted",
            session_id="fixture-session-001",
            message="ok",
        )

        # Mock trigger_pipeline_run as an async method that does the real sync work
        # but skips the HTTP POST to the server
        uploaded_turn_sets = []

        original_upload = mock_client.upload_trajectory

        def capturing_upload(*, turn_set, project_id):
            uploaded_turn_sets.append(turn_set)
            return original_upload(turn_set=turn_set, project_id=project_id)

        mock_client.upload_trajectory = MagicMock(side_effect=capturing_upload)

        # Monkeypatch CodexSource to use our temp directory
        monkeypatch.setattr(
            "mega_code.client.history.sources.codex.CodexSource",
            lambda *a, **kw: _RealCodexSource(base_path=codex_base),
        )

        # Call sync_codex_trajectories directly (the core of what trigger_pipeline_run does)
        project_cwd = "/home/user/projects/test-project"
        synced = sync_codex_trajectories(
            project_dir=project_dir,
            client=mock_client,
            project_id=project_dir.name,
            project_path=project_cwd,
        )

        # Assertions
        assert synced == 1, "Expected 1 session to be synced"
        assert mock_client.upload_trajectory.call_count == 1
        assert len(uploaded_turn_sets) == 1

        turn_set = uploaded_turn_sets[0]
        assert len(turn_set.turns) > 0, "TurnSet should have non-empty turns"
        assert turn_set.session_id == "fixture-session-001"

    def test_codex_sync_via_trigger_pipeline_run(self, tmp_path, monkeypatch):
        """End-to-end: MegaCodeRemote.trigger_pipeline_run with include_codex=True.

        Tests the async trigger_pipeline_run method which offloads
        sync_codex_trajectories to a thread.
        """
        from mega_code.client.api.remote import MegaCodeRemote

        # Set up Codex sessions
        codex_base = tmp_path / ".codex" / "sessions"
        codex_base.mkdir(parents=True)
        shutil.copy2(FIXTURES_DIR / "golden_session.jsonl", codex_base / "s1.jsonl")

        project_dir = tmp_path / "project-data"
        project_dir.mkdir()

        monkeypatch.setattr(
            "mega_code.client.history.sources.codex.CodexSource",
            lambda *a, **kw: _RealCodexSource(base_path=codex_base),
        )

        # Create a real MegaCodeRemote but mock the HTTP calls
        client = MegaCodeRemote(server_url="http://fake-server:8000", api_key="test-key")

        # Mock upload_trajectory (sync call used by codex_sync)
        client.upload_trajectory = MagicMock(  # type: ignore[assignment]
            return_value=UploadResult(
                status="accepted", session_id="fixture-session-001", message="ok"
            )
        )

        # Mock the async HTTP POST for pipeline trigger
        mock_async_client = AsyncMock()
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "run_id": "test-run-123",
            "status": "queued",
            "message": "Pipeline triggered",
        }
        mock_response.raise_for_status = MagicMock()
        mock_async_client.post.return_value = mock_response

        monkeypatch.setattr(client, "_get_async_client", lambda: mock_async_client)

        # Mock sync_trajectories where it's imported from
        monkeypatch.setattr(
            "mega_code.client.api.sync.sync_trajectories",
            lambda *a, **kw: 0,
        )

        result = asyncio.run(
            client.trigger_pipeline_run(
                project_id=project_dir.name,
                project_path=project_dir,
                include_codex=True,
                include_claude=False,
                project_cwd="/home/user/projects/test-project",
            )
        )

        assert result.run_id == "test-run-123"
        assert result.status == "queued"
        # Verify codex sync actually uploaded
        assert client.upload_trajectory.call_count == 1
        call_kwargs = client.upload_trajectory.call_args
        turn_set = call_kwargs.kwargs.get("turn_set") or call_kwargs[1].get("turn_set")
        assert turn_set is not None
        assert len(turn_set.turns) > 0

    def test_project_cwd_none_resolves_from_mapping(self, tmp_path, monkeypatch):
        """FIX VERIFICATION: when project_cwd is None, the real project path
        is resolved from the mapping file instead of falling back to the
        mega-code data dir.

        Previously this was the bug: codex_match_path fell back to
        str(project_path) (the data dir), which never matched any session cwd.
        """
        import json

        from mega_code.client.api.remote import MegaCodeRemote

        # Set up Codex sessions with a real project cwd
        codex_base = tmp_path / ".codex" / "sessions"
        codex_base.mkdir(parents=True)
        shutil.copy2(FIXTURES_DIR / "golden_session.jsonl", codex_base / "s1.jsonl")

        # The mega-code DATA dir (NOT the project dir)
        folder_name = "test-project_a1b2c3d4"
        data_dir = tmp_path / "mega-code-data" / "projects" / folder_name
        data_dir.mkdir(parents=True)

        # Write mapping file so the fix can resolve the real project path
        mapping_dir = tmp_path / "mega-code-data"
        mapping_file = mapping_dir / "mapping.json"
        mapping_file.write_text(json.dumps({folder_name: "/home/user/projects/test-project"}))
        monkeypatch.setattr(
            "mega_code.client.stats.get_mapping_file",
            lambda: mapping_file,
        )

        monkeypatch.setattr(
            "mega_code.client.history.sources.codex.CodexSource",
            lambda *a, **kw: _RealCodexSource(base_path=codex_base),
        )

        client = MegaCodeRemote(server_url="http://fake-server:8000", api_key="test-key")
        client.upload_trajectory = MagicMock(  # type: ignore[assignment]
            return_value=UploadResult(
                status="accepted", session_id="fixture-session-001", message="ok"
            )
        )

        mock_async_client = AsyncMock()
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "run_id": "test-run-123",
            "status": "queued",
            "message": "",
        }
        mock_response.raise_for_status = MagicMock()
        mock_async_client.post.return_value = mock_response
        monkeypatch.setattr(client, "_get_async_client", lambda: mock_async_client)
        monkeypatch.setattr(
            "mega_code.client.api.sync.sync_trajectories",
            lambda *a, **kw: 0,
        )

        result = asyncio.run(
            client.trigger_pipeline_run(
                project_id=data_dir.name,
                project_path=data_dir,
                include_codex=True,
                include_claude=False,
                project_cwd=None,  # <-- was the bug condition, now resolved via mapping
            )
        )

        assert result.run_id == "test-run-123"
        assert client.upload_trajectory.call_count == 1, (
            "Codex sessions should be uploaded after resolving real project "
            "path from mapping when project_cwd is None"
        )

    def test_project_cwd_empty_string_resolves_from_mapping(self, tmp_path, monkeypatch):
        """FIX VERIFICATION: empty string project_cwd also resolves from mapping."""
        import json

        from mega_code.client.api.remote import MegaCodeRemote

        codex_base = tmp_path / ".codex" / "sessions"
        codex_base.mkdir(parents=True)
        shutil.copy2(FIXTURES_DIR / "golden_session.jsonl", codex_base / "s1.jsonl")

        folder_name = "test-project_a1b2c3d4"
        data_dir = tmp_path / "mega-code-data" / "projects" / folder_name
        data_dir.mkdir(parents=True)

        mapping_dir = tmp_path / "mega-code-data"
        mapping_file = mapping_dir / "mapping.json"
        mapping_file.write_text(json.dumps({folder_name: "/home/user/projects/test-project"}))
        monkeypatch.setattr(
            "mega_code.client.stats.get_mapping_file",
            lambda: mapping_file,
        )

        monkeypatch.setattr(
            "mega_code.client.history.sources.codex.CodexSource",
            lambda *a, **kw: _RealCodexSource(base_path=codex_base),
        )

        client = MegaCodeRemote(server_url="http://fake-server:8000", api_key="test-key")
        client.upload_trajectory = MagicMock(  # type: ignore[assignment]
            return_value=UploadResult(
                status="accepted", session_id="fixture-session-001", message="ok"
            )
        )

        mock_async_client = AsyncMock()
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "run_id": "test-run-123",
            "status": "queued",
            "message": "",
        }
        mock_response.raise_for_status = MagicMock()
        mock_async_client.post.return_value = mock_response
        monkeypatch.setattr(client, "_get_async_client", lambda: mock_async_client)
        monkeypatch.setattr(
            "mega_code.client.api.sync.sync_trajectories",
            lambda *a, **kw: 0,
        )

        result = asyncio.run(
            client.trigger_pipeline_run(
                project_id=data_dir.name,
                project_path=data_dir,
                include_codex=True,
                include_claude=False,
                project_cwd="",  # <-- falsy, resolved via mapping
            )
        )

        assert result.run_id == "test-run-123"
        assert client.upload_trajectory.call_count == 1, (
            "Codex sessions should be uploaded after resolving real project "
            "path from mapping when project_cwd is empty string"
        )
