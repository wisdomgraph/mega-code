# tests/client/test_cleaning.py
"""Tests for mega_code.client.filters.cleaning — anchor detection, expansion, and public API."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from mega_code.client.filters.cleaning import (
    CleaningResult,
    _has_mega_command,
    _has_mega_content,
    _has_mega_path,
    _has_skill_injection,
    _has_user_trigger,
    _is_interrupt_like,
    _is_mega_anchor,
    _is_meta_tool_turn,
    _is_near_anchor_assistant,
    _segment_mega_blocks,
    clean_mega_code_turns,
    save_cleaning_debug,
)
from mega_code.client.models import Turn

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _turn(
    turn_id: int = 0,
    role: str = "user",
    content: str = "",
    tool_name: str | None = None,
    tool_target: str | None = None,
    command: str | None = None,
    is_error: bool = False,
    exit_code: int | None = None,
) -> Turn:
    return Turn(
        turn_id=turn_id,
        role=role,
        content=content,
        tool_name=tool_name,
        tool_target=tool_target,
        command=command,
        is_error=is_error,
        exit_code=exit_code,
    )


# ===========================================================================
# Anchor detection — user triggers
# ===========================================================================


class TestHasUserTrigger:
    def test_command_name_tag(self):
        t = _turn(content="<command-name>/mega-code:run</command-name>")
        assert _has_user_trigger(t)

    def test_command_name_without_slash(self):
        t = _turn(content="<command-name>mega-code:status</command-name>")
        assert _has_user_trigger(t)

    def test_command_message_tag(self):
        t = _turn(content="<command-message>mega-code:login</command-message>")
        assert _has_user_trigger(t)

    def test_dollar_skill_invocation(self):
        t = _turn(content="$mega-code-wisdom-gen ")
        assert _has_user_trigger(t)

    def test_skill_xml_block(self):
        t = _turn(content="<skill>\n  <name>mega-code-run</name>\n</skill>")
        assert _has_user_trigger(t)

    def test_ignores_assistant_role(self):
        t = _turn(role="assistant", content="<command-name>mega-code:run</command-name>")
        assert not _has_user_trigger(t)

    def test_no_match_on_plain_text(self):
        t = _turn(content="Please fix the bug in auth.py")
        assert not _has_user_trigger(t)


# ===========================================================================
# Anchor detection — skill injection
# ===========================================================================


class TestHasSkillInjection:
    def test_base_directory_line(self):
        t = _turn(
            content="Base directory for this skill: "
            "/Users/dev/.claude/plugins/cache/mind-ai-mega-code/mega-code/1.0.1-beta/skills/run"
        )
        assert _has_skill_injection(t)

    def test_skill_md_path(self):
        t = _turn(content="<path>/home/user/.claude/plugins/mega-code-run/SKILL.md</path>")
        assert _has_skill_injection(t)

    def test_plugins_path(self):
        t = _turn(content="Loading from plugins/cache/mega-code/skills")
        assert _has_skill_injection(t)

    def test_ignores_assistant(self):
        t = _turn(role="assistant", content="Base directory for this skill: mega-code")
        assert not _has_skill_injection(t)

    def test_no_match_on_unrelated(self):
        t = _turn(content="The base directory is /home/user/project")
        assert not _has_skill_injection(t)


# ===========================================================================
# Anchor detection — mega commands
# ===========================================================================


class TestHasMegaCommand:
    def test_mega_env_var_in_command(self):
        t = _turn(command="echo $MEGA_CODE_API_KEY")
        assert _has_mega_command(t)

    def test_mega_code_python_module(self):
        t = _turn(command="python -m mega_code.pipeline")
        assert _has_mega_command(t)

    def test_uv_run_mega_code(self):
        t = _turn(command="uv run --directory /path/mega-code python script.py")
        assert _has_mega_command(t)

    def test_run_pipeline_async(self):
        t = _turn(command="python run_pipeline_async.py --project abc")
        assert _has_mega_command(t)

    def test_megacode_eureka(self):
        t = _turn(command="megacode-eureka extract")
        assert _has_mega_command(t)

    def test_no_command_returns_false(self):
        t = _turn(command=None)
        assert not _has_mega_command(t)

    def test_empty_command_returns_false(self):
        t = _turn(command="")
        assert not _has_mega_command(t)

    def test_normal_command(self):
        t = _turn(command="git status")
        assert not _has_mega_command(t)


# ===========================================================================
# Anchor detection — mega content
# ===========================================================================


class TestHasMegaContent:
    def test_strategies_marker(self):
        t = _turn(content="mega-code:strategies:start")
        assert _has_mega_content(t)

    def test_localhost_viewer(self):
        t = _turn(content="Open http://localhost:3117 to view")
        assert _has_mega_content(t)

    def test_viewer_pid(self):
        t = _turn(content="Check viewer.pid for the process")
        assert _has_mega_content(t)

    def test_mega_env_var(self):
        t = _turn(content="Set MEGA_CODE_API_KEY in .env")
        assert _has_mega_content(t)

    def test_megacode_word(self):
        t = _turn(content="The MEGACODE pipeline ran successfully")
        assert _has_mega_content(t)

    def test_pending_skills(self):
        t = _turn(content="You have 3 pending skills to review")
        assert _has_mega_content(t)

    def test_pending_strategies(self):
        t = _turn(content="Found 2 pending strategies")
        assert _has_mega_content(t)

    def test_stored_on_server(self):
        t = _turn(content="Results are stored on the server")
        assert _has_mega_content(t)

    def test_archived_items(self):
        t = _turn(content="Successfully archived 5 items")
        assert _has_mega_content(t)

    def test_plugin_root(self):
        t = _turn(content="Setting plugin-root for hooks")
        assert _has_mega_content(t)

    def test_archive_pending_items_func(self):
        t = _turn(content="Calling archive_pending_items()")
        assert _has_mega_content(t)

    def test_empty_content(self):
        t = _turn(content="")
        assert not _has_mega_content(t)

    def test_normal_content(self):
        t = _turn(content="Implemented the login form with React")
        assert not _has_mega_content(t)


# ===========================================================================
# Anchor detection — tool targets
# ===========================================================================


class TestHasMegaPath:
    def test_mega_data_path(self):
        t = _turn(tool_target="/home/user/.local/share/mega-code/projects/abc/events.jsonl")
        assert _has_mega_path(t)

    def test_plugin_cache_path(self):
        t = _turn(tool_target="/home/user/.claude/plugins/cache/mind/mega-code/SKILL.md")
        assert _has_mega_path(t)

    def test_agents_skills_path(self):
        t = _turn(tool_target="/home/user/.agents/skills/mega-code-run/config.json")
        assert _has_mega_path(t)

    def test_agents_rules_path(self):
        t = _turn(tool_target="/home/user/.agents/rules/mega-code/rule.md")
        assert _has_mega_path(t)

    def test_mega_path_in_command(self):
        t = _turn(command="cat ~/.local/share/mega-code/mapping.json")
        assert _has_mega_path(t)

    def test_mega_path_in_content(self):
        t = _turn(content="Reading from ~/.local/share/mega-code/projects/")
        assert _has_mega_path(t)

    def test_none_target(self):
        t = _turn(tool_target=None)
        assert not _has_mega_path(t)

    def test_normal_path(self):
        t = _turn(tool_target="/home/user/project/src/main.py")
        assert not _has_mega_path(t)


# ===========================================================================
# Composite anchor check
# ===========================================================================


class TestIsMegaAnchor:
    def test_user_trigger_is_anchor(self):
        t = _turn(content="<command-name>/mega-code:run</command-name>")
        assert _is_mega_anchor(t)

    def test_command_is_anchor(self):
        t = _turn(role="assistant", command="uv run --directory /mega-code python x.py")
        assert _is_mega_anchor(t)

    def test_content_is_anchor(self):
        t = _turn(role="assistant", content="You have 3 pending skills")
        assert _is_mega_anchor(t)

    def test_tool_target_is_anchor(self):
        t = _turn(role="assistant", tool_target="/home/.local/share/mega-code/x.json")
        assert _is_mega_anchor(t)

    def test_normal_turn_not_anchor(self):
        t = _turn(content="Fix the typo in README.md")
        assert not _is_mega_anchor(t)


# ===========================================================================
# Near-anchor absorption
# ===========================================================================


class TestNearAnchorAbsorption:
    def test_empty_content_is_interrupt_like(self):
        t = _turn(content="")
        assert _is_interrupt_like(t)

    def test_whitespace_only_is_interrupt_like(self):
        t = _turn(content="   ")
        assert _is_interrupt_like(t)

    def test_request_interrupted_is_interrupt_like(self):
        t = _turn(content="[Request interrupted by user]")
        assert _is_interrupt_like(t)

    def test_local_command_caveat_is_interrupt_like(self):
        t = _turn(content="<local-command-caveat>some caveat</local-command-caveat>")
        assert _is_interrupt_like(t)

    def test_normal_content_not_interrupt_like(self):
        t = _turn(content="Please review the code")
        assert not _is_interrupt_like(t)

    def test_meta_tool_turn(self):
        for name in ["AskUserQuestion", "ToolSearch", "request_user_input", "write_stdin"]:
            t = _turn(tool_name=name)
            assert _is_meta_tool_turn(t)

    def test_non_meta_tool(self):
        t = _turn(tool_name="Bash")
        assert not _is_meta_tool_turn(t)

    def test_near_anchor_assistant_mega_code_ref(self):
        t = _turn(role="assistant", content="Running mega-code-run skill")
        assert _is_near_anchor_assistant(t)

    def test_near_anchor_assistant_oauth(self):
        t = _turn(role="assistant", content="Starting the oauth flow")
        assert _is_near_anchor_assistant(t)

    def test_near_anchor_assistant_available_skills(self):
        t = _turn(role="assistant", content="Here are the available skills")
        assert _is_near_anchor_assistant(t)

    def test_near_anchor_ignores_user_role(self):
        t = _turn(role="user", content="Running mega-code-run")
        assert not _is_near_anchor_assistant(t)

    def test_near_anchor_ignores_empty_assistant(self):
        t = _turn(role="assistant", content="")
        assert not _is_near_anchor_assistant(t)


# ===========================================================================
# Block segmentation
# ===========================================================================


class TestSegmentMegaBlocks:
    def test_no_anchors_returns_all_false(self):
        turns = [_turn(i, content="normal") for i in range(5)]
        assert _segment_mega_blocks(turns) == [False] * 5

    def test_single_anchor_marked(self):
        turns = [
            _turn(0, content="normal task"),
            _turn(1, content="<command-name>mega-code:run</command-name>"),
            _turn(2, content="working on feature"),
        ]
        mask = _segment_mega_blocks(turns)
        assert mask == [False, True, False]

    def test_anchor_absorbs_interrupt_neighbors(self):
        turns = [
            _turn(0, content=""),  # empty = interrupt-like, will be absorbed backward
            _turn(1, content="<command-name>mega-code:run</command-name>"),  # anchor
            _turn(2, content=""),  # empty = interrupt-like, absorbed forward
            _turn(3, content="working on feature"),  # real content, not absorbed
        ]
        mask = _segment_mega_blocks(turns)
        assert mask == [True, True, True, False]

    def test_expansion_stops_at_max_neighbor_steps(self):
        # 6 empty turns before anchor — only 4 should be absorbed (MAX_NEIGHBOR_STEPS)
        turns = [_turn(i, content="") for i in range(6)]
        turns.append(_turn(6, content="<command-name>mega-code:run</command-name>"))
        mask = _segment_mega_blocks(turns)
        # turns 0,1 not absorbed (too far), turns 2-6 absorbed
        assert mask == [False, False, True, True, True, True, True]

    def test_expansion_stops_at_non_absorbable(self):
        turns = [
            _turn(0, content="I was working on auth"),  # not absorbable
            _turn(1, content=""),  # absorbable
            _turn(2, content="<command-name>mega-code:run</command-name>"),  # anchor
        ]
        mask = _segment_mega_blocks(turns)
        # turn 0 blocks backward expansion even though turn 1 is absorbable
        assert mask == [False, True, True]

    def test_multiple_anchors_merge(self):
        turns = [
            _turn(0, content="<command-name>mega-code:login</command-name>"),
            _turn(1, content="Base directory for this skill: mega-code"),
            _turn(2, content="<command-name>mega-code:run</command-name>"),
        ]
        mask = _segment_mega_blocks(turns)
        assert mask == [True, True, True]

    def test_meta_tool_near_anchor_absorbed(self):
        turns = [
            _turn(0, content="<command-name>mega-code:run</command-name>"),
            _turn(1, tool_name="ToolSearch", content=""),  # meta tool
            _turn(2, content="implementing feature X"),
        ]
        mask = _segment_mega_blocks(turns)
        assert mask == [True, True, False]


# ===========================================================================
# Public API — clean_mega_code_turns
# ===========================================================================


class TestCleanMegaCodeTurns:
    def test_empty_input(self):
        result = clean_mega_code_turns([])
        assert result.kept == []
        assert result.removed == []

    def test_no_mega_content_passes_through(self):
        turns = [
            _turn(0, content="Fix the login bug"),
            _turn(1, role="assistant", content="I'll look at auth.py"),
            _turn(2, content="Thanks, looks good"),
        ]
        result = clean_mega_code_turns(turns)
        assert len(result.kept) == 3
        assert result.removed == []
        # Turns should be returned as-is (same objects)
        assert result.kept is turns

    def test_removes_mega_turns_and_reindexes(self):
        turns = [
            _turn(0, content="Fix the login bug"),
            _turn(1, content="<command-name>mega-code:run</command-name>"),
            _turn(2, content="Base directory for this skill: mega-code"),
            _turn(3, role="assistant", content="Done fixing the bug"),
        ]
        result = clean_mega_code_turns(turns)
        assert len(result.kept) == 2
        assert len(result.removed) == 2
        # Check reindexing
        assert result.kept[0].turn_id == 0
        assert result.kept[0].content == "Fix the login bug"
        assert result.kept[1].turn_id == 1
        assert result.kept[1].content == "Done fixing the bug"

    def test_all_turns_removed_returns_empty(self):
        turns = [
            _turn(0, content="<command-name>mega-code:login</command-name>"),
            _turn(1, content="Base directory for this skill: mega-code"),
        ]
        result = clean_mega_code_turns(turns)
        assert result.kept == []
        assert len(result.removed) == 2

    def test_realistic_session_with_login_flow(self):
        """Simulates a session where user logs in, runs pipeline, then does real work."""
        turns = [
            # Mega-code login flow (should be removed)
            _turn(0, content="<command-name>/mega-code:login</command-name>"),
            _turn(
                1,
                content="Base directory for this skill: "
                "/Users/dev/.claude/plugins/cache/mega-code/skills/login",
            ),
            _turn(2, role="assistant", content="Starting the oauth flow for login"),
            _turn(
                3,
                role="assistant",
                content="Login successful. MEGA_CODE_API_KEY is set.",
            ),
            # Real work (should be kept)
            _turn(4, content="Fix the bug in user registration"),
            _turn(5, role="assistant", content="I'll check the registration endpoint"),
            _turn(
                6,
                role="assistant",
                content="Found the issue in validate_email()",
                tool_name="Read",
                tool_target="/project/src/auth.py",
            ),
            _turn(7, content="Looks good, ship it"),
        ]
        result = clean_mega_code_turns(turns)
        assert len(result.removed) == 4
        assert len(result.kept) == 4
        assert result.kept[0].content == "Fix the bug in user registration"
        assert result.kept[0].turn_id == 0  # reindexed

    def test_mega_turns_interspersed_with_real_work(self):
        """Mega-code turns in the middle of real work — only mega block removed."""
        turns = [
            _turn(0, content="Add caching to the API"),
            _turn(1, role="assistant", content="I'll add Redis caching"),
            # Mega interruption
            _turn(2, content="<command-name>mega-code:status</command-name>"),
            _turn(3, role="assistant", content="You have 3 pending skills to review"),
            # Back to real work
            _turn(4, content="OK continue with caching"),
            _turn(5, role="assistant", content="Added Redis client to the service"),
        ]
        result = clean_mega_code_turns(turns)
        assert len(result.removed) == 2
        assert len(result.kept) == 4


# ===========================================================================
# save_cleaning_debug
# ===========================================================================


class TestSaveCleaningDebug:
    def test_no_removed_skips_writing(self, tmp_path):
        turns = [_turn(0, content="hello")]
        result = CleaningResult(kept=turns, removed=[])
        save_cleaning_debug(turns, result, tmp_path)
        assert not (tmp_path / "turns-original.jsonl").exists()
        assert not (tmp_path / "turns-removed.jsonl").exists()

    def test_writes_debug_files(self, tmp_path):
        original = [
            _turn(0, content="hello"),
            _turn(1, content="<command-name>mega-code:run</command-name>"),
        ]
        removed = [original[1]]
        kept = [original[0]]
        result = CleaningResult(kept=kept, removed=removed)

        save_cleaning_debug(original, result, tmp_path)

        original_path = tmp_path / "turns-original.jsonl"
        removed_path = tmp_path / "turns-removed.jsonl"
        assert original_path.exists()
        assert removed_path.exists()

        original_lines = original_path.read_text().strip().split("\n")
        assert len(original_lines) == 2

        removed_lines = removed_path.read_text().strip().split("\n")
        assert len(removed_lines) == 1

        removed_turn = json.loads(removed_lines[0])
        assert "mega-code:run" in removed_turn["content"]

    def test_oserror_logs_warning(self, tmp_path, monkeypatch, caplog):
        original = [_turn(0, content="x")]
        removed = [_turn(1, content="<command-name>mega-code:run</command-name>")]
        result = CleaningResult(kept=original, removed=removed)

        import builtins

        real_open = builtins.open

        def fake_open(path, *args, **kwargs):
            if "turns-original.jsonl" in str(path):
                raise PermissionError("denied")
            return real_open(path, *args, **kwargs)

        monkeypatch.setattr(builtins, "open", fake_open)

        with caplog.at_level("WARNING"):
            save_cleaning_debug(original, result, tmp_path)

        warnings = [r for r in caplog.records if "Failed to save cleaning debug" in r.message]
        assert warnings
        assert warnings[-1].exc_info is not None

    def test_caller_must_pass_anonymized_turns(self, tmp_path):
        """S1-2 contract: save_cleaning_debug writes whatever it's given verbatim,
        so callers must anonymize *before* calling it. This test pins that the
        function does not itself scrub secrets — a guard that the caller-side
        pipelines (sync.py, collector.py) stay responsible for masking."""
        raw = [_turn(0, content="API key: mg_live_abc123", command="echo $MEGA_CODE_API_KEY")]
        removed = [_turn(1, content="<command-name>mega-code:run</command-name>")]
        result = CleaningResult(kept=raw, removed=removed)
        save_cleaning_debug(raw + removed, result, tmp_path)
        text = (tmp_path / "turns-original.jsonl").read_text()
        # If this ever changes (e.g. save_cleaning_debug gains its own masker),
        # the call-site reorder in sync.py/collector.py can be simplified.
        assert "mg_live_abc123" in text

    def test_creates_directory_if_missing(self, tmp_path):
        nested = tmp_path / "deep" / "session"
        original = [_turn(0, content="x")]
        removed = [_turn(1, content="<command-name>mega-code:run</command-name>")]
        result = CleaningResult(kept=original, removed=removed)
        save_cleaning_debug(original, result, nested)
        assert (nested / "turns-removed.jsonl").exists()


# ===========================================================================
# Real-session regression fixtures
#
# Each fixture directory under tests/fixtures/cleaning_sessions/<session_id>/
# contains a real captured session:
#   turns-original.jsonl — input turns (pre-cleaning)
#   turns-removed.jsonl  — turns the filter should remove (pinned expected output)
#
# These guard against silent regressions in the anchor/expansion rules when the
# cleaning module is refactored. To refresh: re-run the cleaning pipeline on a
# trusted session and copy both files in.
# ===========================================================================


FIXTURES_DIR = Path(__file__).parent.parent / "fixtures" / "cleaning_sessions"


def _load_turns(path: Path) -> list[Turn]:
    with open(path) as f:
        return [Turn(**json.loads(line)) for line in f if line.strip()]


def _fixture_session_ids() -> list[str]:
    if not FIXTURES_DIR.exists():
        return []
    return sorted(
        d.name
        for d in FIXTURES_DIR.iterdir()
        if d.is_dir() and (d / "turns-original.jsonl").exists()
    )


class TestRealSessionRegression:
    @pytest.mark.parametrize("session_id", _fixture_session_ids())
    def test_removed_turns_match_pinned_output(self, session_id: str):
        session_dir = FIXTURES_DIR / session_id
        original = _load_turns(session_dir / "turns-original.jsonl")
        expected_removed = _load_turns(session_dir / "turns-removed.jsonl")

        result = clean_mega_code_turns(original)

        # Compare by turn_id — the stable identifier from the original session.
        actual_ids = [t.turn_id for t in result.removed]
        expected_ids = [t.turn_id for t in expected_removed]
        assert actual_ids == expected_ids, (
            f"Removed turn_ids diverged for session {session_id}.\n"
            f"  expected: {expected_ids}\n"
            f"  actual:   {actual_ids}"
        )

        # And full content equality — catches field-level regressions.
        assert result.removed == expected_removed
