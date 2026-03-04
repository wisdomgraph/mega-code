"""Shared environment utilities for MEGA-Code CLI scripts.

Provides common helpers for environment variable diagnostics and loading
used by collector.py, run_pipeline_async.py, and check_pending_skills.py.
"""

import os
import sys

# Key environment variables to show in diagnostics
_ENV_DEBUG_KEYS = [
    "MEGA_CODE_CLIENT_MODE",
    "MEGA_CODE_SERVER_URL",
    "OTEL_EXPORTER_OTLP_ENDPOINT",
]


def print_env_debug(
    keys: list[str] | None = None,
    file=None,
) -> None:
    """Print key env vars to stderr for diagnostics.

    Args:
        keys: List of env var names to print. Defaults to _ENV_DEBUG_KEYS.
        file: Output file object. Defaults to sys.stderr.
    """
    if file is None:
        file = sys.stderr
    for key in keys or _ENV_DEBUG_KEYS:
        print(f"  {key}={os.environ.get(key, '<unset>')}", file=file)
