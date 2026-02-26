"""Cursor IDE data source (~/Library/Application Support/Cursor).

Loads historical conversation data from Cursor IDE's storage format.
"""

import json
import logging
import os
import platform
import sqlite3
from pathlib import Path
from typing import Any, Iterator

from mega_code.client.history.models import HistorySessionMetadata, Message, Session, ToolCall

logger = logging.getLogger(__name__)

DEFAULT_CURSOR_PATHS = {
    "Darwin": "~/Library/Application Support/Cursor/User",
    "Linux": "~/.config/Cursor/User",
    "Windows": "%APPDATA%/Cursor/User",
}


class CursorSource:
    """Load Cursor IDE data from SQLite databases.

    Parses Cursor's conversation storage format including:
    - Content-addressed blobs (protobuf for checkpoints, mixed for messages)
    - Checkpoint-based conversation reconstruction
    - Tool calls and reasoning

    Example:
        source = CursorSource()
        # Or with custom path:
        source = CursorSource(base_path=Path("/custom/path"))

        for session in source.iter_sessions():
            print(f"Session: {session.metadata.session_id}")
            print(f"  Messages: {len(session.messages)}")
            print(f"  Tool calls: {session.stats.tool_call_count}")
    """

    def __init__(self, base_path: Path | None = None, use_global: bool = True):
        """Initialize the Cursor source.

        Args:
            base_path: Base directory for Cursor user data.
                      Defaults to platform-specific path.
            use_global: Whether to use global DB (True) or workspace DBs (False).
        """
        if base_path:
            self.base_path = base_path
        else:
            self.base_path = self._get_cursor_user_path()

        self.use_global = use_global

    @property
    def name(self) -> str:
        """Return the source identifier."""
        return "cursor"

    def _get_cursor_user_path(self) -> Path:
        """Get platform-specific Cursor user directory path.

        Returns:
            Path to Cursor user directory.

        Raises:
            ValueError: If platform is not supported.
        """
        system = platform.system()
        if system not in DEFAULT_CURSOR_PATHS:
            raise ValueError(f"Unsupported platform: {system}")

        path_str = DEFAULT_CURSOR_PATHS[system]
        path = Path(os.path.expandvars(path_str)).expanduser()

        return path

    def _decode_blob(self, hex_data: str) -> dict[str, Any] | None:
        """Decode hex-encoded JSON blob (for message blobs).

        Message blobs are stored as hex(json_string).

        Args:
            hex_data: Hex-encoded string from database.

        Returns:
            Decoded JSON data or None on failure.
        """
        try:
            # Hex decode to get JSON string
            json_str = bytes.fromhex(hex_data).decode("utf-8")
            # Parse JSON
            data = json.loads(json_str)
            return data
        except (ValueError, json.JSONDecodeError, UnicodeDecodeError) as e:
            logger.warning(f"Failed to decode blob: {e}")
            return None

    def _parse_protobuf_checkpoint(self, hex_data: str) -> list[str]:
        """Parse protobuf checkpoint blob to extract message hashes.

        Checkpoint blobs use protobuf wire format with repeated field 1
        containing 32-byte SHA256 hashes.

        Wire format: 0x0a 0x20 [32 bytes] 0x0a 0x20 [32 bytes] ...
        - Tag 0x0a = Field 1, Wire Type 2 (length-delimited)
        - Length 0x20 = 32 bytes
        - Payload = SHA256 hash

        Args:
            hex_data: Hex-encoded protobuf data from database.

        Returns:
            List of blob hashes (hex strings).
        """
        try:
            data = bytes.fromhex(hex_data)
        except ValueError as e:
            logger.warning(f"Invalid hex data: {e}")
            return []

        hashes = []
        offset = 0

        while offset < len(data):
            # Read tag
            if offset >= len(data):
                break

            tag = data[offset]
            offset += 1

            # Expect field 1, wire type 2 (0x0a = 00001|010)
            if tag != 0x0A:
                logger.debug(f"Unexpected tag at offset {offset - 1}: 0x{tag:02x}")
                break

            # Read length
            if offset >= len(data):
                break

            length = data[offset]
            offset += 1

            # Expect 32 bytes (SHA256)
            if length != 0x20:
                logger.debug(f"Unexpected length at offset {offset - 1}: {length}")
                break

            # Read hash payload
            if offset + length > len(data):
                logger.debug(f"Truncated payload at offset {offset}")
                break

            hash_bytes = data[offset : offset + length]
            offset += length

            # Convert to hex string
            hash_hex = hash_bytes.hex()
            hashes.append(hash_hex)

        return hashes

    def _get_global_db_path(self) -> Path:
        """Get path to global Cursor database.

        Returns:
            Path to state.vscdb in globalStorage.
        """
        return self.base_path / "globalStorage" / "state.vscdb"

    def _iter_workspace_dbs(self) -> Iterator[Path]:
        """Iterate over workspace database paths.

        Yields:
            Paths to workspace state.vscdb files.
        """
        workspace_dir = self.base_path / "workspaceStorage"

        if not workspace_dir.exists():
            logger.warning(f"Workspace directory not found: {workspace_dir}")
            return

        for workspace_hash_dir in workspace_dir.iterdir():
            if workspace_hash_dir.is_dir():
                db_path = workspace_hash_dir / "state.vscdb"
                if db_path.exists():
                    yield db_path

    def _iter_composer_ids(self, db_path: Path) -> Iterator[str]:
        """Iterate over composer IDs in a database.

        Args:
            db_path: Path to state.vscdb file.

        Yields:
            Composer IDs (UUIDs).
        """
        if not db_path.exists():
            logger.debug(f"Database not found: {db_path}")
            return

        try:
            conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
            cursor = conn.cursor()

            # Find checkpoint keys
            cursor.execute("SELECT key FROM cursorDiskKV WHERE key LIKE 'agentKv:checkpoint:%'")

            for row in cursor.fetchall():
                key = row[0]
                # Extract composer ID from "agentKv:checkpoint:{composerId}"
                composer_id = key.replace("agentKv:checkpoint:", "")
                yield composer_id

            conn.close()
        except sqlite3.Error as e:
            logger.error(f"Database error reading {db_path}: {e}")

    def _extract_checkpoint(self, db_path: Path, composer_id: str) -> list[str]:
        """Extract list of message blob hashes from checkpoint.

        Args:
            db_path: Path to database.
            composer_id: Composer/session ID.

        Returns:
            List of blob hashes in conversation order.
        """
        try:
            conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
            cursor = conn.cursor()

            # Get checkpoint blob hash
            cursor.execute(
                "SELECT value FROM cursorDiskKV WHERE key = ?",
                (f"agentKv:checkpoint:{composer_id}",),
            )

            result = cursor.fetchone()
            conn.close()

            if not result:
                return []

            # Checkpoint value is a blob hash (not double-encoded)
            checkpoint_hash = result[0]

            # Get checkpoint blob content
            conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
            cursor = conn.cursor()
            cursor.execute(
                "SELECT value FROM cursorDiskKV WHERE key = ?",
                (f"agentKv:blob:{checkpoint_hash}",),
            )

            blob_result = cursor.fetchone()
            conn.close()

            if not blob_result:
                return []

            # Decode checkpoint blob (protobuf format)
            blob_hex = blob_result[0]

            # Try protobuf parser first
            blob_hashes = self._parse_protobuf_checkpoint(blob_hex)

            if blob_hashes:
                return blob_hashes

            # Fallback: Try JSON decoder (for backward compatibility)
            checkpoint_data = self._decode_blob(blob_hex)

            if checkpoint_data:
                # Checkpoint might be list of hashes in JSON format
                if isinstance(checkpoint_data, list):
                    return checkpoint_data

            return []

        except sqlite3.Error as e:
            logger.error(f"Failed to extract checkpoint: {e}")
            return []

    def _extract_tool_calls(self, content: list[dict[str, Any]]) -> list[ToolCall]:
        """Extract tool calls from content array.

        Args:
            content: Array of content items.

        Returns:
            List of ToolCall objects.
        """
        tool_calls = []

        for item in content:
            if not isinstance(item, dict):
                continue

            if item.get("type") == "tool-call":
                tool_calls.append(
                    ToolCall(
                        tool_id=item.get("toolCallId", ""),
                        tool_name=item.get("toolName", "unknown"),
                        input=item.get("args", {}),
                        output=None,  # Tool results not in same message
                        is_error=False,
                        duration_ms=None,
                        status="success",
                    )
                )

        return tool_calls

    def _extract_tool_results(self, content: list[dict[str, Any]]) -> list[ToolCall]:
        """Extract tool results from content array.

        Args:
            content: Array of content items from tool message.

        Returns:
            List of ToolCall objects with results populated.
        """
        tool_results = []

        for item in content:
            if not isinstance(item, dict):
                continue

            if item.get("type") == "tool-result":
                tool_results.append(
                    ToolCall(
                        tool_id=item.get("toolCallId", ""),
                        tool_name=item.get("toolName", "unknown"),
                        input={},  # Not available in result message
                        output=item.get("result", ""),
                        is_error=False,  # TODO: Check if error field exists
                        duration_ms=None,
                        status="success",
                    )
                )

        return tool_results

    def _parse_message(self, data: dict[str, Any], composer_id: str) -> Message | None:
        """Parse message JSON into Message object.

        Args:
            data: Decoded message JSON.
            composer_id: Session/composer ID for context.

        Returns:
            Message object or None if should be filtered (e.g., tool messages).
        """
        role = data.get("role", "user")

        # Mark tool messages for post-processing (will be filtered out)
        if role == "tool":
            msg_id = data.get("id", f"{composer_id}-{id(data)}")
            return Message(
                id=msg_id,
                role="user",  # Temporary placeholder (will be filtered)
                content="[TOOL_MESSAGE]",  # Special marker
                tool_calls=[],
                tool_results=self._extract_tool_results(data.get("content", [])),
                timestamp=None,
                token_usage=None,
                model=None,
                reasoning=None,
                raw=data,
            )

        if role not in ("user", "assistant", "system"):
            logger.warning(f"Unknown role: {role}, defaulting to user")
            role = "user"

        msg_id = data.get("id", f"{composer_id}-{id(data)}")
        content = data.get("content", "")

        # Extract text content and tool calls
        tool_calls = []
        text_parts = []

        if isinstance(content, str):
            text_parts.append(content)
        elif isinstance(content, list):
            for item in content:
                if isinstance(item, dict):
                    if item.get("type") == "text":
                        text_parts.append(item.get("text", ""))
                    elif item.get("type") == "tool-call":
                        # Tool calls extracted separately
                        pass

            tool_calls = self._extract_tool_calls(content)

        text_content = "\n".join(text_parts)

        # TODO: Extract timestamp, token_usage, model, reasoning if available

        return Message(
            id=msg_id,
            role=role,  # type: ignore[arg-type]
            content=text_content,
            tool_calls=tool_calls,
            tool_results=[],
            timestamp=None,
            token_usage=None,
            model=None,
            reasoning=None,
            raw=data,
        )

    def _load_message_blob(self, db_path: Path, blob_hash: str) -> Message | None:
        """Load and parse a message blob.

        Args:
            db_path: Path to database.
            blob_hash: Hash of message blob.

        Returns:
            Parsed Message or None on failure.
        """
        try:
            conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
            cursor = conn.cursor()

            cursor.execute(
                "SELECT value FROM cursorDiskKV WHERE key = ?",
                (f"agentKv:blob:{blob_hash}",),
            )

            result = cursor.fetchone()
            conn.close()

            if not result:
                logger.warning(f"Blob not found: {blob_hash}")
                return None

            # Decode blob
            data = self._decode_blob(result[0])

            if not data:
                return None

            # Parse message
            return self._parse_message(data, blob_hash[:8])

        except sqlite3.Error as e:
            logger.error(f"Failed to load message blob: {e}")
            return None

    def _attach_tool_results(self, messages: list[Message]) -> list[Message]:
        """Post-process messages to attach tool results to previous assistant messages.

        Finds messages marked as tool messages (content == "[TOOL_MESSAGE]")
        and attaches their tool_results to the previous assistant message.

        Args:
            messages: List of parsed messages.

        Returns:
            Filtered list with tool messages removed and results attached.
        """
        processed = []
        i = 0

        while i < len(messages):
            msg = messages[i]

            # Check if this is a tool message placeholder
            if msg.content == "[TOOL_MESSAGE]" and msg.tool_results:
                # Find previous assistant message
                for prev_msg in reversed(processed):
                    if prev_msg.role == "assistant" and prev_msg.tool_calls:
                        # Match tool results to tool calls by tool_id
                        result_map = {tr.tool_id: tr for tr in msg.tool_results}

                        # Update tool calls with results
                        for tc in prev_msg.tool_calls:
                            if tc.tool_id in result_map:
                                result = result_map[tc.tool_id]
                                tc.output = result.output
                                tc.is_error = result.is_error

                        # Also populate tool_results field
                        prev_msg.tool_results.extend(msg.tool_results)
                        break

                # Skip tool message (don't add to processed)
                i += 1
                continue

            # Regular message - add to processed
            processed.append(msg)
            i += 1

        return processed

    def _build_session(self, db_path: Path, composer_id: str, messages: list[Message]) -> Session:
        """Build Session object from composer ID and messages.

        Args:
            db_path: Path to database file.
            composer_id: Composer/session ID.
            messages: List of messages.

        Returns:
            Complete Session object.
        """
        # Extract metadata
        metadata = HistorySessionMetadata(
            session_id=composer_id,
            source=self.name,
            project_path=None,  # TODO: Resolve from workspace hash
            git_branch=None,
            model_id=None,  # TODO: Extract from system message
            started_at=None,
            ended_at=None,
            first_prompt=None,
            extra={"cursor_db_path": str(db_path)},
        )

        # Extract first user message
        for msg in messages:
            if msg.role == "user":
                metadata.first_prompt = msg.content[:200]
                break

        return Session(metadata=metadata, messages=messages)

    def list_sessions(self) -> Iterator[HistorySessionMetadata]:
        """List available sessions without loading messages."""
        db_path = self._get_global_db_path()

        if not db_path.exists():
            logger.warning(f"Global database not found: {db_path}")
            return

        for composer_id in self._iter_composer_ids(db_path):
            yield HistorySessionMetadata(
                session_id=composer_id,
                source=self.name,
                project_path=None,
                git_branch=None,
                model_id=None,
                started_at=None,
                ended_at=None,
                first_prompt=None,
                extra={"cursor_db_path": str(db_path)},
            )

    def load_session(self, session_id: str) -> Session:
        """Load a complete session by ID.

        Args:
            session_id: The composer ID to load.

        Returns:
            Complete Session object.

        Raises:
            KeyError: If session not found.
        """
        db_path = self._get_global_db_path()

        if not db_path.exists():
            raise KeyError(f"Session not found: {session_id}")

        # Get message hashes from checkpoint
        blob_hashes = self._extract_checkpoint(db_path, session_id)

        if not blob_hashes:
            raise KeyError(f"Session not found: {session_id}")

        # Load messages
        messages = []
        for blob_hash in blob_hashes:
            msg = self._load_message_blob(db_path, blob_hash)
            if msg:
                messages.append(msg)

        # Post-process: attach tool results to previous assistant messages
        messages = self._attach_tool_results(messages)

        return self._build_session(db_path, session_id, messages)

    def iter_sessions(self) -> Iterator[Session]:
        """Iterate over all sessions with lazy loading."""
        db_path = self._get_global_db_path()

        if not db_path.exists():
            logger.warning(f"Global database not found: {db_path}")
            return

        for composer_id in self._iter_composer_ids(db_path):
            try:
                yield self.load_session(composer_id)
            except Exception as e:
                logger.warning(f"Failed to load session {composer_id}: {e}")

    def count_sessions(self) -> int:
        """Return the total number of sessions."""
        return sum(1 for _ in self.list_sessions())
