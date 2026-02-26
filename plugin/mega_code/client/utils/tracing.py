"""Tracing. Uses OpenTelemetry when installed, no-op otherwise.

Client-side tracing module. No edition concept — simply auto-detects
whether opentelemetry packages are available at import time.

When installed:
    pip install mega-code[telemetry]
    → opentelemetry-api, opentelemetry-sdk, opentelemetry-exporter-otlp

When NOT installed:
    → all functions degrade to no-ops transparently.
"""

from __future__ import annotations

import contextlib
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

try:
    from opentelemetry import trace as _trace

    _HAS_OTEL = True

    def get_tracer(name: str):
        """Get a tracer by name."""
        return _trace.get_tracer(name)

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

    def setup_tracing(service_name: str = "mega-code-client") -> bool:
        """Initialize tracing exporter (call once at startup).

        Only sets up if OTEL_EXPORTER_OTLP_ENDPOINT env var is configured.
        Uses gRPC protocol for both Phoenix (local) and Honeycomb (deployed).

        Supported env vars:
            OTEL_EXPORTER_OTLP_ENDPOINT: gRPC endpoint (e.g. http://localhost:4317)
            OTEL_EXPORTER_OTLP_HEADERS: Auth headers (e.g. x-honeycomb-team=hcaik_xxx)

        Returns:
            True if tracing was set up, False otherwise.
        """
        global _client_initialized
        if _client_initialized:
            return True

        import os

        endpoint = os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT")
        if not endpoint:
            return False

        try:
            from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
            from opentelemetry.sdk.resources import Resource
            from opentelemetry.sdk.trace import TracerProvider
            from opentelemetry.sdk.trace.export import BatchSpanProcessor

            headers = _parse_otlp_headers(os.environ.get("OTEL_EXPORTER_OTLP_HEADERS", ""))
            resource = Resource.create({"service.name": service_name})
            insecure = endpoint.startswith("http://")
            exporter = OTLPSpanExporter(
                endpoint=endpoint,
                headers=headers,
                insecure=insecure,
            )
            provider = TracerProvider(resource=resource)
            provider.add_span_processor(BatchSpanProcessor(exporter))
            _trace.set_tracer_provider(provider)
            _client_initialized = True
            logger.info("OpenTelemetry tracing initialized: endpoint=%s", endpoint)
            return True
        except ImportError:
            logger.debug("OTLP gRPC exporter not available, tracing disabled")
            return False

except ImportError:
    # OpenTelemetry not installed — no-op stubs
    _HAS_OTEL = False

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
