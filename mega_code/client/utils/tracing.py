"""Tracing. Uses OpenTelemetry when installed, no-op otherwise.

Client-side tracing module. No edition concept — simply auto-detects
whether opentelemetry packages are available at import time.

When installed:
    pip install mega-code[telemetry]
    → opentelemetry-api, opentelemetry-sdk, opentelemetry-exporter-otlp-proto-grpc

When NOT installed:
    → all functions degrade to no-ops transparently.
"""

from __future__ import annotations

import contextlib
import importlib.util
import logging

logger = logging.getLogger(__name__)


def _parse_otlp_headers(headers_str: str) -> tuple[tuple[str, str], ...]:
    """Parse OTEL_EXPORTER_OTLP_HEADERS env var into tuple of (key, value) pairs.

    Format: "key1=value1,key2=value2"
    """
    if not headers_str:
        return ()
    return tuple(
        tuple(h.strip().split("=", 1))  # type: ignore[misc]
        for h in headers_str.split(",")
        if "=" in h
    )


_client_initialized = False

_HAS_OTEL = importlib.util.find_spec("opentelemetry") is not None

if _HAS_OTEL:
    import json as _json

    from opentelemetry import trace as _trace

    def get_tracer(name: str):
        """Get a tracer by name."""
        return _trace.get_tracer(name)

    def set_span_attributes(**attrs) -> None:
        """Attach key/value attributes to the **current** span.

        Mirrors ``MegaCodeRemote._set_current_span_attrs`` (used in
        ``mega_code/client/api/remote.py``) — same coercion rules so a span
        viewer like Phoenix gets uniform attribute shapes across both
        clients. Non-scalar values are JSON-encoded so dicts / lists are
        searchable instead of stringifying to ``"<object>"``.
        """
        span = _trace.get_current_span()
        for k, v in attrs.items():
            if v is None:
                continue
            if isinstance(v, (str, int, float, bool)):
                span.set_attribute(k, v)
            else:
                span.set_attribute(k, _json.dumps(v, default=str))

    def traced(name_or_fn=None, *, kind: str = "INTERNAL", openinference_kind: str | None = None):
        """Decorator: create a span around a function.

        Supports both @traced and @traced("name") usage.

        Args:
            name_or_fn: Span name (str) or the function itself (when used without args).
            kind: OTel SpanKind name (unused in lightweight client tracing).
            openinference_kind: OpenInference span kind (unused in lightweight client tracing).
        """
        import asyncio
        import functools

        def decorator(fn):
            span_name = name_or_fn if isinstance(name_or_fn, str) else fn.__qualname__
            tracer = _trace.get_tracer(fn.__module__)

            @functools.wraps(fn)
            async def async_wrapper(*a, **kw):
                with tracer.start_as_current_span(span_name):
                    return await fn(*a, **kw)

            @functools.wraps(fn)
            def sync_wrapper(*a, **kw):
                with tracer.start_as_current_span(span_name):
                    return fn(*a, **kw)

            if asyncio.iscoroutinefunction(fn):
                return async_wrapper
            return sync_wrapper

        if callable(name_or_fn):
            return decorator(name_or_fn)
        return decorator

    def setup_tracing(
        service_name: str = "mega-code-client",
        session_id: str | None = None,
    ) -> bool:
        """Initialize tracing exporter (call once at startup).

        Only sets up if OTEL_EXPORTER_OTLP_ENDPOINT env var is configured.
        Uses gRPC protocol for both Phoenix (local) and Honeycomb (deployed).

        Supported env vars:
            OTEL_EXPORTER_OTLP_ENDPOINT: gRPC endpoint (e.g. http://localhost:4317)
            OTEL_EXPORTER_OTLP_HEADERS: Auth headers (e.g. x-honeycomb-team=hcaik_xxx)
            MEGA_CODE_SESSION_ID: Optional correlation id, also picked up
                automatically when ``session_id`` arg is omitted.

        Args:
            service_name: ``service.name`` resource attribute.
            session_id: Optional correlation id for one slash-command run.
                When set, attached as ``mega_code.session_id`` resource
                attribute so every span this process emits joins the same
                logical run in Phoenix/Honeycomb. The slash command sets
                it once in ``setup.sh`` so all later ``python -m``
                invocations share the value.

        Returns:
            True if tracing was set up, False otherwise.
        """
        global _client_initialized
        if _client_initialized:
            return True

        import os

        if os.environ.get("OTEL_SDK_DISABLED", "").lower() in ("true", "1"):
            return False

        endpoint = os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT")
        if not endpoint:
            return False

        from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor

        headers = _parse_otlp_headers(os.environ.get("OTEL_EXPORTER_OTLP_HEADERS", ""))
        resource_attrs: dict[str, str] = {"service.name": service_name}
        session_id = session_id or os.environ.get("MEGA_CODE_SESSION_ID") or None
        if session_id:
            resource_attrs["mega_code.session_id"] = session_id
        resource = Resource.create(resource_attrs)
        insecure = endpoint.startswith("http://")
        exporter = OTLPSpanExporter(
            endpoint=endpoint,
            headers=headers,
            insecure=insecure,
        )
        provider = TracerProvider(resource=resource)
        provider.add_span_processor(BatchSpanProcessor(exporter))
        _trace.set_tracer_provider(provider)
        # Short-lived `python -m` invocations (e.g. list_cached, resolve_skill)
        # finish well under the BatchSpanProcessor's 5s schedule_delay, so
        # without an explicit shutdown the queued spans get dropped on exit.
        # provider.shutdown() flushes synchronously.
        import atexit

        atexit.register(provider.shutdown)
        _client_initialized = True
        logger.info(
            "OpenTelemetry tracing initialized: endpoint=%s session_id=%s",
            endpoint,
            session_id or "<unset>",
        )
        return True

else:
    # OpenTelemetry not installed — no-op stubs

    class _NoOpSpan:
        """No-op span for when OTEL is not available."""

        def set_attribute(self, key, value):
            pass

        def record_exception(self, exception):
            pass

        def set_status(self, status):
            pass

        def get_span_context(self):
            return None

        def __enter__(self):
            return self

        def __exit__(self, *a):
            pass

    class _NoOpTracer:
        """No-op tracer for when OTEL is not available."""

        def start_as_current_span(self, name, **kw):
            return contextlib.nullcontext(_NoOpSpan())

        def start_span(self, name, **kw):
            return _NoOpSpan()

    def get_tracer(name: str):  # type: ignore[misc]
        """Return a no-op tracer."""
        return _NoOpTracer()

    def set_span_attributes(**attrs) -> None:  # type: ignore[misc]
        """No-op — tracing not available."""
        return None

    def traced(*args, **kwargs):  # type: ignore[misc]
        """No-op decorator — returns function unchanged."""

        def decorator(fn):
            return fn

        if len(args) == 1 and callable(args[0]):
            return args[0]
        return decorator

    def setup_tracing(**kwargs) -> bool:  # type: ignore[misc]
        """No-op — tracing not available."""
        return False


def has_opentelemetry() -> bool:
    """Check whether opentelemetry is available.

    Returns:
        True if opentelemetry packages are installed.
    """
    return _HAS_OTEL
