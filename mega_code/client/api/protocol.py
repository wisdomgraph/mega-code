"""Client protocol and response models for the MEGA-Code API.

All Pydantic models for the HTTP layer live here.  Types are kept in sync
with ``spec/openapi.yaml`` via ``tests/test_spec_sync.py`` — no code
generator needed.
"""

from __future__ import annotations

__all__ = [
    "ACTIVE_STATUSES",
    "ERROR_STATUSES",
    "TERMINAL_STATUSES",
    "APISessionMetadata",
    "ActivePipelineItem",
    "ActivePipelinesResult",
    "Decision",
    "EnhanceSkillResult",
    "ErrorResponse",
    "HealthResponse",
    "LessonSummary",
    "LessonsListResponse",
    "MegaCodeBaseClient",
    "OutputsResult",
    "PendingLessonData",
    "PendingSkillData",
    "PendingStrategyData",
    "PipelineOutputs",
    "PipelineProgress",
    "PipelineRunRequest",
    "PipelineStatusResult",
    "PipelineStopResult",
    "ProfileResult",
    "ProfileUpdateRequest",
    "SkillArtifactData",
    "SkillRefItem",
    "Status",
    "TrajectoryUploadRequest",
    "TriggerPipelineResult",
    "TurnPayload",
    "UploadResult",
    "UserProfile",
    "WisdomCurateResult",
    "WisdomFeedbackResult",
    "WisdomResultItem",
]

from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field, field_validator

from mega_code.client.models import TurnSet

# =============================================================================
# Enums
# =============================================================================


class Status(StrEnum):
    queued = "queued"
    running = "running"
    completed = "completed"
    failed = "failed"
    timeout = "timeout"
    stopped = "stopped"


class Decision(StrEnum):
    PASS = "PASS"
    MARGINAL = "MARGINAL"
    FAIL = "FAIL"


# =============================================================================
# Shared / Error Models
# =============================================================================


class ErrorResponse(BaseModel):
    """Error detail — either a string or a structured object."""

    detail: str | dict[str, Any]


class HealthResponse(BaseModel):
    status: str = "ok"
    version: str = "0.1.0"


# =============================================================================
# Trajectory Models
# =============================================================================


class TurnPayload(BaseModel):
    turn_id: int
    role: str = Field(
        ...,
        description="Raw role string. Accepted: human, user, assistant, tool_use, tool_result.",
    )
    content: str
    tool_name: str | None = None
    tool_target: str | None = None
    is_error: bool = False
    exit_code: int | None = None
    command: str | None = None


class APISessionMetadata(BaseModel):
    """Transport-level session metadata (distinct from client.models.SessionMetadata)."""

    session_id: str
    project_path: str | None = None
    git_branch: str | None = None
    model_id: str | None = None
    started_at: datetime | None = None


class TrajectoryUploadRequest(BaseModel):
    session_id: str = Field(..., description="Session UUID.")
    project_id: str = Field(..., description="Project identifier.")
    turns: list[TurnPayload] = Field(default_factory=list)
    metadata: APISessionMetadata


# =============================================================================
# Pipeline Models
# =============================================================================


class PipelineRunRequest(BaseModel):
    project_id: str = Field(..., description="Project identifier.")
    session_id: str | None = None
    steps: list[str] | None = None
    force: bool = False
    limit: int | None = None
    concurrency: int = 64
    model: str | None = None
    include_claude: bool = False
    include_codex: bool = False


class PipelineProgress(BaseModel):
    current_phase: str = ""
    sessions_total: int = 0
    sessions_processed: int = 0


class PipelineOutputs(BaseModel):
    sessions_processed: int = 0
    skill_artifacts: list[SkillArtifactData] = Field(default_factory=list)
    pending_skills: list[PendingSkillData] = Field(default_factory=list)
    pending_strategies: list[PendingStrategyData] = Field(default_factory=list)
    pending_lessons: list[PendingLessonData] = Field(default_factory=list)
    report: dict[str, Any] | None = None


# =============================================================================
# Profile Models
# =============================================================================


class ProfileUpdateRequest(BaseModel):
    """Request body for PUT /profile."""

    language: str | None = None
    level: str | None = None
    style: str | None = None
    eureka: bool = True
    goals: list[str] = Field(default_factory=list)
    enabled: bool = True
    autoPermission: bool = False


# =============================================================================
# Lesson Models
# =============================================================================


class LessonSummary(BaseModel):
    slug: str
    title: str
    project_id: str | None = None
    run_id: str | None = None
    created_at: str | None = None
    updated_at: str | None = None


class LessonsListResponse(BaseModel):
    lessons: list[LessonSummary] = Field(default_factory=list)


# =============================================================================
# Pipeline Run Status Constants
# =============================================================================
# All valid statuses: queued → running → completed | failed | timeout | stopped
#
# These sets are the single source of truth for status classification.
# Use them instead of inline tuples to keep poll loops, queries, and
# branch logic consistent when new statuses are added.

TERMINAL_STATUSES: frozenset[str] = frozenset({"completed", "failed", "timeout", "stopped"})
"""Pipeline is done — poll loops should exit."""

ACTIVE_STATUSES: frozenset[str] = frozenset({"queued", "running"})
"""Pipeline is in progress — used for conflict checks and active-run queries."""

ERROR_STATUSES: frozenset[str] = frozenset({"failed", "timeout"})
"""Pipeline finished with an error — client should report the error field."""

# =============================================================================
# Pipeline Output Models
# =============================================================================
# These hand-written models guarantee non-nullable collection defaults
# (list vs None) so consumers can safely iterate without None-checks.
# The generated counterparts use ``list[...] | None = None`` per the spec.


class PendingSkillData(BaseModel):
    """Data for a pending skill awaiting user review."""

    skill_name: str
    skill_md: str = Field(description="SKILL.md content")
    injection_rules: str = Field(description="JSON string of InjectionRules")
    evidence: str = Field(description="JSON string of evidence array")
    metadata: str = Field(description="JSON string of metadata dict")
    installed: bool = False
    approved: bool = False
    author: str = ""
    version: str = ""
    tags: list[str] = Field(default_factory=list)


class SkillArtifactData(BaseModel):
    """Data for a generated skill artifact (pipeline output)."""

    skill_id: str
    skill_type: str = ""
    decision: str = Field(description="PASS, MARGINAL, or FAIL")
    files: dict[str, str] = Field(default_factory=dict, description="filename -> content map")
    installed: bool = False
    approved: bool = False


class PendingStrategyData(BaseModel):
    """Data for a pending strategy awaiting user review."""

    strategy_name: str
    content: str = Field(description="Markdown content")
    category: str | None = None
    installed: bool = False
    approved: bool = False
    author: str = ""
    version: str = ""
    tags: list[str] = Field(default_factory=list)


class PendingLessonData(BaseModel):
    """Data for a lesson document generated by the pipeline."""

    slug: str
    title: str
    rendered_md: str = Field("", description="Rendered markdown content")
    run_id: str | None = None
    project_id: str | None = None
    author: str = ""
    version: str = ""
    tags: list[str] = Field(default_factory=list)


# =============================================================================
# Response Models
# =============================================================================


class UploadResult(BaseModel):
    """Result of uploading trajectory data."""

    status: str = Field("accepted", description="Upload status")
    session_id: str = Field("", description="Session UUID")
    message: str = Field("", description="Human-readable message")


class OutputsResult(BaseModel):
    """Pipeline outputs for a project run."""

    skill_artifacts: list[SkillArtifactData] = Field(default_factory=list)
    pending_skills: list[PendingSkillData] = Field(default_factory=list)
    pending_strategies: list[PendingStrategyData] = Field(default_factory=list)
    pending_lessons: list[PendingLessonData] = Field(default_factory=list)


class TriggerPipelineResult(BaseModel):
    """Result of triggering a pipeline run."""

    run_id: str = Field(..., description="Unique pipeline run ID")
    status: str = Field("queued", description="Initial status")
    message: str = Field("", description="Human-readable message")


class PipelineStatusResult(BaseModel):
    """Status of a pipeline run.

    Uses ``PipelineProgress`` (generated) instead of raw dict for the
    progress field. The ``status`` field stays as ``str`` so it works
    with the string-based ``TERMINAL_STATUSES`` / ``ACTIVE_STATUSES`` sets.
    """

    run_id: str
    project_id: str
    status: str
    started_at: str | None = None
    completed_at: str | None = None
    progress: PipelineProgress | None = None
    outputs: OutputsResult | None = None
    report: dict | None = None
    error: str | None = None


class UserProfile(BaseModel):
    """User profile for personalization.

    Fields language/level/style are set via CLI. Fields eureka/goals/enabled/autoPermission
    are shared with the MegaEureka VSCode extension.

    Uses ``auto_permission`` with ``alias="autoPermission"`` so the field is
    Pythonic but serialises to camelCase for the API.
    """

    language: str | None = Field(None, description="Preferred language (e.g. English, Thai)")
    level: str | None = Field(None, description="Experience level (Beginner, Intermediate, Expert)")
    style: str | None = Field(None, description="Teaching style (Mentor, Formal, Concise)")
    eureka: bool = Field(True, description="[MegaEureka] Enable learning cards generation")
    goals: list[str] = Field(
        default_factory=list,
        description="[MegaEureka] Learning goals for personalized content",
        validate_default=True,
    )

    @field_validator("goals", mode="before")
    @classmethod
    def _coerce_null_goals(cls, v):
        """Server may return null for goals — coerce to empty list."""
        return v if v is not None else []

    enabled: bool = Field(True, description="[MegaEureka] Master switch for personalization")
    auto_permission: bool = Field(
        False,
        alias="autoPermission",
        description="[MegaEureka] Auto-approve bash permissions in hooks",
    )
    email: str | None = Field(
        None,
        description="Authenticated user's email. Server-populated, read-only on client.",
    )

    model_config = ConfigDict(populate_by_name=True)


class ProfileResult(BaseModel):
    """Result of saving a profile."""

    success: bool = Field(True, description="Whether profile was saved")
    message: str = Field("", description="Human-readable message")


class EnhanceSkillResult(BaseModel):
    """Result of updating a skill with enhanced content."""

    success: bool = Field(True, description="Whether skill was enhanced")
    message: str = Field("", description="Human-readable message")


class PipelineStopResult(BaseModel):
    """Result of stopping a pipeline run."""

    run_id: str
    status: str
    message: str = ""


class ActivePipelineItem(BaseModel):
    """Summary of a single active pipeline run (client-side)."""

    run_id: str
    project_id: str
    status: str
    started_at: str | None = None
    progress: PipelineProgress | None = None


class ActivePipelinesResult(BaseModel):
    """List of active pipeline runs for the authenticated user."""

    active: bool = False
    runs: list[ActivePipelineItem] = Field(default_factory=list)


# =============================================================================
# Wisdom Curate (PCR Skill Networking)
# =============================================================================


class WisdomResultItem(BaseModel):
    """Single wisdom item from curate results."""

    wisdom_id: str = Field(description="Unique identifier of the wisdom record")
    score: float = Field(description="Relevance score from the wisdom graph")
    is_seed: bool = Field(default=False, description="Whether this wisdom is a seed node")


class SkillRefItem(BaseModel):
    """Reference to a curated skill file."""

    name: str = Field(description="Skill name derived from the wisdom graph")
    path: str = Field(description="Relative path to the skill file within the ZIP")
    url: str = Field(default="", description="Pre-signed URL to download the skill ZIP")


class ServedWisdomItem(BaseModel):
    """Served wisdom metadata for NL feedback session recovery."""

    wisdom_id: str = Field(description="Wisdom identifier")
    description: str = Field(default="", description="Wisdom description")
    combined_score: float = Field(description="Similarity score used for transfer curves")
    stage: str = Field(description="PCR stage (e.g. diagnosis, implementation)")
    step_id: str = Field(description="Step identifier in the curation workflow")


class WisdomCurateResult(BaseModel):
    """Result from wisdom curate endpoint."""

    session_id: str = Field(description="Session identifier for linking feedback")
    query: str = Field(description="Original user query")
    curation: str = Field(description="Markdown curation document with step-by-step workflow")
    skills: list[SkillRefItem] = Field(default_factory=list, description="Curated skill references")
    wisdoms: list[WisdomResultItem] = Field(
        default_factory=list, description="Retrieved wisdom records with scores"
    )
    served_wisdoms: list[ServedWisdomItem] = Field(
        default_factory=list, description="Served wisdom metadata for feedback session recovery"
    )
    token_count: int = Field(default=0, description="Total LLM tokens consumed")
    cost_usd: float = Field(default=0.0, description="Total LLM cost in USD")


class WisdomFeedbackResult(BaseModel):
    """Result from wisdom feedback endpoint."""

    session_id: str = Field(description="Session identifier from the curate call")
    feedback_id: str = Field(description="Unique identifier for the submitted feedback")
    status: str = Field(default="saved", description="Feedback submission status")


# Client Protocol
# =============================================================================


@runtime_checkable
class MegaCodeBaseClient(Protocol):
    """Client protocol for interacting with the MEGA-Code system.

    Implementations:
    - MegaCodeRemote: HTTP client to a MEGA-Code FastAPI server
    - MegaCodeLocal: In-process implementation for server-side use
    """

    def upload_trajectory(
        self,
        *,
        turn_set: TurnSet,
        project_id: str,
    ) -> UploadResult: ...

    def get_outputs(
        self,
        *,
        project_id: str,
        run_id: str,
    ) -> OutputsResult: ...

    async def trigger_pipeline_run(
        self,
        *,
        project_id: str,
        project_path: Path | None = None,
        session_id: str | None = None,
        steps: list[str] | None = None,
        force: bool = False,
        limit: int | None = None,
        concurrency: int = 64,
        model: str | None = None,
        include_codex: bool = False,
        project_cwd: str | None = None,
        agent: str = "",
    ) -> TriggerPipelineResult: ...

    def get_pipeline_status(
        self,
        *,
        run_id: str,
    ) -> PipelineStatusResult: ...

    def save_profile(
        self,
        *,
        profile: UserProfile,
    ) -> ProfileResult: ...

    def load_profile(self) -> UserProfile: ...

    def stop_pipeline(self, *, run_id: str) -> PipelineStopResult: ...

    def get_active_pipelines(self) -> ActivePipelinesResult: ...

    def enhance_skill(
        self,
        *,
        skill_name: str,
        skill_md: str,
        version: str,
        metadata: dict | None = None,
        project_id: str = "",
        parent_skill_name: str = "",
    ) -> EnhanceSkillResult: ...
