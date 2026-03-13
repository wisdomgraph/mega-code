"""Remote client implementation.

MegaCodeRemote connects to the FastAPI server via HTTP.
"""

from __future__ import annotations

import asyncio
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
        include_claude: bool = False,
        include_codex: bool = False,
        project_cwd: str | None = None,
    ) -> TriggerPipelineResult:
        """Trigger pipeline run via POST /api/megacode/v1/pipeline/run.

        If project_path is given, syncs local trajectories to the server
        first via sync_trajectories(), then triggers the pipeline.

        Args:
            project_cwd: The actual working directory (e.g. /tmp/test-project).
                Used for Codex session path matching. Falls back to
                project_path if not provided.
        """
        # Sync local sessions to server if project_path provided.
        # sync_trajectories is sync (uses self._client internally),
        # so offload to a thread to avoid blocking the event loop.
        if project_path is not None:
            from mega_code.client.api.sync import sync_trajectories

            await asyncio.to_thread(sync_trajectories, project_path, self, project_id)

        if project_path is not None and include_claude:
            from mega_code.client.api.sync import sync_claude_trajectories

            await asyncio.to_thread(sync_claude_trajectories, project_path, self, project_id)

        if include_codex and project_path is not None:
            from mega_code.client.api.codex_sync import sync_codex_trajectories

            # Use the actual project CWD for codex session matching, not the
            # mega-code data dir. The codex sessions record the real working
            # directory in their 'cwd' field.
            codex_match_path = project_cwd or str(project_path)
            await asyncio.to_thread(
                sync_codex_trajectories, project_path, self, project_id, codex_match_path
            )

        payload = {
            "project_id": project_id,
            "force": force,
            "concurrency": concurrency,
            "include_claude": include_claude,
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
        """Poll pipeline status via GET /api/megacode/v1/pipeline/status/{run_id}.

        Uses a one-shot httpx.get() (fresh TCP connection) instead of the
        persistent self._client to avoid stale connections during long polling.
        """
        base = str(self._client.base_url).rstrip("/")
        url = f"{base}/api/megacode/v1/pipeline/status/{run_id}"
        resp = httpx.get(
            url,
            headers={**self._client.headers, "Cache-Control": "no-cache"},
            timeout=self._client.timeout,
        )
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
