"""Publish mega-code-oss to GitHub via Copybara.

Patches copy.bara.sky in-place with the commit message, runs Copybara,
then restores the original placeholder. The file is excluded from both
git (via .gitignore) and Copybara (via _EXCLUDE), so in-place patching
is safe.

Usage:
    python scripts/publish-oss.py --message "Release v0.1.30: conflict handling"
    python scripts/publish-oss.py --init --message "Initial OSS release"
    python scripts/publish-oss.py --dry-run --message "Test message"
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
CONFIG_FILE = REPO_ROOT / "copy.bara.sky"
PLACEHOLDER = 'metadata.replace_message("PLACEHOLDER: update before publish")'
COPYBARA_IMAGE = "sharelatex/copybara:latest"


def patch_config(message: str) -> str:
    """Patch copy.bara.sky in-place. Returns original content for restore."""
    original = CONFIG_FILE.read_text()
    escaped = message.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
    patched = original.replace(PLACEHOLDER, f'metadata.replace_message("{escaped}")')
    if patched == original:
        print("ERROR: placeholder not found in copy.bara.sky", file=sys.stderr)
        sys.exit(1)
    CONFIG_FILE.write_text(patched)
    return original


def restore_config(original: str) -> None:
    """Restore copy.bara.sky to its original content."""
    CONFIG_FILE.write_text(original)


def run_copybara(*, init: bool = False, dry_run: bool = False) -> int:
    """Run Copybara via Docker and return the exit code."""
    ssh_dir = Path.home() / ".ssh"

    # Use a temp dir under REPO_ROOT so Docker on macOS can see it
    # (/Users is shared with the Docker VM, but /tmp and /var/folders are not).
    copybara_tmp = REPO_ROOT / ".copybara-tmp"
    copybara_tmp.mkdir(exist_ok=True)

    # Build a temporary SSH config that forces id_ed25519_mind_ai for GitHub
    # and routes through ssh.github.com:443. Write it to a temp file on the host,
    # then mount it as /root/.ssh/config in the container.
    ssh_config_content = (
        "Host github.com\n"
        "    Hostname ssh.github.com\n"
        "    Port 443\n"
        "    IdentityFile /root/.ssh/id_ed25519_mind_ai\n"
        "    IdentitiesOnly yes\n"
        "Host bitbucket.org\n"
        "    IdentityFile /root/.ssh/id_ed25519_mind_ai\n"
        "    IdentitiesOnly yes\n"
    )
    tmp_ssh_config = Path(tempfile.mktemp(prefix="ssh-config-", dir=str(REPO_ROOT / ".copybara-tmp")))
    tmp_ssh_config.write_text(ssh_config_content)

    # Pre-build known_hosts on the host (where network works reliably)
    tmp_known_hosts = Path(tempfile.mktemp(prefix="known-hosts-", dir=str(REPO_ROOT / ".copybara-tmp")))
    subprocess.run(
        "ssh-keyscan -p 443 ssh.github.com 2>/dev/null; ssh-keyscan bitbucket.org 2>/dev/null",
        shell=True, capture_output=True, text=True,
        check=False,
    )
    # Combine host's existing known_hosts with freshly scanned GitHub keys.
    # The host's known_hosts already has bitbucket.org; we add ssh.github.com:443.
    existing = (ssh_dir / "known_hosts").read_text() if (ssh_dir / "known_hosts").exists() else ""
    scan = subprocess.run(
        "ssh-keyscan -p 443 ssh.github.com 2>/dev/null",
        shell=True, capture_output=True, text=True, check=False,
    )
    tmp_known_hosts.write_text(existing + "\n" + scan.stdout)

    # Mount SSH as a self-contained temp directory (avoids conflicts
    # from overlaying files on top of a bind-mounted directory).
    tmp_ssh_dir = Path(tempfile.mkdtemp(prefix="ssh-dir-", dir=str(REPO_ROOT / ".copybara-tmp")))
    shutil.copy2(ssh_dir / "id_ed25519_mind_ai", tmp_ssh_dir / "id_ed25519_mind_ai")
    (tmp_ssh_dir / "id_ed25519_mind_ai").chmod(0o600)
    shutil.copy2(tmp_ssh_config, tmp_ssh_dir / "config")
    shutil.copy2(tmp_known_hosts, tmp_ssh_dir / "known_hosts")

    volumes = [
        "-v", f"{REPO_ROOT}:/usr/src/app",
        "-v", f"{tmp_ssh_dir}:/root/.ssh",
    ]

    flags = ["--ignore-noop"]
    if init:
        flags += ["--force", "--init-history"]
    if dry_run:
        flags.append("--dry-run")

    shell_cmd = (
        "java -jar /opt/copybara/copybara_deploy.jar "
        "--git-committer-name 'mega-code-team' "
        "--git-committer-email 'support@megacode.ai' "
        + " ".join(flags)
        + " migrate /usr/src/app/copy.bara.sky"
    )
    cmd = [
        "docker", "run", "--rm",
        *volumes,
        COPYBARA_IMAGE,
        "bash", "-c", shell_cmd,
    ]

    print(f"Running: {' '.join(cmd)}")
    try:
        result = subprocess.run(cmd)
    finally:
        shutil.rmtree(tmp_ssh_dir, ignore_errors=True)
        tmp_ssh_config.unlink(missing_ok=True)
        tmp_known_hosts.unlink(missing_ok=True)
    return result.returncode


def main() -> None:
    parser = argparse.ArgumentParser(description="Publish mega-code-oss to GitHub via Copybara")
    parser.add_argument("--message", "-m", required=True, help="Commit message for the GitHub push")
    parser.add_argument("--init", action="store_true", help="First-time bootstrap (--init-history)")
    parser.add_argument("--dry-run", action="store_true", help="Dry run (no actual push)")
    args = parser.parse_args()

    if not CONFIG_FILE.exists():
        print(f"ERROR: {CONFIG_FILE} not found", file=sys.stderr)
        sys.exit(1)

    original = patch_config(args.message)
    try:
        rc = run_copybara(init=args.init, dry_run=args.dry_run)
    finally:
        restore_config(original)
        print("Restored copy.bara.sky to placeholder.")

    if rc == 0:
        print("Copybara push succeeded.")
    else:
        print(f"Copybara exited with code {rc}", file=sys.stderr)
    sys.exit(rc)


if __name__ == "__main__":
    main()
