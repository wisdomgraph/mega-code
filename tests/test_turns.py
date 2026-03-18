"""Tests for TurnExtractor — codex key mapping and validation."""

import pytest

from mega_code.client.history.models import Message, ToolCall
from mega_code.client.turns import TurnExtractor


def _msg_with_tool_call(tool_name: str, inputs: dict) -> Message:
    """Create an assistant message with a single tool call."""
    return Message(
        id="msg-1",
        role="assistant",
        content="running command",
        tool_calls=[
            ToolCall(
                tool_id="tc-1",
                tool_name=tool_name,
                input=inputs,
            )
        ],
    )


class TestExecCommandCmdKey:
    """exec_command turns must use the 'cmd' key (codex format)."""

    def test_cmd_key_extracted(self):
        msg = _msg_with_tool_call("exec_command", {"cmd": "free -h"})
        extractor = TurnExtractor(compact_code=False)
        turn = extractor._message_to_turn(0, msg)

        assert turn is not None
        assert turn.command == "free -h"
        assert turn.tool_name == "exec_command"

    def test_exec_command_missing_cmd_raises(self):
        msg = _msg_with_tool_call("exec_command", {"command": "free -h"})
        extractor = TurnExtractor(compact_code=False)

        with pytest.raises(ValueError, match="exec_command turn 0 has no 'cmd'"):
            extractor._message_to_turn(0, msg)

    def test_exec_command_empty_input_raises(self):
        msg = _msg_with_tool_call("exec_command", {})
        extractor = TurnExtractor(compact_code=False)

        with pytest.raises(ValueError, match="exec_command turn 0 has no 'cmd'"):
            extractor._message_to_turn(0, msg)


class TestToolTarget:
    """tool_target should check file_path, path, and target_file keys."""

    def test_file_path_key(self):
        msg = _msg_with_tool_call("read_file", {"file_path": "/tmp/foo.py"})
        extractor = TurnExtractor(compact_code=False)
        turn = extractor._message_to_turn(0, msg)

        assert turn is not None
        assert turn.tool_target == "/tmp/foo.py"

    def test_path_key(self):
        msg = _msg_with_tool_call("read_file", {"path": "/tmp/bar.py"})
        extractor = TurnExtractor(compact_code=False)
        turn = extractor._message_to_turn(0, msg)

        assert turn is not None
        assert turn.tool_target == "/tmp/bar.py"

    def test_target_file_key(self):
        msg = _msg_with_tool_call("apply_diff", {"target_file": "/tmp/baz.py"})
        extractor = TurnExtractor(compact_code=False)
        turn = extractor._message_to_turn(0, msg)

        assert turn is not None
        assert turn.tool_target == "/tmp/baz.py"


class TestNonExecCommandTools:
    """Non-exec_command tools should not raise when 'cmd' is absent."""

    def test_read_file_no_cmd_ok(self):
        msg = _msg_with_tool_call("read_file", {"file_path": "/tmp/foo.py"})
        extractor = TurnExtractor(compact_code=False)
        turn = extractor._message_to_turn(0, msg)

        assert turn is not None
        assert turn.command is None
