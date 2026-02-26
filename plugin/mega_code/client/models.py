"""Shared Pydantic models for mega_code client.

Extracted from pipeline/models.py, pipeline/feedback.py and pipeline/lesson.py
so that client/ has zero enterprise dependencies. These models are used by both
open-source (client) and internal (pipeline) codepaths.

Enterprise code should import from here:
    from mega_code.client.models import Turn, TurnSet, SessionMetadata
    from mega_code.client.models import LessonSection, LessonDoc
"""

from __future__ import annotations

__all__ = ["Turn", "SessionMetadata", "TurnSet", "FeedbackItem"]

from datetime import datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


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


class FeedbackItem(BaseModel):
    """Feedback for a single skill or strategy.

    Type-specific ratings dict allows different dimensions per type:
    - Skills: focus, accuracy, completeness, conciseness, clarity (1-5 scale)
    - Strategies: accuracy, relevance, specificity (1-5 scale)
    """

    item_id: str
    item_type: Literal["skill", "strategy"]
    ratings: dict[str, int] = Field(default_factory=dict)
    useful: Literal["yes", "no", "maybe"] | None = None

    @field_validator("useful", mode="before")
    @classmethod
    def _normalize_useful(cls, v: str | bool | None) -> str | None:
        if isinstance(v, bool):
            return "yes" if v else "no"
        return v

    reason: str | None = None
    improvement_suggestion: str | None = None
    correction: str | None = None
    action_taken: str | None = None  # "installed", "installed_enhanced", "skipped", "pending"
    item_path: str | None = None
    item_name: str | None = None


class LessonSection(BaseModel):
    """One section of a lesson document."""

    heading: str
    content: str


class LessonDoc(BaseModel):
    """A single lesson learned document with flexible sections."""

    title: str
    slug: str
    sections: list[LessonSection]
