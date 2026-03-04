"""OpenCode CLI data source (~/.local/share/opencode/storage/).

Loads historical conversation data from OpenCode's multi-file JSON storage format.
"""

import json
import logging
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

from mega_code.client.history.models import (
    HistorySessionMetadata,
    Message,
    Session,
    TokenUsage,
    ToolCall,
)

logger = logging.getLogger(__name__)


class OpenCodeSource:
    """Load OpenCode data from multi-file JSON storage.

    Parses OpenCode's distributed storage format including:
    - Session metadata (session files)
    - Message chains (message files with parentID links)
    - Content parts (text, tool calls, step markers)
    - Project metadata
    """

    def __init__(self, base_path: Path | None = None):
        """Initialize OpenCode source.

        Args:
            base_path: Base directory for OpenCode storage.
                      Defaults to ~/.local/share/opencode/storage/.
        """
        if base_path:
            self.base_path = Path(base_path).expanduser()
        else:
            self.base_path = Path("~/.local/share/opencode/storage").expanduser()

        self.session_dir = self.base_path / "session"
        self.message_dir = self.base_path / "message"
        self.part_dir = self.base_path / "part"
        self.project_dir = self.base_path / "project"

        # Cache for session_id → project_id mapping
        self._session_to_project: dict[str, str] = {}

    @property
    def name(self) -> str:
        """Return the source identifier."""
        return "opencode"

    def _ensure_session_map(self):
        """Build session_id → project_id mapping on first access."""
        if self._session_to_project:
            return

        if not self.session_dir.exists():
            return

        for project_dir in self.session_dir.iterdir():
            if not project_dir.is_dir():
                continue

            project_id = project_dir.name
            for session_file in project_dir.glob("*.json"):
                session_id = session_file.stem
                self._session_to_project[session_id] = project_id

    def list_sessions(self) -> Iterator[HistorySessionMetadata]:
        """List available sessions without loading messages."""
        if not self.session_dir.exists():
            logger.warning(f"Session directory not found: {self.session_dir}")
            return

        for project_dir in self.session_dir.iterdir():
            if not project_dir.is_dir():
                continue

            project_id = project_dir.name

            for session_file in project_dir.glob("*.json"):
                try:
                    with open(session_file) as f:
                        session_data = json.load(f)

                    session_id = session_data.get("id", session_file.stem)

                    # Load project metadata for directory path
                    project_data = self._load_project(project_id)
                    project_path = project_data.get("worktree") if project_data else None

                    # Parse timestamps
                    created_at = None
                    ended_at = None
                    if "time" in session_data:
                        created_ms = session_data["time"].get("created")
                        updated_ms = session_data["time"].get("updated")

                        if created_ms:
                            created_at = datetime.fromtimestamp(created_ms / 1000, tz=timezone.utc)
                        if updated_ms:
                            ended_at = datetime.fromtimestamp(updated_ms / 1000, tz=timezone.utc)

                    yield HistorySessionMetadata(
                        session_id=session_id,
                        source=self.name,
                        project_path=project_path,
                        git_branch=None,
                        model_id=None,
                        started_at=created_at,
                        ended_at=ended_at,
                        first_prompt=None,
                        extra={
                            "title": session_data.get("title"),
                            "project_id": project_id,
                            "version": session_data.get("version"),
                            "directory": session_data.get("directory"),
                        },
                    )

                except (json.JSONDecodeError, OSError) as e:
                    logger.warning(f"Failed to parse session file {session_file}: {e}")

    def count_sessions(self) -> int:
        """Return the total number of sessions."""
        return sum(1 for _ in self.list_sessions())

    def iter_sessions(self) -> Iterator[Session]:
        """Iterate over all sessions with lazy loading."""
        for metadata in self.list_sessions():
            try:
                yield self.load_session(metadata.session_id)
            except Exception as e:
                logger.warning(f"Failed to load session {metadata.session_id}: {e}")

    def load_session(self, session_id: str) -> Session:
        """Load a complete session by ID.

        Args:
            session_id: The session ID to load.

        Returns:
            Complete Session object.

        Raises:
            KeyError: If session not found.
        """
        # Ensure mapping is built
        self._ensure_session_map()

        # Find project_id for this session
        project_id = self._session_to_project.get(session_id)
        if not project_id:
            raise KeyError(f"Session not found: {session_id}")

        # Load session file
        session_file = self.session_dir / project_id / f"{session_id}.json"
        if not session_file.exists():
            raise KeyError(f"Session not found: {session_id}")

        with open(session_file) as f:
            session_data = json.load(f)

        # Load messages
        messages = self._load_messages(session_id)

        # Build metadata
        project_data = self._load_project(project_id)
        project_path = project_data.get("worktree") if project_data else None

        created_at = None
        ended_at = None
        if "time" in session_data:
            if created_ms := session_data["time"].get("created"):
                created_at = datetime.fromtimestamp(created_ms / 1000, tz=timezone.utc)
            if updated_ms := session_data["time"].get("updated"):
                ended_at = datetime.fromtimestamp(updated_ms / 1000, tz=timezone.utc)

        metadata = HistorySessionMetadata(
            session_id=session_id,
            source=self.name,
            project_path=project_path,
            git_branch=None,
            model_id=None,
            started_at=created_at,
            ended_at=ended_at,
            first_prompt=None,
            extra={
                "title": session_data.get("title"),
                "project_id": project_id,
                "version": session_data.get("version"),
                "directory": session_data.get("directory"),
            },
        )

        # Set first_prompt from first user message
        for msg in messages:
            if msg.role == "user":
                metadata.first_prompt = msg.content[:200]
                break

        return Session(metadata=metadata, messages=messages)

    def _load_messages(self, session_id: str) -> list[Message]:
        """Load all messages for a session, ordered by conversation flow.

        Args:
            session_id: Session ID to load messages for.

        Returns:
            List of messages in conversation order.
        """
        message_session_dir = self.message_dir / session_id

        if not message_session_dir.exists():
            logger.warning(f"Message directory not found: {message_session_dir}")
            return []

        # Load all message files
        message_files = list(message_session_dir.glob("*.json"))
        messages_data = []

        for msg_file in message_files:
            try:
                with open(msg_file) as f:
                    msg_data = json.load(f)
                    messages_data.append(msg_data)
            except (json.JSONDecodeError, OSError) as e:
                logger.warning(f"Failed to parse message file {msg_file}: {e}")

        # Order messages by parentID chain
        ordered_data = self._order_messages(messages_data)

        # Build Message objects
        messages = []
        for msg_data in ordered_data:
            try:
                msg = self._build_message(msg_data)
                if msg:
                    messages.append(msg)
            except Exception as e:
                logger.warning(f"Failed to build message {msg_data.get('id')}: {e}")

        return messages

    def _order_messages(self, messages: list[dict]) -> list[dict]:
        """Order messages by parentID chain and creation time.

        Args:
            messages: List of message data dicts.

        Returns:
            Ordered list following conversation flow.
        """
        if not messages:
            return []

        # Find root message (no parentID)
        roots = [m for m in messages if not m.get("parentID")]

        if not roots:
            # Fallback: sort by creation time
            logger.warning("No root message found, sorting by timestamp")
            return sorted(messages, key=lambda m: m.get("time", {}).get("created", 0))

        # Use earliest root if multiple
        root = min(roots, key=lambda m: m.get("time", {}).get("created", 0))

        # Build parent → children mapping
        children = defaultdict(list)
        msg_by_id = {m["id"]: m for m in messages}

        for msg in messages:
            if parent_id := msg.get("parentID"):
                children[parent_id].append(msg)

        # Traverse depth-first
        ordered = []
        visited = set()

        def traverse(msg_id):
            if msg_id in visited:
                logger.warning(f"Circular reference detected: {msg_id}")
                return

            visited.add(msg_id)

            if msg_id in msg_by_id:
                ordered.append(msg_by_id[msg_id])

            # Process children in creation time order
            child_list = sorted(
                children.get(msg_id, []), key=lambda m: m.get("time", {}).get("created", 0)
            )

            for child in child_list:
                traverse(child["id"])

        traverse(root["id"])

        # Add any orphaned messages at the end
        for msg in messages:
            if msg["id"] not in visited:
                ordered.append(msg)

        return ordered

    def _build_message(self, msg_data: dict) -> Message | None:
        """Build Message object from message data and parts.

        Args:
            msg_data: Message metadata dict.

        Returns:
            Complete Message object.
        """
        msg_id = msg_data.get("id", "")
        role = msg_data.get("role", "user")

        if role not in ("user", "assistant", "system"):
            logger.warning(f"Unknown role: {role}, defaulting to user")
            role = "user"

        # Load parts for this message
        parts = self._load_parts(msg_id)

        # Extract content from text parts
        text_parts = []
        for part in parts:
            if part.get("type") == "text":
                if text := part.get("text"):
                    text_parts.append(text)

        content = "\n".join(text_parts).strip()

        # Extract tool calls from tool parts
        tool_calls = []
        for part in parts:
            if part.get("type") == "tool":
                if tool_call := self._extract_tool_call(part):
                    tool_calls.append(tool_call)

        # Extract token usage
        token_usage = self._extract_token_usage(msg_data, parts)

        # Parse timestamp
        timestamp = None
        if "time" in msg_data:
            if created_ms := msg_data["time"].get("created"):
                timestamp = datetime.fromtimestamp(created_ms / 1000, tz=timezone.utc)

        # Extract model info
        model = None
        if model_id := msg_data.get("modelID"):
            provider = msg_data.get("providerID", "")
            model = f"{provider}/{model_id}" if provider else model_id

        return Message(
            id=msg_id,
            role=role,  # type: ignore[arg-type]
            content=content,
            tool_calls=tool_calls,
            tool_results=[],  # OpenCode embeds tool results in tool parts
            timestamp=timestamp,
            token_usage=token_usage,
            model=model,
            reasoning=None,
            raw=msg_data,
        )

    def _load_parts(self, message_id: str) -> list[dict]:
        """Load all parts for a message, ordered by creation time.

        Args:
            message_id: Message ID to load parts for.

        Returns:
            List of part dicts in creation order.
        """
        part_message_dir = self.part_dir / message_id

        if not part_message_dir.exists():
            return []

        parts = []
        for part_file in part_message_dir.glob("*.json"):
            try:
                with open(part_file) as f:
                    part_data = json.load(f)
                    parts.append(part_data)
            except (json.JSONDecodeError, OSError) as e:
                logger.warning(f"Failed to parse part file {part_file}: {e}")

        # Sort by creation time
        parts.sort(key=lambda p: p.get("time", {}).get("start", 0))

        return parts

    def _extract_tool_call(self, part: dict) -> ToolCall | None:
        """Extract ToolCall from tool part.

        Args:
            part: Tool part dict.

        Returns:
            ToolCall object or None on error.
        """
        try:
            state = part.get("state", {})

            tool_id = part.get("callID", "")
            tool_name = part.get("tool", "unknown")
            tool_input = state.get("input", {})
            tool_output = state.get("output")

            # Calculate duration from timing
            duration_ms = None
            if timing := state.get("time"):
                start_ms = timing.get("start")
                end_ms = timing.get("end")

                if start_ms and end_ms:
                    duration_ms = end_ms - start_ms

            # Check status
            status = state.get("status", "completed")
            is_error = status != "completed"

            return ToolCall(
                tool_id=tool_id,
                tool_name=tool_name,
                input=tool_input,
                output=tool_output,
                is_error=is_error,
                duration_ms=duration_ms,
                status=status,
            )

        except Exception as e:
            logger.warning(f"Failed to extract tool call from part: {e}")
            return None

    def _extract_token_usage(self, msg_data: dict, parts: list[dict]) -> TokenUsage | None:
        """Extract token usage from message and step-finish parts.

        Args:
            msg_data: Message metadata dict.
            parts: List of part dicts.

        Returns:
            TokenUsage object or None if no token data.
        """
        # Start with message-level tokens
        msg_tokens = msg_data.get("tokens", {})

        input_tokens = msg_tokens.get("input", 0)
        output_tokens = msg_tokens.get("output", 0)
        cache_read = msg_tokens.get("cache", {}).get("read", 0)
        cache_write = msg_tokens.get("cache", {}).get("write", 0)

        # Override with step-finish tokens if present (more accurate)
        for part in parts:
            if part.get("type") == "step-finish":
                part_tokens = part.get("tokens", {})
                input_tokens = part_tokens.get("input", input_tokens)
                output_tokens = part_tokens.get("output", output_tokens)
                cache_read = part_tokens.get("cache", {}).get("read", cache_read)
                cache_write = part_tokens.get("cache", {}).get("write", cache_write)

        # Only return if we have any token data
        if input_tokens or output_tokens or cache_read or cache_write:
            return TokenUsage(
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                cache_read_tokens=cache_read,
                cache_create_tokens=cache_write,
            )

        return None

    def _load_project(self, project_id: str) -> dict | None:
        """Load project metadata.

        Args:
            project_id: Project ID (hash).

        Returns:
            Project data dict or None if not found.
        """
        project_file = self.project_dir / f"{project_id}.json"

        if not project_file.exists():
            return None

        try:
            with open(project_file) as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            logger.warning(f"Failed to load project {project_id}: {e}")
            return None
