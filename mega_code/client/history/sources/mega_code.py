"""MEGA-Code collector data source (~/.local/share/mega-code/).

Loads historical conversation data collected by MEGA-Code's session collector.
"""

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator

from mega_code.client.history.models import (
    HistorySessionMetadata,
    HistorySessionStats,
    Message,
    Session,
    TokenUsage,
    ToolCall,
)

logger = logging.getLogger(__name__)


class MegaCodeSource:
    """Load Claude Code data from MEGA-Code collector storage.

    The MEGA-Code collector stores session data in a structured format:
    - mapping.json: Maps project paths to folder names
    - projects/{folder}/{session_id}/: Session directories
      - metadata.json: Session metadata
      - stats.json: Aggregated statistics
      - events.jsonl: Raw event log

    Example:
        source = MegaCodeSource()
        # Or with custom path:
        source = MegaCodeSource(base_path=Path("/custom/path"))

        for session in source.iter_sessions():
            print(f"Session: {session.metadata.session_id}")
            print(f"  Cost: ${session.stats.estimated_cost_usd:.4f}")
    """

    def __init__(self, base_path: Path | None = None):
        """Initialize the MEGA-Code source.

        Args:
            base_path: Base directory for MEGA-Code data.
                      Defaults to ~/.local/share/mega-code/
        """
        if base_path is None:
            from mega_code.client.dirs import data_dir

            base_path = data_dir()
        self.base_path = base_path
        self._mapping: dict[str, str] | None = None

    @property
    def name(self) -> str:
        """Return the source identifier."""
        return "mega_code"

    def _load_mapping(self) -> dict[str, str]:
        """Load project folder mapping.

        Returns:
            Dictionary mapping project paths to folder names.
        """
        if self._mapping is not None:
            return self._mapping

        mapping_path = self.base_path / "mapping.json"
        if not mapping_path.exists():
            self._mapping = {}
            return self._mapping

        try:
            with open(mapping_path, "r", encoding="utf-8") as f:
                self._mapping = json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            logger.warning(f"Failed to load mapping from {mapping_path}: {e}")
            self._mapping = {}

        return self._mapping

    def _iter_session_dirs(self) -> Iterator[tuple[Path, str]]:
        """Iterate over session directories.

        Yields:
            Tuples of (session_dir_path, project_path).
        """
        projects_dir = self.base_path / "projects"
        if not projects_dir.exists():
            return

        mapping = self._load_mapping()
        # Reverse mapping: folder -> project_path
        reverse_mapping = {v: k for k, v in mapping.items()}

        for project_folder in projects_dir.iterdir():
            if not project_folder.is_dir():
                continue

            project_path = reverse_mapping.get(project_folder.name, project_folder.name)

            for session_dir in project_folder.iterdir():
                if session_dir.is_dir():
                    yield session_dir, project_path

    def _load_metadata(self, session_dir: Path) -> dict[str, Any]:
        """Load metadata.json from a session directory.

        Args:
            session_dir: Path to the session directory.

        Returns:
            Metadata dictionary.
        """
        metadata_path = session_dir / "metadata.json"
        if not metadata_path.exists():
            return {}

        try:
            with open(metadata_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            logger.warning(f"Failed to load metadata from {metadata_path}: {e}")
            return {}

    def _load_stats(self, session_dir: Path) -> dict[str, Any]:
        """Load stats.json from a session directory.

        Args:
            session_dir: Path to the session directory.

        Returns:
            Stats dictionary.
        """
        stats_path = session_dir / "stats.json"
        if not stats_path.exists():
            return {}

        try:
            with open(stats_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            logger.warning(f"Failed to load stats from {stats_path}: {e}")
            return {}

    def _load_events(self, session_dir: Path) -> list[dict[str, Any]]:
        """Load events.jsonl from a session directory.

        Args:
            session_dir: Path to the session directory.

        Returns:
            List of event entries.
        """
        events_path = session_dir / "events.jsonl"
        events: list[dict[str, Any]] = []

        if not events_path.exists():
            return events

        try:
            with open(events_path, "r", encoding="utf-8") as f:
                for line_num, line in enumerate(f, 1):
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        events.append(json.loads(line))
                    except json.JSONDecodeError as e:
                        logger.debug(f"Failed to parse line {line_num} in {events_path}: {e}")
        except OSError as e:
            logger.warning(f"Failed to read events file {events_path}: {e}")

        return events

    def _parse_event_to_message(self, event: dict[str, Any], session_id: str) -> Message | None:
        """Parse an event entry into a Message object.

        Args:
            event: A dictionary from events.jsonl.
            session_id: Session ID for context.

        Returns:
            A Message object or None if the event is not a message.
        """
        event_type = event.get("type", "")

        if event_type not in ("user", "assistant", "system"):
            return None

        msg_data = event.get("message", {})

        # Extract basic fields
        msg_id = event.get("uuid", f"{session_id}-{id(event)}")
        role = msg_data.get("role", event_type)
        if role not in ("user", "assistant", "system"):
            role = event_type

        # Parse timestamp
        timestamp = None
        ts_str = event.get("timestamp")
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
            raw=event,
        )

    def _build_metadata(
        self,
        session_dir: Path,
        project_path: str,
        meta: dict[str, Any],
    ) -> HistorySessionMetadata:
        """Build HistorySessionMetadata from session directory data.

        Args:
            session_dir: Path to the session directory.
            project_path: Original project path.
            meta: Loaded metadata.json contents.

        Returns:
            HistorySessionMetadata object.
        """
        session_id = meta.get("session_id", session_dir.name)

        # Parse timestamps
        started_at = None
        ended_at = None
        if meta.get("started_at"):
            try:
                started_at = datetime.fromisoformat(meta["started_at"].replace("Z", "+00:00"))
            except (ValueError, AttributeError):
                pass
        if meta.get("ended_at"):
            try:
                ended_at = datetime.fromisoformat(meta["ended_at"].replace("Z", "+00:00"))
            except (ValueError, AttributeError):
                pass

        return HistorySessionMetadata(
            session_id=session_id,
            source=self.name,
            project_path=meta.get("project_dir", project_path),
            git_branch=meta.get("git_branch"),
            model_id=meta.get("model_id"),
            started_at=started_at,
            ended_at=ended_at,
            extra={
                "version": meta.get("version"),
                "session_dir": str(session_dir),
            },
        )

    def _build_stats(self, stats_data: dict[str, Any]) -> HistorySessionStats:
        """Build HistorySessionStats from stats.json data.

        Args:
            stats_data: Loaded stats.json contents.

        Returns:
            HistorySessionStats object.
        """
        counts = stats_data.get("counts", {})
        tokens = stats_data.get("tokens", {})
        cost = stats_data.get("cost", {})

        return HistorySessionStats(
            message_count=counts.get("user_prompts", 0) + counts.get("assistant_responses", 0),
            user_message_count=counts.get("user_prompts", 0),
            assistant_message_count=counts.get("assistant_responses", 0),
            tool_call_count=counts.get("tool_calls", 0),
            tool_calls_by_type=counts.get("tool_calls_by_type", {}),
            error_count=counts.get("errors", 0),
            total_tokens=TokenUsage(
                input_tokens=tokens.get("total_input", 0),
                output_tokens=tokens.get("total_output", 0),
                cache_read_tokens=tokens.get("total_cache_read", 0),
                cache_create_tokens=tokens.get("total_cache_create", 0),
            ),
            estimated_cost_usd=cost.get("estimated_usd"),
        )

    def list_sessions(self) -> Iterator[HistorySessionMetadata]:
        """List available sessions without loading messages."""
        for session_dir, project_path in self._iter_session_dirs():
            meta = self._load_metadata(session_dir)
            yield self._build_metadata(session_dir, project_path, meta)

    def load_session(self, session_id: str) -> Session:
        """Load a complete session by ID."""
        for session_dir, project_path in self._iter_session_dirs():
            meta = self._load_metadata(session_dir)
            if meta.get("session_id", session_dir.name) == session_id:
                return self._load_session_from_dir(session_dir, project_path, meta)

        raise KeyError(f"Session not found: {session_id}")

    def _load_session_from_dir(
        self,
        session_dir: Path,
        project_path: str,
        meta: dict[str, Any],
    ) -> Session:
        """Load a session from a session directory.

        Args:
            session_dir: Path to the session directory.
            project_path: Original project path.
            meta: Loaded metadata.json contents.

        Returns:
            Complete Session object.
        """
        session_id = meta.get("session_id", session_dir.name)
        metadata = self._build_metadata(session_dir, project_path, meta)

        # Load stats if available
        stats_data = self._load_stats(session_dir)
        stats = self._build_stats(stats_data) if stats_data else None

        # Load and parse events
        events = self._load_events(session_dir)
        messages: list[Message] = []
        for event in events:
            msg = self._parse_event_to_message(event, session_id)
            if msg:
                messages.append(msg)

        session = Session(metadata=metadata, messages=messages)
        if stats:
            session.stats = stats

        return session

    def iter_sessions(
        self,
        project_folder: str | None = None,
        session_id: str | None = None,
    ) -> Iterator[Session]:
        """Iterate over sessions with optional filtering.

        Args:
            project_folder: Filter to sessions in this project folder only.
            session_id: Filter to this specific session ID only.

        Yields:
            Session objects matching the filter criteria.
        """
        for session_dir, project_path in self._iter_session_dirs():
            # Filter by project folder if specified
            if project_folder and session_dir.parent.name != project_folder:
                continue

            # Filter by session ID if specified
            if session_id and session_dir.name != session_id:
                continue

            try:
                meta = self._load_metadata(session_dir)
                yield self._load_session_from_dir(session_dir, project_path, meta)
            except Exception as e:
                logger.warning(f"Failed to load session from {session_dir}: {e}")

    def iter_sessions_from_path(self, path: Path) -> Iterator[Session]:
        """Iterate over sessions from a specific path.

        The path can be:
        - A session directory (contains metadata.json)
        - A project directory (contains session subdirectories)

        Args:
            path: Path to session or project directory.

        Yields:
            Session objects found at the path.
        """
        path = path.resolve()

        if not path.exists():
            logger.warning(f"Path does not exist: {path}")
            return

        # Check if this is a session directory (has metadata.json)
        if (path / "metadata.json").exists():
            # Single session
            project_path = path.parent.name
            try:
                meta = self._load_metadata(path)
                yield self._load_session_from_dir(path, project_path, meta)
            except Exception as e:
                logger.warning(f"Failed to load session from {path}: {e}")
            return

        # Check if this is a project directory (has session subdirectories)
        session_dirs = [d for d in path.iterdir() if d.is_dir() and (d / "metadata.json").exists()]
        if session_dirs:
            project_path = path.name
            for session_dir in session_dirs:
                try:
                    meta = self._load_metadata(session_dir)
                    yield self._load_session_from_dir(session_dir, project_path, meta)
                except Exception as e:
                    logger.warning(f"Failed to load session from {session_dir}: {e}")
            return

        logger.warning(f"Path is not a valid session or project directory: {path}")

    def count_sessions(self) -> int:
        """Return the total number of sessions."""
        return sum(1 for _ in self._iter_session_dirs())
