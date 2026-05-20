"""Retry-semantics tests for ``GatewayClient.upload`` / ``get_job`` / ``get_result``.

Pins that transient 5xx (502/503/504/429) is retried via tenacity before
surfacing as ``NetworkError``. Earlier shape of these methods caught
``HTTPStatusError`` inside the function body and converted it to
``NetworkError`` before tenacity could see it — so retries were silently
disabled. These tests catch any regression of that bug.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch
from uuid import uuid4

import httpx
import pytest
from tenacity import wait_none

from mega_code.client.remote_enhance.client import (
    ApiError,
    GatewayClient,
    NetworkError,
)


@pytest.fixture(autouse=True)
def _api_key(monkeypatch):
    monkeypatch.setenv("MEGA_CODE_API_KEY", "test-key")
    monkeypatch.setenv("MEGA_CODE_SERVER_URL", "http://gateway.test")


@pytest.fixture(autouse=True)
def _no_retry_wait(monkeypatch):
    """Drop tenacity's exponential backoff in tests."""
    for method in (
        GatewayClient.upload,
        GatewayClient.get_job,
        GatewayClient.get_result,
    ):
        monkeypatch.setattr(method.retry, "wait", wait_none())


def _bad_5xx(status: int = 503) -> MagicMock:
    bad = MagicMock(spec=httpx.Response)
    bad.status_code = status
    bad.headers = {}
    bad.reason_phrase = "Service Unavailable"
    bad.text = "unavail"
    bad.raise_for_status.side_effect = httpx.HTTPStatusError(
        f"{status}", request=MagicMock(), response=bad
    )
    return bad


def _good_upload_body() -> dict:
    return {
        "job_id": str(uuid4()),
        "content_hash": "ch-deadbeef",
        "s3_uri": "s3://bucket/key",
        "file_count": 1,
        "uploaded_bytes": 100,
        "source_revision": "v-1",
    }


def _good_job_body() -> dict:
    return {"status": "running", "phase_public": "enhancing"}


def _good_result_body() -> dict:
    return {"status": "succeeded", "artifact_kind": "skill"}


def _good(body: dict) -> MagicMock:
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = 200
    resp.headers = {}
    resp.json.return_value = body
    resp.text = json.dumps(body)
    resp.reason_phrase = "OK"
    return resp


def _4xx_envelope(code: str = "invalid_skill_id", status: int = 400) -> MagicMock:
    body = {"error": {"code": code, "message": "bad"}}
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = status
    resp.headers = {}
    resp.json.return_value = body
    resp.text = json.dumps(body)
    resp.reason_phrase = "Bad Request"
    return resp


# ---------------------------------------------------------------------------
# upload — retry on 5xx
# ---------------------------------------------------------------------------


def test_upload_retries_on_transient_503():
    """Two 503s then 200 → succeeds; exactly 3 POSTs.

    This is the regression guard for the silently-broken retry path:
    before the fix, the inner ``try/except httpx.HTTPStatusError`` caught
    the 503 and converted to ``NetworkError`` before tenacity could see
    it — so call_count would be 1 and the method would raise.
    """
    good = _good(_good_upload_body())
    with (
        GatewayClient() as client,
        patch("httpx.Client.post", side_effect=[_bad_5xx(), _bad_5xx(), good]) as post,
    ):
        body = client.upload(archive_bytes=b"PK", source="test", skill_id="s")
    assert post.call_count == 3
    assert body["job_id"]


def test_upload_persistent_503_surfaces_as_network_error():
    """Full retry budget exhausted (5 attempts), then NetworkError."""
    with GatewayClient() as client, patch("httpx.Client.post", return_value=_bad_5xx()) as post:
        with pytest.raises(NetworkError):
            client.upload(archive_bytes=b"PK", source="test", skill_id="s")
    assert post.call_count == 5


def test_upload_no_retry_on_4xx():
    """4xx is not retryable — exactly one POST."""
    with (
        GatewayClient() as client,
        patch("httpx.Client.post", return_value=_4xx_envelope()) as post,
    ):
        with pytest.raises(ApiError) as exc:
            client.upload(archive_bytes=b"PK", source="test", skill_id="s")
    assert post.call_count == 1
    assert exc.value.code == "invalid_skill_id"


# ---------------------------------------------------------------------------
# get_job — same retry semantics
# ---------------------------------------------------------------------------


def test_get_job_retries_on_transient_503():
    job_id = uuid4()
    good = _good(_good_job_body())
    with (
        GatewayClient() as client,
        patch("httpx.Client.get", side_effect=[_bad_5xx(), good]) as g,
    ):
        body = client.get_job(job_id)
    assert g.call_count == 2
    assert body["status"] == "running"


def test_get_job_persistent_503_surfaces_as_network_error():
    job_id = uuid4()
    with GatewayClient() as client, patch("httpx.Client.get", return_value=_bad_5xx()) as g:
        with pytest.raises(NetworkError):
            client.get_job(job_id)
    assert g.call_count == 5


# ---------------------------------------------------------------------------
# get_result — same retry semantics
# ---------------------------------------------------------------------------


def test_get_result_retries_on_transient_503():
    job_id = uuid4()
    good = _good(_good_result_body())
    with (
        GatewayClient() as client,
        patch("httpx.Client.get", side_effect=[_bad_5xx(), good]) as g,
    ):
        body = client.get_result(job_id)
    assert g.call_count == 2
    assert body["status"] == "succeeded"


def test_get_result_persistent_503_surfaces_as_network_error():
    job_id = uuid4()
    with GatewayClient() as client, patch("httpx.Client.get", return_value=_bad_5xx()) as g:
        with pytest.raises(NetworkError):
            client.get_result(job_id)
    assert g.call_count == 5
