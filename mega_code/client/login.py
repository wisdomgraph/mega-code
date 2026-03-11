"""CLI login flow for MEGA-Code.

Authenticates users via OAuth (GitHub/Google) through mega-service's CLI session
endpoints. Supports two modes:

**Combined mode** (default, for terminal use):
    python -m mega_code.client.login

**Two-step mode** (for Claude Code / non-interactive use):
    # Step 1: Create session, print JSON with login_url (fast, non-blocking)
    python -m mega_code.client.login --step create

    # Step 2: Poll until complete, save API key (run in background)
    python -m mega_code.client.login --step poll --client-id <ID> --url <URL>

Environment variables:
  MEGA_CODE_SERVER_URL -- server base URL (default: https://console.megacode.ai)
                         The mega-service API path is derived automatically.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from pathlib import Path

import httpx

from mega_code.client.cli import get_env_path, load_env_file, save_env_file

logger = logging.getLogger(__name__)

_DEFAULT_SERVER_URL = "https://console.megacode.ai"
_MEGA_SERVICE_API_PATH = "/api/mega-service/v1"
_DEFAULT_PROVIDER = "google"
_POLL_INTERVAL_SECONDS = 3
_POLL_TIMEOUT_SECONDS = 600  # 10 minutes, matches server-side expiry
_MAX_TRANSIENT_RETRIES = 3  # consecutive network failures before aborting


def _resolve_mega_service_url() -> str:
    """Resolve the mega-service API URL from MEGA_CODE_SERVER_URL.

    Reads the server base URL and appends the API path.

    Priority:
    1. MEGA_CODE_SERVER_URL from the .env file
    2. MEGA_CODE_SERVER_URL environment variable
    3. Default URL
    """
    env_vars = load_env_file(get_env_path())
    server = (
        env_vars.get("MEGA_CODE_SERVER_URL")
        or os.environ.get("MEGA_CODE_SERVER_URL")
        or _DEFAULT_SERVER_URL
    )
    return server.rstrip("/") + _MEGA_SERVICE_API_PATH


def _derive_server_url(base_url: str) -> str:
    """Derive MEGA_CODE_SERVER_URL from a mega-service API URL.

    Strips the /api/mega-service/v1 suffix if present.
    """
    if base_url.endswith(_MEGA_SERVICE_API_PATH):
        return base_url[: -len(_MEGA_SERVICE_API_PATH)]
    return base_url


def create_cli_session(base_url: str, provider: str) -> dict:
    """Create a CLI auth session on mega-service.

    Args:
        base_url: mega-service API base URL.
        provider: OAuth provider ("github" or "google").

    Returns:
        Dict with "client_id" and "login_url".

    Raises:
        httpx.HTTPStatusError: On non-2xx response.
        httpx.ConnectError: If mega-service is unreachable.
    """
    resp = httpx.post(
        f"{base_url}/auth/cli/session",
        json={"provider": provider},
        timeout=10.0,
    )
    resp.raise_for_status()
    return resp.json()


def poll_cli_session(
    base_url: str,
    client_id: str,
    *,
    timeout: int = _POLL_TIMEOUT_SECONDS,
    interval: int = _POLL_INTERVAL_SECONDS,
) -> str:
    """Poll a CLI session until the user completes login.

    Args:
        base_url: mega-service API base URL.
        client_id: The session client_id from create_cli_session.
        timeout: Max seconds to poll before giving up.
        interval: Seconds between poll requests.

    Returns:
        The raw API key string (e.g. "mg_...").

    Raises:
        TimeoutError: If the session does not complete within timeout.
        httpx.HTTPStatusError: On unexpected HTTP errors.
        ValueError: If session completes but api_key is missing.
    """
    deadline = time.monotonic() + timeout
    url = f"{base_url}/auth/cli/session/{client_id}"
    consecutive_errors = 0

    while time.monotonic() < deadline:
        try:
            resp = httpx.get(url, timeout=10.0)
        except (httpx.ConnectError, httpx.TimeoutException):
            consecutive_errors += 1
            if consecutive_errors >= _MAX_TRANSIENT_RETRIES:
                raise
            logger.debug(
                "Transient network error (%d/%d), retrying...",
                consecutive_errors,
                _MAX_TRANSIENT_RETRIES,
            )
            time.sleep(interval)
            continue

        consecutive_errors = 0  # reset on successful request

        if resp.status_code == 404:
            raise TimeoutError("Session expired or not found. Please run login again.")

        resp.raise_for_status()
        data = resp.json()

        if data.get("status") == "complete":
            api_key = data.get("api_key")
            if not api_key:
                raise ValueError("Session complete but no api_key in response.")
            return api_key

        # Still pending -- wait and retry
        remaining = deadline - time.monotonic()
        wait = min(interval, remaining)
        if wait <= 0:
            break
        time.sleep(wait)

    raise TimeoutError(f"Login timed out after {timeout} seconds. Please try again.")


def _save_api_key(api_key: str, base_url: str) -> tuple[Path, dict[str, str]]:
    """Save API key and related env vars to the .env file.

    Sets:
      - MEGA_CODE_API_KEY
      - MEGA_CODE_CLIENT_MODE=remote (if not already set)
      - MEGA_CODE_SERVER_URL (if not already set, derived from base_url)

    Returns:
        Tuple of (env_path, updated env_vars dict).
    """
    env_path = get_env_path()
    env_vars = load_env_file(env_path)

    env_vars["MEGA_CODE_API_KEY"] = api_key

    # Ensure remote client mode and server URL are configured
    if env_vars.get("MEGA_CODE_CLIENT_MODE") != "remote":
        env_vars["MEGA_CODE_CLIENT_MODE"] = "remote"

    # Always update server URL to match the service we authenticated against
    env_vars["MEGA_CODE_SERVER_URL"] = _derive_server_url(base_url)

    env_path.parent.mkdir(parents=True, exist_ok=True)
    save_env_file(env_path, env_vars)

    return env_path, env_vars


# =========================================================================
# Step functions (two-step mode for Claude Code)
# =========================================================================


def run_create(
    provider: str = _DEFAULT_PROVIDER,
    base_url: str | None = None,
) -> int:
    """Step 1: Create a CLI session and print login info as JSON.

    Prints a JSON object to stdout with login_url, client_id, and base_url.
    Claude Code parses this to show the URL and then runs poll in background.

    Returns 0 on success, 1 on failure.
    """
    if base_url is None:
        base_url = _resolve_mega_service_url()

    try:
        session = create_cli_session(base_url, provider)
    except httpx.ConnectError:
        print(json.dumps({"error": f"Cannot connect to mega-service at {base_url}"}))
        return 1
    except httpx.HTTPStatusError as exc:
        print(
            json.dumps(
                {"error": f"Failed to create login session: {exc.response.status_code}"}
            )
        )
        return 1

    print(
        json.dumps(
            {
                "login_url": session["login_url"],
                "client_id": session["client_id"],
                "base_url": base_url,
            }
        )
    )
    return 0


def run_poll(client_id: str, base_url: str) -> int:
    """Step 2: Poll for login completion and save the API key.

    Blocks until the user completes login or timeout is reached.
    On success, saves the API key and env vars to .env.

    Returns 0 on success, 1 on failure.
    """
    try:
        api_key = poll_cli_session(base_url, client_id)
    except TimeoutError as exc:
        print(f"Error: {exc}")
        return 1
    except httpx.ConnectError:
        print(f"Error: Lost connection to mega-service at {base_url}")
        return 1
    except (httpx.HTTPStatusError, ValueError) as exc:
        print(f"Error: {exc}")
        return 1

    env_path, env_vars = _save_api_key(api_key, base_url)

    print("Login successful!")
    print(f"API key saved to: {env_path}")
    print("Client mode: remote")
    print(f"Server URL: {env_vars['MEGA_CODE_SERVER_URL']}")

    return 0


# =========================================================================
# Combined mode (legacy, for terminal use)
# =========================================================================


def run_login(
    provider: str = _DEFAULT_PROVIDER,
    base_url: str | None = None,
) -> int:
    """Run the full CLI login flow (combined create + poll).

    Creates a session, prints the login URL, polls for completion,
    and saves the API key to the .env file.

    Args:
        provider: OAuth provider ("github" or "google").
        base_url: mega-service URL. Auto-resolved if None.

    Returns:
        0 on success, 1 on failure.
    """
    if base_url is None:
        base_url = _resolve_mega_service_url()

    logger.debug("Login server: %s", base_url)
    print(f"Logging in via {provider}...")
    print()

    # Step 1: Create CLI session
    try:
        session = create_cli_session(base_url, provider)
    except httpx.ConnectError:
        print(f"Error: Cannot connect to mega-service at {base_url}")
        return 1
    except httpx.HTTPStatusError as exc:
        print(f"Error: Failed to create login session: {exc.response.status_code}")
        return 1

    client_id = session["client_id"]
    login_url = session["login_url"]

    print("Please open the following URL in your web browser to sign in:")
    print()
    print(f"  {login_url}")
    print()
    print("Waiting for login to complete (timeout: 10 minutes)...")

    # Step 2: Poll for completion
    try:
        api_key = poll_cli_session(base_url, client_id)
    except TimeoutError as exc:
        print(f"\nError: {exc}")
        return 1
    except httpx.ConnectError:
        print(f"\nError: Lost connection to mega-service at {base_url}")
        return 1
    except (httpx.HTTPStatusError, ValueError) as exc:
        print(f"\nError: {exc}")
        return 1

    # Step 3: Save API key to .env
    env_path, env_vars = _save_api_key(api_key, base_url)

    print()
    print("Login successful!")
    print(f"API key saved to: {env_path}")
    print("Client mode: remote")
    print(f"Server URL: {env_vars['MEGA_CODE_SERVER_URL']}")

    return 0


# =========================================================================
# CLI entry point
# =========================================================================


def main() -> int:
    """CLI entry point for login.

    Supports:
      --step create   Fast, non-blocking: prints JSON with login_url + client_id
      --step poll     Blocking: polls until complete, saves API key
      (no --step)     Combined mode: create + poll in one call
    """
    parser = argparse.ArgumentParser(
        prog="mega-code-login",
        description="Sign in to MEGA-Code via OAuth",
    )
    parser.add_argument(
        "--step",
        choices=["create", "poll"],
        default=None,
        help="Run a specific step: 'create' (get URL) or 'poll' (wait for completion)",
    )
    parser.add_argument(
        "--provider",
        choices=["github", "google"],
        default=_DEFAULT_PROVIDER,
        help=f"OAuth provider (default: {_DEFAULT_PROVIDER})",
    )
    parser.add_argument(
        "--url",
        type=str,
        default=None,
        help="Server URL (e.g. https://console.megacode.ai). Appends API path automatically.",
    )
    parser.add_argument(
        "--client-id",
        type=str,
        default=None,
        help="Session client_id (required for --step poll)",
    )

    args = parser.parse_args()

    # Auto-append API path if --url is a bare server URL
    if args.url and not args.url.rstrip("/").endswith(
        _MEGA_SERVICE_API_PATH.rstrip("/")
    ):
        args.url = args.url.rstrip("/") + _MEGA_SERVICE_API_PATH

    if args.step == "create":
        return run_create(provider=args.provider, base_url=args.url)
    elif args.step == "poll":
        if not args.client_id:
            print("Error: --client-id is required for --step poll", file=sys.stderr)
            return 1
        base_url = args.url or _resolve_mega_service_url()
        return run_poll(client_id=args.client_id, base_url=base_url)
    else:
        return run_login(provider=args.provider, base_url=args.url)


if __name__ == "__main__":
    sys.exit(main())
