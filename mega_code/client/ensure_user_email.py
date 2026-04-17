"""Resolve and inject user email into pending SKILL.md frontmatter.

Usage:
    python -m mega_code.client.ensure_user_email --resolve-and-apply
    python -m mega_code.client.ensure_user_email --resolve-and-apply --non-interactive
    python -m mega_code.client.ensure_user_email --set-from-env
    python -m mega_code.client.ensure_user_email --apply-all-pending
    python -m mega_code.client.ensure_user_email --show

Exit codes:
    0 — success (or silent no-op)
    1 — invalid input or missing cache
    2 — EMAIL_REQUIRED (interactive prompt needed)
"""

from __future__ import annotations

import logging
import os
import re
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

from mega_code.client.cli import get_env_path, load_env_file, save_env_file

_ENV_KEY = "MEGA_CODE_USER_EMAIL"
_INPUT_ENV_KEY = "MEGA_CODE_EMAIL_INPUT"

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _load_env_into_os() -> None:
    """Mirror check_auth.py:31-32 so create_client() finds the API key."""
    for k, v in load_env_file(get_env_path()).items():
        os.environ.setdefault(k, v)


def _load_cached() -> str:
    """Return cached email from .env, or empty string."""
    env = load_env_file(get_env_path())
    return env.get(_ENV_KEY, "")


def _save_cached(email: str) -> None:
    """Persist email to .env."""
    save_env_file(get_env_path(), {_ENV_KEY: email})


def _try_resolve_from_profile() -> str:
    """Attempt to fetch email from server profile. Returns empty string on failure."""
    try:
        _load_env_into_os()
        from mega_code.client.api import create_client

        client = create_client()
        profile = client.load_profile()
        email = getattr(profile, "email", None) or ""
        if email:
            _save_cached(email)
        return email
    except Exception:
        logger.debug("Profile email resolution failed", exc_info=True)
        return ""


def _iter_pending_skill_files() -> list[Path]:
    """List SKILL.md files in pending-skills directory."""
    from mega_code.client.pending import PENDING_SKILLS_DIR

    if not PENDING_SKILLS_DIR.exists():
        return []
    return [
        d / "SKILL.md"
        for d in PENDING_SKILLS_DIR.iterdir()
        if d.is_dir() and (d / "SKILL.md").is_file()
    ]


def _apply_to(paths: list[Path], email: str) -> int:
    """Idempotent via ensure_skill_frontmatter's ``creator not in metadata`` guard."""
    from mega_code.client.skill_utils import ensure_skill_frontmatter

    touched = 0
    for path in paths:
        content = path.read_text(encoding="utf-8")
        updated = ensure_skill_frontmatter(content, skill_name=path.parent.name, email=email)
        if updated != content:
            path.write_text(updated, encoding="utf-8")
            touched += 1
    print(f"ensure_user_email: applied email to {touched}/{len(paths)} skill(s)")
    return 0


def _resolve_and_apply(non_interactive: bool) -> int:
    paths = _iter_pending_skill_files()
    if not paths:
        return 0
    email = _load_cached() or _try_resolve_from_profile()
    if not email:
        if non_interactive:
            return 0
        print("EMAIL_REQUIRED", file=sys.stderr)
        return 2
    return _apply_to(paths, email)


def _set_from_env() -> int:
    email = os.environ.get(_INPUT_ENV_KEY, "").strip()
    if not EMAIL_RE.match(email):
        print(f"invalid email format: '{email}'", file=sys.stderr)
        return 1
    _save_cached(email)
    return 0


def _apply_all_pending() -> int:
    email = _load_cached()
    if not email:
        print("no cached email — run --set-from-env or --resolve-and-apply first", file=sys.stderr)
        return 1
    paths = _iter_pending_skill_files()
    if not paths:
        print("ensure_user_email: no pending skills to patch")
        return 0
    return _apply_to(paths, email)


def _show() -> int:
    email = _load_cached()
    if email:
        print(email)
    else:
        print("(not set)")
    return 0


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Manage user email for skill attribution.")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--resolve-and-apply", action="store_true")
    group.add_argument("--set-from-env", action="store_true")
    group.add_argument("--apply-all-pending", action="store_true")
    group.add_argument("--show", action="store_true")
    parser.add_argument("--non-interactive", action="store_true")

    args = parser.parse_args()

    if args.resolve_and_apply:
        return _resolve_and_apply(args.non_interactive)
    elif args.set_from_env:
        return _set_from_env()
    elif args.apply_all_pending:
        return _apply_all_pending()
    elif args.show:
        return _show()
    return 0


if __name__ == "__main__":
    sys.exit(main())
