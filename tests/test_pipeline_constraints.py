"""Tests for pipeline execution constraints.

Covers:
1. Server blocks concurrent pipelines for the same project (409 response)
2. Different projects can run pipelines concurrently
3. Force flag behaviour
4. Client handles pipeline-busy errors gracefully
"""

from unittest.mock import AsyncMock, patch

import httpx
import pytest

from mega_code.client.api.protocol import (
    PipelineStatusResult,
    TriggerPipelineResult,
)
from mega_code.client.api.remote import MegaCodeRemote

# ═══════════════════════════════════════════════════════════════════════════
# Unit tests: client handles pipeline-busy (409) responses
# ═══════════════════════════════════════════════════════════════════════════


class TestPipelineBusyResponse:
    """Remote client raises on 409 Conflict when a pipeline is already running."""

    @pytest.fixture
    def client(self):
        return MegaCodeRemote(
            server_url="https://test.megacode.ai",
            api_key="mg_test_key",
        )

    @pytest.mark.asyncio
    async def test_trigger_returns_409_when_project_busy(self, client):
        """Server returns 409 when another pipeline for the same project is running."""
        response_409 = httpx.Response(
            409,
            json={"detail": "Pipeline already running for project test-project"},
            request=httpx.Request("POST", "https://test.megacode.ai/api/megacode/v1/pipeline/run"),
        )

        with patch.object(client, "_get_async_client") as mock_ac:
            mock_async = AsyncMock()
            mock_async.post.return_value = response_409
            mock_ac.return_value = mock_async

            with pytest.raises(httpx.HTTPStatusError) as exc_info:
                await client.trigger_pipeline_run(
                    project_id="test-project",
                    force=False,
                )

            assert exc_info.value.response.status_code == 409

    @pytest.mark.asyncio
    async def test_trigger_succeeds_for_different_project(self, client):
        """Two different projects can trigger pipelines independently."""
        success_response = httpx.Response(
            200,
            json={"run_id": "run-abc", "status": "queued", "message": "ok"},
            request=httpx.Request("POST", "https://test.megacode.ai/api/megacode/v1/pipeline/run"),
        )

        with patch.object(client, "_get_async_client") as mock_ac:
            mock_async = AsyncMock()
            mock_async.post.return_value = success_response
            mock_ac.return_value = mock_async

            result_a = await client.trigger_pipeline_run(project_id="project-a")
            result_b = await client.trigger_pipeline_run(project_id="project-b")

            assert result_a.run_id == "run-abc"
            assert result_b.run_id == "run-abc"
            assert mock_async.post.call_count == 2

            # Verify each call used the correct project_id
            calls = mock_async.post.call_args_list
            assert calls[0].kwargs["json"]["project_id"] == "project-a"
            assert calls[1].kwargs["json"]["project_id"] == "project-b"

    @pytest.mark.asyncio
    async def test_force_flag_sent_in_payload(self, client):
        """The force flag is passed through to the server payload."""
        success_response = httpx.Response(
            200,
            json={"run_id": "run-force", "status": "queued", "message": "forced"},
            request=httpx.Request("POST", "https://test.megacode.ai/api/megacode/v1/pipeline/run"),
        )

        with patch.object(client, "_get_async_client") as mock_ac:
            mock_async = AsyncMock()
            mock_async.post.return_value = success_response
            mock_ac.return_value = mock_async

            await client.trigger_pipeline_run(
                project_id="test-project",
                force=True,
            )

            payload = mock_async.post.call_args.kwargs["json"]
            assert payload["force"] is True

    @pytest.mark.asyncio
    async def test_trigger_sends_include_flags(self, client):
        """Include flags (claude/codex) are sent in the pipeline trigger payload."""
        success_response = httpx.Response(
            200,
            json={"run_id": "run-flags", "status": "queued", "message": "ok"},
            request=httpx.Request("POST", "https://test.megacode.ai/api/megacode/v1/pipeline/run"),
        )

        with patch.object(client, "_get_async_client") as mock_ac:
            mock_async = AsyncMock()
            mock_async.post.return_value = success_response
            mock_ac.return_value = mock_async

            await client.trigger_pipeline_run(
                project_id="test-project",
                include_claude=True,
                include_codex=True,
            )

            payload = mock_async.post.call_args.kwargs["json"]
            assert payload["include_claude"] is True
            assert payload["include_codex"] is True


# ═══════════════════════════════════════════════════════════════════════════
# Unit tests: pipeline status polling
# ═══════════════════════════════════════════════════════════════════════════


class TestPipelineStatusPolling:
    """Pipeline status reflects running/completed/failed states."""

    @pytest.fixture
    def client(self):
        return MegaCodeRemote(
            server_url="https://test.megacode.ai",
            api_key="mg_test_key",
        )

    def test_status_returns_running(self, client):
        """Pipeline status shows 'running' while in progress."""
        with patch.object(client._client, "get") as mock_get:
            mock_get.return_value = httpx.Response(
                200,
                json={
                    "run_id": "run-1",
                    "project_id": "proj-a",
                    "status": "running",
                    "started_at": "2026-03-11T10:00:00Z",
                },
                request=httpx.Request(
                    "GET",
                    "https://test.megacode.ai/api/megacode/v1/pipeline/status/run-1",
                ),
            )

            status = client.get_pipeline_status(run_id="run-1")
            assert status.status == "running"
            assert status.project_id == "proj-a"

    def test_status_returns_failed_with_error(self, client):
        """Failed pipeline status includes error message."""
        with patch.object(client._client, "get") as mock_get:
            mock_get.return_value = httpx.Response(
                200,
                json={
                    "run_id": "run-2",
                    "project_id": "proj-b",
                    "status": "failed",
                    "error": "Pipeline already running for this project",
                },
                request=httpx.Request(
                    "GET",
                    "https://test.megacode.ai/api/megacode/v1/pipeline/status/run-2",
                ),
            )

            status = client.get_pipeline_status(run_id="run-2")
            assert status.status == "failed"
            assert status.error is not None
            assert "already running" in status.error


# ═══════════════════════════════════════════════════════════════════════════
# Integration tests: pipeline trigger payload construction
# ═══════════════════════════════════════════════════════════════════════════


class TestTriggerPayloadConstruction:
    """Verify trigger_pipeline_run constructs correct HTTP payloads."""

    @pytest.fixture
    def client(self):
        return MegaCodeRemote(
            server_url="https://test.megacode.ai",
            api_key="mg_test_key",
        )

    @pytest.mark.asyncio
    async def test_minimal_payload(self, client):
        """Minimal trigger sends project_id, force, concurrency, include flags."""
        success = httpx.Response(
            200,
            json={"run_id": "r1", "status": "queued", "message": ""},
            request=httpx.Request("POST", "https://test.megacode.ai/api/megacode/v1/pipeline/run"),
        )

        with patch.object(client, "_get_async_client") as mock_ac:
            mock_async = AsyncMock()
            mock_async.post.return_value = success
            mock_ac.return_value = mock_async

            await client.trigger_pipeline_run(project_id="my-proj")

            payload = mock_async.post.call_args.kwargs["json"]
            assert payload == {
                "project_id": "my-proj",
                "force": False,
                "concurrency": 64,
                "include_claude": False,
                "include_codex": False,
            }

    @pytest.mark.asyncio
    async def test_full_payload_with_optional_fields(self, client):
        """All optional fields are included in payload when specified."""
        success = httpx.Response(
            200,
            json={"run_id": "r2", "status": "queued", "message": ""},
            request=httpx.Request("POST", "https://test.megacode.ai/api/megacode/v1/pipeline/run"),
        )

        with patch.object(client, "_get_async_client") as mock_ac:
            mock_async = AsyncMock()
            mock_async.post.return_value = success
            mock_ac.return_value = mock_async

            await client.trigger_pipeline_run(
                project_id="my-proj",
                steps=["step0", "step1"],
                force=True,
                limit=5,
                concurrency=8,
                model="gpt-5-mini",
                include_claude=True,
                include_codex=True,
            )

            payload = mock_async.post.call_args.kwargs["json"]
            assert payload["steps"] == ["step0", "step1"]
            assert payload["force"] is True
            assert payload["limit"] == 5
            assert payload["concurrency"] == 8
            assert payload["model"] == "gpt-5-mini"
            assert payload["include_claude"] is True
            assert payload["include_codex"] is True

    @pytest.mark.asyncio
    async def test_auth_error_raises_descriptive_message(self, client):
        """401/403 raises ValueError with helpful re-auth instructions."""
        resp_401 = httpx.Response(
            401,
            json={"detail": "Invalid API key"},
            request=httpx.Request("POST", "https://test.megacode.ai/api/megacode/v1/pipeline/run"),
        )

        with patch.object(client, "_get_async_client") as mock_ac:
            mock_async = AsyncMock()
            mock_async.post.return_value = resp_401
            mock_ac.return_value = mock_async

            with pytest.raises(ValueError, match="Authentication failed"):
                await client.trigger_pipeline_run(project_id="test")


# ═══════════════════════════════════════════════════════════════════════════
# Acceptance tests (manual verification guide)
# ═══════════════════════════════════════════════════════════════════════════


class TestPipelineConstraintsAcceptance:
    """Acceptance test stubs documenting manual verification procedures.

    These tests verify the documented constraints by checking that the
    client code correctly handles the server's concurrency enforcement.

    Manual verification steps:
    1. Start a pipeline: /mega-code:run --project @my-project
    2. While running, try again: /mega-code:run --project @my-project
       → Expected: server returns 409, client shows "pipeline already running"
    3. While running, try different project: /mega-code:run --project @other-project
       → Expected: succeeds, runs independently
    4. Use --force flag: /mega-code:run --project @my-project --force
       → Expected: server decides whether to allow override
    """

    def test_trigger_result_model_has_status_field(self):
        """TriggerPipelineResult includes status for queue/rejection feedback."""
        result = TriggerPipelineResult(run_id="r1", status="queued", message="ok")
        assert result.status == "queued"

    def test_pipeline_status_model_has_error_field(self):
        """PipelineStatusResult includes error field for constraint messages."""
        result = PipelineStatusResult(
            run_id="r1",
            project_id="p1",
            status="failed",
            error="Pipeline already running for project p1",
        )
        assert result.error is not None
        assert "already running" in result.error

    def test_force_flag_in_protocol(self):
        """The protocol defines force parameter for overriding constraints."""
        import inspect

        sig = inspect.signature(MegaCodeRemote.trigger_pipeline_run)
        assert "force" in sig.parameters
        assert sig.parameters["force"].default is False
