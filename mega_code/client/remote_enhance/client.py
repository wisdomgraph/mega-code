"""HTTP wrapper around the three skill-enhance gateway endpoints.

Sync client (no asyncio in the CLI flow). Reuses the same retry shape
``mega_code.client.api.remote.MegaCodeRemote`` uses (tenacity exponential
backoff on 429/502/503/504, ValueError on 401/403). Auth headers and
upstream URL are built from ``MEGA_CODE_SERVER_URL`` + ``MEGA_CODE_API_KEY``
in the env — same env-var convention as the existing remote client so
``MEGA_CODE_CLIENT_MODE=remote`` users don't need new config.

The three error classes below are what ``__main__.py`` catches to map
to exit codes 2/4/5.
"""

from __future__ import annotations

import functools
import logging
import os
from typing import Any
from uuid import UUID

import httpx
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential

from mega_code.client.utils.tracing import set_span_attributes, traced

logger = logging.getLogger(__name__)

_GATEWAY_PREFIX = "/api/megacode/v1/skill-enhance"
_RETRYABLE_STATUS = {429, 502, 503, 504}


def _coerce_job_id(job_id: UUID | str) -> UUID:
    """Validate ``job_id`` before it enters a URL path component.

    The upload response is a network trust boundary: the gateway returns
    ``body["job_id"]`` which the caller passes straight back into
    ``f"…/jobs/{job_id}"``. A malicious or compromised gateway returning
    e.g. ``"abc/../admin"`` or ``"abc?leak=1"`` would otherwise route the
    bearer-authenticated client at attacker-chosen endpoints
    (confused-deputy). Coerce to ``UUID`` and let ``ValueError`` short
    circuit before any network call.
    """
    if isinstance(job_id, UUID):
        return job_id
    return UUID(str(job_id))


class GatewayError(Exception):
    """Base class for gateway-side errors surfaced from the client."""


class AuthError(GatewayError):
    """401/403 from the gateway. Maps to exit code 5 in ``__main__``."""


class NetworkError(GatewayError):
    """Connect / timeout / 5xx-after-retries. Maps to exit code 5."""


class ApiError(GatewayError):
    """4xx pass-through with the canonical ``{"error": {...}}`` envelope.

    Carries ``status``, ``code`` (e.g. ``"duplicate_content_hash"``),
    ``message``, and the upstream ``details`` dict (or ``None``). The CLI
    dispatch table in ``__main__.py`` reads ``code`` to decide between
    exit 2 (``duplicate_content_hash``) and exit 4 (everything else).
    """

    def __init__(
        self,
        *,
        status: int,
        code: str,
        message: str,
        details: dict[str, Any] | None = None,
    ):
        super().__init__(f"{status} {code}: {message}")
        self.status = status
        self.code = code
        self.message = message
        self.details = details


def _translate_persistent_http_status(fn):
    """Convert a post-retry ``HTTPStatusError`` to ``NetworkError``.

    Apply *outside* the ``@retry`` decorator so tenacity sees the original
    ``HTTPStatusError`` (and can match it against ``_RETRYABLE_STATUS``). After
    the retry budget is exhausted, tenacity re-raises the ``HTTPStatusError``
    and this wrapper translates it to the public ``NetworkError`` contract
    callers expect.

    Earlier, the conversion lived inside the ``@retry``-wrapped function body,
    which silently disabled retries on 5xx because tenacity saw a
    ``NetworkError`` (not in ``_is_retryable``'s allowlist). Pinned by
    ``tests/client/test_remote_enhance_client.py``.
    """

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except httpx.HTTPStatusError as exc:
            raise NetworkError(
                f"persistent {exc.response.status_code} from gateway after retries"
            ) from exc

    return wrapper


def _is_retryable(exc: BaseException) -> bool:
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code in _RETRYABLE_STATUS
    return isinstance(exc, (httpx.NetworkError, httpx.TimeoutException))


def _raise_for_status(resp: httpx.Response) -> None:
    """Convert a 4xx/5xx response into a typed gateway error.

    4xx maps to ``ApiError`` with the upstream ``error.code`` parsed out.
    Skill-enhance-server's envelope is ``{"error": {"code", "message", "details"?}}``
    — verified in the implementation plan §"Resolved by the server team".
    The gateway forwards this byte-for-byte for 4xx (``_passthrough_or_502``
    in the router), so the same shape arrives here. 5xx doesn't reach this
    function — tenacity retries it; if it persists, ``HTTPStatusError`` bubbles
    up and the surrounding try/except converts to ``NetworkError``.
    """
    status = resp.status_code
    if status < 400:
        return
    if status in (401, 403):
        raise AuthError(f"authentication failed ({status} {resp.reason_phrase})")
    if 400 <= status < 500:
        try:
            body = resp.json()
        except ValueError:
            raise ApiError(
                status=status,
                code=f"http_{status}",
                message=resp.text or resp.reason_phrase,
                details=None,
            ) from None
        err = body.get("error") if isinstance(body, dict) else None
        if not isinstance(err, dict):
            raise ApiError(
                status=status,
                code=f"http_{status}",
                message=str(body),
                details=None,
            )
        raise ApiError(
            status=status,
            code=str(err.get("code", f"http_{status}")),
            message=str(err.get("message", "")),
            details=err.get("details"),
        )
    resp.raise_for_status()  # 5xx → tenacity catches HTTPStatusError, retries


class GatewayClient:
    """Sync HTTP client for the three skill-enhance gateway routes.

    Per-request retry: 429/502/503/504 → exponential backoff up to 5 attempts.
    401/403 → ``AuthError`` immediately (no retry — auth doesn't fix itself).
    Other 4xx → ``ApiError`` immediately. Network / persistent 5xx →
    ``NetworkError`` after the retry budget is exhausted.

    The gateway URL prefix is hard-coded — there's only one mega-code server,
    and the path is part of the public API. Server URL + API key come from
    env so the CLI can be driven from any working directory.
    """

    def __init__(
        self,
        *,
        server_url: str | None = None,
        api_key: str | None = None,
        timeout: float = 1200.0,
    ):
        self._server_url = (
            server_url or os.environ.get("MEGA_CODE_SERVER_URL", "http://localhost:8000")
        ).rstrip("/")
        self._api_key = api_key if api_key is not None else os.environ.get("MEGA_CODE_API_KEY", "")
        if not self._api_key:
            raise AuthError("MEGA_CODE_API_KEY not set — remote enhance requires an API key")
        self._timeout = timeout
        self._client = httpx.Client(
            base_url=self._server_url,
            headers={"Authorization": f"Bearer {self._api_key}"},
            timeout=timeout,
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> GatewayClient:
        return self

    def __exit__(self, *_exc) -> None:
        self.close()

    # ------------------------------------------------------------------
    # Endpoints
    # ------------------------------------------------------------------

    @traced("client.remote_enhance.upload", kind="CLIENT", openinference_kind="TOOL")
    @_translate_persistent_http_status
    @retry(
        retry=retry_if_exception(_is_retryable),
        wait=wait_exponential(multiplier=0.5, min=0.5, max=8.0),
        stop=stop_after_attempt(5),
        reraise=True,
    )
    def upload(self, *, archive_bytes: bytes, source: str, skill_id: str) -> dict[str, Any]:
        """``POST /skill-enhance/uploads``. Returns the ``UploadResponse`` body.

        The upstream ``UploadResponse`` carries ``{s3_uri, content_hash,
        file_count, uploaded_bytes, source_revision, job_id}`` — the caller
        reads ``job_id`` from the top level. The server's own
        ``content_hash`` is also returned for parity-checking against the
        packager's locally-computed hash.
        """
        set_span_attributes(skill_id=skill_id, source=source, archive_bytes=len(archive_bytes))
        try:
            resp = self._client.post(
                f"{_GATEWAY_PREFIX}/uploads",
                files={"archive": ("archive.zip", archive_bytes, "application/zip")},
                data={"source": source, "skill_id": skill_id},
            )
        except (httpx.NetworkError, httpx.TimeoutException) as exc:
            raise NetworkError(f"network failure during upload: {exc}") from exc
        # _raise_for_status raises HTTPStatusError for retryable 5xx — let
        # tenacity catch + retry it; the @_translate_persistent_http_status
        # wrapper converts the post-budget exception to NetworkError.
        _raise_for_status(resp)
        body = resp.json()
        set_span_attributes(job_id=body.get("job_id"), content_hash=body.get("content_hash"))
        return body

    @traced("client.remote_enhance.get_job", kind="CLIENT", openinference_kind="TOOL")
    @_translate_persistent_http_status
    @retry(
        retry=retry_if_exception(_is_retryable),
        wait=wait_exponential(multiplier=0.5, min=0.5, max=8.0),
        stop=stop_after_attempt(5),
        reraise=True,
    )
    def get_job(self, job_id: UUID | str) -> dict[str, Any]:
        """``GET /skill-enhance/jobs/{id}``. Returns the ``JobDetail`` body.

        Used by the poller — the caller treats ``status='queued'`` and
        ``running`` as non-terminal, everything else as terminal.
        """
        job_uuid = _coerce_job_id(job_id)
        set_span_attributes(job_id=str(job_uuid))
        try:
            resp = self._client.get(f"{_GATEWAY_PREFIX}/jobs/{job_uuid}")
        except (httpx.NetworkError, httpx.TimeoutException) as exc:
            raise NetworkError(f"network failure during get_job: {exc}") from exc
        _raise_for_status(resp)
        body = resp.json()
        set_span_attributes(status=body.get("status"), phase_public=body.get("phase_public"))
        return body

    @traced("client.remote_enhance.upload_prebuilt", kind="CLIENT", openinference_kind="TOOL")
    @_translate_persistent_http_status
    @retry(
        retry=retry_if_exception(_is_retryable),
        wait=wait_exponential(multiplier=0.5, min=0.5, max=8.0),
        stop=stop_after_attempt(5),
        reraise=True,
    )
    def upload_prebuilt(
        self,
        *,
        archive_bytes: bytes,
        skill_id: str,
        idempotency_key: str | None = None,
    ) -> tuple[dict[str, Any], bool]:
        """``POST /skill-enhance/uploads/prebuilt`` → ``(body, replayed)``.

        Retry behavior matches the other three endpoints: tenacity retries
        transient 5xx + 429 with exponential backoff; the
        ``_translate_persistent_http_status`` wrapper converts a post-budget
        ``HTTPStatusError`` to the public ``NetworkError`` contract.

        ``replayed`` is ``True`` iff the upstream returned the
        ``Idempotent-Replayed: true`` response header — surfaces the
        gateway's pass-through of that header from skill-enhance-server.
        """
        set_span_attributes(
            skill_id=skill_id,
            archive_bytes=len(archive_bytes),
            has_idempotency_key=idempotency_key is not None,
        )
        headers = {"Idempotency-Key": idempotency_key} if idempotency_key else {}
        try:
            resp = self._client.post(
                f"{_GATEWAY_PREFIX}/uploads/prebuilt",
                files={"archive": ("archive.zip", archive_bytes, "application/zip")},
                data={"skill_id": skill_id},
                headers=headers,
            )
        except (httpx.NetworkError, httpx.TimeoutException) as exc:
            raise NetworkError(f"network failure during prebuilt upload: {exc}") from exc
        # _raise_for_status raises HTTPStatusError for retryable 5xx — let
        # tenacity catch it. 401/403 → AuthError, 4xx → ApiError (both
        # non-retryable, propagate immediately).
        _raise_for_status(resp)
        body = resp.json()
        replayed = resp.headers.get("Idempotent-Replayed", "").lower() == "true"
        set_span_attributes(
            s3_prefix=body.get("s3_prefix"),
            content_hash=body.get("content_hash"),
            replayed=replayed,
        )
        return body, replayed

    @traced("client.remote_enhance.get_result", kind="CLIENT", openinference_kind="TOOL")
    @_translate_persistent_http_status
    @retry(
        retry=retry_if_exception(_is_retryable),
        wait=wait_exponential(multiplier=0.5, min=0.5, max=8.0),
        stop=stop_after_attempt(5),
        reraise=True,
    )
    def get_result(self, job_id: UUID | str) -> dict[str, Any]:
        """``GET /skill-enhance/jobs/{id}/result``. Returns the ``JobResult`` body.

        Called exactly once after the poller observes a terminal status.
        On non-terminal status the upstream returns 409 ``not_terminal``;
        the polling invariant (§8.11) guarantees the canonical client never
        trips this, so a 409 here surfaces as ``ApiError`` and maps to exit 5
        in ``__main__`` (defensive coverage only).
        """
        job_uuid = _coerce_job_id(job_id)
        set_span_attributes(job_id=str(job_uuid))
        try:
            resp = self._client.get(f"{_GATEWAY_PREFIX}/jobs/{job_uuid}/result")
        except (httpx.NetworkError, httpx.TimeoutException) as exc:
            raise NetworkError(f"network failure during get_result: {exc}") from exc
        _raise_for_status(resp)
        body = resp.json()
        set_span_attributes(status=body.get("status"), artifact_kind=body.get("artifact_kind"))
        return body
