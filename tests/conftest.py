"""Shared test fixtures for mega-code-oss tests."""

import json
import shutil
from pathlib import Path
from typing import Any

import pytest

from mega_code.client.history.sources.codex import CodexSource

FIXTURES_DIR = Path(__file__).parent / "fixtures" / "codex"


@pytest.fixture
def codex_fixtures_dir() -> Path:
    """Return path to the codex fixtures directory."""
    return FIXTURES_DIR


@pytest.fixture
def golden_session_path(codex_fixtures_dir: Path) -> Path:
    """Return path to the golden session JSONL fixture."""
    return codex_fixtures_dir / "golden_session.jsonl"


@pytest.fixture
def codex_base(tmp_path: Path) -> Path:
    """Create and return a temporary .codex/sessions directory."""
    base = tmp_path / ".codex" / "sessions"
    base.mkdir(parents=True)
    return base


@pytest.fixture
def codex_source(codex_base: Path) -> CodexSource:
    """Return a CodexSource pointing at the temporary directory."""
    return CodexSource(base_path=codex_base)


@pytest.fixture
def write_codex_session(codex_base: Path):
    """Factory fixture: write a JSONL session file into the codex_base.

    Usage:
        path = write_codex_session("session.jsonl", entries_or_path)
        path = write_codex_session("session.jsonl", entries_or_path, "2026-03-10")
    """

    def _write(
        filename: str,
        entries_or_path: list[dict[str, Any]] | Path,
        date_subdir: str | None = None,
    ) -> Path:
        if date_subdir:
            target_dir = codex_base / date_subdir
        else:
            target_dir = codex_base
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / filename

        if isinstance(entries_or_path, Path):
            shutil.copy2(entries_or_path, target)
        else:
            with open(target, "w") as f:
                for entry in entries_or_path:
                    f.write(json.dumps(entry) + "\n")
        return target

    return _write
