"""Tests for orphaned subagent transcript discovery in claude_native.

Covers the gap where a project's main-session transcripts were deleted,
leaving only ``<uuid>/subagents/agent-*.jsonl`` — those must be discovered
as first-class sessions even though they carry ``isSidechain: true``.
"""

from __future__ import annotations

import json
from pathlib import Path

from mega_code.client.history.sources.claude_native import ClaudeNativeSource

PROJECT_CWD = "/Users/x/Documents/new_mega-code-project"


def _write_jsonl(path: Path, events: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(e) for e in events) + "\n", encoding="utf-8")


def _make_project(base: Path, name: str) -> Path:
    project_dir = base / name
    project_dir.mkdir(parents=True)
    return project_dir


def test_discovers_orphaned_subagent_transcripts(tmp_path: Path) -> None:
    project_dir = _make_project(tmp_path, "-Users-x-proj")
    parent_uuid = "11111111-1111-1111-1111-111111111111"
    _write_jsonl(
        project_dir / parent_uuid / "subagents" / "agent-aaa.jsonl",
        [{"cwd": PROJECT_CWD, "gitBranch": "main", "isSidechain": True, "type": "user"}],
    )

    source = ClaudeNativeSource(base_path=tmp_path)
    entries = source._discover_sessions_from_jsonl(project_dir, set())

    assert len(entries) == 1
    entry = entries[0]
    assert entry["sessionId"] == f"{parent_uuid}-agent-aaa"
    assert entry["projectPath"] == PROJECT_CWD
    assert entry["gitBranch"] == "main"
    # Admitted as a first-class session, not skipped as a sidechain.
    assert entry["isSidechain"] is False


def test_synthetic_ids_are_unique_across_parents(tmp_path: Path) -> None:
    project_dir = _make_project(tmp_path, "-Users-x-proj")
    # Same agent filename stem under two different parent uuids must not collide.
    for parent in ("aaaaaaaa-0000-0000-0000-000000000000", "bbbbbbbb-0000-0000-0000-000000000000"):
        _write_jsonl(
            project_dir / parent / "subagents" / "agent-1.jsonl",
            [{"cwd": PROJECT_CWD, "isSidechain": True, "type": "user"}],
        )

    source = ClaudeNativeSource(base_path=tmp_path)
    entries = source._discover_sessions_from_jsonl(project_dir, set())

    ids = {e["sessionId"] for e in entries}
    assert len(ids) == 2


def test_synthetic_ids_are_stable_across_runs(tmp_path: Path) -> None:
    project_dir = _make_project(tmp_path, "-Users-x-proj")
    parent_uuid = "11111111-1111-1111-1111-111111111111"
    _write_jsonl(
        project_dir / parent_uuid / "subagents" / "agent-aaa.jsonl",
        [{"cwd": PROJECT_CWD, "isSidechain": True, "type": "user"}],
    )

    source = ClaudeNativeSource(base_path=tmp_path)
    first = source._discover_sessions_from_jsonl(project_dir, set())
    second = source._discover_sessions_from_jsonl(project_dir, set())

    assert [e["sessionId"] for e in first] == [e["sessionId"] for e in second]


def test_cwd_matching_includes_subagent_sessions(tmp_path: Path) -> None:
    project_dir = _make_project(tmp_path, "-Users-x-proj")
    parent_uuid = "11111111-1111-1111-1111-111111111111"
    _write_jsonl(
        project_dir / parent_uuid / "subagents" / "agent-aaa.jsonl",
        [{"cwd": PROJECT_CWD, "isSidechain": True, "type": "user"}],
    )

    source = ClaudeNativeSource(base_path=tmp_path)
    matched = list(source.iter_sessions_by_project_paths([PROJECT_CWD]))

    assert len(matched) == 1
    assert matched[0]["sessionId"] == f"{parent_uuid}-agent-aaa"


def test_subagent_only_project_yields_sessions(tmp_path: Path) -> None:
    """Integration: a project with only subagents/ folders (main transcripts
    deleted) yields N sessions, no content filtering at the client."""
    project_dir = _make_project(tmp_path, "-Users-x-proj")
    for i in range(5):
        parent = f"{i:08d}-0000-0000-0000-000000000000"
        _write_jsonl(
            project_dir / parent / "subagents" / f"agent-{i}.jsonl",
            [{"cwd": PROJECT_CWD, "isSidechain": True, "type": "user"}],
        )

    source = ClaudeNativeSource(base_path=tmp_path)
    matched = list(source.iter_sessions_by_project_paths([PROJECT_CWD]))

    assert len(matched) == 5


def test_explore_only_subagent_not_prefiltered(tmp_path: Path) -> None:
    """A subagent with zero code writes is still discovered — step0 decides
    usability, not the client."""
    project_dir = _make_project(tmp_path, "-Users-x-proj")
    parent_uuid = "11111111-1111-1111-1111-111111111111"
    _write_jsonl(
        project_dir / parent_uuid / "subagents" / "agent-explore.jsonl",
        [
            {"cwd": PROJECT_CWD, "isSidechain": True, "type": "user"},
            {"type": "assistant", "message": {"role": "assistant", "content": "read only"}},
        ],
    )

    source = ClaudeNativeSource(base_path=tmp_path)
    entries = source._discover_sessions_from_jsonl(project_dir, set())

    assert len(entries) == 1


def test_inline_sidechain_in_main_transcript_still_dropped(tmp_path: Path) -> None:
    """Regression: a top-level transcript whose first cwd-bearing entry is a
    sidechain is still skipped (behavior unchanged for main transcripts)."""
    project_dir = _make_project(tmp_path, "-Users-x-proj")
    _write_jsonl(
        project_dir / "sidechain-main.jsonl",
        [{"cwd": PROJECT_CWD, "isSidechain": True, "type": "user"}],
    )

    source = ClaudeNativeSource(base_path=tmp_path)
    entries = source._discover_sessions_from_jsonl(project_dir, set())

    assert entries == []


def test_ordinary_top_level_discovery_unaffected(tmp_path: Path) -> None:
    """Regression: ordinary main-session discovery still works."""
    project_dir = _make_project(tmp_path, "-Users-x-proj")
    _write_jsonl(
        project_dir / "abc123.jsonl",
        [{"cwd": PROJECT_CWD, "gitBranch": "main", "type": "user"}],
    )

    source = ClaudeNativeSource(base_path=tmp_path)
    entries = source._discover_sessions_from_jsonl(project_dir, set())

    assert len(entries) == 1
    assert entries[0]["sessionId"] == "abc123"
    assert entries[0]["isSidechain"] is False
