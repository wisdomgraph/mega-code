#!/usr/bin/env python3
"""
MEGA-Code CLI - Manage mega-code plugin for Claude Code.

Usage:
    mega-code status
    mega-code configure [--user-id <id>] [--api-key <key>] [--server-url <url>]
    mega-code login [--provider github|google]
    mega-code profile [--language <lang>] [--level <level>] [--style <style>]

Installation is handled via Claude Code marketplace plugin.
"""

import argparse
import json
import os
import sys
from pathlib import Path

from mega_code.client.dirs import data_dir as get_data_dir
from mega_code.client.profile import get_profile_path

# ═══════════════════════════════════════════════════════════════════
# Path helpers
# ═══════════════════════════════════════════════════════════════════


def get_projects_data_dir() -> Path:
    """Get the data directory for project data storage."""
    return get_data_dir() / "projects"


def _get_plugin_root() -> Path | None:
    """Get the plugin root directory.

    Priority:
    1. CLAUDE_PLUGIN_ROOT environment variable (set by Claude Code)
    2. Breadcrumb file <data_dir>/plugin-root
    3. None if not found
    """
    # Check env var first (available inside hook execution)
    env_root = os.environ.get("CLAUDE_PLUGIN_ROOT")
    if env_root:
        return Path(env_root)

    # Check breadcrumb written by session-start.sh
    breadcrumb = get_data_dir() / "plugin-root"
    if breadcrumb.exists():
        root = breadcrumb.read_text().strip()
        if root and Path(root).is_dir():
            return Path(root)

    return None


def get_env_path() -> Path:
    """Get the path to the stable .env credential file."""
    return get_data_dir() / ".env"


# ═══════════════════════════════════════════════════════════════════
# Env file helpers (used by configure)
# ═══════════════════════════════════════════════════════════════════


def load_env_file(env_path: Path) -> dict[str, str]:
    """Load environment variables from .env file using python-dotenv."""
    if not env_path.exists():
        return {}
    from dotenv import dotenv_values

    return {k: v for k, v in dotenv_values(env_path).items() if v is not None}


def save_env_file(env_path: Path, env_vars: dict[str, str]) -> None:
    """Save environment variables to .env file."""
    existing_lines = []
    existing_keys = set()

    if env_path.exists():
        with open(env_path) as f:
            for line in f:
                stripped = line.strip()
                if stripped and not stripped.startswith("#") and "=" in stripped:
                    key = stripped.split("=", 1)[0].strip()
                    if key in env_vars:
                        existing_lines.append(f"{key}={env_vars[key]}\n")
                        existing_keys.add(key)
                    else:
                        existing_lines.append(line)
                else:
                    existing_lines.append(line)

    for key, value in env_vars.items():
        if key not in existing_keys:
            existing_lines.append(f"{key}={value}\n")

    with open(env_path, "w") as f:
        f.writelines(existing_lines)

    env_path.chmod(0o600)


# ═══════════════════════════════════════════════════════════════════
# Shared helpers
# ═══════════════════════════════════════════════════════════════════


def _load_env() -> None:
    """Load .env credentials into os.environ (setdefault, won't override)."""
    for key, value in load_env_file(get_env_path()).items():
        os.environ.setdefault(key, value)


# ═══════════════════════════════════════════════════════════════════
# Commands
# ═══════════════════════════════════════════════════════════════════


def cmd_status(args: argparse.Namespace) -> int:
    """Check mega-code installation status."""
    data_root = get_data_dir()
    data_dir = get_projects_data_dir()
    plugin_root = _get_plugin_root()

    print("MEGA-Code Status")
    print("=" * 60)

    # Check plugin installation
    print("\nPlugin:")
    if plugin_root:
        print(f"   Root: {plugin_root}")
        print("   Status: Installed (marketplace)")
    else:
        print("   Status: Not detected")
        print("   Install via: /install-plugin mega-code@wisdomgraph-mega-code")

    # Check profile
    profile_path = data_root / "profile.json"
    print(f"\nProfile: {profile_path}")
    if profile_path.exists():
        try:
            with open(profile_path) as f:
                profile = json.load(f)
            api_key = profile.get("api_key", "")
            server_url = profile.get("server_url", "")
            client_mode = profile.get("client_mode", "local")
            print(f"   API Key: {'configured' if api_key else 'not set'}")
            print(f"   Server URL: {server_url or 'not set'}")
            print(f"   Client Mode: {client_mode}")
        except (json.JSONDecodeError, OSError):
            print("   Status: Error reading profile")
    else:
        print("   Status: Not initialized (will be created on first session)")

    # Check Python environment
    if plugin_root:
        venv_path = plugin_root / ".venv"
        print(f"\nEnvironment: {venv_path}")
        print(f"   Status: {'Ready' if venv_path.is_dir() else 'Not synced (will auto-sync)'}")

    # Check data — count sessions across all project folders
    print(f"\nData: {data_dir}")
    if data_dir.exists():
        session_count = sum(
            1
            for project_folder in data_dir.iterdir()
            if project_folder.is_dir()
            for session_dir in project_folder.iterdir()
            if session_dir.is_dir()
        )
        project_count = sum(1 for d in data_dir.iterdir() if d.is_dir())
        print(f"   Status: {session_count} sessions across {project_count} projects")
    else:
        print("   Status: No data yet")

    # Overall
    print("\n" + "=" * 60)
    if plugin_root:
        print("MEGA-Code is installed and ready")
        return 0
    else:
        print("MEGA-Code plugin not detected")
        return 1


def cmd_configure(args: argparse.Namespace) -> int:
    """Configure MEGA-Code settings."""
    env_path = get_env_path()

    print("Configuring MEGA-Code...")
    print(f"   Config file: {env_path}")

    env_vars = load_env_file(env_path)
    updated = []

    # (arg_name, env_var_key, is_secret)
    _CONFIG_FIELDS = [
        ("user_id", "MEGA_CODE_USER_ID", False),
        ("api_key", "MEGA_CODE_API_KEY", True),
        ("server_url", "MEGA_CODE_SERVER_URL", False),
        ("client_mode", "MEGA_CODE_CLIENT_MODE", False),
        ("openai_api_key", "OPENAI_API_KEY", True),
        ("gemini_api_key", "GEMINI_API_KEY", True),
    ]

    for attr, env_key, is_secret in _CONFIG_FIELDS:
        value = getattr(args, attr, None)
        if value:
            env_vars[env_key] = value
            updated.append(f"{env_key}={'***' if is_secret else value}")

    if not updated:
        print("\nCurrent configuration:")
        for key, value in env_vars.items():
            if "TOKEN" in key or "KEY" in key:
                print(f"   {key}=***")
            else:
                print(f"   {key}={value}")
        print("\nUse --api-key, --server-url, --client-mode, etc. to update values")
        return 0

    save_env_file(env_path, env_vars)

    print("\nConfiguration updated:")
    for item in updated:
        print(f"   {item}")

    return 0


def cmd_login(args: argparse.Namespace) -> int:
    """Login to MEGA-Code via OAuth."""
    from mega_code.client.login import run_login

    kwargs: dict = {"base_url": args.url}
    if args.provider is not None:
        kwargs["provider"] = args.provider
    return run_login(**kwargs)


def cmd_profile(args: argparse.Namespace) -> int:
    """View or update user profile."""
    from mega_code.client.api import create_client
    from mega_code.client.api.protocol import UserProfile

    _load_env()
    client = create_client()

    # Reset
    if args.reset:
        # Clear local file
        profile_path = get_profile_path()
        profile_existed = profile_path.exists()
        if profile_existed:
            profile_path.unlink()
        # Sync to remote server only — local mode handles reset via file deletion above
        from mega_code.client.api.remote import MegaCodeRemote

        if isinstance(client, MegaCodeRemote):
            client.save_profile(profile=UserProfile())
        if profile_existed:
            print("Profile reset.")
        else:
            print("No profile to reset.")
        return 0

    has_updates = any(x is not None for x in [args.language, args.level, args.style])

    if not has_updates:
        # Show current profile via client (remote mode reads from mega-service DB)
        user_profile = client.load_profile()
        if all(v is None for v in [user_profile.language, user_profile.level, user_profile.style]):
            print("No profile set.")
            print("\nSet your profile with:")
            print("  mega-code profile --language English --level Expert --style Concise")
            return 0

        print("Current profile:")
        for key, value in user_profile.model_dump(by_alias=True).items():
            print(f"   {key}: {value}")
        return 0

    # Load existing from authoritative source, merge updates, save
    user_profile = client.load_profile()
    data = user_profile.model_dump(by_alias=True)

    if args.language is not None:
        data["language"] = args.language
    if args.level is not None:
        data["level"] = args.level
    if args.style is not None:
        data["style"] = args.style

    updated_profile = UserProfile(**data)
    client.save_profile(profile=updated_profile)

    print("Profile updated:")
    for key, value in updated_profile.model_dump(by_alias=True).items():
        print(f"   {key}: {value}")
    return 0


# ═══════════════════════════════════════════════════════════════════
# Pipeline control commands
# ═══════════════════════════════════════════════════════════════════


def cmd_pipeline_status(args: argparse.Namespace) -> int:
    """Show active pipeline runs."""
    from mega_code.client.api import create_client
    from mega_code.client.api.remote import MegaCodeRemote

    _load_env()
    client = create_client()
    if not isinstance(client, MegaCodeRemote):
        print("Pipeline status requires remote mode.")
        return 0

    try:
        result = client.get_active_pipelines()
    except Exception as e:
        print(f"Could not reach server: {e}")
        return 1

    if not result.active:
        print("No active pipeline runs.")
        return 0

    print("Active pipeline runs:\n")
    for i, run in enumerate(result.runs, 1):
        progress_str = ""
        if run.progress:
            phase = run.progress.get("current_phase", "")
            processed = run.progress.get("sessions_processed", "?")
            total = run.progress.get("sessions_total", "?")
            if phase:
                progress_str = f" | Phase: {phase} ({processed}/{total})"
        started_str = ""
        if run.started_at:
            started_str = f" | Started: {run.started_at}"
        line = f"  [{i}] {run.run_id} | project: {run.project_id}"
        line += f" | {run.status}{progress_str}{started_str}"
        print(line)

    return 0


def cmd_pipeline_stop(args: argparse.Namespace) -> int:
    """Stop a running pipeline by run_id."""
    from mega_code.client.api import create_client
    from mega_code.client.api.remote import MegaCodeRemote

    _load_env()
    client = create_client()
    if not isinstance(client, MegaCodeRemote):
        print(json.dumps({"error": "pipeline-stop requires remote mode"}))
        return 1

    try:
        result = client.stop_pipeline(run_id=args.run_id)
    except Exception as e:
        print(json.dumps({"error": str(e)}))
        return 1
    print(json.dumps(result.model_dump(), default=str))
    return 0


# ═══════════════════════════════════════════════════════════════════
# Main entry point
# ═══════════════════════════════════════════════════════════════════


def main():
    """Main entry point for the CLI."""
    parser = argparse.ArgumentParser(
        prog="mega-code",
        description="MEGA-Code CLI - Manage mega-code plugin for Claude Code",
    )

    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # Status command
    subparsers.add_parser("status", help="Check installation status")

    # Configure command
    configure_parser = subparsers.add_parser("configure", help="Configure mega-code settings")
    configure_parser.add_argument("--user-id", "-u", type=str, help="Set your user identifier")
    configure_parser.add_argument("--api-key", "-k", type=str, help="Set MEGA-Code API key")
    configure_parser.add_argument(
        "--server-url",
        type=str,
        help="Set MEGA-Code server URL (e.g. http://localhost:8000)",
    )
    configure_parser.add_argument(
        "--client-mode",
        type=str,
        choices=["local", "remote"],
        help="Set client mode (local or remote)",
    )
    configure_parser.add_argument("--openai-api-key", type=str, help="Set OpenAI API key")
    configure_parser.add_argument("--gemini-api-key", type=str, help="Set Gemini API key")

    # Login command (default provider imported from login module)
    login_parser = subparsers.add_parser("login", help="Sign in via OAuth to get an API key")
    login_parser.add_argument(
        "--provider",
        choices=["github", "google"],
        default=None,  # defers to login.run_login default
        help="OAuth provider (default: google)",
    )
    login_parser.add_argument(
        "--url",
        type=str,
        default=None,
        help="mega-service API URL (overrides MEGA_CODE_SERVER_URL-derived URL)",
    )

    # Profile command
    profile_parser = subparsers.add_parser("profile", help="View or update your developer profile")
    profile_parser.add_argument(
        "--language",
        "-l",
        type=str,
        help="Preferred communication language (e.g. 'English', 'Thai')",
    )
    profile_parser.add_argument(
        "--level",
        type=str,
        choices=["Beginner", "Intermediate", "Expert"],
        help="Experience level",
    )
    profile_parser.add_argument(
        "--style",
        type=str,
        choices=["Mentor", "Formal", "Concise"],
        help="Preferred teaching style",
    )
    profile_parser.add_argument("--reset", action="store_true", help="Reset profile to defaults")

    # Pipeline status command
    subparsers.add_parser("pipeline-status", help="Show active pipeline runs")

    # Pipeline stop command
    pstop_parser = subparsers.add_parser("pipeline-stop", help="Stop a running pipeline")
    pstop_parser.add_argument("--run-id", required=True, help="Run ID to stop")

    args = parser.parse_args()

    match args.command:
        case None:
            parser.print_help()
            return 1
        case "status":
            return cmd_status(args)
        case "configure":
            return cmd_configure(args)
        case "login":
            return cmd_login(args)
        case "profile":
            return cmd_profile(args)
        case "pipeline-status":
            return cmd_pipeline_status(args)
        case "pipeline-stop":
            return cmd_pipeline_stop(args)
        case _:
            return 0


if __name__ == "__main__":
    sys.exit(main())
