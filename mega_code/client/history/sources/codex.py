"""Codex CLI data source (~/.codex/sessions/).

Loads historical conversation data from Codex CLI's storage format.
"""

import hashlib
import json
import logging
from collections.abc import Callable, Iterator
from datetime import datetime
from pathlib import Path
from typing import Any

from mega_code.client.history.models import (
    HistorySessionMetadata,
    Message,
    Session,
    TokenUsage,
    ToolCall,
)

logger = logging.getLogger(__name__)


class CodexSource:
    """Load Codex CLI data from ~/.codex/sessions/.

    Parses the Codex CLI session format including:
    - JSONL session files organized by date
    - Session metadata, messages, tool calls, and reasoning

    Example:
        source = CodexSource()
        # Or with custom path:
        source = CodexSource(base_path=Path("/custom/path"))

        for session in source.iter_sessions():
            print(f"Session: {session.metadata.session_id}")
            print(f"  Messages: {len(session.messages)}")
            print(f"  Tool calls: {session.stats.tool_call_count}")
    """

    def __init__(self, base_path: Path | None = None):
        """Initialize the Codex source.

        Args:
            base_path: Base directory for Codex sessions.
                      Defaults to ~/.codex/sessions/
        """
        self.base_path = base_path or Path.home() / ".codex" / "sessions"

    @property
    def name(self) -> str:
        """Return the source identifier."""
        return "codex_cli"

    def _iter_session_files(self) -> Iterator[Path]:
        """Iterate over session JSONL files.

        Searches recursively for *.jsonl files in the sessions directory.
        """
        if not self.base_path.exists():
            logger.warning(f"Codex sessions directory not found: {self.base_path}")
            return

        for jsonl_file in self.base_path.rglob("*.jsonl"):
            yield jsonl_file

    def _load_jsonl_entries(self, jsonl_path: Path) -> list[dict[str, Any]]:
        """Load all entries from a JSONL file.

        Args:
            jsonl_path: Path to the JSONL file.

        Returns:
            List of parsed JSON entries.
        """
        entries = []
        if not jsonl_path.exists():
            return entries

        try:
            with open(jsonl_path, encoding="utf-8") as f:
                for line_num, line in enumerate(f, 1):
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        entries.append(json.loads(line))
                    except json.JSONDecodeError as e:
                        logger.debug(f"Failed to parse line {line_num} in {jsonl_path}: {e}")
        except OSError as e:
            logger.warning(f"Failed to read JSONL file {jsonl_path}: {e}")

        return entries

    def _extract_session_metadata(
        self, entries: list[dict[str, Any]], session_path: Path
    ) -> HistorySessionMetadata | None:
        """Extract session metadata from JSONL entries.

        Args:
            entries: List of JSONL entries.
            session_path: Path to the session file.

        Returns:
            HistorySessionMetadata object, or None if no valid session_meta found.
        """
        # Find session_meta entry
        session_meta = next((e for e in entries if e.get("type") == "session_meta"), None)
        if not session_meta:
            return None

        payload = session_meta.get("payload", {})
        if not payload:
            return None
        session_id = payload.get("id", "")

        # Get first turn context for model
        first_turn = next((e for e in entries if e.get("type") == "turn_context"), None)
        model_id = first_turn.get("payload", {}).get("model") if first_turn else None

        # Get first user message
        first_prompt = None
        for entry in entries:
            if entry.get("type") == "event_msg" and entry["payload"].get("type") == "user_message":
                first_prompt = entry["payload"].get("message", "")
                break

        # Get session timestamps
        started_at = None
        if payload.get("timestamp"):
            try:
                started_at = datetime.fromisoformat(payload["timestamp"].replace("Z", "+00:00"))
            except (ValueError, AttributeError):
                pass

        ended_at = None
        if entries:
            timestamps = [e["timestamp"] for e in entries if e.get("timestamp")]
            last_timestamp = max(timestamps) if timestamps else None
            if last_timestamp:
                try:
                    ended_at = datetime.fromisoformat(last_timestamp.replace("Z", "+00:00"))
                except (ValueError, AttributeError):
                    pass

        # Extract git info
        git_info = payload.get("git", {})

        return HistorySessionMetadata(
            session_id=session_id,
            source=self.name,
            project_path=payload.get("cwd"),
            git_branch=git_info.get("branch"),
            model_id=model_id,
            started_at=started_at,
            ended_at=ended_at,
            first_prompt=first_prompt,
            extra={
                "codex_originator": payload.get("originator"),
                "codex_cli_version": payload.get("cli_version"),
                "codex_source": payload.get("source"),
                "codex_model_provider": payload.get("model_provider"),
                "codex_git_commit": git_info.get("commit_hash"),
                "codex_git_repository": git_info.get("repository_url"),
                "codex_session_file": str(session_path),
            },
        )

    def _parse_messages(self, entries: list[dict[str, Any]]) -> list[Message]:
        """Parse messages from JSONL entries.

        Args:
            entries: List of JSONL entries.

        Returns:
            List of Message objects.
        """
        messages = []
        pending_items: list[dict[str, Any]] = []  # Items before next message
        token_usage_map: dict[int, dict[str, Any]] = {}

        # Collect token usage by index
        token_count_idx = 0
        for entry in entries:
            if entry.get("type") == "event_msg" and entry["payload"].get("type") == "token_count":
                info = entry["payload"].get("info")
                if info and info.get("last_token_usage"):
                    token_usage_map[token_count_idx] = info["last_token_usage"]
                    token_count_idx += 1

        # Process response_items
        for entry in entries:
            if entry.get("type") != "response_item":
                continue

            payload = entry["payload"]
            payload_type = payload.get("type")

            if payload_type == "message":
                # Create message with pending items (tool calls before this message)
                msg = self._assemble_message(
                    [entry] + pending_items, {}, token_usage_map.get(len(messages))
                )
                if msg:  # Skip None (filtered developer messages)
                    messages.append(msg)
                pending_items = []

            elif payload_type in ("function_call", "function_call_output", "reasoning"):
                # Collect items that belong to the next message
                pending_items.append(entry)

        return messages

    def _assemble_message(
        self,
        items: list[dict[str, Any]],
        tool_calls_map: dict[str, dict[str, Any]],
        token_usage_data: dict[str, Any] | None,
    ) -> Message | None:
        """Assemble a message from turn items.

        Args:
            items: List of response_item entries for this turn.
            tool_calls_map: Map of tool call IDs to call/output data.
            token_usage_data: Token usage data for this turn.

        Returns:
            Message object or None if message should be filtered.
        """
        # Find the message entry
        msg_entry = next((item for item in items if item["payload"]["type"] == "message"), None)
        if not msg_entry:
            return None

        payload = msg_entry["payload"]
        role = payload.get("role", "")

        # Filter out developer role messages
        if role == "developer":
            return None

        # Map role
        role_map = {"user": "user", "assistant": "assistant"}
        mapped_role = role_map.get(role, role)
        if mapped_role not in ("user", "assistant", "system"):
            return None

        # Extract content
        content_parts = []
        for block in payload.get("content", []):
            if isinstance(block, dict):
                block_type = block.get("type", "")
                if block_type in ("input_text", "output_text"):
                    content_parts.append(block.get("text", ""))

        content = "\n".join(content_parts)

        # Extract timestamp
        timestamp = None
        ts_str = msg_entry.get("timestamp")
        if ts_str:
            try:
                timestamp = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
            except (ValueError, AttributeError):
                pass

        # Extract tool calls and match with results
        tool_calls = []
        call_entries = {}
        output_entries = {}

        for item in items:
            item_payload = item["payload"]
            if item_payload["type"] == "function_call":
                call_id = item_payload.get("call_id")
                if not call_id:
                    continue
                call_entries[call_id] = item
            elif item_payload["type"] == "function_call_output":
                call_id = item_payload.get("call_id")
                if not call_id:
                    continue
                output_entries[call_id] = item

        # Build ToolCall objects
        for call_id, call_entry in call_entries.items():
            call_payload = call_entry["payload"]
            output_entry = output_entries.get(call_id)

            # Parse arguments
            try:
                arguments = json.loads(call_payload.get("arguments", "{}"))
            except json.JSONDecodeError:
                arguments = {}

            # Extract output and error status
            output = None
            is_error = False
            if output_entry:
                output = output_entry["payload"].get("output", "")
                # Check for non-zero exit codes
                if isinstance(output, str) and "Exit code:" in output:
                    if not output.strip().startswith("Exit code: 0"):
                        is_error = True

            # Compute duration
            duration_ms = None
            if output_entry:
                try:
                    call_time = datetime.fromisoformat(
                        call_entry["timestamp"].replace("Z", "+00:00")
                    )
                    output_time = datetime.fromisoformat(
                        output_entry["timestamp"].replace("Z", "+00:00")
                    )
                    delta = output_time - call_time
                    duration_ms = int(delta.total_seconds() * 1000)
                except (ValueError, AttributeError):
                    pass

            tool_calls.append(
                ToolCall(
                    tool_id=call_id,
                    tool_name=call_payload.get("name", "unknown"),
                    input=arguments,
                    output=output,
                    is_error=is_error,
                    duration_ms=duration_ms,
                    status="success" if not is_error else "error",
                )
            )

        # Extract reasoning (skip encrypted)
        reasoning = []
        for item in items:
            if item["payload"]["type"] == "reasoning":
                # Skip if encrypted
                if item["payload"].get("encrypted_content"):
                    continue

                # Extract summary
                summary_texts = [s.get("text", "") for s in item["payload"].get("summary", [])]
                if summary_texts:
                    reasoning.append(
                        {
                            "summary": "\n".join(summary_texts),
                            "timestamp": item.get("timestamp", ""),
                        }
                    )

        # Parse token usage
        token_usage = None
        if token_usage_data:
            token_usage = TokenUsage(
                input_tokens=token_usage_data.get("input_tokens", 0),
                output_tokens=token_usage_data.get("output_tokens", 0),
                cache_read_tokens=token_usage_data.get("cached_input_tokens", 0),
                cache_create_tokens=0,  # Not separately tracked
            )

        content_hash = hashlib.sha256(json.dumps(msg_entry, sort_keys=True).encode()).hexdigest()[
            :12
        ]
        return Message(
            id=f"{msg_entry.get('timestamp', '')}-{content_hash}",
            role=mapped_role,  # type: ignore[arg-type]
            content=content,
            tool_calls=tool_calls,
            tool_results=[],  # Results embedded in tool_calls
            timestamp=timestamp,
            token_usage=token_usage,
            model=None,  # Model is per-turn in turn_context, not per-message
            reasoning=reasoning if reasoning else None,
            raw=msg_entry,
        )

    def list_sessions(self) -> Iterator[HistorySessionMetadata]:
        """List available sessions without loading messages."""
        for session_path in self._iter_session_files():
            try:
                entries = self._load_jsonl_entries(session_path)
                if entries:
                    metadata = self._extract_session_metadata(entries, session_path)
                    if metadata is not None:
                        yield metadata
            except Exception as e:
                logger.warning(f"Failed to extract metadata from {session_path}: {e}")

    def load_session(self, session_id: str) -> Session:
        """Load a complete session by ID.

        Searches all session files for the matching session ID.

        Args:
            session_id: The session ID to load.

        Returns:
            Complete Session object.

        Raises:
            KeyError: If session not found.
        """
        for session_path in self._iter_session_files():
            entries = self._load_jsonl_entries(session_path)
            if entries:
                session_meta = next((e for e in entries if e.get("type") == "session_meta"), None)
                if session_meta and session_meta.get("payload", {}).get("id") == session_id:
                    return self._load_session_from_entries(entries, session_path)

        raise KeyError(f"Session not found: {session_id}")

    def _load_session_from_entries(
        self, entries: list[dict[str, Any]], session_path: Path
    ) -> Session:
        """Load a session from JSONL entries.

        Args:
            entries: List of JSONL entries.
            session_path: Path to the session file.

        Returns:
            Complete Session object.
        """
        metadata = self._extract_session_metadata(entries, session_path)
        if metadata is None:
            metadata = HistorySessionMetadata(
                session_id="",
                source=self.name,
            )
        messages = self._parse_messages(entries)

        return Session(metadata=metadata, messages=messages)

    def iter_sessions(self) -> Iterator[Session]:
        """Iterate over all sessions with lazy loading."""
        for session_path in self._iter_session_files():
            try:
                entries = self._load_jsonl_entries(session_path)
                if entries:
                    yield self._load_session_from_entries(entries, session_path)
            except Exception as e:
                logger.warning(f"Failed to load session from {session_path}: {e}")

    def count_sessions(self) -> int:
        """Return the total number of sessions."""
        count = 0
        for _ in self._iter_session_files():
            count += 1
        return count

    def iter_sessions_by_project_paths(
        self,
        project_paths: list[str],
        path_matcher: Callable[[str, set[str]], bool] | None = None,
    ) -> Iterator[dict[str, Any]]:
        """Iterate over session entries matching project paths.

        Args:
            project_paths: List of project paths to filter by.
            path_matcher: Optional custom path matching function.
                         Signature: (session_path: str, target_paths: set[str]) -> bool
                         If None, uses path_utils.should_include_session.

        Yields:
            Session metadata entries (dicts) containing session_meta payload
            and session_file_path for loading full session.

        Notes:
            - Returns raw session metadata, not full Session objects
            - Matches against 'cwd' field in session metadata
            - Excludes sessions without 'cwd' field
            - Uses path_utils for normalization and matching
        """
        if not project_paths:
            return

        # Import path utilities
        from mega_code.client.utils.path_utils import normalize_path, should_include_session

        # Normalize target paths
        normalized_targets = {normalize_path(p) for p in project_paths}

        # Iterate over all JSONL files
        for jsonl_file in self._iter_session_files():
            try:
                entries = self._load_jsonl_entries(jsonl_file)
                if not entries:
                    continue

                # Find session_meta entry
                session_meta = next((e for e in entries if e.get("type") == "session_meta"), None)
                if not session_meta:
                    continue

                # Extract cwd from payload
                payload = session_meta.get("payload", {})
                session_cwd = payload.get("cwd")
                if not session_cwd:
                    continue

                # Check if session matches target paths
                try:
                    if path_matcher is not None:
                        if path_matcher(session_cwd, normalized_targets):
                            yield {
                                "payload": payload,
                                "session_file_path": str(jsonl_file),
                            }
                    else:
                        if should_include_session(session_cwd, normalized_targets):
                            yield {
                                "payload": payload,
                                "session_file_path": str(jsonl_file),
                            }
                except Exception as e:
                    logger.warning(
                        f"Error checking path match for session {payload.get('id', 'unknown')}: {e}"
                    )
            except Exception as e:
                logger.debug(f"Failed to process session file {jsonl_file}: {e}")
