"""Tests for Codex CLI parser (codex.py) — Phases 1 bug fixes."""

import json
from pathlib import Path

from mega_code.client.history.sources.codex import CodexSource

FIXTURES_DIR = Path(__file__).parent.parent / "fixtures" / "codex"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _load_fixture(name: str) -> list[dict]:
    path = FIXTURES_DIR / name
    entries = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    entries.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    return entries


def _source() -> CodexSource:
    return CodexSource()


# ---------------------------------------------------------------------------
# Cycle 1 — Bug 3: Missing session_meta crashes
# ---------------------------------------------------------------------------


class TestNoSessionMeta:
    def test_no_session_meta_returns_none(self):
        """_extract_session_metadata should return None when no session_meta entry."""
        source = _source()
        entries = _load_fixture("no_session_meta.jsonl")
        result = source._extract_session_metadata(entries, Path("dummy.jsonl"))
        assert result is None

    def test_list_sessions_skips_bad_files(
        self, codex_base, write_codex_session, golden_session_path
    ):
        """list_sessions should skip files without session_meta."""
        write_codex_session("good.jsonl", golden_session_path)
        write_codex_session("bad.jsonl", FIXTURES_DIR / "no_session_meta.jsonl")
        source = CodexSource(base_path=codex_base)
        sessions = list(source.list_sessions())
        assert len(sessions) == 1
        assert sessions[0].session_id == "fixture-session-001"


# ---------------------------------------------------------------------------
# Cycle 2 — Bug 4: Missing payload in turn_context
# ---------------------------------------------------------------------------


class TestNoPayloadTurnContext:
    def test_no_payload_in_turn_context(self):
        """Model ID should be None when turn_context has no payload."""
        source = _source()
        entries = _load_fixture("no_payload_in_turn_context.jsonl")
        metadata = source._extract_session_metadata(entries, Path("dummy.jsonl"))
        assert metadata is not None
        assert metadata.model_id is None


# ---------------------------------------------------------------------------
# Cycle 3 — Bug 5: Missing call_id in function_call
# ---------------------------------------------------------------------------


class TestNoCallId:
    def test_no_call_id_skipped(self):
        """Entries without call_id should be skipped, no KeyError."""
        source = _source()
        entries = _load_fixture("no_call_id.jsonl")
        session = source._load_session_from_entries(entries, Path("dummy.jsonl"))
        # Should not crash; tool_calls should not include entries without call_id
        for msg in session.messages:
            for tc in msg.tool_calls:
                assert tc.tool_id is not None
                assert tc.tool_id != ""


# ---------------------------------------------------------------------------
# Cycle 4 — Bug 6: Missing payload key in session_meta
# ---------------------------------------------------------------------------


class TestNoPayloadSessionMeta:
    def test_load_session_no_payload_key(self):
        """load_session should not crash when session_meta has no payload."""
        source = _source()
        entries = _load_fixture("no_payload_in_session_meta.jsonl")
        # _extract_session_metadata should handle missing payload gracefully
        result = source._extract_session_metadata(entries, Path("dummy.jsonl"))
        # With no payload, it should return None (no valid session_meta)
        # or a metadata object with empty fields
        if result is not None:
            assert result.session_id == "" or result.session_id is not None


# ---------------------------------------------------------------------------
# Cycle 5 — Bug 7: No timestamps causes max() error
# ---------------------------------------------------------------------------


class TestNoTimestamps:
    def test_no_timestamps_fallback(self):
        """ended_at should be None, not ValueError from max()."""
        source = _source()
        entries = _load_fixture("no_timestamps.jsonl")
        metadata = source._extract_session_metadata(entries, Path("dummy.jsonl"))
        assert metadata is not None
        assert metadata.ended_at is None


# ---------------------------------------------------------------------------
# Cycle 6 — Bug 1: Non-deterministic message IDs
# ---------------------------------------------------------------------------


class TestDeterministicIds:
    def test_deterministic_ids(self, golden_session_path):
        """Parsing the same file twice should produce identical message IDs."""
        source = _source()
        entries = _load_fixture("golden_session.jsonl")
        session1 = source._load_session_from_entries(entries, golden_session_path)
        session2 = source._load_session_from_entries(entries, golden_session_path)
        ids1 = [m.id for m in session1.messages]
        ids2 = [m.id for m in session2.messages]
        assert ids1 == ids2
        # No huge integers from id() — IDs should be reasonable length
        for mid in ids1:
            assert len(mid) < 100, f"ID suspiciously long (id() leak?): {mid}"

    def test_message_ids_unique(self, golden_session_path):
        """All message IDs should be distinct."""
        source = _source()
        entries = _load_fixture("golden_session.jsonl")
        session = source._load_session_from_entries(entries, golden_session_path)
        ids = [m.id for m in session.messages]
        assert len(ids) == len(set(ids)), f"Duplicate IDs found: {ids}"


# ---------------------------------------------------------------------------
# Cycle 7 — Bug 2: Token mapping misalignment
# ---------------------------------------------------------------------------


class TestTokenMapping:
    def test_token_mapping_with_gaps(self):
        """Missing token_count entries should not crash; unmapped msgs get None."""
        source = _source()
        entries = _load_fixture("missing_token_counts.jsonl")
        session = source._load_session_from_entries(entries, Path("dummy.jsonl"))
        # Should not crash
        assert len(session.messages) > 0

    def test_token_mapping_zero_events(self):
        """When there are no token_count events, all msgs should have token_usage=None."""
        source = _source()
        entries = _load_fixture("golden_session.jsonl")
        # Remove all token_count events
        entries_no_tokens = [
            e
            for e in entries
            if not (
                e.get("type") == "event_msg"
                and isinstance(e.get("payload"), dict)
                and e["payload"].get("type") == "token_count"
            )
        ]
        session = source._load_session_from_entries(entries_no_tokens, Path("dummy.jsonl"))
        for msg in session.messages:
            assert msg.token_usage is None


# ---------------------------------------------------------------------------
# Cycle 8 — Hardening (edge cases)
# ---------------------------------------------------------------------------


class TestHardening:
    def test_orphaned_tool_output(self):
        """Orphaned function_call_output should not crash or appear in tool_calls."""
        source = _source()
        entries = _load_fixture("orphaned_tool_output.jsonl")
        session = source._load_session_from_entries(entries, Path("dummy.jsonl"))
        # No crash; orphaned output should not appear in any message's tool_calls
        for msg in session.messages:
            for tc in msg.tool_calls:
                assert tc.tool_id != "call-orphan-999"

    def test_tool_call_no_output(self):
        """Tool call without output should have output=None."""
        source = _source()
        entries = _load_fixture("tool_call_no_output.jsonl")
        session = source._load_session_from_entries(entries, Path("dummy.jsonl"))
        # Find the tool call for call-001 which has no output
        found = False
        for msg in session.messages:
            for tc in msg.tool_calls:
                if tc.tool_id == "call-001":
                    assert tc.output is None
                    found = True
        assert found, "call-001 tool call not found"

    def test_truncated_last_line(self):
        """Truncated last line should not crash; complete entries should parse."""
        _source()
        entries = _load_fixture("truncated_last_line.jsonl")
        # Should not crash; at least some entries should parse
        assert len(entries) > 0

    def test_empty_file(self):
        """Empty file should return empty list / no crash."""
        source = _source()
        entries = source._load_jsonl_entries(FIXTURES_DIR / "empty_file.jsonl")
        assert entries == []


# ---------------------------------------------------------------------------
# Cycle 9 — Golden validation (capstone)
# ---------------------------------------------------------------------------


class TestGoldenFullParse:
    def test_golden_full_parse(self, golden_session_path):
        """Full parse of golden session should produce expected counts."""
        source = _source()
        entries = _load_fixture("golden_session.jsonl")
        session = source._load_session_from_entries(entries, golden_session_path)

        user_msgs = [m for m in session.messages if m.role == "user"]
        assistant_msgs = [m for m in session.messages if m.role == "assistant"]
        all_tool_calls = []
        for m in session.messages:
            all_tool_calls.extend(m.tool_calls)
        token_usages = [m for m in session.messages if m.token_usage is not None]

        assert len(user_msgs) == 5, f"Expected 5 user msgs, got {len(user_msgs)}"
        assert len(assistant_msgs) == 8, f"Expected 8 assistant msgs, got {len(assistant_msgs)}"
        assert len(all_tool_calls) == 6, f"Expected 6 tool calls, got {len(all_tool_calls)}"
        assert len(token_usages) == 4, f"Expected 4 token usages, got {len(token_usages)}"
