"""Turn extraction from Claude Code session events (client edition).

This module handles the extraction and transformation of raw session events
(from events.jsonl) into structured Turn objects for pipeline processing.

It also provides persistence for turns as turns.jsonl files, making turns
the single source of truth for the pipeline.
"""

import json
import logging
from pathlib import Path
from typing import Literal, cast

from mega_code.client.history.models import Message, Session
from mega_code.client.compaction import CodeBlockCompactor
from mega_code.client.models import SessionMetadata, Turn, TurnSet
from mega_code.client.utils.tracing import traced

logger = logging.getLogger(__name__)


class TurnExtractor:
    """Extract turns from session events with optional content compaction.

    The extractor processes session messages and creates Turn objects with:
    - Role (user/assistant)
    - Content (optionally compacted to replace code blocks with placeholders)
    - Tool information (name, target, command)
    - Error status from tool results

    Example:
        extractor = TurnExtractor(compact_code=True)
        turns, metadata = extractor.extract(session)
    """

    def __init__(self, compact_code: bool = True):
        """Initialize the turn extractor.

        Args:
            compact_code: Whether to replace code blocks with placeholders.
        """
        self.compact_code = compact_code
        self.compactor = CodeBlockCompactor() if compact_code else None

    def extract(self, session: Session) -> tuple[list[Turn], SessionMetadata]:
        """Extract turns from a session.

        Args:
            session: Session object with messages.

        Returns:
            Tuple of (turns, metadata).
            - turns: List of Turn objects
            - metadata: SessionMetadata with session info
        """
        if self.compactor:
            self.compactor.reset()

        all_turns: list[Turn] = []

        for i, msg in enumerate(session.messages):
            turn = self._message_to_turn(i, msg)
            if turn is not None:
                all_turns.append(turn)

        # Filter out empty turns (only have turn_id and role, no meaningful content)
        turns = [t for t in all_turns if not t.is_empty()]
        filtered_count = len(all_turns) - len(turns)
        if filtered_count > 0:
            logger.info(f"Filtered {filtered_count} empty turns (no meaningful content)")

        metadata = self._build_metadata(session)

        return turns, metadata

    def _message_to_turn(self, index: int, msg: Message) -> Turn | None:
        """Convert a Message to a Turn with optional compaction.

        Args:
            index: Message index (used as turn_id).
            msg: Message object.

        Returns:
            Turn object or None if message should be skipped.
        """
        # Only process user and assistant messages
        if msg.role not in ("user", "assistant"):
            return None

        # Extract tool info from tool_calls
        tool_name = None
        tool_target = None
        command = None
        is_error = False

        if msg.tool_calls:
            tc = msg.tool_calls[0]  # Primary tool call
            tool_name = tc.tool_name
            tool_target = tc.input.get("file_path") or tc.input.get("path")
            command = tc.input.get("command")

        if msg.tool_results:
            is_error = any(tr.is_error for tr in msg.tool_results)

        # Apply compaction if enabled
        content = msg.content
        if self.compactor and content:
            result = self.compactor.compact(content)
            content = result.compacted

        role = cast(Literal["user", "assistant"], msg.role)
        return Turn(
            turn_id=index,
            role=role,
            content=content,
            tool_name=tool_name,
            tool_target=tool_target,
            is_error=is_error,
            command=command,
        )

    def _build_metadata(self, session: Session) -> SessionMetadata:
        """Build SessionMetadata from session.

        Args:
            session: Session object.

        Returns:
            SessionMetadata object.
        """
        return SessionMetadata(
            session_id=session.metadata.session_id,
            project_path=session.metadata.project_path,
            git_branch=session.metadata.git_branch,
            model_id=session.metadata.model_id,
            started_at=session.metadata.started_at,
        )


@traced("step0.extract_turns")
def extract_turns(
    session: Session,
    compact_code: bool = True,
) -> tuple[list[Turn], SessionMetadata]:
    """Convenience function to extract turns from a session.

    Args:
        session: Session object with messages.
        compact_code: Whether to replace code blocks with placeholders.

    Returns:
        Tuple of (turns, metadata).
    """
    extractor = TurnExtractor(compact_code=compact_code)
    return extractor.extract(session)


# =============================================================================
# Turns JSONL Persistence
# =============================================================================

TURNS_FILENAME = "turns.jsonl"


def save_turns_jsonl(
    turns: list[Turn],
    metadata: SessionMetadata,
    output_dir: Path,
) -> Path:
    """Save turns and metadata to a turns.jsonl file.

    Format: first line is metadata (with _meta key), subsequent lines are Turn objects.
    This mirrors the events.jsonl pattern and is easy to stream/append.

    Args:
        turns: List of Turn objects to save.
        metadata: Session metadata.
        output_dir: Directory to write turns.jsonl into.

    Returns:
        Path to the saved turns.jsonl file.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / TURNS_FILENAME

    with open(output_path, "w", encoding="utf-8") as f:
        # First line: metadata with _meta wrapper
        meta_line = json.dumps({"_meta": metadata.model_dump(mode="json")}, default=str)
        f.write(meta_line + "\n")
        # Subsequent lines: one Turn per line
        for turn in turns:
            f.write(json.dumps(turn.model_dump(mode="json"), default=str) + "\n")

    logger.info(f"Saved {len(turns)} turns to {output_path}")
    return output_path


def load_turns_jsonl(turns_path: Path) -> TurnSet | None:
    """Load turns and metadata from a turns.jsonl file.

    Args:
        turns_path: Path to turns.jsonl file.

    Returns:
        TurnSet with loaded turns and metadata, or None if file doesn't exist.
    """
    if not turns_path.exists():
        return None

    turns: list[Turn] = []
    metadata: SessionMetadata | None = None

    with open(turns_path, "r", encoding="utf-8") as f:
        for line_num, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                data = json.loads(line)
            except json.JSONDecodeError as e:
                logger.warning(f"Failed to parse line {line_num} in {turns_path}: {e}")
                continue

            if "_meta" in data:
                metadata = SessionMetadata.model_validate(data["_meta"])
            else:
                turns.append(Turn.model_validate(data))

    if metadata is None:
        logger.warning(f"No metadata found in {turns_path}")
        return None

    return TurnSet(
        session_id=metadata.session_id,
        session_dir=turns_path.parent,
        turns=turns,
        metadata=metadata,
    )
