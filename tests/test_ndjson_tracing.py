"""Tests for NDJSON-based tracing module."""

from __future__ import annotations

import json
import os
from unittest.mock import MagicMock, patch

import pytest

import mega_code.client.utils.ndjson_tracing as _ndjson_mod
from mega_code.client.utils.ndjson_tracing import (
    SPAN_KIND_CLIENT,
    STATUS_ERROR,
    STATUS_OK,
    NdjsonSpan,
    NdjsonSpanWriter,
    NdjsonTracer,
    _build_otlp_envelope,
    _make_trace_id,
    _read_spans_from_file,
    export_traces,
    flush_traces,
    format_traceparent,
)


@pytest.fixture(autouse=True)
def _clean_global_span_stack():
    """Ensure the global span stack is empty before/after each test."""
    _ndjson_mod._global_span_stack.clear()
    yield
    _ndjson_mod._global_span_stack.clear()


@pytest.fixture
def trace_dir(tmp_path):
    """Create a temporary trace directory."""
    d = tmp_path / "trace"
    d.mkdir()
    return d


@pytest.fixture
def writer(trace_dir):
    """Create a span writer."""
    return NdjsonSpanWriter(trace_dir, "a" * 32)


# ---- Trace ID generation ----


class TestTraceId:
    def test_deterministic_with_session_id(self):
        """TraceId is deterministic (md5) when session_id is provided."""
        tid1 = _make_trace_id("my-session-123")
        tid2 = _make_trace_id("my-session-123")
        assert tid1 == tid2
        assert len(tid1) == 32

    def test_different_session_ids_produce_different_trace_ids(self):
        tid1 = _make_trace_id("session-a")
        tid2 = _make_trace_id("session-b")
        assert tid1 != tid2

    def test_random_without_session_id(self):
        """TraceId is random uuid4 when no session_id."""
        tid1 = _make_trace_id(None)
        tid2 = _make_trace_id(None)
        assert tid1 != tid2
        assert len(tid1) == 32


# ---- Span Writer ----


class TestNdjsonSpanWriter:
    def test_write_span_creates_file(self, writer, trace_dir):
        """Writing a span creates the NDJSON file."""
        writer.write_span({"spanId": "abc", "name": "test"})
        assert writer.file_path.exists()

    def test_write_span_appends_json_line(self, writer):
        """Each write appends a JSON line."""
        writer.write_span({"spanId": "s1", "name": "first"})
        writer.write_span({"spanId": "s2", "name": "second"})
        lines = writer.file_path.read_text().strip().split("\n")
        assert len(lines) == 2
        assert json.loads(lines[0])["spanId"] == "s1"
        assert json.loads(lines[1])["spanId"] == "s2"

    def test_write_never_raises(self, tmp_path):
        """Writing to an invalid path doesn't raise."""
        w = NdjsonSpanWriter(tmp_path / "nonexistent" / "deep", "tid")
        # This should not raise even if directory creation fails
        w.write_span({"spanId": "x"})


# ---- NdjsonSpan ----


class TestNdjsonSpan:
    def test_crash_safe_two_lines(self, writer):
        """A completed span writes two lines (open + close)."""
        span = NdjsonSpan(writer, "test-span", "t" * 32, "s" * 16)
        with span:
            span.set_attribute("key", "value")
        lines = writer.file_path.read_text().strip().split("\n")
        assert len(lines) == 2

        # First line: endTime == startTime (crash-safe marker)
        first = json.loads(lines[0])
        assert first["startTimeUnixNano"] == first["endTimeUnixNano"]

        # Second line: endTime > startTime, has attributes
        second = json.loads(lines[1])
        assert int(second["endTimeUnixNano"]) >= int(second["startTimeUnixNano"])
        assert any(a["key"] == "key" for a in second["attributes"])

    def test_status_ok_on_success(self, writer):
        """Status is OK when no exception occurs."""
        span = NdjsonSpan(writer, "ok-span", "t" * 32, "s" * 16)
        with span:
            pass
        lines = writer.file_path.read_text().strip().split("\n")
        final = json.loads(lines[-1])
        assert final["status"]["code"] == STATUS_OK

    def test_status_error_on_exception(self, writer):
        """Status is ERROR when exception is raised."""
        span = NdjsonSpan(writer, "err-span", "t" * 32, "s" * 16)
        with pytest.raises(ValueError):
            with span:
                raise ValueError("boom")
        lines = writer.file_path.read_text().strip().split("\n")
        final = json.loads(lines[-1])
        assert final["status"]["code"] == STATUS_ERROR
        assert len(final["events"]) == 1
        assert final["events"][0]["name"] == "exception"

    def test_record_exception_adds_event(self, writer):
        """record_exception adds an exception event."""
        span = NdjsonSpan(writer, "span", "t" * 32, "s" * 16)
        with span:
            span.record_exception(RuntimeError("oops"))
        lines = writer.file_path.read_text().strip().split("\n")
        final = json.loads(lines[-1])
        assert final["status"]["code"] == STATUS_ERROR
        event_attrs = {
            a["key"]: a["value"]["stringValue"] for a in final["events"][0]["attributes"]
        }
        assert event_attrs["exception.type"] == "RuntimeError"
        assert event_attrs["exception.message"] == "oops"

    def test_set_attribute_types(self, writer):
        """Attributes support string, int, float, bool."""
        span = NdjsonSpan(writer, "span", "t" * 32, "s" * 16)
        with span:
            span.set_attribute("str_key", "hello")
            span.set_attribute("int_key", 42)
            span.set_attribute("float_key", 3.14)
            span.set_attribute("bool_key", True)

        lines = writer.file_path.read_text().strip().split("\n")
        final = json.loads(lines[-1])
        attrs = {a["key"]: a["value"] for a in final["attributes"]}
        assert attrs["str_key"] == {"stringValue": "hello"}
        assert attrs["int_key"] == {"intValue": "42"}
        assert attrs["float_key"] == {"doubleValue": 3.14}
        assert attrs["bool_key"] == {"boolValue": True}

    def test_span_fields(self, writer):
        """Span has all required OTLP fields."""
        span = NdjsonSpan(
            writer,
            "my-span",
            "abcd" * 8,
            "1234" * 4,
            parent_span_id="parent123",
            kind=SPAN_KIND_CLIENT,
        )
        with span:
            pass
        lines = writer.file_path.read_text().strip().split("\n")
        final = json.loads(lines[-1])
        assert final["traceId"] == "abcd" * 8
        assert final["spanId"] == "1234" * 4
        assert final["parentSpanId"] == "parent123"
        assert final["name"] == "my-span"
        assert final["kind"] == SPAN_KIND_CLIENT


# ---- NdjsonTracer ----


class TestNdjsonTracer:
    def test_start_as_current_span(self, writer):
        """start_as_current_span creates a span and yields it."""
        tracer = NdjsonTracer(writer, "t" * 32)
        with tracer.start_as_current_span("my-op") as span:
            assert span.span_id is not None
            assert span.trace_id == "t" * 32

    def test_nested_spans_have_parent(self, writer):
        """Nested spans get the parent's span ID."""
        tracer = NdjsonTracer(writer, "t" * 32)
        with tracer.start_as_current_span("parent") as parent_span:
            with tracer.start_as_current_span("child"):
                pass

        lines = writer.file_path.read_text().strip().split("\n")
        spans_by_name = {}
        for line in lines:
            s = json.loads(line)
            # Keep last occurrence (dedup behavior)
            spans_by_name[s["name"]] = s

        assert spans_by_name["child"]["parentSpanId"] == parent_span.span_id
        assert spans_by_name["parent"]["parentSpanId"] == ""

    def test_context_stack_pops_on_exit(self, writer):
        """After a span exits, the stack returns to previous state."""
        tracer = NdjsonTracer(writer, "t" * 32)
        assert tracer.current_span_id is None

        with tracer.start_as_current_span("outer") as outer:
            assert tracer.current_span_id == outer.span_id
            with tracer.start_as_current_span("inner") as inner:
                assert tracer.current_span_id == inner.span_id
            assert tracer.current_span_id == outer.span_id

        assert tracer.current_span_id is None

    def test_cross_tracer_parent_child(self, writer):
        """Spans from different tracer instances share the global context stack."""
        tracer_a = NdjsonTracer(writer, "t" * 32, name="module_a")
        tracer_b = NdjsonTracer(writer, "t" * 32, name="module_b")

        with tracer_a.start_as_current_span("parent_from_a") as parent_span:
            with tracer_b.start_as_current_span("child_from_b"):
                pass

        # Parse spans from file
        lines = writer.file_path.read_text().strip().split("\n")
        spans_by_name = {}
        for line in lines:
            s = json.loads(line)
            spans_by_name[s["name"]] = s  # last-wins dedup

        # Child from tracer_b should have parent from tracer_a
        assert spans_by_name["child_from_b"]["parentSpanId"] == parent_span.span_id
        assert spans_by_name["parent_from_a"]["parentSpanId"] == ""


# ---- Deduplication ----


class TestDedup:
    def test_read_spans_dedup_last_wins(self, trace_dir):
        """Dedup keeps the last occurrence of each spanId."""
        f = trace_dir / "test.ndjson"
        # Write open line (endTime == startTime)
        open_span = {"spanId": "s1", "name": "op", "endTimeUnixNano": "100", "status": {"code": 0}}
        # Write close line (endTime > startTime)
        close_span = {"spanId": "s1", "name": "op", "endTimeUnixNano": "200", "status": {"code": 1}}
        f.write_text(json.dumps(open_span) + "\n" + json.dumps(close_span) + "\n")

        spans, offset = _read_spans_from_file(f)
        assert len(spans) == 1
        assert spans[0]["endTimeUnixNano"] == "200"
        assert spans[0]["status"]["code"] == 1


# ---- OTLP Envelope ----


class TestOtlpEnvelope:
    def test_envelope_structure(self):
        """OTLP envelope has correct structure."""
        spans = [{"spanId": "s1", "name": "test"}]
        env = _build_otlp_envelope(spans, "my-service", "1.0.0")

        assert "resourceSpans" in env
        rs = env["resourceSpans"][0]

        # Resource attributes
        attrs = {a["key"]: a["value"] for a in rs["resource"]["attributes"]}
        assert attrs["service.name"] == {"stringValue": "my-service"}
        assert attrs["service.version"] == {"stringValue": "1.0.0"}

        # Scope spans
        ss = rs["scopeSpans"][0]
        assert ss["scope"]["name"] == "mega-code"
        assert ss["spans"] == spans

    def test_envelope_without_version(self):
        """Envelope omits service.version when empty."""
        env = _build_otlp_envelope([], "svc", "")
        attrs = {a["key"] for a in env["resourceSpans"][0]["resource"]["attributes"]}
        assert "service.name" in attrs
        assert "service.version" not in attrs


# ---- Traceparent ----


class TestTraceparent:
    def test_format(self):
        assert format_traceparent("a" * 32, "b" * 16) == f"00-{'a' * 32}-{'b' * 16}-01"


# ---- Export ----


class TestExport:
    def test_export_no_files_returns_true(self, trace_dir):
        """Export with no trace files succeeds."""
        assert export_traces(trace_dir) is True

    def test_export_nonexistent_dir_returns_true(self, tmp_path):
        """Export with nonexistent dir succeeds."""
        assert export_traces(tmp_path / "nope") is True

    def test_export_sends_post(self, writer):
        """Export reads spans and POSTs to endpoint."""
        # Write a completed span
        span = NdjsonSpan(writer, "test", writer.trace_id, "span123")
        with span:
            span.set_attribute("test", "yes")

        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()

        with (
            patch("httpx.post", return_value=mock_resp) as mock_post,
            patch.dict(
                os.environ,
                {
                    "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT": "https://test.example.com/v1/traces",
                    "OTEL_EXPORTER_OTLP_HEADERS": "x-honeycomb-team=test123",
                },
            ),
        ):
            result = export_traces(writer.file_path.parent, writer=writer)

        assert result is True
        mock_post.assert_called_once()
        call_kwargs = mock_post.call_args

        # Verify endpoint
        assert call_kwargs.args[0] == "https://test.example.com/v1/traces"

        # Verify OTLP envelope structure in POST body
        posted_json = call_kwargs.kwargs["json"]
        assert "resourceSpans" in posted_json

        # Verify headers include honeycomb team
        posted_headers = call_kwargs.kwargs["headers"]
        assert posted_headers["x-honeycomb-team"] == "test123"

    def test_export_cleans_up_file(self, writer):
        """Successful export removes the NDJSON file."""
        span = NdjsonSpan(writer, "test", writer.trace_id, "s1")
        with span:
            pass

        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()

        with (
            patch("httpx.post", return_value=mock_resp),
            patch.dict(
                os.environ,
                {
                    "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT": "https://test.example.com/v1/traces",
                },
            ),
        ):
            export_traces(writer.file_path.parent, writer=writer)

        assert not writer.file_path.exists()

    def test_flush_does_not_delete_file(self, writer):
        """Flush exports but keeps the NDJSON file."""
        span = NdjsonSpan(writer, "test", writer.trace_id, "s1")
        with span:
            pass

        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()

        with (
            patch("httpx.post", return_value=mock_resp),
            patch.dict(
                os.environ,
                {
                    "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT": "https://test.example.com/v1/traces",
                },
            ),
        ):
            result = flush_traces(writer=writer)

        assert result is True
        # File should still exist after flush (unlike export)
        assert writer.file_path.exists()


# ---- API Compat (tracing.py integration) ----


class TestTracingModuleIntegration:
    """Test that the tracing.py module works with NDJSON backend."""

    def test_setup_tracing_disabled_by_default(self):
        """Tracing is disabled when MEGA_CODE_TRACING is not set."""
        import mega_code.client.utils.tracing as mod

        # Reset module state
        mod._initialized = False
        mod._tracing_enabled = False
        mod._writer = None
        mod._tracer_cache.clear()

        with patch.dict(os.environ, {}, clear=True):
            result = mod.setup_tracing()
        assert result is False

        # Reset again for other tests
        mod._initialized = False

    def test_setup_tracing_enabled(self, tmp_path):
        """Tracing is enabled when MEGA_CODE_TRACING=true."""
        import mega_code.client.utils.tracing as mod

        mod._initialized = False
        mod._tracing_enabled = False
        mod._writer = None
        mod._tracer_cache.clear()

        with patch.dict(
            os.environ,
            {
                "MEGA_CODE_TRACING": "true",
                "MEGA_CODE_DATA_DIR": str(tmp_path),
            },
        ):
            result = mod.setup_tracing(session_id="test-session")
        assert result is True
        assert mod._writer is not None
        assert (tmp_path / "trace").exists()

        # Reset
        mod._initialized = False
        mod._tracing_enabled = False
        mod._writer = None
        mod._tracer_cache.clear()

    def test_traced_decorator_noop_when_disabled(self):
        """@traced decorator is transparent when tracing disabled."""
        import mega_code.client.utils.tracing as mod

        mod._initialized = False
        mod._tracing_enabled = False
        mod._writer = None
        mod._tracer_cache.clear()

        with patch.dict(os.environ, {}, clear=True):
            mod.setup_tracing()

        @mod.traced
        def my_func(x):
            return x + 1

        assert my_func(5) == 6

        @mod.traced("custom-name")
        def my_func2(x):
            return x * 2

        assert my_func2(3) == 6

        # Reset
        mod._initialized = False

    def test_get_current_trace_context_none_when_disabled(self):
        """get_current_trace_context returns None when disabled."""
        import mega_code.client.utils.tracing as mod

        mod._initialized = True
        mod._tracing_enabled = False
        assert mod.get_current_trace_context() is None

        # Reset
        mod._initialized = False
