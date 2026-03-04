"""Client factory. Remote by default, local via lazy import when available."""

from __future__ import annotations

import os

from mega_code.client.api.protocol import MegaCodeBaseClient
from mega_code.client.api.remote import MegaCodeRemote


def _default_mode() -> str:
    """Detect whether pipeline is available for local mode."""
    try:
        import mega_code.pipeline  # noqa: F401

        return "local"
    except ImportError:
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
                raise ValueError(
                    "MEGA_CODE_API_KEY is required for remote mode but not set.\n"
                    "\n"
                    "Run the following command to configure your API key:\n"
                    "  uv run --directory ~/.claude/mega-code mega-code configure"
                    " --api-key <your_key>\n"
                )
            kwargs["api_key"] = api_key
        if "server_url" not in kwargs:
            kwargs["server_url"] = os.environ.get("MEGA_CODE_SERVER_URL", "http://localhost:8000")
        return MegaCodeRemote(**kwargs)
    elif mode == "local":
        try:
            from mega_code.pipeline.local_client import MegaCodeLocal
        except ImportError:
            raise ValueError(
                "Local mode requires the full mega-code package (with pipeline).\n"
                "This is not available in the open-source edition.\n"
                "Use mode='remote' to connect to a MEGA-Code server instead."
            ) from None
        return MegaCodeLocal(**kwargs)

    raise ValueError(f"Unknown client mode: {mode!r}. Expected 'local' or 'remote'.")


__all__ = [
    "MegaCodeBaseClient",
    "MegaCodeRemote",
    "create_client",
]
