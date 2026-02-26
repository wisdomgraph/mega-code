"""Parquet dataset source for Claude Code historical data.

Supports loading from Parquet files containing conversation trajectories,
such as ZAI CC-Bench and NLILE datasets.
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


class ParquetDatasetSource:
    """Load Claude Code data from Parquet dataset files.

    Supports various Parquet formats including:
    - ZAI CC-Bench trajectories (trajectory column with JSON array)
    - NLILE Claude Code traces (messages_json column)

    Example:
        source = ParquetDatasetSource(
            path=Path("datasets/zai-cc-bench/train.parquet"),
            source_name="zai_bench",
            trajectory_column="trajectory",
        )

        for session in source.iter_sessions():
            print(f"Session {session.metadata.session_id}: {len(session.messages)} messages")
    """

    def __init__(
        self,
        path: Path,
        source_name: str,
        trajectory_column: str = "trajectory",
        id_column: str = "id",
    ):
        """Initialize the Parquet dataset source.

        Args:
            path: Path to the Parquet file or directory containing shards.
            source_name: Identifier for this source (e.g., 'zai_bench', 'nlile').
            trajectory_column: Column name containing the trajectory JSON.
            id_column: Column name for the session/record ID.
        """
        self.path = Path(path)
        self._source_name = source_name
        self.trajectory_column = trajectory_column
        self.id_column = id_column
        self._df: Any = None  # Lazy loaded DataFrame

    @property
    def name(self) -> str:
        """Return the source identifier."""
        return self._source_name

    def _load_dataframe(self) -> Any:
        """Lazily load the Parquet file into a DataFrame."""
        if self._df is not None:
            return self._df

        try:
            import pyarrow.parquet as pq
        except ImportError as e:
            raise ImportError(
                "pyarrow is required for Parquet support. " "Install with: pip install pyarrow"
            ) from e

        if self.path.is_dir():
            # Load all parquet files in directory
            table = pq.read_table(self.path)
        else:
            table = pq.read_table(self.path)

        self._df = table.to_pandas()
        return self._df

    def _parse_trajectory(self, trajectory_json: str, record_id: str) -> list[Message]:
        """Parse trajectory JSON string into a list of Message objects.

        Args:
            trajectory_json: JSON string containing the trajectory data.
            record_id: Record ID for logging purposes.

        Returns:
            List of Message objects.
        """
        messages: list[Message] = []

        try:
            trajectory = json.loads(trajectory_json)
        except json.JSONDecodeError as e:
            logger.warning(f"Failed to parse trajectory JSON for {record_id}: {e}")
            return messages

        if not isinstance(trajectory, list):
            logger.warning(f"Trajectory is not a list for {record_id}")
            return messages

        for entry in trajectory:
            msg = self._parse_entry(entry, record_id)
            if msg:
                messages.append(msg)

        return messages

    def _parse_entry(self, entry: dict[str, Any], record_id: str) -> Message | None:
        """Parse a single trajectory entry into a Message.

        Args:
            entry: A dictionary representing a single message entry.
            record_id: Record ID for logging purposes.

        Returns:
            A Message object or None if parsing fails.
        """
        entry_type = entry.get("type", "")
        msg_data = entry.get("message", {})

        # Skip non-message entries
        if entry_type not in ("user", "assistant", "system"):
            return None

        # Extract basic fields
        msg_id = entry.get("uuid", entry.get("id", f"{record_id}-{id(entry)}"))
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
                    # Extended thinking - optionally include
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
                            tool_name="",  # Not available in result
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

    def _row_to_metadata(self, row: Any, idx: int) -> HistorySessionMetadata:
        """Convert a DataFrame row to HistorySessionMetadata.

        Args:
            row: A pandas Series representing a row.
            idx: Row index.

        Returns:
            HistorySessionMetadata object.
        """
        # Try to get ID from configured column or use index
        if self.id_column in row.index:
            session_id = str(row[self.id_column])
        else:
            session_id = str(idx)

        # Extract extra metadata from known columns
        extra: dict[str, Any] = {}
        known_meta_columns = [
            "task_id",
            "task_category",
            "model_name",
            "user_messages",
            "assistant_messages",
            "total_input_tokens",
            "total_output_tokens",
            "tool_calls",
            "tool_failures",
            "failure_rate",
        ]

        for col in known_meta_columns:
            if col in row.index:
                val = row[col]
                # Handle numpy types
                if hasattr(val, "item"):
                    val = val.item()
                extra[col] = val

        model_id = extra.get("model_name")

        return HistorySessionMetadata(
            session_id=session_id,
            source=self._source_name,
            model_id=model_id,
            extra=extra,
        )

    def list_sessions(self) -> Iterator[HistorySessionMetadata]:
        """List available sessions without loading messages."""
        df = self._load_dataframe()

        for idx, row in df.iterrows():
            yield self._row_to_metadata(row, idx)

    def load_session(self, session_id: str) -> Session:
        """Load a complete session by ID."""
        df = self._load_dataframe()

        # Find the row with matching ID
        matching = None
        if self.id_column in df.columns:
            # Try exact string match first
            mask = df[self.id_column].astype(str) == session_id
            matching = df[mask]

            # If not found and ID looks numeric, try integer match
            if len(matching) == 0 and session_id.isdigit():
                try:
                    int_id = int(session_id)
                    mask = df[self.id_column] == int_id
                    matching = df[mask]
                except (ValueError, TypeError):
                    pass

        # Fallback to positional index
        if matching is None or len(matching) == 0:
            try:
                idx = int(session_id)
                if 0 <= idx < len(df):
                    matching = df.iloc[[idx]]
                else:
                    matching = df.iloc[0:0]  # Empty DataFrame
            except (ValueError, IndexError):
                raise KeyError(f"Session not found: {session_id}")

        if len(matching) == 0:
            raise KeyError(f"Session not found: {session_id}")

        row = matching.iloc[0]
        idx = matching.index[0]

        metadata = self._row_to_metadata(row, idx)

        # Parse trajectory
        trajectory_json = row.get(self.trajectory_column, "[]")
        if trajectory_json is None:
            trajectory_json = "[]"
        messages = self._parse_trajectory(str(trajectory_json), session_id)

        return Session(metadata=metadata, messages=messages)

    def iter_sessions(self) -> Iterator[Session]:
        """Iterate over all sessions with lazy loading."""
        df = self._load_dataframe()

        for idx, row in df.iterrows():
            session_id = str(row.get(self.id_column, idx))
            metadata = self._row_to_metadata(row, idx)

            trajectory_json = row.get(self.trajectory_column, "[]")
            if trajectory_json is None:
                trajectory_json = "[]"

            try:
                messages = self._parse_trajectory(str(trajectory_json), session_id)
            except Exception as e:
                logger.warning(f"Failed to parse session {session_id}: {e}")
                messages = []

            yield Session(metadata=metadata, messages=messages)

    def count_sessions(self) -> int:
        """Return the total number of sessions."""
        df = self._load_dataframe()
        return len(df)
