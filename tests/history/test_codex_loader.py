"""Tests for loader.py — Codex early-return fix (Phase 2)."""

import json
import shutil
from pathlib import Path
from unittest.mock import patch

import pytest

from mega_code.client.history.loader import load_sessions_from_project
from mega_code.client.history.models import (
    HistorySessionMetadata,
    Message,
    Session,
)
from mega_code.client.history.sources.codex import CodexSource

FIXTURES_DIR = Path(__file__).parent.parent / "fixtures" / "codex"


def _make_session(session_id: str, source: str, project_path: str) -> Session:
    """Create a minimal Session object for testing."""
    return Session(
        metadata=HistorySessionMetadata(
            session_id=session_id,
            source=source,
            project_path=project_path,
        ),
        messages=[
            Message(id=f"{session_id}-1", role="user", content="hello"),
            Message(id=f"{session_id}-2", role="assistant", content="hi"),
        ],
    )


def _setup_codex_sessions(codex_base: Path, fixture_names: list[str]) -> CodexSource:
    """Copy fixture files into codex_base and return a CodexSource."""
    for i, name in enumerate(fixture_names):
        src = FIXTURES_DIR / name
        dst = codex_base / f"session_{i}.jsonl"
        shutil.copy2(src, dst)
    return CodexSource(base_path=codex_base)


class TestCodexOnlyProject:
    """Cycle 1: Core bug — Codex-only project returns zero sessions."""

    def test_codex_only_project(self, codex_base, tmp_path):
        """With 0 MEGA sessions but 2 Codex sessions, should return 2."""
        # Set up codex with 2 sessions (golden + multi_project with matching cwd)
        # Use golden_session.jsonl (cwd=/home/user/projects/test-project)
        shutil.copy2(FIXTURES_DIR / "golden_session.jsonl", codex_base / "s1.jsonl")
        # Create second session with same cwd but different id
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

        project_path = tmp_path / "mega-code-data"
        project_path.mkdir()

        with (
            patch("mega_code.client.history.loader.MegaCodeSource") as MockMegaSource,
            patch(
                "mega_code.client.history.sources.codex.CodexSource",
            ) as _,
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

        codex_results = [s for s in result if s.metadata.source == "codex_cli"]
        assert len(codex_results) == 2


class TestCodexPlusMega:
    """Cycle 2: Mixed MEGA + Codex sessions."""

    def test_codex_plus_mega(self, codex_base, tmp_path):
        """2 MEGA + 2 Codex → 4 total."""
        shutil.copy2(FIXTURES_DIR / "golden_session.jsonl", codex_base / "s1.jsonl")
        entries = []
        with open(FIXTURES_DIR / "golden_session.jsonl") as f:
            for line in f:
                line = line.strip()
                if line:
                    entries.append(json.loads(line))
        for e in entries:
            if e.get("type") == "session_meta":
                e["payload"]["id"] = "codex-session-002"
        with open(codex_base / "s2.jsonl", "w") as f:
            for e in entries:
                f.write(json.dumps(e) + "\n")

        mega_sessions = [
            _make_session("mega-001", "mega_code", "/home/user/projects/test-project"),
            _make_session("mega-002", "mega_code", "/home/user/projects/test-project"),
        ]

        with (
            patch("mega_code.client.history.loader.MegaCodeSource") as MockMegaSource,
            patch(
                "mega_code.client.history.sources.codex.CodexSource",
                return_value=CodexSource(base_path=codex_base),
            ),
        ):
            mock_source = MockMegaSource.return_value
            mock_source.iter_sessions_from_path.return_value = iter(mega_sessions)

            result = load_sessions_from_project(
                project_path=Path("/home/user/projects/test-project"),
                include_codex=True,
            )

        assert len(result) == 4


class TestCodexNotLoadedWhenFlagFalse:
    """Cycle 3: Codex sessions exist but include_codex=False."""

    def test_codex_not_loaded_when_flag_false(self, codex_base, tmp_path):
        shutil.copy2(FIXTURES_DIR / "golden_session.jsonl", codex_base / "s1.jsonl")

        with (
            patch("mega_code.client.history.loader.MegaCodeSource") as MockMegaSource,
        ):
            mock_source = MockMegaSource.return_value
            mock_source.iter_sessions_from_path.return_value = iter([])

            result = load_sessions_from_project(
                project_path=Path("/home/user/projects/test-project"),
                include_codex=False,
            )

        assert len(result) == 0


class TestIncludeFlagMatrix:
    """Cycle 4: Parameterized flag combinations."""

    @pytest.mark.parametrize(
        "include_claude,include_codex,expect_codex",
        [
            (False, False, False),
            (False, True, True),
            (True, False, False),
            (True, True, True),
        ],
    )
    def test_include_flag_matrix(
        self, codex_base, tmp_path, include_claude, include_codex, expect_codex
    ):
        shutil.copy2(FIXTURES_DIR / "golden_session.jsonl", codex_base / "s1.jsonl")

        with (
            patch("mega_code.client.history.loader.MegaCodeSource") as MockMegaSource,
            patch("mega_code.client.history.loader.ClaudeNativeSource") as MockClaudeSource,
            patch(
                "mega_code.client.history.sources.codex.CodexSource",
                return_value=CodexSource(base_path=codex_base),
            ),
        ):
            mock_source = MockMegaSource.return_value
            mock_source.iter_sessions_from_path.return_value = iter([])
            mock_claude = MockClaudeSource.return_value
            mock_claude.iter_sessions_by_project_paths.return_value = iter([])

            result = load_sessions_from_project(
                project_path=Path("/home/user/projects/test-project"),
                include_claude=include_claude,
                include_codex=include_codex,
            )

        codex_results = [s for s in result if s.metadata.source == "codex_cli"]
        if expect_codex:
            assert len(codex_results) >= 1
        else:
            assert len(codex_results) == 0


class TestLimitAfterMerge:
    """Cycle 5: Limit applied after merge."""

    def test_limit_after_merge(self, codex_base, tmp_path):
        # Create 3 codex sessions
        for i in range(3):
            entries = []
            with open(FIXTURES_DIR / "golden_session.jsonl") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        entries.append(json.loads(line))
            for e in entries:
                if e.get("type") == "session_meta":
                    e["payload"]["id"] = f"codex-{i}"
            with open(codex_base / f"s{i}.jsonl", "w") as f:
                for e in entries:
                    f.write(json.dumps(e) + "\n")

        mega_sessions = [
            _make_session(f"mega-{i}", "mega_code", "/home/user/projects/test-project")
            for i in range(3)
        ]

        with (
            patch("mega_code.client.history.loader.MegaCodeSource") as MockMegaSource,
            patch(
                "mega_code.client.history.sources.codex.CodexSource",
                return_value=CodexSource(base_path=codex_base),
            ),
        ):
            mock_source = MockMegaSource.return_value
            mock_source.iter_sessions_from_path.return_value = iter(mega_sessions)

            result = load_sessions_from_project(
                project_path=Path("/home/user/projects/test-project"),
                include_codex=True,
                limit=4,
            )

        assert len(result) == 4


class TestProjectPathFiltering:
    """Cycle 6: Codex session with mismatched cwd is filtered out."""

    def test_project_path_filtering(self, codex_base, tmp_path):
        # multi_project.jsonl has cwd=/home/user/projects/other-project
        shutil.copy2(FIXTURES_DIR / "multi_project.jsonl", codex_base / "s1.jsonl")

        with (
            patch("mega_code.client.history.loader.MegaCodeSource") as MockMegaSource,
            patch(
                "mega_code.client.history.sources.codex.CodexSource",
                return_value=CodexSource(base_path=codex_base),
            ),
        ):
            mega_sessions = [
                _make_session("mega-001", "mega_code", "/home/user/projects/test-project"),
            ]
            mock_source = MockMegaSource.return_value
            mock_source.iter_sessions_from_path.return_value = iter(mega_sessions)

            result = load_sessions_from_project(
                project_path=Path("/home/user/projects/test-project"),
                include_codex=True,
            )

        codex_results = [s for s in result if s.metadata.source == "codex_cli"]
        assert len(codex_results) == 0


class TestEmptyEverything:
    """Cycle 7: No sessions anywhere."""

    def test_empty_everything(self, codex_base, tmp_path):
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

        assert result == []
