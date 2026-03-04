"""Gemini CLI data source (~/.gemini/tmp/).

Loads historical conversation data from Gemini CLI's storage format.
"""

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator

from mega_code.client.history.models import (
    HistorySessionMetadata,
    Message,
    Session,
    TokenUsage,
    ToolCall,
)

logger = logging.getLogger(__name__)


class GeminiSource:
    """Load Gemini CLI data from ~/.gemini/tmp/.

    Parses the Gemini CLI session format including:
    - Chat JSON files in chats/ subdirectories
    - Project-specific session storage

    Example:
        source = GeminiSource()
        # Or with custom path:
        source = GeminiSource(base_path=Path("/custom/path"))

        for session in source.iter_sessions():
            print(f"Session: {session.metadata.session_id}")
            print(f"  Messages: {len(session.messages)}")
            print(f"  Tool calls: {session.stats.tool_call_count}")
    """

    def __init__(self, base_path: Path | None = None):
        """Initialize the Gemini source.

        Args:
            base_path: Base directory for Gemini temporary files.
                      Defaults to ~/.gemini/tmp/
        """
        self.base_path = base_path or Path.home() / ".gemini" / "tmp"

    @property
    def name(self) -> str:
        """Return the source identifier."""
        return "gemini_cli"

    def _iter_project_dirs(self) -> Iterator[Path]:
        """Iterate over project directories (project hashes)."""
        if not self.base_path.exists():
            logger.warning(f"Gemini tmp directory not found: {self.base_path}")
            return

        for project_dir in self.base_path.iterdir():
            if project_dir.is_dir() and (project_dir / "chats").exists():
                yield project_dir

    def _iter_chat_files(self, project_dir: Path) -> Iterator[Path]:
        """Iterate over chat JSON files in a project's chats directory.

        Args:
            project_dir: Path to the project directory.

        Returns:
            Iterator of chat file paths.
        """
        chats_dir = project_dir / "chats"
        if not chats_dir.exists():
            return

        for chat_file in chats_dir.glob("*.json"):
            yield chat_file

    def _resolve_project_path(self, project_hash: str) -> str | None:
        """Attempt to resolve project hash to actual project path.

        Args:
            project_hash: The project hash directory name.

        Returns:
            Resolved project path or None if not found.
        """
        # TODO: Implement project path resolution from Gemini config
        # For now, we don't have a reliable way to reverse the hash
        # Could potentially scan common project directories and hash them
        # to match, but that's expensive. Leave as None for now.
        return None

    def _load_chat_file(self, chat_path: Path) -> dict[str, Any] | None:
        """Load a chat JSON file.

        Args:
            chat_path: Path to the chat JSON file.

        Returns:
            Parsed chat data or None on failure.
        """
        if not chat_path.exists():
            return None

        try:
            with open(chat_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            logger.warning(f"Failed to load chat file {chat_path}: {e}")
            return None

    def _parse_message(self, msg_data: dict[str, Any], session_id: str) -> Message | None:
        """Parse a Gemini message into a Message object.

        Args:
            msg_data: Raw message data from Gemini chat file.
            session_id: Session ID for context.

        Returns:
            A Message object or None if the message should be filtered.
        """
        msg_type = msg_data.get("type", "")

        # Filter out info messages
        if msg_type == "info":
            return None

        # Map role
        role_map = {"user": "user", "gemini": "assistant"}
        role = role_map.get(msg_type, msg_type)

        if role not in ("user", "assistant", "system"):
            return None

        # Extract basic fields
        msg_id = msg_data.get("id", f"{session_id}-{id(msg_data)}")
        content = msg_data.get("content", "")

        # Parse timestamp
        timestamp = None
        ts_str = msg_data.get("timestamp")
        if ts_str:
            try:
                timestamp = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
            except (ValueError, AttributeError):
                pass

        # Parse reasoning/thoughts
        reasoning = None
        if msg_data.get("thoughts"):
            reasoning = [
                {
                    "subject": t.get("subject", ""),
                    "description": t.get("description", ""),
                    "timestamp": t.get("timestamp", ""),
                }
                for t in msg_data["thoughts"]
            ]

        # Parse tool calls (with embedded results)
        tool_calls: list[ToolCall] = []
        if role == "assistant" and msg_data.get("toolCalls"):
            for tc in msg_data["toolCalls"]:
                # Extract result
                result_list = tc.get("result", [])
                result = result_list[0] if result_list else {}
                func_response = result.get("functionResponse", {}).get("response", {})

                output = func_response.get("output")
                error = func_response.get("error")

                # Determine status and error state
                status = tc.get("status", "success")
                is_error = (status == "cancelled") or (error is not None)

                tool_calls.append(
                    ToolCall(
                        tool_id=tc.get("id", ""),
                        tool_name=tc.get("name", "unknown"),
                        input=tc.get("args", {}),
                        output=error if error else output,
                        is_error=is_error,
                        status=status,
                        duration_ms=None,  # Not tracked in Gemini
                    )
                )

        # Parse token usage
        token_usage = None
        if msg_data.get("tokens"):
            tokens = msg_data["tokens"]
            token_usage = TokenUsage(
                input_tokens=tokens.get("input", 0),
                output_tokens=tokens.get("output", 0),
                cache_read_tokens=tokens.get("cached", 0),
                cache_create_tokens=0,  # Not tracked in Gemini
            )

        # Get model info
        model = msg_data.get("model")

        return Message(
            id=msg_id,
            role=role,  # type: ignore[arg-type]
            content=content,
            tool_calls=tool_calls,
            tool_results=[],  # Not used for Gemini (results embedded in tool_calls)
            timestamp=timestamp,
            token_usage=token_usage,
            model=model,
            reasoning=reasoning,
            raw=msg_data,  # Store full message for debugging
        )

    def _chat_to_metadata(
        self, chat_data: dict[str, Any], project_dir: Path, chat_path: Path
    ) -> HistorySessionMetadata:
        """Convert chat data to HistorySessionMetadata.

        Args:
            chat_data: Parsed chat JSON data.
            project_dir: Path to the project directory.
            chat_path: Path to the chat file.

        Returns:
            HistorySessionMetadata object.
        """
        session_id = chat_data.get("sessionId", "")
        project_hash = project_dir.name

        # Try to resolve actual project path
        project_path = self._resolve_project_path(project_hash)
        if not project_path:
            # Fall back to tmp directory path
            project_path = str(project_dir)

        # Parse timestamps
        started_at = None
        ended_at = None
        if chat_data.get("startTime"):
            try:
                started_at = datetime.fromisoformat(chat_data["startTime"].replace("Z", "+00:00"))
            except (ValueError, AttributeError):
                pass
        if chat_data.get("lastUpdated"):
            try:
                ended_at = datetime.fromisoformat(chat_data["lastUpdated"].replace("Z", "+00:00"))
            except (ValueError, AttributeError):
                pass

        # Extract first user message as first_prompt
        # TODO: Add truncation logic here if needed in the future
        first_prompt = None
        messages = chat_data.get("messages", [])
        for msg in messages:
            if msg.get("type") == "user":
                first_prompt = msg.get("content", "")
                break

        # Determine primary model from messages
        model_id = None
        for msg in messages:
            if msg.get("type") == "gemini" and msg.get("model"):
                model_id = msg["model"]
                break

        return HistorySessionMetadata(
            session_id=session_id,
            source=self.name,
            project_path=project_path,
            git_branch=None,  # Not tracked in Gemini
            model_id=model_id,
            started_at=started_at,
            ended_at=ended_at,
            first_prompt=first_prompt,  # Not truncated for now
            extra={
                "gemini_project_hash": project_hash,
                "gemini_chat_file": str(chat_path),
            },
        )

    def list_sessions(self) -> Iterator[HistorySessionMetadata]:
        """List available sessions without loading messages."""
        for project_dir in self._iter_project_dirs():
            for chat_path in self._iter_chat_files(project_dir):
                chat_data = self._load_chat_file(chat_path)
                if chat_data:
                    yield self._chat_to_metadata(chat_data, project_dir, chat_path)

    def load_session(self, session_id: str) -> Session:
        """Load a complete session by ID.

        Searches all project directories for the session.

        Args:
            session_id: The session ID to load.

        Returns:
            Complete Session object.

        Raises:
            KeyError: If session not found.
        """
        for project_dir in self._iter_project_dirs():
            for chat_path in self._iter_chat_files(project_dir):
                chat_data = self._load_chat_file(chat_path)
                if chat_data and chat_data.get("sessionId") == session_id:
                    return self._load_session_from_chat(chat_data, project_dir, chat_path)

        raise KeyError(f"Session not found: {session_id}")

    def _load_session_from_chat(
        self, chat_data: dict[str, Any], project_dir: Path, chat_path: Path
    ) -> Session:
        """Load a session from chat data.

        Args:
            chat_data: Parsed chat JSON data.
            project_dir: Path to the project directory.
            chat_path: Path to the chat file.

        Returns:
            Complete Session object.
        """
        session_id = chat_data.get("sessionId", "")
        metadata = self._chat_to_metadata(chat_data, project_dir, chat_path)

        # Parse messages
        messages: list[Message] = []
        for msg_data in chat_data.get("messages", []):
            msg = self._parse_message(msg_data, session_id)
            if msg:
                messages.append(msg)

        return Session(metadata=metadata, messages=messages)

    def iter_sessions(self) -> Iterator[Session]:
        """Iterate over all sessions with lazy loading."""
        for project_dir in self._iter_project_dirs():
            for chat_path in self._iter_chat_files(project_dir):
                chat_data = self._load_chat_file(chat_path)
                if chat_data:
                    try:
                        yield self._load_session_from_chat(chat_data, project_dir, chat_path)
                    except Exception as e:
                        session_id = chat_data.get("sessionId", "unknown")
                        logger.warning(f"Failed to load session {session_id}: {e}")

    def count_sessions(self) -> int:
        """Return the total number of sessions."""
        count = 0
        for project_dir in self._iter_project_dirs():
            for _ in self._iter_chat_files(project_dir):
                count += 1
        return count
