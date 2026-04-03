"""Client factory. Remote by default, local via lazy import when available."""

from __future__ import annotations

import importlib.util
import logging
import os
from urllib.parse import urlparse

from mega_code.client.api.protocol import MegaCodeBaseClient
from mega_code.client.api.remote import MegaCodeRemote

logger = logging.getLogger(__name__)


def _default_mode() -> str:
    """Detect whether pipeline is available for local mode."""
    if importlib.util.find_spec("mega_code.pipeline") is not None:
        return "local"
    return "remote"


def create_client(mode: str | None = None, **kwargs) -> MegaCodeBaseClient:
    """Create a MEGA-Code client.

    Args:
        mode: Client mode ("local" or "remote"). If None, reads from
            MEGA_CODE_CLIENT_MODE env var, or auto-detects (local if
            pipeline is installed, remote otherwise).
        **kwargs: Backend-specific arguments.
            local: backend, model_name, project_id, + create_store kwargs.
            remote: server_url, api_key, timeout.

    Returns:
        A MegaCodeBaseClient implementation.
    """
    if mode is None:
        mode = os.environ.get("MEGA_CODE_CLIENT_MODE", _default_mode())

    if mode == "remote":
        if "api_key" not in kwargs:
            api_key = os.environ.get("MEGA_CODE_API_KEY", "")
            if not api_key:
                raise ValueError("Not logged in. Run /mega-code-login first.")
            kwargs["api_key"] = api_key
        if "server_url" not in kwargs:
            kwargs["server_url"] = os.environ.get("MEGA_CODE_SERVER_URL", "http://localhost:8000")
        server_url = kwargs["server_url"]
        parsed = urlparse(server_url)
        if parsed.scheme == "http" and parsed.hostname not in ("localhost", "127.0.0.1"):
            logger.warning(
                "Server URL %s uses plaintext HTTP — API key will be sent unencrypted. "
                "Set MEGA_CODE_SERVER_URL to an https:// URL for production use.",
                server_url,
            )
        return MegaCodeRemote(**kwargs)
    elif mode == "local":
        from mega_code.pipeline.local_client import MegaCodeLocal

        return MegaCodeLocal(**kwargs)

    raise ValueError(f"Unknown client mode: {mode!r}. Expected 'local' or 'remote'.")


def resolve_mode(mode_arg: str | None = None) -> str:
    """Determine execution mode (local or remote).

    Priority:
    1. Explicit mode_arg
    2. MEGA_CODE_CLIENT_MODE env var
    3. Default to 'local'

    Args:
        mode_arg: Explicit mode string, or None for auto-detection.

    Returns:
        'local' or 'remote'.
    """
    if mode_arg:
        return mode_arg
    return os.environ.get("MEGA_CODE_CLIENT_MODE", "local")


__all__ = [
    "MegaCodeBaseClient",
    "MegaCodeRemote",
    "create_client",
    "resolve_mode",
]
