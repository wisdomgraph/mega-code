"""Claude Code native data source (~/.claude/projects/).

Loads historical conversation data from Claude Code's native storage format.
"""

import json
import logging
from collections.abc import Callable, Iterator
from datetime import UTC, datetime
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


class ClaudeNativeSource:
    """Load Claude Code data from ~/.claude/projects/.

    Parses the native Claude Code session format including:
    - sessions-index.json for session enumeration
    - *.jsonl files for conversation messages
    - Subagent conversations in subagents/ subdirectories

    Example:
        source = ClaudeNativeSource()
        # Or with custom path:
        source = ClaudeNativeSource(base_path=Path("/custom/path"))

        for session in source.iter_sessions():
            print(f"Session: {session.metadata.session_id}")
            print(f"  Messages: {len(session.messages)}")
            print(f"  Tool calls: {session.stats.tool_call_count}")
    """

    def __init__(self, base_path: Path | str | None = None):
        """Initialize the Claude native source.

        Args:
            base_path: Base directory for Claude projects.
                      Defaults to ~/.claude/projects/
        """
        if base_path is None:
            self.base_path = Path.home() / ".claude" / "projects"
        elif isinstance(base_path, str):
            self.base_path = Path(base_path)
        else:
            self.base_path = base_path

    @property
    def name(self) -> str:
        """Return the source identifier."""
        return "claude_native"

    def _iter_project_dirs(self) -> Iterator[Path]:
        """Iterate over project directories."""
        if not self.base_path.exists():
            logger.warning(f"Claude projects directory not found: {self.base_path}")
            return

        for project_dir in self.base_path.iterdir():
            if project_dir.is_dir():
                yield project_dir

    def _load_sessions_index(self, project_dir: Path) -> list[dict[str, Any]]:
        """Load sessions-index.json, augmented with filesystem-discovered sessions.

        First loads the index file (if present), then scans for JSONL files
        not listed in the index. This handles stale/missing index files.

        Args:
            project_dir: Path to the project directory.

        Returns:
            List of session entries (from index + filesystem discovery).
        """
        index_path = project_dir / "sessions-index.json"
        entries: list[dict[str, Any]] = []

        if index_path.exists():
            try:
                with open(index_path, encoding="utf-8") as f:
                    data = json.load(f)
                entries = data.get("entries", [])
            except (json.JSONDecodeError, OSError) as e:
                logger.warning(f"Failed to load sessions index from {index_path}: {e}")

        # Discover JSONL files not in the index
        indexed_ids = {e.get("sessionId") for e in entries}
        discovered = self._discover_sessions_from_jsonl(project_dir, indexed_ids)
        entries.extend(discovered)

        return entries

    def _discover_sessions_from_jsonl(
        self, project_dir: Path, indexed_ids: set[str]
    ) -> list[dict[str, Any]]:
        """Discover sessions from JSONL files not in sessions-index.json.

        Scans *.jsonl files in the project directory and builds synthetic
        index entries by reading metadata from the first 'progress' entry.

        Args:
            project_dir: Claude project directory (e.g. ~/.claude/projects/-Users-...).
            indexed_ids: Session IDs already in sessions-index.json (to skip).

        Returns:
            List of synthetic session index entries.
        """
        discovered: list[dict[str, Any]] = []

        try:
            jsonl_files = list(project_dir.glob("*.jsonl"))
        except OSError as e:
            logger.warning(f"Failed to scan for JSONL files in {project_dir}: {e}")
            return discovered

        for jsonl_path in jsonl_files:
            session_id = jsonl_path.stem
            if session_id in indexed_ids:
                continue

            # Read first progress entry for metadata
            cwd = None
            git_branch = None
            is_sidechain = False
            try:
                with open(jsonl_path, encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            entry = json.loads(line)
                        except json.JSONDecodeError:
                            continue
                        if entry.get("type") == "progress" and entry.get("cwd"):
                            cwd = entry["cwd"]
                            git_branch = entry.get("gitBranch")
                            is_sidechain = entry.get("isSidechain", False)
                            break
            except OSError as e:
                logger.debug(f"Failed to read JSONL {jsonl_path}: {e}")
                continue

            if is_sidechain:
                continue

            # Build synthetic index entry
            try:
                stat = jsonl_path.stat()
            except OSError as e:
                logger.debug(f"Failed to stat {jsonl_path}: {e}")
                continue
            discovered.append(
                {
                    "sessionId": session_id,
                    "fullPath": str(jsonl_path),
                    "projectPath": cwd,
                    "gitBranch": git_branch,
                    "isSidechain": False,
                    "created": datetime.fromtimestamp(stat.st_ctime, tz=UTC).isoformat(),
                    "modified": datetime.fromtimestamp(stat.st_mtime, tz=UTC).isoformat(),
                }
            )

        if discovered:
            logger.info(
                f"Discovered {len(discovered)} sessions from JSONL files "
                f"in {project_dir.name} (not in index)"
            )

        return discovered

    def _parse_jsonl_file(self, jsonl_path: Path) -> list[dict[str, Any]]:
        """Parse a JSONL file into a list of entries.

        Args:
            jsonl_path: Path to the JSONL file.

        Returns:
            List of parsed JSON entries.
        """
        entries: list[dict[str, Any]] = []

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

    def _parse_entry_to_message(self, entry: dict[str, Any], session_id: str) -> Message | None:
        """Parse a JSONL entry into a Message object.

        Args:
            entry: A dictionary from the JSONL file.
            session_id: Session ID for context.

        Returns:
            A Message object or None if the entry is not a message.
        """
        entry_type = entry.get("type", "")

        # Only process user, assistant, and system messages
        if entry_type not in ("user", "assistant", "system"):
            return None

        msg_data = entry.get("message", {})

        # Extract basic fields
        msg_id = entry.get("uuid", f"{session_id}-{id(entry)}")
        role = msg_data.get("role", entry_type)
        if role not in ("user", "assistant", "system"):
            role = entry_type

        # Parse timestamp
        timestamp = None
        ts_str = entry.get("timestamp")
        if ts_str:
            try:
                timestamp = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
            except (ValueError, AttributeError):
                pass

        # Parse content and tool calls
        content_parts: list[str] = []
        tool_calls: list[ToolCall] = []
        tool_results: list[ToolCall] = []

        raw_content = msg_data.get("content", "")
        if isinstance(raw_content, str):
            content_parts.append(raw_content)
        elif isinstance(raw_content, list):
            for block in raw_content:
                if not isinstance(block, dict):
                    continue
                block_type = block.get("type", "")

                if block_type == "text":
                    content_parts.append(block.get("text", ""))
                elif block_type == "thinking":
                    # Extended thinking - skip for now
                    pass
                elif block_type == "tool_use":
                    tool_calls.append(
                        ToolCall(
                            tool_id=block.get("id", ""),
                            tool_name=block.get("name", "unknown"),
                            input=block.get("input", {}),
                        )
                    )
                elif block_type == "tool_result":
                    tool_results.append(
                        ToolCall(
                            tool_id=block.get("tool_use_id", ""),
                            tool_name="",
                            output=str(block.get("content", "")),
                            is_error=block.get("is_error", False),
                        )
                    )

        # Parse token usage
        token_usage = None
        usage_data = msg_data.get("usage")
        if usage_data and isinstance(usage_data, dict):
            token_usage = TokenUsage(
                input_tokens=usage_data.get("input_tokens", 0),
                output_tokens=usage_data.get("output_tokens", 0),
                cache_read_tokens=usage_data.get("cache_read_input_tokens", 0),
                cache_create_tokens=usage_data.get("cache_creation_input_tokens", 0),
            )

        # Get model info
        model = msg_data.get("model")

        return Message(
            id=msg_id,
            role=role,  # type: ignore[arg-type]
            content="\n".join(content_parts).strip(),
            tool_calls=tool_calls,
            tool_results=tool_results,
            timestamp=timestamp,
            token_usage=token_usage,
            model=model,
            raw=entry,
        )

    def _index_entry_to_metadata(
        self, entry: dict[str, Any], project_dir: Path
    ) -> HistorySessionMetadata:
        """Convert a sessions-index entry to HistorySessionMetadata.

        Args:
            entry: Entry from sessions-index.json.
            project_dir: Path to the project directory.

        Returns:
            HistorySessionMetadata object.
        """
        session_id = entry.get("sessionId", "")

        # Parse timestamps
        started_at = None
        ended_at = None
        if entry.get("created"):
            try:
                started_at = datetime.fromisoformat(entry["created"].replace("Z", "+00:00"))
            except (ValueError, AttributeError):
                pass
        if entry.get("modified"):
            try:
                ended_at = datetime.fromisoformat(entry["modified"].replace("Z", "+00:00"))
            except (ValueError, AttributeError):
                pass

        return HistorySessionMetadata(
            session_id=session_id,
            source=self.name,
            project_path=entry.get("projectPath"),
            git_branch=entry.get("gitBranch"),
            started_at=started_at,
            ended_at=ended_at,
            first_prompt=entry.get("firstPrompt"),
            extra={
                "fullPath": entry.get("fullPath"),
                "messageCount": entry.get("messageCount"),
                "isSidechain": entry.get("isSidechain", False),
                "project_dir": str(project_dir),
            },
        )

    def list_sessions(self) -> Iterator[HistorySessionMetadata]:
        """List available sessions without loading messages."""
        for project_dir in self._iter_project_dirs():
            entries = self._load_sessions_index(project_dir)
            for entry in entries:
                # Skip sidechain sessions (subagents)
                if entry.get("isSidechain", False):
                    continue
                yield self._index_entry_to_metadata(entry, project_dir)

    def load_session(self, session_id: str) -> Session:
        """Load a complete session by ID.

        Searches all project directories for the session.
        """
        # Search for the session in all projects
        for project_dir in self._iter_project_dirs():
            entries = self._load_sessions_index(project_dir)
            for entry in entries:
                if entry.get("sessionId") == session_id:
                    return self._load_session_from_entry(entry, project_dir)

        raise KeyError(f"Session not found: {session_id}")

    def _load_session_from_entry(self, entry: dict[str, Any], project_dir: Path) -> Session:
        """Load a session from an index entry.

        Args:
            entry: Entry from sessions-index.json.
            project_dir: Path to the project directory.

        Returns:
            Complete Session object.
        """
        session_id = entry.get("sessionId", "")
        metadata = self._index_entry_to_metadata(entry, project_dir)

        # Find the JSONL file
        jsonl_path = None
        full_path = entry.get("fullPath")
        if full_path:
            jsonl_path = Path(full_path)
        else:
            # Try to construct the path
            jsonl_path = project_dir / f"{session_id}.jsonl"

        if jsonl_path is None or not jsonl_path.exists():
            logger.warning(f"Session file not found for {session_id}")
            return Session(metadata=metadata, messages=[])

        # Parse messages
        entries = self._parse_jsonl_file(jsonl_path)
        messages: list[Message] = []
        for e in entries:
            msg = self._parse_entry_to_message(e, session_id)
            if msg:
                messages.append(msg)

        return Session(metadata=metadata, messages=messages)

    def iter_sessions(self) -> Iterator[Session]:
        """Iterate over all sessions with lazy loading."""
        for project_dir in self._iter_project_dirs():
            entries = self._load_sessions_index(project_dir)
            for entry in entries:
                # Skip sidechain sessions
                if entry.get("isSidechain", False):
                    continue
                try:
                    yield self._load_session_from_entry(entry, project_dir)
                except Exception as e:
                    session_id = entry.get("sessionId", "unknown")
                    logger.warning(f"Failed to load session {session_id}: {e}")

    def count_sessions(self) -> int:
        """Return the total number of sessions."""
        count = 0
        for project_dir in self._iter_project_dirs():
            entries = self._load_sessions_index(project_dir)
            # Count non-sidechain sessions
            count += sum(1 for e in entries if not e.get("isSidechain", False))
        return count

    def iter_sessions_by_project_paths(
        self,
        project_paths: list[str],
        path_matcher: Callable[[str, set[str]], bool] | None = None,
    ) -> Iterator[dict[str, Any]]:
        """Iterate over session index entries matching project paths.

        Args:
            project_paths: List of project paths to filter by.
            path_matcher: Optional custom function to match session paths against target paths.
                         Signature: (session_path: str, target_paths: set[str]) -> bool
                         If None, uses default exact match behavior.

        Yields:
            Session index entries (dicts) from sessions-index.json that match the given paths.

        Notes:
            - Returns raw session index entries, not full Session objects
            - Excludes sidechain sessions
            - If project_paths is empty, returns immediately
            - Uses path_utils.should_include_session for matching unless custom matcher provided
        """
        if not project_paths:
            return

        # Import path utilities
        from mega_code.client.utils.path_utils import normalize_path, should_include_session

        # Normalize target paths
        normalized_targets = {normalize_path(p) for p in project_paths}

        # Iterate over all project directories
        for project_dir in self._iter_project_dirs():
            entries = self._load_sessions_index(project_dir)

            for entry in entries:
                # Skip sidechain sessions
                if entry.get("isSidechain", False):
                    continue

                # Skip sessions without projectPath
                session_path = entry.get("projectPath")
                if not session_path:
                    continue

                # Use custom matcher if provided, otherwise use default
                try:
                    if path_matcher is not None:
                        if path_matcher(session_path, normalized_targets):
                            yield entry
                    else:
                        if should_include_session(session_path, normalized_targets):
                            yield entry
                except Exception as e:
                    logger.warning(
                        "Error checking path match for session "
                        + f"{entry.get('sessionId', 'unknown')}: {e}"
                    )
