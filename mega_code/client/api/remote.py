"""Remote client implementation.

MegaCodeRemote connects to the FastAPI server via HTTP.
"""

from __future__ import annotations

import asyncio
import json
import logging
import random as _random
from pathlib import Path

import httpx
from tenacity import (
    retry,
    retry_if_exception,
    stop_after_attempt,
)

from mega_code.client.api.protocol import (
    ActivePipelinesResult,
    OutputsResult,
    PipelineStatusResult,
    PipelineStopResult,
    ProfileResult,
    TriggerPipelineResult,
    UploadResult,
    UserProfile,
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
    logger.warning(
        "upload_trajectory attempt %d/%d failed (%s: %s), retrying…",
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
    @traced("client.remote.upload_trajectory", kind="CLIENT")
    def upload_trajectory(
        self,
        *,
        turn_set: TurnSet,
        project_id: str,
    ) -> UploadResult:
        """Upload TurnSet to the server via POST /api/megacode/v1/trajectory."""
        from mega_code.client.utils.tracing import get_tracer

        tracer = get_tracer(__name__)
        with tracer.start_as_current_span("http.POST /trajectory") as http_span:
            http_span.set_attribute("http.method", "POST")
            http_span.set_attribute(
                "http.url", f"{self._client.base_url}/api/megacode/v1/trajectory"
            )
            http_span.set_attribute("upload.session_id", turn_set.session_id)
            http_span.set_attribute("upload.project_id", project_id)
            http_span.set_attribute("upload.turn_count", len(turn_set.turns))
            http_span.set_attribute("upload.session_dir", str(turn_set.session_dir))
            if turn_set.metadata:
                http_span.set_attribute(
                    "upload.metadata.project_path", turn_set.metadata.project_path or ""
                )
                http_span.set_attribute(
                    "upload.metadata.git_branch", turn_set.metadata.git_branch or ""
                )
                http_span.set_attribute(
                    "upload.metadata.model_id", turn_set.metadata.model_id or ""
                )
                if turn_set.metadata.started_at:
                    http_span.set_attribute(
                        "upload.metadata.started_at", str(turn_set.metadata.started_at)
                    )

            payload = {
                "session_id": turn_set.session_id,
                "project_id": project_id,
                "turns": [t.model_dump() for t in turn_set.turns],
                "metadata": turn_set.metadata.model_dump(mode="json"),
            }
            payload_json = json.dumps(payload)
            http_span.set_attribute("upload.payload_size_bytes", len(payload_json))
            http_span.set_attribute("upload.payload_json", payload_json)

            resp = self._client.post("/api/megacode/v1/trajectory", json=payload)
            http_span.set_attribute("http.status_code", resp.status_code)
            resp_data = resp.json()
            http_span.set_attribute("upload.response_json", json.dumps(resp_data))
            self._check_response(resp)
            result = UploadResult(**resp_data)
            http_span.set_attribute("upload.response.status", getattr(result, "status", ""))
            http_span.set_attribute("upload.response.message", getattr(result, "message", ""))
            return result

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

    async def _sync_codex(
        self,
        project_path: Path,
        project_id: str,
        project_cwd: str | None,
    ) -> None:
        """Sync Codex native sessions to the server."""
        from mega_code.client.api.codex_sync import sync_codex_trajectories

        # Use the actual project CWD for codex session matching, not the
        # mega-code data dir. The codex sessions record the real working
        # directory in their 'cwd' field.
        #
        # If project_cwd is not provided, resolve the real project path
        # from the mapping file. Falling back to str(project_path) would
        # use the mega-code data dir which never matches any session cwd.
        codex_match_path = project_cwd or None
        if not codex_match_path:
            from mega_code.client.stats import load_mapping

            mapping = load_mapping()
            codex_match_path = mapping.get(project_path.name)

        if not codex_match_path:
            logger.warning(
                "Codex sync skipped: project_cwd not provided and could not "
                "resolve real project path from mapping for %s",
                project_path.name,
            )
        else:
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
                ledger_dir=project_path,
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
        include_codex: bool = False,
        project_cwd: str | None = None,
        agent: str = "",
    ) -> TriggerPipelineResult:
        """Trigger pipeline run via POST /api/megacode/v1/pipeline/run.

        If project_path is given, syncs local trajectories to the server
        first. Codex sessions are synced by default. Mega-code sessions
        are synced as a fallback when agent is unknown.

        Args:
            project_cwd: The actual working directory (e.g. /tmp/test-project).
                Used for Codex session path matching. Falls back to
                project_path if not provided.
            agent: The current coding agent identity (``"codex"`` or ``""``).
                Controls which sessions are synced by default.
        """
        # Sync local sessions to server if project_path provided.
        # sync_trajectories is sync (uses self._client internally),
        # so offload to a thread to avoid blocking the event loop.
        if project_path is not None:
            if agent == "codex":
                # Codex agent: sync codex sessions
                await self._sync_codex(project_path, project_id, project_cwd)
            else:
                # Unknown/unset agent: sync mega-code sessions (backward compat)
                from mega_code.client.api.sync import sync_trajectories

                await asyncio.to_thread(sync_trajectories, project_path, self, project_id)

            # Explicit codex include (opt-in when agent is not codex)
            if include_codex and agent != "codex":
                await self._sync_codex(project_path, project_id, project_cwd)

        from mega_code.client.utils.tracing import get_tracer

        tracer = get_tracer(__name__)
        with tracer.start_as_current_span("http.POST /pipeline/run") as http_span:
            http_span.set_attribute("http.method", "POST")
            http_span.set_attribute(
                "http.url", f"{self._client.base_url}/api/megacode/v1/pipeline/run"
            )
            http_span.set_attribute("trigger.project_id", project_id)
            http_span.set_attribute("trigger.session_id", session_id or "")
            http_span.set_attribute("trigger.project_path", str(project_path or ""))
            http_span.set_attribute("trigger.force", force)
            http_span.set_attribute("trigger.limit", limit or 0)
            http_span.set_attribute("trigger.concurrency", concurrency)
            http_span.set_attribute("trigger.steps", ",".join(steps) if steps else "all")
            http_span.set_attribute("trigger.model", model or "server-default")
            http_span.set_attribute("trigger.include_codex", include_codex)
            http_span.set_attribute("trigger.project_cwd", project_cwd or "")
            http_span.set_attribute("trigger.agent", agent)
            if project_path is not None:
                http_span.set_attribute("trigger.synced_project_path", str(project_path))

            payload = {
                "project_id": project_id,
                "force": force,
                "concurrency": concurrency,
                "include_codex": include_codex,
            }
            if session_id is not None:
                payload["session_id"] = session_id
            if steps is not None:
                payload["steps"] = steps
            if limit is not None:
                payload["limit"] = limit
            if model is not None:
                payload["model"] = model

            http_span.set_attribute("trigger.payload_json", json.dumps(payload))

            # Propagate trace context via W3C traceparent header
            extra_headers: dict[str, str] = {}
            try:
                from mega_code.client.utils.tracing import get_current_trace_context

                traceparent = get_current_trace_context()
                if traceparent:
                    extra_headers["traceparent"] = traceparent
                    http_span.set_attribute("trigger.traceparent", traceparent)
            except Exception:
                pass

            async_client = self._get_async_client()
            resp = await async_client.post(
                "/api/megacode/v1/pipeline/run", json=payload, headers=extra_headers
            )
            http_span.set_attribute("http.status_code", resp.status_code)
            self._check_response(resp)
            resp_data = resp.json()
            http_span.set_attribute("trigger.response_json", json.dumps(resp_data))
            result = TriggerPipelineResult(**resp_data)
            http_span.set_attribute("trigger.response.run_id", result.run_id)
            http_span.set_attribute("trigger.response.status", result.status)
            return result

    @traced("client.remote.get_pipeline_status")
    def get_pipeline_status(
        self,
        *,
        run_id: str,
    ) -> PipelineStatusResult:
        """Poll pipeline status via GET /api/megacode/v1/pipeline/status/{run_id}.

        Uses a one-shot httpx.get() (fresh TCP connection) instead of the
        persistent self._client to avoid stale connections during long polling.
        """
        from mega_code.client.utils.tracing import get_tracer

        tracer = get_tracer(__name__)
        with tracer.start_as_current_span("http.GET /pipeline/status") as http_span:
            base = str(self._client.base_url).rstrip("/")
            url = f"{base}/api/megacode/v1/pipeline/status/{run_id}"
            http_span.set_attribute("http.method", "GET")
            http_span.set_attribute("http.url", url)
            http_span.set_attribute("status_poll.run_id", run_id)

            resp = httpx.get(
                url,
                headers={**self._client.headers, "Cache-Control": "no-cache"},
                timeout=self._client.timeout,
            )
            http_span.set_attribute("http.status_code", resp.status_code)
            self._check_response(resp)
            data = resp.json()
            http_span.set_attribute("status_poll.response_json", json.dumps(data))

            http_span.set_attribute("status_poll.status", data.get("status", ""))
            http_span.set_attribute("status_poll.project_id", data.get("project_id", ""))
            http_span.set_attribute("status_poll.error", data.get("error", "") or "")
            if data.get("progress"):
                http_span.set_attribute(
                    "status_poll.phase", data["progress"].get("current_phase", "")
                )
                http_span.set_attribute(
                    "status_poll.sessions_processed", data["progress"].get("sessions_processed", 0)
                )
                http_span.set_attribute(
                    "status_poll.sessions_total", data["progress"].get("sessions_total", 0)
                )
            http_span.set_attribute("status_poll.has_outputs", data.get("outputs") is not None)

            # Parse outputs into OutputsResult if present
            outputs_raw = data.get("outputs")
            outputs = OutputsResult(**outputs_raw) if outputs_raw else None

            if outputs:
                http_span.set_attribute(
                    "status_poll.pending_skills_count", len(outputs.pending_skills or [])
                )
                http_span.set_attribute(
                    "status_poll.pending_strategies_count", len(outputs.pending_strategies or [])
                )
                http_span.set_attribute(
                    "status_poll.pending_lessons_count", len(outputs.pending_lessons or [])
                )

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
        payload = profile.model_dump(by_alias=True)
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

    @traced("client.remote.load_profile", kind="CLIENT", openinference_kind="TOOL")
    def load_profile(self) -> UserProfile:
        """Load user profile via GET /api/megacode/v1/profile."""
        resp = self._client.get("/api/megacode/v1/profile")
        self._check_response(resp)
        return UserProfile(**resp.json())

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
