"""Tracing. Uses NDJSON file-based tracing when enabled, no-op otherwise.

Client-side tracing module. Opt-in via MEGA_CODE_TRACING env var.

When MEGA_CODE_TRACING=true/1/yes:
    → NDJSON spans written to ~/.local/share/mega-code/trace/
    → Exported to OTLP endpoint via HTTP/JSON

When not set (default):
    → all functions degrade to no-ops transparently.
"""

from __future__ import annotations

import contextlib
import logging
import os
from typing import Any

logger = logging.getLogger(__name__)

_initialized = False
_tracing_enabled = False
_writer: Any = None  # NdjsonSpanWriter | None
_tracer_cache: dict[str, Any] = {}
_trace_id: str | None = None
_service_name: str = "mega-code-client"


def _is_tracing_enabled() -> bool:
    """Check if tracing is enabled via env var."""
    val = os.environ.get("MEGA_CODE_TRACING", "").lower()
    return val in ("true", "1", "yes")


# ---------------------------------------------------------------------------
# No-op stubs (default when tracing is disabled)
# ---------------------------------------------------------------------------


class _NoOpSpan:
    """No-op span for when tracing is not enabled."""

    def set_attribute(self, _key: str, _value: Any) -> None:
        pass

    def record_exception(self, _exception: BaseException) -> None:
        pass

    def set_status(self, *_args: Any, **_kwargs: Any) -> None:
        pass

    def get_span_context(self) -> None:
        return None

    def __enter__(self) -> _NoOpSpan:
        return self

    def __exit__(self, *_args: Any) -> None:
        pass


class _NoOpTracer:
    """No-op tracer for when tracing is not enabled."""

    def start_as_current_span(self, _name: str, **_kw: Any) -> contextlib.AbstractContextManager:
        return contextlib.nullcontext(_NoOpSpan())

    def start_span(self, _name: str, **_kw: Any) -> _NoOpSpan:
        return _NoOpSpan()

    @property
    def current_span_id(self) -> None:
        return None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def setup_tracing(service_name: str = "mega-code-client", session_id: str | None = None) -> bool:
    """Initialize tracing (call once at startup).

    Tracing is opt-in via MEGA_CODE_TRACING env var. When enabled, creates an
    NDJSON span writer in ~/.local/share/mega-code/trace/.

    Args:
        service_name: Service name for OTLP resource attributes.
        session_id: If provided, trace ID is deterministic (md5 of session_id).

    Returns:
        True if tracing was set up, False otherwise.
    """
    global _initialized, _tracing_enabled, _writer, _trace_id, _service_name

    if _initialized:
        return _tracing_enabled

    _initialized = True
    _service_name = service_name

    if not _is_tracing_enabled():
        _tracing_enabled = False
        return False

    try:
        from mega_code.client.dirs import data_dir
        from mega_code.client.utils.ndjson_tracing import NdjsonSpanWriter, _make_trace_id

        _trace_id = _make_trace_id(session_id)
        trace_dir = data_dir() / "trace"
        _writer = NdjsonSpanWriter(trace_dir, _trace_id)
        _tracing_enabled = True
        logger.info(
            "NDJSON tracing initialized: trace_id=%s, file=%s", _trace_id, _writer.file_path
        )
        return True
    except Exception:
        logger.debug("Failed to initialize tracing", exc_info=True)
        _tracing_enabled = False
        return False


def get_tracer(name: str):
    """Get a tracer by name.

    Returns an NdjsonTracer if tracing is enabled, _NoOpTracer otherwise.
    """
    if name in _tracer_cache:
        return _tracer_cache[name]

    if _tracing_enabled and _writer is not None and _trace_id is not None:
        from mega_code.client.utils.ndjson_tracing import NdjsonTracer

        tracer = NdjsonTracer(_writer, _trace_id, name=name)
    else:
        tracer = _NoOpTracer()

    _tracer_cache[name] = tracer
    return tracer


def traced(
    name_or_fn=None,
    *,
    kind: str = "INTERNAL",
    openinference_kind: str | None = None,
):
    """Decorator: create a span around a function.

    Supports both @traced and @traced("name") usage.

    Args:
        name_or_fn: Span name (str) or the function itself (when used without args).
        kind: Span kind name (INTERNAL, CLIENT, SERVER, etc.).
        openinference_kind: OpenInference span kind (kept for API compat with callers).
    """
    del openinference_kind  # unused, kept for call-site compatibility
    import asyncio
    import functools

    from mega_code.client.utils.ndjson_tracing import _SPAN_KIND_MAP

    def decorator(fn):
        span_name = name_or_fn if isinstance(name_or_fn, str) else fn.__qualname__
        span_kind = _SPAN_KIND_MAP.get(kind, 1)

        @functools.wraps(fn)
        async def async_wrapper(*a, **kw):
            tracer = get_tracer(fn.__module__)
            with tracer.start_as_current_span(span_name, kind=span_kind):
                return await fn(*a, **kw)

        @functools.wraps(fn)
        def sync_wrapper(*a, **kw):
            tracer = get_tracer(fn.__module__)
            with tracer.start_as_current_span(span_name, kind=span_kind):
                return fn(*a, **kw)

        if asyncio.iscoroutinefunction(fn):
            return async_wrapper
        return sync_wrapper

    if callable(name_or_fn):
        return decorator(name_or_fn)
    return decorator


def get_current_trace_context() -> str | None:
    """Return W3C traceparent string for the current span, or None.

    Format: "00-<traceId>-<spanId>-01"
    """
    if not _tracing_enabled or _trace_id is None:
        return None

    try:
        from mega_code.client.utils.ndjson_tracing import format_traceparent

        # Find any active tracer with a current span
        for tracer in _tracer_cache.values():
            span_id = getattr(tracer, "current_span_id", None)
            if span_id:
                return format_traceparent(_trace_id, span_id)
    except Exception:
        pass
    return None


def get_span_writer():
    """Return the current NdjsonSpanWriter, or None if tracing is disabled."""
    return _writer


def has_opentelemetry() -> bool:
    """Check whether tracing is available.

    Returns True when NDJSON tracing is enabled.
    """
    return _tracing_enabled
