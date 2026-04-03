"""Skill installer — download pre-built ZIPs from presigned S3 URLs.

Downloads skill ZIP archives and extracts them to {data_dir}/skills/{name}/.
Always overwrites (clean + extract) to ensure latest content.
"""

from __future__ import annotations

import io
import logging
import os
import shutil
import zipfile
from pathlib import Path
from urllib.parse import urlparse

import httpx

from mega_code.client.api.protocol import SkillRefItem
from mega_code.client.dirs import data_dir

logger = logging.getLogger(__name__)

_MAX_DOWNLOAD_SIZE = min(int(os.environ.get("SKILL_MAX_DOWNLOAD_MB", "100")), 500) * 1024 * 1024
_DOWNLOAD_TIMEOUT = min(int(os.environ.get("SKILL_DOWNLOAD_TIMEOUT", "120")), 300)


def skills_dir() -> Path:
    """Skills directory: {data_dir}/skills/."""
    d = data_dir() / "skills"
    d.mkdir(parents=True, exist_ok=True)
    return d


def install_skill(skill: SkillRefItem) -> str:
    """Download ZIP from presigned URL, clean old folder, extract fresh.

    Returns: "installed" | "skipped" (no url).
    """
    if not skill.url:
        return "skipped"

    # Validate URL scheme (SSRF protection)
    parsed = urlparse(skill.url)
    if parsed.scheme != "https":
        raise ValueError(f"Refusing non-HTTPS URL: {skill.url[:80]}")

    # Validate skill name (path traversal protection)
    sd = skills_dir()
    dest = (sd / skill.name).resolve()
    if not dest.is_relative_to(sd.resolve()):
        raise ValueError(f"Invalid skill name: {skill.name}")

    # Clean old installation to avoid stale files after update
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True, exist_ok=True)

    # Download + extract (HTTPS-only, no redirects to prevent SSRF)
    resp = httpx.get(skill.url, timeout=_DOWNLOAD_TIMEOUT, follow_redirects=False)
    resp.raise_for_status()
    if len(resp.content) > _MAX_DOWNLOAD_SIZE:
        raise ValueError(f"Skill ZIP exceeds {_MAX_DOWNLOAD_SIZE // 1024 // 1024}MB limit")

    with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
        for info in zf.infolist():
            target = (dest / info.filename).resolve()
            if not target.is_relative_to(dest.resolve()):
                raise ValueError(f"Zip slip detected: {info.filename}")
        zf.extractall(dest)

    logger.info("Installed skill %s → %s", skill.name, dest)
    return "installed"


def install_skills(skills: list[SkillRefItem]) -> dict[str, str]:
    """Install all skills from presigned URLs. Returns {name: status}."""
    results: dict[str, str] = {}
    for skill in skills:
        try:
            results[skill.name] = install_skill(skill)
        except (OSError, ValueError, httpx.HTTPError, zipfile.BadZipFile) as e:
            logger.warning("Failed to install skill %s: %s", skill.name, e)
            results[skill.name] = "failed"
    return results


def list_installed_skills() -> list[str]:
    """List skill names installed in the skills directory."""
    sd = skills_dir()
    return sorted(d.name for d in sd.iterdir() if d.is_dir() and (d / "SKILL.md").exists())


def get_skill_path(skill_name: str) -> Path | None:
    """Get path to an installed skill, or None if not found."""
    p = skills_dir() / skill_name
    return p if (p / "SKILL.md").exists() else None
