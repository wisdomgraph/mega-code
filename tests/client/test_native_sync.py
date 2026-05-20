"""Characterization tests for Claude / Codex native session sync.

Pinned BEFORE the simplify refactor so we can verify nothing breaks.
Covers:
  - sync_claude_trajectories: no-match, upload + ledger, skip-already-synced, mtime resync
  - MegaCodeRemote._sync_claude / _sync_codex: dispatch, project_cwd fallback, no-match warning
  - dispatcher arity matches the real sync_*_trajectories signatures
"""

from __future__ import annotations

import asyncio
import inspect
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from mega_code.client.api import claude_sync as claude_sync_mod
from mega_code.client.api import codex_sync as codex_sync_mod
from mega_code.client.api.claude_sync import sync_claude_trajectories
from mega_code.client.api.protocol import UploadResult
from mega_code.client.api.remote import MegaCodeRemote
from mega_code.client.models import SessionMetadata, Turn, TurnSet


def _make_turn_set(session_id: str = "sess-1") -> TurnSet:
    return TurnSet(
        session_id=session_id,
        turns=[Turn(turn_id=1, role="user", content="hi")],
        metadata=SessionMetadata(session_id=session_id),
    )


class _StubClient:
    """Minimal stand-in for MegaCodeRemote satisfying the upload protocol."""

    def __init__(self) -> None:
        self.uploads: list[tuple[str, str]] = []

    def upload_trajectory(self, *, turn_set: TurnSet, project_id: str) -> UploadResult:
        self.uploads.append((turn_set.session_id, project_id))
        return UploadResult(status="accepted", session_id=turn_set.session_id, message="ok")


@pytest.fixture
def fake_session_file(tmp_path: Path) -> Path:
    sf = tmp_path / "sessions" / "abc123.jsonl"
    sf.parent.mkdir(parents=True)
    sf.write_text('{"cwd": "/Users/x/proj"}\n')
    return sf


@pytest.fixture
def patched_source(fake_session_file: Path):
    """Patch ClaudeNativeSource so sync sees one matching session entry."""
    entry = {"sessionId": "abc123", "fullPath": str(fake_session_file)}
    src = MagicMock()
    src.iter_sessions_by_project_paths.return_value = iter([entry])
    src.load_session_from_entry.return_value = MagicMock()
    with patch(
        "mega_code.client.history.sources.claude_native.ClaudeNativeSource",
        return_value=src,
    ):
        yield src


def test_sync_claude_no_matches_returns_zero(tmp_path: Path):
    src = MagicMock()
    src.iter_sessions_by_project_paths.return_value = iter([])
    with patch(
        "mega_code.client.history.sources.claude_native.ClaudeNativeSource",
        return_value=src,
    ):
        n = sync_claude_trajectories(
            project_dir=tmp_path,
            client=_StubClient(),
            project_id="proj-1",
            project_path="/Users/x/proj",
        )
    assert n == 0
    assert not (tmp_path / "claude-sync-ledger.json").exists()


def test_sync_claude_uploads_and_writes_ledger(tmp_path: Path, patched_source):
    client = _StubClient()
    with patch.object(
        claude_sync_mod, "_session_to_turnset", return_value=_make_turn_set("abc123")
    ):
        n = sync_claude_trajectories(
            project_dir=tmp_path,
            client=client,
            project_id="proj-1",
            project_path="/Users/x/proj",
        )

    assert n == 1
    assert client.uploads == [("abc123", "proj-1")]

    ledger_path = tmp_path / "claude-sync-ledger.json"
    assert ledger_path.exists()
    ledger = json.loads(ledger_path.read_text())
    assert "abc123" in ledger["sessions"]
    assert "file_mtime" in ledger["sessions"]["abc123"]
    assert ledger["sessions"]["abc123"]["turn_count"] == 1


def test_sync_claude_skips_already_synced(tmp_path: Path, fake_session_file: Path, patched_source):
    mtime = fake_session_file.stat().st_mtime
    ledger_path = tmp_path / "claude-sync-ledger.json"
    ledger_path.write_text(
        json.dumps(
            {
                "sessions": {
                    "abc123": {
                        "uploaded_at": "2026-01-01T00:00:00+00:00",
                        "turn_count": 1,
                        "file_mtime": mtime,
                    }
                }
            }
        )
    )
    client = _StubClient()
    with patch.object(
        claude_sync_mod, "_session_to_turnset", return_value=_make_turn_set("abc123")
    ):
        n = sync_claude_trajectories(
            project_dir=tmp_path,
            client=client,
            project_id="proj-1",
            project_path="/Users/x/proj",
        )

    assert n == 0
    assert client.uploads == []


def test_sync_claude_resyncs_when_mtime_changes(
    tmp_path: Path, fake_session_file: Path, patched_source
):
    ledger_path = tmp_path / "claude-sync-ledger.json"
    ledger_path.write_text(
        json.dumps(
            {
                "sessions": {
                    "abc123": {
                        "uploaded_at": "2026-01-01T00:00:00+00:00",
                        "turn_count": 1,
                        "file_mtime": 1.0,  # stale
                    }
                }
            }
        )
    )
    client = _StubClient()
    with patch.object(
        claude_sync_mod, "_session_to_turnset", return_value=_make_turn_set("abc123")
    ):
        n = sync_claude_trajectories(
            project_dir=tmp_path,
            client=client,
            project_id="proj-1",
            project_path="/Users/x/proj",
        )

    assert n == 1
    assert client.uploads == [("abc123", "proj-1")]
    ledger = json.loads(ledger_path.read_text())
    assert ledger["sessions"]["abc123"]["file_mtime"] == fake_session_file.stat().st_mtime


# ---------------------------------------------------------------------------
# _sync_claude / _sync_codex dispatch on MegaCodeRemote
# ---------------------------------------------------------------------------


def _make_remote() -> MegaCodeRemote:
    return MegaCodeRemote(server_url="http://localhost:9999", api_key="")


def test_sync_claude_dispatch_uses_project_cwd(tmp_path: Path):
    remote = _make_remote()
    with patch.object(claude_sync_mod, "sync_claude_trajectories", return_value=2) as mocked:
        asyncio.run(
            remote._sync_claude(
                project_path=tmp_path,
                project_id="proj-1",
                project_cwd="/Users/x/proj",
            )
        )
    assert mocked.call_count == 1
    args = mocked.call_args.args
    # arity must match real signature
    real_arity = len(inspect.signature(sync_claude_trajectories).parameters)
    assert len(args) <= real_arity, f"too many positional args: {args}"
    # project_cwd flows through as the match path
    assert "/Users/x/proj" in args


def test_sync_claude_dispatch_falls_back_to_mapping(tmp_path: Path):
    remote = _make_remote()
    with (
        patch.object(claude_sync_mod, "sync_claude_trajectories", return_value=0) as mocked,
        patch(
            "mega_code.client.stats.load_mapping",
            return_value={tmp_path.name: "/Users/x/realproj"},
        ),
    ):
        asyncio.run(remote._sync_claude(project_path=tmp_path, project_id="p", project_cwd=None))
    assert mocked.call_count == 1
    assert "/Users/x/realproj" in mocked.call_args.args


def test_sync_claude_dispatch_skips_when_no_match(tmp_path: Path, caplog):
    remote = _make_remote()
    with (
        patch.object(claude_sync_mod, "sync_claude_trajectories") as mocked,
        patch("mega_code.client.stats.load_mapping", return_value={}),
        caplog.at_level("WARNING"),
    ):
        asyncio.run(remote._sync_claude(project_path=tmp_path, project_id="p", project_cwd=None))
    mocked.assert_not_called()
    assert any("Claude sync skipped" in r.message for r in caplog.records)


# =============================================================================
# _detect_current_claude_session — hook-less fallback for no-flag wisdom-gen
# =============================================================================


def _make_jsonl(path: Path, cwd: str, mtime: float | None = None) -> Path:
    """Write a minimal Claude-native JSONL whose first entry carries ``cwd``."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"type": "user", "cwd": cwd}) + "\n")
    if mtime is not None:
        import os as _os

        _os.utime(path, (mtime, mtime))
    return path


def test_detect_current_session_returns_newest_mtime(tmp_path: Path):
    """Newest mtime wins when multiple JSONLs match the same project_cwd."""
    from mega_code.client.history.sources.claude_native import ClaudeNativeSource
    from mega_code.client.run_pipeline import _detect_current_claude_session

    project_root = tmp_path / "claude_projects"
    project_dir = project_root / "-Users-x-proj"
    target_cwd = "/Users/x/proj"

    _make_jsonl(project_dir / "old-session.jsonl", target_cwd, mtime=1_000_000.0)
    _make_jsonl(project_dir / "newest-session.jsonl", target_cwd, mtime=3_000_000.0)
    _make_jsonl(project_dir / "middle-session.jsonl", target_cwd, mtime=2_000_000.0)

    with patch.object(
        ClaudeNativeSource,
        "__init__",
        lambda self: setattr(self, "base_path", project_root) or None,
    ):
        sid = _detect_current_claude_session(target_cwd)

    assert sid == "newest-session"


def test_detect_current_session_no_matches_returns_none(tmp_path: Path):
    """Empty result when no JSONL matches the target cwd."""
    from mega_code.client.history.sources.claude_native import ClaudeNativeSource
    from mega_code.client.run_pipeline import _detect_current_claude_session

    project_root = tmp_path / "claude_projects"
    project_dir = project_root / "-Users-x-other"
    _make_jsonl(project_dir / "unrelated.jsonl", "/Users/x/other", mtime=1_000_000.0)

    with patch.object(
        ClaudeNativeSource,
        "__init__",
        lambda self: setattr(self, "base_path", project_root) or None,
    ):
        sid = _detect_current_claude_session("/Users/x/proj")

    assert sid is None


def test_detect_current_session_handles_empty_directory(tmp_path: Path):
    """Returns None when the Claude projects dir is missing entirely."""
    from mega_code.client.history.sources.claude_native import ClaudeNativeSource
    from mega_code.client.run_pipeline import _detect_current_claude_session

    missing = tmp_path / "does-not-exist"
    with patch.object(
        ClaudeNativeSource, "__init__", lambda self: setattr(self, "base_path", missing) or None
    ):
        sid = _detect_current_claude_session("/Users/x/proj")

    assert sid is None


def test_detect_current_session_skips_unstattable_entry(tmp_path: Path):
    """An entry whose fullPath fails stat() is treated as mtime=0, not crashing."""
    from mega_code.client.history.sources.claude_native import ClaudeNativeSource
    from mega_code.client.run_pipeline import _detect_current_claude_session

    project_root = tmp_path / "claude_projects"
    project_dir = project_root / "-Users-x-proj"
    target_cwd = "/Users/x/proj"

    _make_jsonl(project_dir / "real.jsonl", target_cwd, mtime=1_500_000.0)

    # iter_sessions_by_project_paths yields a stale entry pointing at a
    # vanished file, then the real one. Real one should still be picked.
    real_iter = ClaudeNativeSource(base_path=project_root).iter_sessions_by_project_paths
    extra = {
        "sessionId": "ghost",
        "fullPath": str(project_dir / "ghost.jsonl"),
        "projectPath": target_cwd,
    }

    src = MagicMock()
    src.iter_sessions_by_project_paths.return_value = iter([extra, *real_iter([target_cwd])])
    with patch(
        "mega_code.client.history.sources.claude_native.ClaudeNativeSource",
        return_value=src,
    ):
        sid = _detect_current_claude_session(target_cwd)

    assert sid == "real"


# =============================================================================
# Single-session sync — no-flag wisdom-gen path
# =============================================================================


def test_sync_claude_single_dispatches_with_correct_args(tmp_path: Path):
    """_sync_claude_single forwards (session_id, project_path, self,
    project_id, claude_match_path) to sync_single_claude_session.

    Pins the dispatcher so renaming or reordering the helper signature
    is caught at test time.
    """
    remote = _make_remote()
    with patch.object(claude_sync_mod, "sync_single_claude_session", return_value=1) as mocked:
        synced = asyncio.run(
            remote._sync_claude_single(
                session_id="sess-xyz",
                project_path=tmp_path,
                project_id="proj-1",
                project_cwd="/Users/x/proj",
            )
        )

    assert synced == 1
    assert mocked.call_count == 1
    args = mocked.call_args.args
    # arity must match real signature (modulo the optional ledger_dir kwarg)
    real_params = inspect.signature(claude_sync_mod.sync_single_claude_session).parameters
    assert len(args) <= len(real_params), f"too many positional args: {args}"
    assert args[0] == "sess-xyz"
    assert args[1] == tmp_path
    assert args[2] is remote
    assert args[3] == "proj-1"
    assert args[4] == "/Users/x/proj"


def test_trigger_returns_skipped_when_single_sync_uploads_zero(tmp_path: Path):
    """When _sync_claude_single returns 0, trigger_pipeline_run must
    short-circuit with status='skipped_empty_session' and never POST.

    Pins the no-learnable-content sentinel that run_pipeline.py reads to
    print the friendly notification.
    """
    remote = _make_remote()

    async def _zero(*_a, **_kw):
        return 0

    with (
        patch.object(remote, "_sync_claude_single", side_effect=_zero) as mocked_sync,
        patch.object(remote._client, "post") as mocked_post,
    ):
        result = asyncio.run(
            remote.trigger_pipeline_run(
                project_id="proj-1",
                project_path=tmp_path,
                session_id="sess-xyz",
                agent="claude",
                project_cwd="/Users/x/proj",
            )
        )

    assert mocked_sync.call_count == 1
    mocked_post.assert_not_called()
    assert result.status == "skipped_empty_session"
    assert result.run_id == ""
    assert "no learnable content" in result.message.lower()


def test_run_pipeline_prefers_detected_session_under_inferred_claude_agent(
    tmp_path: Path, monkeypatch
):
    """Integration: with MEGA_CODE_AGENT unset, ~/.claude/projects/ present,
    no --session-id and no --project, the auto-detected session id flows
    into trigger_pipeline_run as session_id and agent='claude'.

    Pins the run_pipeline.py:276-277 + 284-288 inference + detection
    chain — both of which are silent-fail paths if a future refactor
    breaks them.
    """
    from mega_code.client import run_pipeline as rp_mod

    fake_home = tmp_path / "home"
    (fake_home / ".claude" / "projects").mkdir(parents=True)
    project_cwd = tmp_path / "proj"
    project_cwd.mkdir()

    captured: dict = {}

    class _FakeClient:
        async def trigger_pipeline_run(self, **kwargs):
            captured.update(kwargs)
            from mega_code.client.api.protocol import TriggerPipelineResult

            return TriggerPipelineResult(run_id="r1", status="queued", message="")

        async def aclose(self):
            return None

    async def _fake_poll(*_a, **_kw):
        from mega_code.client.api.protocol import PipelineStatusResult

        return PipelineStatusResult(run_id="r1", project_id="p", status="completed")

    def _fake_save(*_a, **_kw):
        from mega_code.client.pending import PendingResult

        return PendingResult()

    monkeypatch.setattr(rp_mod, "_load_env", lambda: None)
    monkeypatch.setattr(rp_mod, "_detect_current_claude_session", lambda _cwd: "detected-sid")
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: fake_home))
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(project_cwd))
    monkeypatch.delenv("MEGA_CODE_AGENT", raising=False)
    monkeypatch.delenv("CLAUDE_SESSION_ID", raising=False)
    monkeypatch.setattr("sys.argv", ["run_pipeline", "--mode", "local"])

    # Patch the deferred imports used inside main()
    import mega_code.client.api as api_mod
    import mega_code.client.pending as pending_mod
    import mega_code.client.stats as stats_mod

    monkeypatch.setattr(api_mod, "create_client", lambda **_kw: _FakeClient())
    monkeypatch.setattr(api_mod, "resolve_mode", lambda _m: "local")
    monkeypatch.setattr(pending_mod, "poll_pipeline_status", _fake_poll)
    monkeypatch.setattr(pending_mod, "save_outputs_to_pending", _fake_save)
    monkeypatch.setattr(pending_mod, "format_pipeline_notification", lambda *_a, **_kw: "")
    monkeypatch.setattr(stats_mod, "get_project_sessions_dir", lambda _p: tmp_path / "sessions")
    monkeypatch.setattr(stats_mod, "resolve_project_path", lambda _p: tmp_path / "sessions")

    (tmp_path / "sessions").mkdir(exist_ok=True)

    try:
        asyncio.run(rp_mod.main())
    except SystemExit as exc:
        assert exc.code == 0

    assert captured.get("agent") == "claude"
    assert captured.get("session_id") == "detected-sid"
    assert captured.get("project_cwd") == str(project_cwd)


def test_sync_codex_dispatch_passes_correct_arity(tmp_path: Path):
    """Regression: _sync_codex must pass exactly the args sync_codex_trajectories takes."""
    from mega_code.client.api.codex_sync import sync_codex_trajectories

    real_arity = len(inspect.signature(sync_codex_trajectories).parameters)
    remote = _make_remote()
    with patch.object(codex_sync_mod, "sync_codex_trajectories", return_value=0) as mocked:
        asyncio.run(
            remote._sync_codex(project_path=tmp_path, project_id="p", project_cwd="/Users/x/proj")
        )
    assert mocked.call_count == 1
    args = mocked.call_args.args
    assert len(args) == real_arity, (
        f"_sync_codex passed {len(args)} positional args but "
        f"sync_codex_trajectories takes {real_arity}: {args}"
    )
