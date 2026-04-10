"""Data models for Codex historical data.

This module provides unified data models for representing Codex
conversation data from multiple sources (native, MEGA-Code, datasets).
"""

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


class ToolCall(BaseModel):
    """Normalized tool invocation representation.

    Represents a single tool call made during a conversation,
    including both the request and response.
    """

    tool_id: str = Field(description="Unique identifier for this tool call (e.g., toolu_XXX)")
    tool_name: str = Field(description="Name of the tool (e.g., Bash, Read, Edit)")
    input: dict[str, Any] = Field(default_factory=dict, description="Tool input parameters")
    output: str | None = Field(default=None, description="Tool output/result")
    is_error: bool = Field(default=False, description="Whether the tool call resulted in error")
    duration_ms: int | None = Field(default=None, description="Execution duration in milliseconds")
    status: str | None = Field(
        default=None, description="Execution status: success, error, cancelled, etc."
    )


class TokenUsage(BaseModel):
    """Token consumption metrics for a message or session."""

    input_tokens: int = Field(default=0, description="Input tokens consumed")
    output_tokens: int = Field(default=0, description="Output tokens generated")
    cache_read_tokens: int = Field(default=0, description="Tokens read from cache")
    cache_create_tokens: int = Field(default=0, description="Tokens written to cache")

    def __add__(self, other: "TokenUsage") -> "TokenUsage":
        """Add two TokenUsage instances together."""
        return TokenUsage(
            input_tokens=self.input_tokens + other.input_tokens,
            output_tokens=self.output_tokens + other.output_tokens,
            cache_read_tokens=self.cache_read_tokens + other.cache_read_tokens,
            cache_create_tokens=self.cache_create_tokens + other.cache_create_tokens,
        )


class Message(BaseModel):
    """Unified message representation.

    Normalizes messages from different sources into a consistent format
    for analysis.
    """

    id: str = Field(description="Unique message identifier (UUID)")
    role: Literal["user", "assistant", "system"] = Field(description="Message role")
    content: str = Field(default="", description="Plain text content of the message")
    tool_calls: list[ToolCall] = Field(
        default_factory=list, description="Tool calls made in this message (assistant only)"
    )
    tool_results: list[ToolCall] = Field(
        default_factory=list, description="Tool results received in this message (user only)"
    )
    timestamp: datetime | None = Field(default=None, description="Message creation timestamp")
    token_usage: TokenUsage | None = Field(default=None, description="Token usage for this message")
    model: str | None = Field(default=None, description="Model used for this message")
    reasoning: list[dict[str, Any]] | None = Field(
        default=None,
        description="Extended thinking/reasoning steps (e.g., Gemini thoughts, Claude thinking)",
    )
    raw: dict[str, Any] | None = Field(
        default=None, description="Original raw data for debugging", exclude=True
    )


class HistorySessionMetadata(BaseModel):
    """Session-level metadata for the multi-source history loader.

    Not to be confused with ``schema.CollectorSessionMetadata`` (collector file I/O)
    or ``models.SessionMetadata`` (pipeline TurnSet metadata).
    """

    session_id: str = Field(description="Unique session identifier")
    source: str = Field(
        description="Data source identifier (claude_native, mega_code, zai_bench, nlile)"
    )
    project_path: str | None = Field(default=None, description="Project directory path")
    git_branch: str | None = Field(default=None, description="Git branch at session time")
    model_id: str | None = Field(default=None, description="Primary model used")
    started_at: datetime | None = Field(default=None, description="Session start timestamp")
    ended_at: datetime | None = Field(default=None, description="Session end timestamp")
    first_prompt: str | None = Field(default=None, description="First user prompt (truncated)")
    extra: dict[str, Any] = Field(default_factory=dict, description="Source-specific metadata")


class HistorySessionStats(BaseModel):
    """Aggregated session statistics for the multi-source history loader.

    Not to be confused with ``schema.SessionStats`` (collector real-time stats).
    """

    message_count: int = Field(default=0, description="Total number of messages")
    user_message_count: int = Field(default=0, description="Number of user messages")
    assistant_message_count: int = Field(default=0, description="Number of assistant messages")
    tool_call_count: int = Field(default=0, description="Total tool invocations")
    tool_calls_by_type: dict[str, int] = Field(
        default_factory=dict, description="Tool calls grouped by tool name"
    )
    error_count: int = Field(default=0, description="Number of tool errors")
    total_tokens: TokenUsage = Field(default_factory=TokenUsage, description="Total token usage")
    estimated_cost_usd: float | None = Field(default=None, description="Estimated API cost")

    @classmethod
    def from_messages(cls, messages: list[Message]) -> "HistorySessionStats":
        """Compute statistics from a list of messages."""
        stats = cls()
        stats.message_count = len(messages)

        tool_calls_by_type: dict[str, int] = {}
        total_tokens = TokenUsage()

        for msg in messages:
            if msg.role == "user":
                stats.user_message_count += 1
                # Count tool results (errors)
                for tr in msg.tool_results:
                    if tr.is_error:
                        stats.error_count += 1
            elif msg.role == "assistant":
                stats.assistant_message_count += 1
                # Count tool calls
                for tc in msg.tool_calls:
                    stats.tool_call_count += 1
                    tool_calls_by_type[tc.tool_name] = tool_calls_by_type.get(tc.tool_name, 0) + 1

            # Accumulate tokens
            if msg.token_usage:
                total_tokens = total_tokens + msg.token_usage

        stats.tool_calls_by_type = tool_calls_by_type
        stats.total_tokens = total_tokens

        return stats


class Session(BaseModel):
    """A complete conversation session/trajectory.

    Contains all messages and metadata for a single conversation.
    """

    metadata: HistorySessionMetadata = Field(description="Session metadata")
    messages: list[Message] = Field(default_factory=list, description="Conversation messages")
    stats: HistorySessionStats | None = Field(default=None, description="Computed statistics")

    def compute_stats(self) -> "Session":
        """Compute and attach statistics from messages."""
        self.stats = HistorySessionStats.from_messages(self.messages)
        return self

    def model_post_init(self, __context: Any) -> None:
        """Auto-compute stats if not provided."""
        if self.stats is None and self.messages:
            self.stats = HistorySessionStats.from_messages(self.messages)
