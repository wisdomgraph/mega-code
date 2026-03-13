"""Shared Pydantic models for mega_code client.

Defines the core data models shared between the open-source plugin client
and any server-side pipeline code. Client code and server-side code both
import from this module to keep a single source of truth.

Server-side code should import from here:
    from mega_code.client.models import Turn, TurnSet, SessionMetadata
    from mega_code.client.models import LessonSection, LessonDoc
"""

from __future__ import annotations

__all__ = ["SessionMetadata", "Turn", "TurnSet"]

from datetime import datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class Turn(BaseModel):
    """A single turn in the conversation."""

    model_config = ConfigDict(frozen=True)  # Immutable for safety

    turn_id: int
    role: Literal["user", "assistant"]
    content: str
    tool_name: str | None = None
    tool_target: str | None = None
    is_error: bool = False
    exit_code: int | None = None
    command: str | None = None

    def is_empty(self) -> bool:
        """Check if this turn has no meaningful content.

        A turn is empty if it lacks all meaningful fields:
        - content (non-empty string)
        - tool_name (any tool call)
        - tool_target (file/path target)
        - command (bash command)
        - is_error (error status, only meaningful if True)

        Returns:
            True if turn has no meaningful content.
        """
        has_content = self.content and self.content.strip()
        has_tool = self.tool_name is not None
        has_target = self.tool_target is not None
        has_command = self.command is not None
        has_error = self.is_error
        return not (has_content or has_tool or has_target or has_command or has_error)

    def _repr_markdown_(self) -> str:
        """Return markdown representation for Jupyter/rich display."""
        parts = [f"**[Turn {self.turn_id}]** `{self.role.upper()}`"]

        if self.tool_name:
            tool_info = f"Tool: `{self.tool_name}`"
            if self.tool_target:
                tool_info += f" → `{self.tool_target}`"
            if self.is_error:
                tool_info += " ⚠️ ERROR"
            parts.append(tool_info)

        if self.command:
            parts.append(f"\n```bash\n{self.command}\n```")

        content_preview = self.content[:500] + "..." if len(self.content) > 500 else self.content
        parts.append(f"\n{content_preview}")

        return "\n".join(parts)


class SessionMetadata(BaseModel):
    """Metadata extracted from session."""

    session_id: str
    project_path: str | None = None
    git_branch: str | None = None
    model_id: str | None = None
    started_at: datetime | None = None


class TurnSet(BaseModel):
    """A set of pre-extracted turns for a single session.

    This is the universal pipeline input. Both local and server paths
    produce TurnSets, making turns the single source of truth.

    - Local CLI: convert_sessions_to_turns() creates TurnSets from Sessions
    - Server API: TurnSets are built directly from uploaded turn data
    """

    session_id: str
    session_dir: Path = Field(default=Path(""))
    turns: list[Turn]
    metadata: SessionMetadata


class LessonSection(BaseModel):
    """One section of a lesson document."""

    heading: str
    content: str


class LessonDoc(BaseModel):
    """A single lesson learned document with flexible sections."""

    title: str
    slug: str
    sections: list[LessonSection]
    tags: list[str] = Field(default_factory=list)
