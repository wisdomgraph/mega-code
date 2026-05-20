"""Remote client implementation.

MegaCodeRemote connects to the FastAPI server via HTTP.
"""

from __future__ import annotations

import asyncio
import json
import logging
import random as _random
from pathlib import Path
from urllib.parse import quote as _url_quote

import httpx
from tenacity import (
    retry,
    retry_if_exception,
    stop_after_attempt,
)

from mega_code.client.api.protocol import (
    ActivePipelinesResult,
    EnhanceSkillResult,
    OutputsResult,
    PipelineStatusResult,
    PipelineStopResult,
    ProfileResult,
    TriggerPipelineResult,
    UploadResult,
    UserProfile,
    WisdomCurateResult,
    WisdomFeedbackResult,
)
from mega_code.client.models import TurnSet
from mega_code.client.utils.tracing import traced

logger = logging.getLogger(__name__)

_MAX_ATTEMPTS = 5

_INITIAL_RETRY_DELAY = 0.5

_MAX_RETRY_DELAY = 8.0

_RETRYABLE_STATUS_CODES = {429, 502, 503, 504}


def _is_retryable(exc: BaseException) -> bool:
    """Retry on transient HTTP errors and network failures."""
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code in _RETRYABLE_STATUS_CODES
    return isinstance(exc, (httpx.NetworkError, httpx.TimeoutException))


def _wait_exponential_jitter(retry_state) -> float:
    """Exponential backoff with multiplicative jitter (Anthropic SDK style).

    Formula: min(0.5 * 2^n, 8.0) * (1 - 0.25 * random())
    Jitter range: [75%, 100%] of base delay.
    """
    n = retry_state.attempt_number - 1
    delay = min(_INITIAL_RETRY_DELAY * (2.0**n), _MAX_RETRY_DELAY)
    return delay * (1 - 0.25 * _random.random())


def _log_retry(retry_state) -> None:
    exc = retry_state.outcome.exception()
    fn_name = getattr(retry_state.fn, "__name__", "request")
    logger.warning(
        "%s attempt %d/%d failed (%s: %s), retrying…",
        fn_name,
        retry_state.attempt_number,
        _MAX_ATTEMPTS,
        exc.__class__.__name__,
        exc,
    )


_AUTH_ERROR_MSG = (
    "Authentication failed ({status} {reason}). Your API key may be invalid or expired.\n"
    "\n"
    "To update your API key, run:\n"
    "  mega-code configure --api-key <your_key>\n"
)


class MegaCodeRemote:
    """HTTP client connecting to the MEGA-Code FastAPI server.

    Uses httpx for HTTP requests with Bearer token auth.
    Sync methods use httpx.Client; async methods use httpx.AsyncClient.
    """

    def __init__(
        self,
        *,
        server_url: str,
        api_key: str = "",
        timeout: float = 30.0,
    ) -> None:
        """Initialise remote client.

        Args:
            server_url: Base URL of the MEGA-Code server (e.g., http://localhost:8000).
            api_key: API key for Bearer token auth. If empty, no auth header is sent.
            timeout: HTTP request timeout in seconds.
        """
        headers = {}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        self._client = httpx.Client(
            base_url=server_url,
            headers=headers,
            timeout=timeout,
        )
        self._async_client: httpx.AsyncClient | None = None
        self._async_client_kwargs = {
            "base_url": server_url,
            "headers": dict(headers),
            "timeout": timeout,
        }

    def _get_async_client(self) -> httpx.AsyncClient:
        """Return (and lazily create) the async HTTP client."""
        if self._async_client is None or self._async_client.is_closed:
            self._async_client = httpx.AsyncClient(**self._async_client_kwargs)
        return self._async_client

    @staticmethod
    def _set_current_span_attrs(**attrs) -> None:
        """Set attributes on the current OTEL span if available."""
        try:
            from opentelemetry import trace

            span = trace.get_current_span()
            for k, v in attrs.items():
                if isinstance(v, (str, int, float, bool)):
                    span.set_attribute(k, v)
                else:
                    span.set_attribute(k, json.dumps(v, default=str))
        except ImportError:
            pass

    @staticmethod
    def _check_response(resp: httpx.Response) -> None:
        """Raise on auth/config errors, otherwise the default HTTPStatusError."""
        if resp.status_code in (401, 403):
            raise ValueError(
                _AUTH_ERROR_MSG.format(status=resp.status_code, reason=resp.reason_phrase)
            )
        if resp.status_code == 400:
            raise ValueError(resp.text)
        resp.raise_for_status()

    @retry(
        retry=retry_if_exception(_is_retryable),
        wait=_wait_exponential_jitter,
        stop=stop_after_attempt(_MAX_ATTEMPTS),
        before_sleep=_log_retry,
        reraise=True,
    )
    @traced("client.remote.upload_trajectory")
    def upload_trajectory(
        self,
        *,
        turn_set: TurnSet,
        project_id: str,
    ) -> UploadResult:
        """Upload TurnSet to the server via POST /api/megacode/v1/trajectory."""
        payload = {
            "session_id": turn_set.session_id,
            "project_id": project_id,
            "turns": [t.model_dump() for t in turn_set.turns],
            "metadata": turn_set.metadata.model_dump(mode="json"),
        }
        resp = self._client.post("/api/megacode/v1/trajectory", json=payload)
        self._check_response(resp)
        return UploadResult(**resp.json())

    @traced("client.remote.get_outputs")
    def get_outputs(
        self,
        *,
        project_id: str,
        run_id: str,
    ) -> OutputsResult:
        """Retrieve outputs via GET /api/megacode/v1/outputs/{project_id}/{run_id}."""
        resp = self._client.get(f"/api/megacode/v1/outputs/{project_id}/{run_id}")
        self._check_response(resp)
        return OutputsResult(**resp.json())

    @staticmethod
    def _resolve_match_path(label: str, project_path: Path, project_cwd: str | None) -> str | None:
        """Resolve the real working directory used to match native sessions.

        Prefers ``project_cwd`` when provided; otherwise looks the project
        name up in the local stats mapping. Logs a warning and returns
        ``None`` if neither resolves — the caller should treat that as a
        no-op sync.
        """
        if project_cwd:
            return project_cwd

        from mega_code.client.stats import load_mapping

        mapping = load_mapping()
        match_path = mapping.get(project_path.name)
        if not match_path:
            logger.warning(
                "%s sync skipped: project_cwd not provided and could not "
                "resolve real project path from mapping for %s",
                label,
                project_path.name,
            )
            return None
        return match_path

    async def _sync_claude(
        self,
        project_path: Path,
        project_id: str,
        project_cwd: str | None,
    ) -> None:
        """Sync Claude Code native sessions to the server.

        Matches sessions whose JSONL transcripts under
        ``~/.claude/projects/`` record ``project_cwd`` as their cwd. Falls
        back to ``load_mapping()`` when ``project_cwd`` is not provided.
        """
        from mega_code.client.api.claude_sync import sync_claude_trajectories

        claude_match_path = self._resolve_match_path("Claude", project_path, project_cwd)
        if claude_match_path is None:
            return

        logger.info(
            "Claude sync: claude_match_path=%s (project_cwd=%s)",
            claude_match_path,
            project_cwd,
        )
        synced = await asyncio.to_thread(
            sync_claude_trajectories,
            project_path,
            self,
            project_id,
            claude_match_path,
        )
        logger.info("Claude sync: uploaded %d session(s)", synced)

    async def _sync_claude_single(
        self,
        session_id: str,
        project_path: Path,
        project_id: str,
        project_cwd: str | None,
    ) -> int:
        """Sync exactly one Claude session — the no-flag wisdom-gen path.

        Returns the number of sessions uploaded (0 or 1). 0 means the
        session was either not found, empty, or filtered to zero turns —
        callers can use this to skip the trigger POST entirely.
        """
        from mega_code.client.api.claude_sync import sync_single_claude_session

        claude_match_path = self._resolve_match_path(
            "Claude single-session", project_path, project_cwd
        )
        if claude_match_path is None:
            return 0

        synced = await asyncio.to_thread(
            sync_single_claude_session,
            session_id,
            project_path,
            self,
            project_id,
            claude_match_path,
        )
        logger.info("Claude single-session sync: uploaded %d session(s)", synced)
        return synced

    async def _sync_codex(
        self,
        project_path: Path,
        project_id: str,
        project_cwd: str | None,
    ) -> None:
        """Sync Codex CLI sessions to the server."""
        from mega_code.client.api.codex_sync import sync_codex_trajectories

        codex_match_path = self._resolve_match_path("Codex", project_path, project_cwd)
        if codex_match_path is None:
            return

        logger.info(
            "Codex sync: codex_match_path=%s (project_cwd=%s)",
            codex_match_path,
            project_cwd,
        )
        synced = await asyncio.to_thread(
            sync_codex_trajectories,
            project_path,
            self,
            project_id,
            codex_match_path,
        )
        logger.info("Codex sync: uploaded %d session(s)", synced)

    @traced("client.remote.trigger_pipeline_run")
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
        project_cwd: str | None = None,
        agent: str = "",
    ) -> TriggerPipelineResult:
        """Trigger pipeline run via POST /api/megacode/v1/pipeline/run.

        If project_path is given, syncs local trajectories to the server
        first. The active ``agent`` controls which sessions are synced —
        Claude sessions when ``agent="claude"``, Codex sessions when
        ``agent="codex"``, MEGA-Code trajectories when ``agent`` is unset
        (the empty-string default; callers typically configure it through
        ``MEGA_CODE_AGENT``).

        Args:
            project_cwd: The real working directory (e.g. /Users/foo/proj).
                Used for cwd matching when scanning native session
                transcripts. Without it, agent-native syncs become no-ops.
            agent: The active coding agent identity (``"claude"``,
                ``"codex"``, or ``""`` for the MEGA-Code branch). Selects
                exactly one sync branch.
        """
        # Sync local sessions to server if project_path provided.
        # Native sync helpers offload to threads internally. When the caller
        # targets a single session_id under Claude (the no-flag wisdom-gen
        # path), upload only that one transcript instead of every matching
        # session — preserving "no-flag = current session only" semantics.
        if project_path is not None:
            if agent == "claude" and session_id is not None:
                synced = await self._sync_claude_single(
                    session_id, project_path, project_id, project_cwd
                )
                # Short-circuit if the target session uploaded nothing
                # (filtered to zero turns, missing, or empty transcript).
                # No point asking the server to run a pipeline on a session
                # it has never received.
                if synced == 0:
                    logger.info(
                        "Skipping pipeline trigger: single-session sync "
                        "uploaded 0 transcripts for session %s",
                        session_id,
                    )
                    return TriggerPipelineResult(
                        run_id="",
                        status="skipped_empty_session",
                        message=(
                            "Current session has no learnable content yet "
                            "(filtered to zero turns or empty transcript). "
                            "Do some real work in this session and retry, "
                            "or use --project to run on the whole project."
                        ),
                    )
            elif agent == "claude":
                await self._sync_claude(project_path, project_id, project_cwd)
            elif agent == "codex":
                await self._sync_codex(project_path, project_id, project_cwd)
            else:
                from mega_code.client.api.sync import sync_trajectories

                await asyncio.to_thread(sync_trajectories, project_path, self, project_id)

        payload = {
            "project_id": project_id,
            "force": force,
            "concurrency": concurrency,
        }
        if session_id is not None:
            payload["session_id"] = session_id
        if steps is not None:
            payload["steps"] = steps
        if limit is not None:
            payload["limit"] = limit
        if model is not None:
            payload["model"] = model

        async_client = self._get_async_client()
        resp = await async_client.post("/api/megacode/v1/pipeline/run", json=payload)
        self._check_response(resp)
        return TriggerPipelineResult(**resp.json())

    @traced("client.remote.get_pipeline_status")
    def get_pipeline_status(
        self,
        *,
        run_id: str,
    ) -> PipelineStatusResult:
        """Poll pipeline status via GET /api/megacode/v1/pipeline/status/{run_id}."""
        resp = self._client.get(f"/api/megacode/v1/pipeline/status/{run_id}")
        self._check_response(resp)
        data = resp.json()

        # Parse outputs into OutputsResult if present
        outputs_raw = data.get("outputs")
        outputs = OutputsResult(**outputs_raw) if outputs_raw else None

        return PipelineStatusResult(
            run_id=data["run_id"],
            project_id=data["project_id"],
            status=data["status"],
            started_at=data.get("started_at"),
            completed_at=data.get("completed_at"),
            progress=data.get("progress"),
            outputs=outputs,
            report=outputs_raw.get("report") if outputs_raw else None,
            error=data.get("error"),
        )

    @traced("client.remote.save_profile", kind="CLIENT", openinference_kind="TOOL")
    def save_profile(
        self,
        *,
        profile: UserProfile,
    ) -> ProfileResult:
        """Save user profile to remote DB then mirror to local JSON file.

        Order of operations:
          1. PUT /api/megacode/v1/profile  → persists to mega-service Postgres
          2. Write ~/.local/share/mega-code/profile.json  → local mirror for inspection
             (only written when the API call succeeds)
        """
        payload = profile.model_dump(by_alias=True, exclude={"email"})
        resp = self._client.put("/api/megacode/v1/profile", json=payload)
        self._check_response(resp)
        data = resp.json()

        # Mirror to local file only after a successful remote save.
        from mega_code.client.profile import save_profile as _save_local

        _save_local(profile)

        return ProfileResult(
            success=data.get("success", True),
            message=data.get("message", ""),
        )

    @traced("client.remote.stop_pipeline", kind="CLIENT", openinference_kind="TOOL")
    def stop_pipeline(
        self,
        *,
        run_id: str,
    ) -> PipelineStopResult:
        """Stop a pipeline run via POST /api/megacode/v1/pipeline/stop/{run_id}."""
        resp = self._client.post(f"/api/megacode/v1/pipeline/stop/{run_id}")
        self._check_response(resp)
        return PipelineStopResult(**resp.json())

    @traced("client.remote.get_active_pipelines", kind="CLIENT", openinference_kind="TOOL")
    def get_active_pipelines(self) -> ActivePipelinesResult:
        """List active pipelines via GET /api/megacode/v1/pipeline/status."""
        resp = self._client.get("/api/megacode/v1/pipeline/status")
        self._check_response(resp)
        return ActivePipelinesResult(**resp.json())

    @traced("client.remote.enhance_skill", kind="CLIENT", openinference_kind="TOOL")
    def enhance_skill(
        self,
        *,
        skill_name: str,
        skill_md: str,
        version: str,
        metadata: dict | None = None,
    ) -> EnhanceSkillResult:
        """Create a new skill version via POST /api/megacode/v1/skills/{skill_name}/enhance."""
        self._set_current_span_attrs(skill_name=skill_name, version=version)
        body: dict = {
            "skill_md": skill_md,
            "version": version,
        }
        if metadata:
            body["metadata"] = metadata
        resp = self._client.post(
            f"/api/megacode/v1/skills/{_url_quote(skill_name, safe='')}/enhance",
            json=body,
        )
        self._check_response(resp)
        return EnhanceSkillResult(**resp.json())

    @traced("client.remote.load_profile", kind="CLIENT", openinference_kind="TOOL")
    def load_profile(self) -> UserProfile:
        """Load user profile via GET /api/megacode/v1/profile."""
        resp = self._client.get("/api/megacode/v1/profile")
        self._check_response(resp)
        return UserProfile(**resp.json())

    # -------------------------------------------------------------------------
    # Wisdom Curate (PCR Skill Networking)
    # -------------------------------------------------------------------------

    @retry(
        retry=retry_if_exception(_is_retryable),
        wait=_wait_exponential_jitter,
        stop=stop_after_attempt(_MAX_ATTEMPTS),
        before_sleep=_log_retry,
        reraise=True,
    )
    @traced("client.remote.wisdom_curate", kind="CLIENT", openinference_kind="TOOL")
    def wisdom_curate(
        self,
        *,
        query: str,
        session_id: str = "",
        top_k: int = 20,
    ) -> WisdomCurateResult:
        """Curate wisdom via POST /api/megacode/v1/wisdom/curate."""
        self._set_current_span_attrs(query=query, session_id=session_id, top_k=top_k)
        body: dict = {"query": query, "top_k": top_k}
        if session_id:
            body["session_id"] = session_id
        resp = self._client.post("/api/megacode/v1/wisdom/curate", json=body)
        self._check_response(resp)
        return WisdomCurateResult(**resp.json())

    @retry(
        retry=retry_if_exception(_is_retryable),
        wait=_wait_exponential_jitter,
        stop=stop_after_attempt(_MAX_ATTEMPTS),
        before_sleep=_log_retry,
        reraise=True,
    )
    @traced("client.remote.wisdom_feedback", kind="CLIENT", openinference_kind="TOOL")
    def wisdom_feedback(
        self,
        *,
        session_id: str,
        feedback_text: str,
    ) -> WisdomFeedbackResult:
        """Submit wisdom feedback via POST /api/megacode/v1/wisdom/feedback."""
        self._set_current_span_attrs(session_id=session_id)
        body = {"session_id": session_id, "feedback_text": feedback_text}
        resp = self._client.post("/api/megacode/v1/wisdom/feedback", json=body)
        self._check_response(resp)
        return WisdomFeedbackResult(**resp.json())

    @property
    def server_url(self) -> str:
        """Get the configured server URL."""
        return str(self._client.base_url)

    def close(self) -> None:
        """Close the sync HTTP client."""
        self._client.close()

    async def aclose(self) -> None:
        """Close both sync and async HTTP clients."""
        self._client.close()
        if self._async_client is not None and not self._async_client.is_closed:
            await self._async_client.aclose()

    def __enter__(self):
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args: object) -> None:
        await self.aclose()
