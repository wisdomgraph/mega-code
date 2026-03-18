"""Tests for Codex sync ledger (Phase 3)."""

import json
import os
import shutil
import time
from pathlib import Path
from unittest.mock import MagicMock

from mega_code.client.api.codex_sync import sync_codex_trajectories
from mega_code.client.api.protocol import UploadResult

FIXTURES_DIR = Path(__file__).parent.parent / "fixtures" / "codex"


def _mock_client() -> MagicMock:
    """Create a mock MegaCodeBaseClient."""
    client = MagicMock()
    client.upload_trajectory.return_value = UploadResult(
        status="accepted",
        session_id="test",
        message="ok",
    )
    return client


def _setup_codex_base(tmp_path: Path, session_files: list[tuple[str, str]]) -> Path:
    """Set up a .codex/sessions directory with session files.

    Args:
        tmp_path: pytest tmp_path
        session_files: list of (filename, fixture_name) pairs
    """
    codex_base = tmp_path / ".codex" / "sessions"
    codex_base.mkdir(parents=True)
    for filename, fixture_name in session_files:
        shutil.copy2(FIXTURES_DIR / fixture_name, codex_base / filename)
    return codex_base


class TestFirstSyncUploadsAll:
    def test_first_sync_uploads_all(self, tmp_path, monkeypatch):
        """First sync should upload all matching sessions."""
        codex_base = _setup_codex_base(
            tmp_path,
            [
                ("s1.jsonl", "golden_session.jsonl"),
            ],
        )
        # Create second session with different id
        entries = []
        with open(FIXTURES_DIR / "golden_session.jsonl") as f:
            for line in f:
                line = line.strip()
                if line:
                    entries.append(json.loads(line))
        for e in entries:
            if e.get("type") == "session_meta":
                e["payload"]["id"] = "fixture-session-002"
        with open(codex_base / "s2.jsonl", "w") as f:
            for e in entries:
                f.write(json.dumps(e) + "\n")

        project_dir = tmp_path / "project-data"
        project_dir.mkdir()
        client = _mock_client()

        monkeypatch.setattr(
            "mega_code.client.history.sources.codex.CodexSource",
            lambda *a, **kw: _make_codex_source(codex_base),
        )

        result = sync_codex_trajectories(
            project_dir=project_dir,
            client=client,
            project_id="test-project",
            project_path="/home/user/projects/test-project",
        )

        assert result == 2
        assert client.upload_trajectory.call_count == 2
        # Verify ledger
        ledger = json.loads((project_dir / "codex-sync-ledger.json").read_text())
        assert len(ledger["sessions"]) == 2


class TestIdempotentSync:
    def test_idempotent_sync(self, tmp_path, monkeypatch):
        """Second sync should upload 0 sessions."""
        codex_base = _setup_codex_base(
            tmp_path,
            [
                ("s1.jsonl", "golden_session.jsonl"),
            ],
        )
        project_dir = tmp_path / "project-data"
        project_dir.mkdir()
        client = _mock_client()

        monkeypatch.setattr(
            "mega_code.client.history.sources.codex.CodexSource",
            lambda *a, **kw: _make_codex_source(codex_base),
        )

        # First sync
        sync_codex_trajectories(
            project_dir=project_dir,
            client=client,
            project_id="test-project",
            project_path="/home/user/projects/test-project",
        )
        # Second sync
        result = sync_codex_trajectories(
            project_dir=project_dir,
            client=client,
            project_id="test-project",
            project_path="/home/user/projects/test-project",
        )

        assert result == 0
        assert client.upload_trajectory.call_count == 1  # only from first sync


class TestMtimeChangeReuploads:
    def test_mtime_change_reuploads(self, tmp_path, monkeypatch):
        """Touching file after first sync should cause re-upload."""
        codex_base = _setup_codex_base(
            tmp_path,
            [
                ("s1.jsonl", "golden_session.jsonl"),
            ],
        )
        project_dir = tmp_path / "project-data"
        project_dir.mkdir()
        client = _mock_client()

        monkeypatch.setattr(
            "mega_code.client.history.sources.codex.CodexSource",
            lambda *a, **kw: _make_codex_source(codex_base),
        )

        # First sync
        sync_codex_trajectories(
            project_dir=project_dir,
            client=client,
            project_id="test-project",
            project_path="/home/user/projects/test-project",
        )

        # Touch the file to change mtime
        session_file = codex_base / "s1.jsonl"
        time.sleep(0.05)
        os.utime(session_file, None)

        # Second sync
        result = sync_codex_trajectories(
            project_dir=project_dir,
            client=client,
            project_id="test-project",
            project_path="/home/user/projects/test-project",
        )

        assert result == 1


class TestNewSessionAdded:
    def test_new_session_added(self, tmp_path, monkeypatch):
        """Adding a new session file after first sync → 1 new upload."""
        codex_base = _setup_codex_base(
            tmp_path,
            [
                ("s1.jsonl", "golden_session.jsonl"),
            ],
        )
        project_dir = tmp_path / "project-data"
        project_dir.mkdir()
        client = _mock_client()

        monkeypatch.setattr(
            "mega_code.client.history.sources.codex.CodexSource",
            lambda *a, **kw: _make_codex_source(codex_base),
        )

        # First sync
        sync_codex_trajectories(
            project_dir=project_dir,
            client=client,
            project_id="test-project",
            project_path="/home/user/projects/test-project",
        )

        # Add second session
        entries = []
        with open(FIXTURES_DIR / "golden_session.jsonl") as f:
            for line in f:
                line = line.strip()
                if line:
                    entries.append(json.loads(line))
        for e in entries:
            if e.get("type") == "session_meta":
                e["payload"]["id"] = "fixture-session-new"
        with open(codex_base / "s2.jsonl", "w") as f:
            for e in entries:
                f.write(json.dumps(e) + "\n")

        result = sync_codex_trajectories(
            project_dir=project_dir,
            client=client,
            project_id="test-project",
            project_path="/home/user/projects/test-project",
        )

        assert result == 1


class TestProjectCwdFiltering:
    def test_project_cwd_filtering(self, tmp_path, monkeypatch):
        """Session with wrong cwd should not be uploaded."""
        codex_base = _setup_codex_base(
            tmp_path,
            [
                ("s1.jsonl", "multi_project.jsonl"),  # cwd=/home/user/projects/other-project
            ],
        )
        project_dir = tmp_path / "project-data"
        project_dir.mkdir()
        client = _mock_client()

        monkeypatch.setattr(
            "mega_code.client.history.sources.codex.CodexSource",
            lambda *a, **kw: _make_codex_source(codex_base),
        )

        result = sync_codex_trajectories(
            project_dir=project_dir,
            client=client,
            project_id="test-project",
            project_path="/home/user/projects/test-project",
        )

        assert result == 0
        assert client.upload_trajectory.call_count == 0


class TestCorruptLedgerFreshSync:
    def test_corrupt_ledger_fresh_sync(self, tmp_path, monkeypatch):
        """Corrupt ledger should result in fresh sync, no crash."""
        codex_base = _setup_codex_base(
            tmp_path,
            [
                ("s1.jsonl", "golden_session.jsonl"),
            ],
        )
        project_dir = tmp_path / "project-data"
        project_dir.mkdir()
        # Write garbage ledger
        (project_dir / "codex-sync-ledger.json").write_text("{corrupt json!!")
        client = _mock_client()

        monkeypatch.setattr(
            "mega_code.client.history.sources.codex.CodexSource",
            lambda *a, **kw: _make_codex_source(codex_base),
        )

        result = sync_codex_trajectories(
            project_dir=project_dir,
            client=client,
            project_id="test-project",
            project_path="/home/user/projects/test-project",
        )

        assert result == 1


class TestMissingLedger:
    def test_missing_ledger(self, tmp_path, monkeypatch):
        """No ledger file should behave same as first sync."""
        codex_base = _setup_codex_base(
            tmp_path,
            [
                ("s1.jsonl", "golden_session.jsonl"),
            ],
        )
        project_dir = tmp_path / "project-data"
        project_dir.mkdir()
        client = _mock_client()

        monkeypatch.setattr(
            "mega_code.client.history.sources.codex.CodexSource",
            lambda *a, **kw: _make_codex_source(codex_base),
        )

        result = sync_codex_trajectories(
            project_dir=project_dir,
            client=client,
            project_id="test-project",
            project_path="/home/user/projects/test-project",
        )

        assert result == 1


class TestEmptySessionSkipped:
    def test_empty_session_skipped(self, tmp_path, monkeypatch):
        """Session with 0 turns should not be uploaded."""
        codex_base = tmp_path / ".codex" / "sessions"
        codex_base.mkdir(parents=True)
        # Create session with only session_meta (no messages → 0 turns)
        with open(codex_base / "s1.jsonl", "w") as f:
            f.write(
                json.dumps(
                    {
                        "type": "session_meta",
                        "timestamp": "2026-03-10T10:00:00Z",
                        "payload": {
                            "id": "empty-session",
                            "cwd": "/home/user/projects/test-project",
                            "timestamp": "2026-03-10T10:00:00Z",
                        },
                    }
                )
                + "\n"
            )

        project_dir = tmp_path / "project-data"
        project_dir.mkdir()
        client = _mock_client()

        monkeypatch.setattr(
            "mega_code.client.history.sources.codex.CodexSource",
            lambda *a, **kw: _make_codex_source(codex_base),
        )

        result = sync_codex_trajectories(
            project_dir=project_dir,
            client=client,
            project_id="test-project",
            project_path="/home/user/projects/test-project",
        )

        assert result == 0
        assert client.upload_trajectory.call_count == 0


class TestLedgerDir:
    """Tests for the ledger_dir parameter — ledger written to project CWD."""

    def test_ledger_written_to_ledger_dir(self, tmp_path, monkeypatch):
        """When ledger_dir is provided, ledger should be written there, not project_dir."""
        codex_base = _setup_codex_base(
            tmp_path,
            [
                ("s1.jsonl", "golden_session.jsonl"),
            ],
        )
        project_dir = tmp_path / "internal-data"
        project_dir.mkdir()
        ledger_dir = tmp_path / "actual-project-cwd"
        ledger_dir.mkdir()
        client = _mock_client()

        monkeypatch.setattr(
            "mega_code.client.history.sources.codex.CodexSource",
            lambda *a, **kw: _make_codex_source(codex_base),
        )

        result = sync_codex_trajectories(
            project_dir=project_dir,
            client=client,
            project_id="test-project",
            project_path="/home/user/projects/test-project",
            ledger_dir=ledger_dir,
        )

        assert result == 1
        # Ledger should be in ledger_dir, not project_dir
        assert (ledger_dir / "codex-sync-ledger.json").exists()
        assert not (project_dir / "codex-sync-ledger.json").exists()

    def test_ledger_dir_none_falls_back_to_project_dir(self, tmp_path, monkeypatch):
        """When ledger_dir is None, ledger should fall back to project_dir."""
        codex_base = _setup_codex_base(
            tmp_path,
            [
                ("s1.jsonl", "golden_session.jsonl"),
            ],
        )
        project_dir = tmp_path / "project-data"
        project_dir.mkdir()
        client = _mock_client()

        monkeypatch.setattr(
            "mega_code.client.history.sources.codex.CodexSource",
            lambda *a, **kw: _make_codex_source(codex_base),
        )

        result = sync_codex_trajectories(
            project_dir=project_dir,
            client=client,
            project_id="test-project",
            project_path="/home/user/projects/test-project",
            ledger_dir=None,
        )

        assert result == 1
        assert (project_dir / "codex-sync-ledger.json").exists()


class TestPathEdgeCases:
    """Regression tests for path matching edge cases that cause 'no turn data'."""

    def _write_session_with_cwd(self, codex_base: Path, cwd: str) -> None:
        """Write a minimal golden session with a custom cwd."""
        entries = []
        with open(FIXTURES_DIR / "golden_session.jsonl") as f:
            for line in f:
                line = line.strip()
                if line:
                    entries.append(json.loads(line))
        for e in entries:
            if e.get("type") == "session_meta":
                e["payload"]["cwd"] = cwd
        with open(codex_base / "s1.jsonl", "w") as f:
            for e in entries:
                f.write(json.dumps(e) + "\n")

    def test_trailing_slash_mismatch(self, tmp_path, monkeypatch):
        """Session cwd has trailing slash, project_path does not."""
        codex_base = _setup_codex_base(tmp_path, [])
        self._write_session_with_cwd(codex_base, "/home/user/projects/test-project/")

        project_dir = tmp_path / "project-data"
        project_dir.mkdir()
        client = _mock_client()

        monkeypatch.setattr(
            "mega_code.client.history.sources.codex.CodexSource",
            lambda *a, **kw: _make_codex_source(codex_base),
        )

        result = sync_codex_trajectories(
            project_dir=project_dir,
            client=client,
            project_id="test-project",
            project_path="/home/user/projects/test-project",
        )

        assert result == 1, "Trailing slash should not prevent matching"

    def test_case_sensitivity(self, tmp_path, monkeypatch):
        """Session cwd has different case than project_path."""
        codex_base = _setup_codex_base(tmp_path, [])
        self._write_session_with_cwd(codex_base, "/Users/Reeyan/Dev/proj")

        project_dir = tmp_path / "project-data"
        project_dir.mkdir()
        client = _mock_client()

        monkeypatch.setattr(
            "mega_code.client.history.sources.codex.CodexSource",
            lambda *a, **kw: _make_codex_source(codex_base),
        )

        result = sync_codex_trajectories(
            project_dir=project_dir,
            client=client,
            project_id="test-project",
            project_path="/users/reeyan/dev/proj",
        )

        assert result == 1, "Case-insensitive matching should find the session"

    def test_symlink_resolution(self, tmp_path, monkeypatch):
        """Session cwd is a symlink target, project_path is the symlink."""
        # Create real directory and symlink
        real_dir = tmp_path / "real-project"
        real_dir.mkdir()
        symlink_dir = tmp_path / "linked-project"
        symlink_dir.symlink_to(real_dir)

        codex_base = _setup_codex_base(tmp_path, [])
        # Session records the real path
        self._write_session_with_cwd(codex_base, str(real_dir))

        project_dir = tmp_path / "project-data"
        project_dir.mkdir()
        client = _mock_client()

        monkeypatch.setattr(
            "mega_code.client.history.sources.codex.CodexSource",
            lambda *a, **kw: _make_codex_source(codex_base),
        )

        # Match using symlink path — should resolve to same real path
        result = sync_codex_trajectories(
            project_dir=project_dir,
            client=client,
            project_id="test-project",
            project_path=str(symlink_dir),
        )

        assert result == 1, "Symlink should resolve and match the real path"

    def test_no_match_different_project(self, tmp_path, monkeypatch):
        """Sanity check: genuinely different paths should not match."""
        codex_base = _setup_codex_base(tmp_path, [])
        self._write_session_with_cwd(codex_base, "/home/user/projects/alpha")

        project_dir = tmp_path / "project-data"
        project_dir.mkdir()
        client = _mock_client()

        monkeypatch.setattr(
            "mega_code.client.history.sources.codex.CodexSource",
            lambda *a, **kw: _make_codex_source(codex_base),
        )

        result = sync_codex_trajectories(
            project_dir=project_dir,
            client=client,
            project_id="test-project",
            project_path="/home/user/projects/beta",
        )

        assert result == 0


class TestPathUtils:
    """Unit tests for path_utils edge cases."""

    def test_normalize_path_trailing_slash(self):
        from mega_code.client.utils.path_utils import normalize_path

        assert normalize_path("/home/user/project/") == normalize_path("/home/user/project")

    def test_normalize_path_case_insensitive(self):
        from mega_code.client.utils.path_utils import normalize_path

        assert normalize_path("/Users/Foo/Bar") == normalize_path("/users/foo/bar")

    def test_should_include_session_trailing_slash(self):
        from mega_code.client.utils.path_utils import normalize_path, should_include_session

        targets = {normalize_path("/home/user/project")}
        assert should_include_session("/home/user/project/", targets) is True

    def test_should_include_session_case_mismatch(self):
        from mega_code.client.utils.path_utils import normalize_path, should_include_session

        targets = {normalize_path("/users/foo/project")}
        assert should_include_session("/Users/Foo/Project", targets) is True

    def test_should_include_session_subdirectory(self):
        from mega_code.client.utils.path_utils import normalize_path, should_include_session

        targets = {normalize_path("/home/user/project")}
        assert should_include_session("/home/user/project/backend", targets) is True

    def test_should_include_session_exact_match_rejects_subdir(self):
        from mega_code.client.utils.path_utils import normalize_path, should_include_session

        targets = {normalize_path("/home/user/project")}
        assert (
            should_include_session("/home/user/project/backend", targets, exact_match=True) is False
        )


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

# Import the real class ONCE at module level, before any monkeypatching
from mega_code.client.history.sources.codex import CodexSource as _RealCodexSource


def _make_codex_source(codex_base: Path):
    """Create a CodexSource with custom base_path using the real class."""
    return _RealCodexSource(base_path=codex_base)
