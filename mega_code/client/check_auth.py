"""Auth gate for mega-code skills.

Usage:
    python -m mega_code.client.check_auth

Exit 0 = authenticated, Exit 1 = not logged in (message printed to stdout).
"""

from __future__ import annotations

import os
import sys

from mega_code.client.cli import get_env_path, load_env_file

_NOT_LOGGED_IN = "Not logged in. Run /mega-code:login first."
_KEY_EXPIRED = "API key expired or invalid. Run /mega-code:login to re-authenticate."


def check_auth() -> bool:
    """Check API key is set and valid via load_profile().

    Returns True if authenticated. Prints a friendly message and returns
    False when the key is missing or rejected by the server.
    Connection errors propagate — if we can't verify the key, we don't
    pretend it's valid.
    """
    # Load .env so create_client() can find MEGA_CODE_API_KEY
    for key, value in load_env_file(get_env_path()).items():
        os.environ.setdefault(key, value)

    if not os.environ.get("MEGA_CODE_API_KEY"):
        print(_NOT_LOGGED_IN)
        return False

    try:
        from mega_code.client.api import create_client

        client = create_client()
        client.load_profile()
        return True
    except ValueError:
        # 401/403 from _check_response, or missing key in create_client
        print(_KEY_EXPIRED)
        return False


def main() -> int:
    return 0 if check_auth() else 1


if __name__ == "__main__":
    sys.exit(main())
