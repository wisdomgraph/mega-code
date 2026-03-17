"""NDJSON-based tracing with OTLP HTTP export.

Lightweight tracing that writes spans as newline-delimited JSON to local files,
then exports them to an OTLP-compatible backend (e.g. Honeycomb) via HTTP/JSON.

Zero extra dependencies beyond stdlib + httpx (already a core dep).

Crash-safe: each span is written twice (open + close), and the exporter deduplicates
by spanId keeping the last occurrence.

Every public method wraps in try/except — tracing must never crash the host process.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Span status codes (OTLP conventions)
# ---------------------------------------------------------------------------
STATUS_UNSET = 0
STATUS_OK = 1
STATUS_ERROR = 2

# SpanKind values (OTLP)
SPAN_KIND_INTERNAL = 1
SPAN_KIND_SERVER = 2
SPAN_KIND_CLIENT = 3
SPAN_KIND_PRODUCER = 4
SPAN_KIND_CONSUMER = 5

_SPAN_KIND_MAP = {
    "INTERNAL": SPAN_KIND_INTERNAL,
    "SERVER": SPAN_KIND_SERVER,
    "CLIENT": SPAN_KIND_CLIENT,
    "PRODUCER": SPAN_KIND_PRODUCER,
    "CONSUMER": SPAN_KIND_CONSUMER,
}

# Max spans per export POST request
_BATCH_SIZE = 500


def _now_ns() -> int:
    """Current time as nanoseconds since epoch."""
    return int(time.time() * 1_000_000_000)


def _new_span_id() -> str:
    """Generate a random 16-hex-char span ID."""
    return uuid.uuid4().hex[:16]


def _make_trace_id(session_id: str | None = None) -> str:
    """Generate a 32-hex-char trace ID.

    Deterministic (md5) when session_id is provided, random otherwise.
    """
    if session_id:
        return hashlib.md5(session_id.encode()).hexdigest()
    return uuid.uuid4().hex


def _otlp_attr(key: str, value: Any) -> dict:
    """Format a key/value pair as an OTLP attribute."""
    if isinstance(value, bool):
        return {"key": key, "value": {"boolValue": value}}
    if isinstance(value, int):
        return {"key": key, "value": {"intValue": str(value)}}
    if isinstance(value, float):
        return {"key": key, "value": {"doubleValue": value}}
    return {"key": key, "value": {"stringValue": str(value)}}


# ---------------------------------------------------------------------------
# NdjsonSpanWriter — append-only file writer
# ---------------------------------------------------------------------------


class NdjsonSpanWriter:
    """Appends span dicts as JSON lines to a file. Never raises."""

    def __init__(self, trace_dir: Path, trace_id: str) -> None:
        self._trace_dir = trace_dir
        self._trace_id = trace_id
        self._file_path = trace_dir / f"{trace_id}.ndjson"
        self._trace_dir.mkdir(parents=True, exist_ok=True)

    @property
    def file_path(self) -> Path:
        return self._file_path

    @property
    def trace_id(self) -> str:
        return self._trace_id

    def write_span(self, span_dict: dict) -> None:
        """Append a single span as a JSON line. Never raises."""
        try:
            line = json.dumps(span_dict, separators=(",", ":"))
            with open(self._file_path, "a", encoding="utf-8") as f:
                f.write(line + "\n")
        except Exception:
            pass


# ---------------------------------------------------------------------------
# NdjsonSpan — context manager that writes open + close lines
# ---------------------------------------------------------------------------


class NdjsonSpan:
    """A span that writes to NDJSON on enter/exit.

    On __enter__: writes a line with endTimeUnixNano == startTimeUnixNano (crash-safe).
    On __exit__: writes a second line with real end time, attributes, status, events.
    """

    def __init__(
        self,
        writer: NdjsonSpanWriter,
        name: str,
        trace_id: str,
        span_id: str,
        parent_span_id: str = "",
        kind: int = SPAN_KIND_INTERNAL,
    ) -> None:
        self._writer = writer
        self._name = name
        self._trace_id = trace_id
        self._span_id = span_id
        self._parent_span_id = parent_span_id
        self._kind = kind
        self._start_ns = _now_ns()
        self._attributes: list[dict] = []
        self._events: list[dict] = []
        self._status_code = STATUS_UNSET
        self._status_message = ""

    @property
    def span_id(self) -> str:
        return self._span_id

    @property
    def trace_id(self) -> str:
        return self._trace_id

    def set_attribute(self, key: str, value: Any) -> None:
        """Record an attribute on this span."""
        try:
            self._attributes.append(_otlp_attr(key, value))
        except Exception:
            pass

    def record_exception(self, exception: BaseException) -> None:
        """Record an exception as a span event."""
        try:
            self._events.append(
                {
                    "name": "exception",
                    "timeUnixNano": str(_now_ns()),
                    "attributes": [
                        _otlp_attr("exception.type", type(exception).__name__),
                        _otlp_attr("exception.message", str(exception)),
                    ],
                }
            )
            self._status_code = STATUS_ERROR
            self._status_message = str(exception)
        except Exception:
            pass

    def set_status(self, code: int, message: str = "") -> None:
        """Set span status (STATUS_OK=1, STATUS_ERROR=2)."""
        try:
            self._status_code = code
            self._status_message = message
        except Exception:
            pass

    def get_span_context(self) -> dict | None:
        """Return trace/span IDs for propagation."""
        return {"trace_id": self._trace_id, "span_id": self._span_id}

    def _to_dict(self, end_ns: int | None = None) -> dict:
        """Serialize span to OTLP-compatible dict."""
        return {
            "traceId": self._trace_id,
            "spanId": self._span_id,
            "parentSpanId": self._parent_span_id,
            "name": self._name,
            "kind": self._kind,
            "startTimeUnixNano": str(self._start_ns),
            "endTimeUnixNano": str(end_ns or self._start_ns),
            "attributes": self._attributes,
            "status": {"code": self._status_code, "message": self._status_message},
            "events": self._events,
        }

    def __enter__(self):
        # Write initial span line (crash-safe: endTime == startTime)
        try:
            self._writer.write_span(self._to_dict())
        except Exception:
            pass
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        try:
            if exc_val is not None:
                self.record_exception(exc_val)
            elif self._status_code == STATUS_UNSET:
                self._status_code = STATUS_OK
                self._status_message = "OK"
            # Write final span line with real end time
            self._writer.write_span(self._to_dict(end_ns=_now_ns()))
        except Exception:
            pass
        return False  # don't suppress exceptions


# ---------------------------------------------------------------------------
# Global span context stack (shared across all tracers in this process)
# ---------------------------------------------------------------------------

_global_span_stack: list[str] = []


# ---------------------------------------------------------------------------
# NdjsonTracer — creates spans with shared global context stack
# ---------------------------------------------------------------------------


class NdjsonTracer:
    """Creates NDJSON spans with automatic parent-child linking.

    All NdjsonTracer instances share a single global span stack so that
    parent-child relationships work correctly across module boundaries
    (e.g. run_pipeline.py -> remote.py -> sync.py).
    """

    def __init__(self, writer: NdjsonSpanWriter, trace_id: str, name: str = "") -> None:
        self._writer = writer
        self._trace_id = trace_id
        self._name = name

    @contextmanager
    def start_as_current_span(self, name: str, *, kind: int = SPAN_KIND_INTERNAL, **kw):
        """Context manager that creates a span and makes it current."""
        span_id = _new_span_id()
        parent_span_id = _global_span_stack[-1] if _global_span_stack else ""
        span = NdjsonSpan(
            writer=self._writer,
            name=name,
            trace_id=self._trace_id,
            span_id=span_id,
            parent_span_id=parent_span_id,
            kind=kind,
        )
        _global_span_stack.append(span_id)
        try:
            with span:
                yield span
        finally:
            if _global_span_stack and _global_span_stack[-1] == span_id:
                _global_span_stack.pop()

    def start_span(self, name: str, **kw) -> NdjsonSpan:
        """Create a span without making it current (for manual lifecycle)."""
        span_id = _new_span_id()
        parent_span_id = _global_span_stack[-1] if _global_span_stack else ""
        return NdjsonSpan(
            writer=self._writer,
            name=name,
            trace_id=self._trace_id,
            span_id=span_id,
            parent_span_id=parent_span_id,
        )

    @property
    def current_span_id(self) -> str | None:
        """Return the current span ID, or None if no span is active."""
        return _global_span_stack[-1] if _global_span_stack else None


# ---------------------------------------------------------------------------
# OTLP HTTP Exporter
# ---------------------------------------------------------------------------

_CHECKPOINT_FILE = ".checkpoint.json"


def _read_checkpoint(trace_dir: Path) -> dict[str, int]:
    """Load checkpoint offsets. Returns {file_path: byte_offset}."""
    try:
        cp = trace_dir / _CHECKPOINT_FILE
        if cp.exists():
            return json.loads(cp.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {}


def _write_checkpoint(trace_dir: Path, checkpoints: dict[str, int]) -> None:
    """Save checkpoint offsets."""
    try:
        cp = trace_dir / _CHECKPOINT_FILE
        cp.write_text(json.dumps(checkpoints), encoding="utf-8")
    except Exception:
        pass


def _read_spans_from_file(
    file_path: Path, offset: int = 0
) -> tuple[list[dict], int]:
    """Read NDJSON spans from file starting at byte offset.

    Returns (spans, new_offset). Deduplicates by spanId (last wins).
    """
    spans_by_id: dict[str, dict] = {}
    new_offset = offset
    try:
        with open(file_path, encoding="utf-8") as f:
            f.seek(offset)
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    span = json.loads(line)
                    span_id = span.get("spanId", "")
                    if span_id:
                        spans_by_id[span_id] = span
                except json.JSONDecodeError:
                    continue
            new_offset = f.tell()
    except Exception:
        pass
    return list(spans_by_id.values()), new_offset


def _resolve_otlp_config() -> tuple[str, dict[str, str]]:
    """Resolve OTLP endpoint and headers from env vars.

    Returns (endpoint_url, headers_dict).
    """
    endpoint = os.environ.get("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT", "")
    if not endpoint:
        base = os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT", "")
        if base:
            endpoint = base.rstrip("/") + "/v1/traces"
        else:
            endpoint = "https://api.honeycomb.io/v1/traces"

    headers: dict[str, str] = {"Content-Type": "application/json"}
    raw_headers = os.environ.get("OTEL_EXPORTER_OTLP_HEADERS", "")
    if raw_headers:
        for pair in raw_headers.split(","):
            pair = pair.strip()
            if "=" in pair:
                k, _, v = pair.partition("=")
                headers[k.strip()] = v.strip()

    return endpoint, headers


def _build_otlp_envelope(
    spans: list[dict],
    service_name: str = "mega-code-client",
    service_version: str = "",
) -> dict:
    """Wrap spans in OTLP JSON envelope."""
    resource_attrs = [
        _otlp_attr("service.name", service_name),
    ]
    if service_version:
        resource_attrs.append(_otlp_attr("service.version", service_version))

    return {
        "resourceSpans": [
            {
                "resource": {"attributes": resource_attrs},
                "scopeSpans": [
                    {
                        "scope": {"name": "mega-code", "version": "1.0.0"},
                        "spans": spans,
                    }
                ],
            }
        ]
    }


def _get_service_version() -> str:
    """Read service version from package metadata or env."""
    version = os.environ.get("OTEL_SERVICE_VERSION", "")
    if version:
        return version
    try:
        from importlib.metadata import version as pkg_version

        return pkg_version("mega-code")
    except Exception:
        return ""


def export_traces(
    trace_dir: Path | None = None,
    service_name: str | None = None,
    writer: NdjsonSpanWriter | None = None,
) -> bool:
    """Export accumulated NDJSON spans to OTLP endpoint.

    Reads spans from the NDJSON file, deduplicates, wraps in OTLP envelope,
    and POSTs to the configured endpoint. On success, cleans up the file.

    Args:
        trace_dir: Directory containing trace files. Defaults to data_dir()/trace.
        service_name: Override service name in OTLP resource.
        writer: If provided, export only this writer's file.

    Returns:
        True if export succeeded (or no spans to export), False on error.
    """
    try:
        if trace_dir is None:
            from mega_code.client.dirs import data_dir

            trace_dir = data_dir() / "trace"

        if not trace_dir.exists():
            return True

        # Determine which files to process
        if writer is not None:
            ndjson_files = [writer.file_path] if writer.file_path.exists() else []
        else:
            ndjson_files = list(trace_dir.glob("*.ndjson"))

        if not ndjson_files:
            return True

        svc_name = service_name or os.environ.get("OTEL_SERVICE_NAME", "mega-code-client")
        svc_version = _get_service_version()
        endpoint, headers = _resolve_otlp_config()
        checkpoints = _read_checkpoint(trace_dir)

        all_ok = True
        for ndjson_file in ndjson_files:
            file_key = str(ndjson_file)
            offset = checkpoints.get(file_key, 0)
            spans, new_offset = _read_spans_from_file(ndjson_file, offset)

            if not spans:
                # No new spans — clean up if file fully read
                if new_offset > 0 and offset > 0:
                    _cleanup_trace_file(ndjson_file, checkpoints, file_key, trace_dir)
                continue

            ok = _send_spans(spans, svc_name, svc_version, endpoint, headers)
            if ok:
                checkpoints[file_key] = new_offset
                _write_checkpoint(trace_dir, checkpoints)
                # Clean up after successful final export
                _cleanup_trace_file(ndjson_file, checkpoints, file_key, trace_dir)
            else:
                all_ok = False

        return all_ok

    except Exception:
        logger.debug("export_traces failed", exc_info=True)
        return False


def flush_traces(writer: NdjsonSpanWriter | None = None) -> bool:
    """Flush (export) accumulated spans without cleanup.

    Unlike export_traces, this is designed for periodic mid-process flushes.
    It advances the checkpoint but does NOT delete the NDJSON file.

    Returns:
        True if flush succeeded, False on error.
    """
    try:
        if writer is None:
            return True

        trace_dir = writer.file_path.parent
        if not writer.file_path.exists():
            return True

        svc_name = os.environ.get("OTEL_SERVICE_NAME", "mega-code-client")
        svc_version = _get_service_version()
        endpoint, headers = _resolve_otlp_config()
        checkpoints = _read_checkpoint(trace_dir)

        file_key = str(writer.file_path)
        offset = checkpoints.get(file_key, 0)
        spans, new_offset = _read_spans_from_file(writer.file_path, offset)

        if not spans:
            return True

        ok = _send_spans(spans, svc_name, svc_version, endpoint, headers)
        if ok:
            checkpoints[file_key] = new_offset
            _write_checkpoint(trace_dir, checkpoints)
        return ok

    except Exception:
        logger.debug("flush_traces failed", exc_info=True)
        return False


def _send_spans(
    spans: list[dict],
    service_name: str,
    service_version: str,
    endpoint: str,
    headers: dict[str, str],
) -> bool:
    """POST spans to OTLP endpoint in batches. Returns True if all succeeded."""
    import httpx
    from tenacity import retry, stop_after_attempt, wait_exponential

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=0.25, max=5),
        reraise=True,
    )
    def _post(batch: list[dict]) -> None:
        envelope = _build_otlp_envelope(batch, service_name, service_version)
        resp = httpx.post(endpoint, json=envelope, headers=headers, timeout=10.0)
        resp.raise_for_status()

    try:
        # Split into batches if needed
        for i in range(0, len(spans), _BATCH_SIZE):
            batch = spans[i : i + _BATCH_SIZE]
            _post(batch)
        return True
    except Exception:
        logger.debug("Failed to send spans to %s", endpoint, exc_info=True)
        return False


def _cleanup_trace_file(
    file_path: Path,
    checkpoints: dict[str, int],
    file_key: str,
    trace_dir: Path,
) -> None:
    """Remove NDJSON file and its checkpoint entry after successful export."""
    try:
        file_path.unlink(missing_ok=True)
        checkpoints.pop(file_key, None)
        _write_checkpoint(trace_dir, checkpoints)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Trace context propagation (W3C traceparent)
# ---------------------------------------------------------------------------


def format_traceparent(trace_id: str, span_id: str) -> str:
    """Format W3C traceparent header value."""
    return f"00-{trace_id}-{span_id}-01"
