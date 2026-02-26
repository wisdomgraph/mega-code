#!/usr/bin/env python3
"""
MEGA-Code CLI - Manage mega-code plugin for Claude Code.

Usage:
    mega-code status
    mega-code upload [--project <path>]
    mega-code configure [--user-id <id>] [--api-key <key>] [--server-url <url>]
    mega-code profile [--language <lang>] [--level <level>] [--style <style>]

Installation is handled via Claude Code marketplace plugin.
"""

import argparse
import json
import os
import platform
import sys
import tarfile
import tempfile
from datetime import datetime
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from mega_code.client.profile import get_profile_path

# Placeholder values that should be replaced by users
PLACEHOLDER_VALUES = frozenset({"YOUR_NAME", "YOUR_ID"})


# ═══════════════════════════════════════════════════════════════════
# Path helpers
# ═══════════════════════════════════════════════════════════════════


def _get_mega_code_data_root() -> Path:
    """Get the base data directory for mega-code."""
    return Path.home() / ".local" / "mega-code"


def get_projects_data_dir() -> Path:
    """Get the data directory for project data storage."""
    return _get_mega_code_data_root() / "projects"


def _get_plugin_root() -> Path | None:
    """Get the plugin root directory.

    Priority:
    1. CLAUDE_PLUGIN_ROOT environment variable (set by Claude Code)
    2. Breadcrumb file ~/.local/mega-code/plugin-root
    3. None if not found
    """
    # Check env var first (available inside hook execution)
    env_root = os.environ.get("CLAUDE_PLUGIN_ROOT")
    if env_root:
        return Path(env_root)

    # Check breadcrumb written by session-start.sh
    breadcrumb = _get_mega_code_data_root() / "plugin-root"
    if breadcrumb.exists():
        root = breadcrumb.read_text().strip()
        if root and Path(root).is_dir():
            return Path(root)

    return None


def get_env_path() -> Path:
    """Get the path to the .env file.

    Priority:
    1. Plugin root (marketplace install) has .env
    2. Fallback to script's repo root (dev mode)
    """
    plugin_root = _get_plugin_root()
    if plugin_root:
        env_path = plugin_root / ".env"
        if env_path.exists():
            return env_path

    # Fallback to current script's repo root
    source_dir = Path(__file__).parent.parent.resolve()
    return source_dir / ".env"


# ═══════════════════════════════════════════════════════════════════
# Env file helpers (used by configure/upload)
# ═══════════════════════════════════════════════════════════════════


def load_env_file(env_path: Path) -> dict[str, str]:
    """Load environment variables from .env file."""
    env_vars = {}
    if not env_path.exists():
        return env_vars

    with open(env_path) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, _, value = line.partition("=")
                key = key.strip()
                value = value.strip()
                # Remove quotes if present
                if len(value) >= 2 and value[0] in ('"', "'") and value[-1] == value[0]:
                    value = value[1:-1]
                env_vars[key] = value
    return env_vars


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
# Commands
# ═══════════════════════════════════════════════════════════════════


def cmd_status(args: argparse.Namespace) -> int:
    """Check mega-code installation status."""
    data_root = _get_mega_code_data_root()
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


def cmd_upload(args: argparse.Namespace) -> int:
    """Upload project data to Bitbucket Downloads."""
    env_path = get_env_path()
    data_dir = get_projects_data_dir()

    print("Uploading MEGA-Code data...")

    # Load credentials
    print("\nLoading configuration...")
    env_vars = load_env_file(env_path)
    user_id = env_vars.get("MEGA_CODE_USER_ID")
    token = env_vars.get("BITBUCKET_ACCESS_TOKEN")

    if not user_id:
        print("Error: MEGA_CODE_USER_ID not found in .env")
        print(f"   Please add it to: {env_path}")
        return 1

    if user_id in PLACEHOLDER_VALUES:
        print(f"Error: MEGA_CODE_USER_ID is set to placeholder value: {user_id}")
        print("   Please set your actual user ID:")
        print("   mega-code configure --user-id <your-name>")
        return 1

    if not token:
        print("Error: BITBUCKET_ACCESS_TOKEN not found in .env")
        print(f"   Please add it to: {env_path}")
        return 1

    print(f"   User ID: {user_id}")
    print("   Credentials loaded")

    # Determine what to upload
    if args.project:
        project_path = Path(args.project).resolve()
        project_name = project_path.name
        project_data_dir = None
        if data_dir.exists():
            for d in data_dir.iterdir():
                if d.is_dir() and d.name.startswith(project_name):
                    project_data_dir = d
                    break
        if not project_data_dir:
            print(f"Error: No data found for project: {project_name}")
            print(f"   Searched in: {data_dir}")
            if data_dir.exists():
                available = [
                    d.name for d in data_dir.iterdir() if d.is_dir() and not d.name.startswith(".")
                ]
                if available:
                    print(f"   Available: {', '.join(sorted(available)[:10])}")
            return 1
        upload_dir = project_data_dir
        archive_suffix = f"_{project_name}"
    else:
        if not data_dir.exists():
            print(f"Error: No data directory found: {data_dir}")
            return 1
        upload_dir = data_dir
        archive_suffix = ""

    file_count = sum(1 for _ in upload_dir.rglob("*") if _.is_file())
    print("\nPreparing archive...")
    print(f"   Source: {upload_dir}")
    print(f"   Files: {file_count}")

    if file_count == 0:
        print("Error: No files to upload")
        return 1

    timestamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    hostname = platform.node().split(".")[0]
    archive_name = f"{user_id}_{hostname}_{timestamp}{archive_suffix}.tar.gz"

    # Collect additional data dirs to include in the archive
    data_root = _get_mega_code_data_root() / "data"
    extra_dirs: list[tuple[Path, str]] = []  # (source_path, arcname)
    if args.project and project_data_dir:
        # Project-scoped: include matching feedback subdir
        project_id = project_data_dir.name
        feedback_dir = data_root / "feedback" / project_id
        if feedback_dir.exists():
            extra_dirs.append((feedback_dir, f"data/feedback/{project_id}"))
    else:
        # Full upload: include entire data dir (feedback, pending-skills, etc.)
        if data_root.exists():
            for sub in sorted(data_root.iterdir()):
                if sub.is_dir():
                    extra_dirs.append((sub, f"data/{sub.name}"))

    extra_file_count = sum(sum(1 for _ in d.rglob("*") if _.is_file()) for d, _ in extra_dirs)

    with tempfile.TemporaryDirectory() as tmp_dir:
        archive_path = Path(tmp_dir) / archive_name

        print(f"   Creating: {archive_name}")
        with tarfile.open(archive_path, "w:gz") as tar:
            tar.add(upload_dir, arcname=upload_dir.name)
            for src_dir, arcname in extra_dirs:
                tar.add(src_dir, arcname=arcname)
            if extra_file_count > 0:
                print(f"   Including data: {extra_file_count} files from {len(extra_dirs)} dirs")

        archive_size = archive_path.stat().st_size
        print(f"   Size: {archive_size / 1024 / 1024:.2f} MB")

        print("\nUploading to Bitbucket...")
        upload_url = "https://api.bitbucket.org/2.0/repositories/mindai/mega-code/downloads"

        with open(archive_path, "rb") as f:
            file_content = f.read()

        boundary = "----WebKitFormBoundary7MA4YWxkTrZu0gW"
        body = (
            (
                f"--{boundary}\r\n"
                f'Content-Disposition: form-data; name="files"; filename="{archive_name}"\r\n'
                f"Content-Type: application/gzip\r\n\r\n"
            ).encode()
            + file_content
            + f"\r\n--{boundary}--\r\n".encode()
        )

        req = Request(
            upload_url,
            data=body,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": f"multipart/form-data; boundary={boundary}",
            },
            method="POST",
        )

        try:
            with urlopen(req, timeout=300) as response:
                if response.status in (200, 201):
                    print("   Upload successful!")
                else:
                    print(f"   Unexpected status: {response.status}")
        except HTTPError as e:
            print(f"Error: Upload failed: {e.code} {e.reason}")
            try:
                error_body = e.read().decode()
                print(f"   Response: {error_body[:500]}")
            except Exception:
                pass
            return 1
        except URLError as e:
            print(f"Error: Upload failed: {e.reason}")
            return 1

    print("\n" + "=" * 60)
    print("Data uploaded successfully!")
    print("=" * 60)
    print(f"\nArchive: {archive_name}")
    print("   View at: https://bitbucket.org/mindai/mega-code/downloads/")

    return 0


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
        ("bitbucket_token", "BITBUCKET_ACCESS_TOKEN", True),
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
    from mega_code.client.api.protocol import UserProfile
    from mega_code.client.profile import load_profile, save_profile

    # Reset
    if args.reset:
        profile_path = get_profile_path()
        if profile_path.exists():
            profile_path.unlink()
            print("Profile reset.")
        else:
            print("No profile to reset.")
        return 0

    has_updates = any(x is not None for x in [args.language, args.level, args.style])

    if not has_updates:
        # Show current profile
        user_profile = load_profile()
        if all(v is None for v in [user_profile.language, user_profile.level, user_profile.style]):
            print("No profile set.")
            print("\nSet your profile with:")
            print("  mega-code profile --language English --level Expert --style Concise")
            return 0

        print("Current profile:")
        for key, value in user_profile.model_dump(by_alias=True).items():
            print(f"   {key}: {value}")
        return 0

    # Load existing, merge updates, save
    user_profile = load_profile()
    data = user_profile.model_dump(by_alias=True)

    if args.language is not None:
        data["language"] = args.language
    if args.level is not None:
        data["level"] = args.level
    if args.style is not None:
        data["style"] = args.style

    updated_profile = UserProfile(**data)
    save_profile(updated_profile.model_dump(by_alias=True))

    print("Profile updated:")
    for key, value in updated_profile.model_dump(by_alias=True).items():
        print(f"   {key}: {value}")
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

    # Upload command
    upload_parser = subparsers.add_parser("upload", help="Upload data to Bitbucket Downloads")
    upload_parser.add_argument(
        "--project", "-p", type=str, metavar="PATH", help="Upload only specific project data"
    )

    # Configure command
    configure_parser = subparsers.add_parser("configure", help="Configure mega-code settings")
    configure_parser.add_argument("--user-id", "-u", type=str, help="Set your user identifier")
    configure_parser.add_argument(
        "--bitbucket-token", "-t", type=str, help="Set Bitbucket access token"
    )
    configure_parser.add_argument("--api-key", "-k", type=str, help="Set MEGA-Code API key")
    configure_parser.add_argument(
        "--server-url", type=str, help="Set MEGA-Code server URL (e.g. http://localhost:8000)"
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
        help="mega-service base URL (overrides MEGA_SERVICE_URL)",
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

    args = parser.parse_args()

    match args.command:
        case None:
            parser.print_help()
            return 1
        case "status":
            return cmd_status(args)
        case "upload":
            return cmd_upload(args)
        case "configure":
            return cmd_configure(args)
        case "login":
            return cmd_login(args)
        case "profile":
            return cmd_profile(args)
        case _:
            return 0


if __name__ == "__main__":
    sys.exit(main())
