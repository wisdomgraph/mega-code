"""Installer tests — sha256 verification (incl. null branch), frontmatter, install."""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path

import httpx
import pytest

from mega_code.client.remote_enhance.installer import (
    InstallerError,
    download_artifact,
    install_to,
    staging_root,
    validate_frontmatter,
)

_VALID_SKILL_MD = b"""---
description: Example
metadata:
  tags: [demo]
  roi: [smoke]
---
# Body
"""


@pytest.fixture
def isolated_data_dir(tmp_path, monkeypatch) -> Path:
    """Redirect data_dir() to a tmp tree so installs don't touch the user's home."""
    monkeypatch.setenv("MEGA_CODE_DATA_DIR", str(tmp_path))
    return tmp_path


def _http_get_factory(responses: dict[str, bytes], *, fail: dict[str, int] | None = None):
    fail = fail or {}

    def http_get(url: str) -> httpx.Response:
        request = httpx.Request("GET", url)
        if url in fail:
            return httpx.Response(fail[url], request=request, content=b"upstream-error")
        return httpx.Response(200, request=request, content=responses[url])

    return http_get


# ---------------------------------------------------------------------------
# Download + sha256 verify
# ---------------------------------------------------------------------------


def test_download_with_matching_sha256_writes_files_to_staging(isolated_data_dir):
    skill = _VALID_SKILL_MD
    sha = hashlib.sha256(skill).hexdigest()
    artifact = {
        "files": [
            {
                "relpath": "SKILL.md",
                "url": "https://test-bucket.s3.amazonaws.com/SKILL.md",
                "sha256": sha,
            },
            {
                "relpath": "scripts/foo.py",
                "url": "https://test-bucket.s3.amazonaws.com/scripts/foo.py",
                "sha256": hashlib.sha256(b"print(1)\n").hexdigest(),
            },
        ]
    }
    http_get = _http_get_factory(
        {
            "https://test-bucket.s3.amazonaws.com/SKILL.md": skill,
            "https://test-bucket.s3.amazonaws.com/scripts/foo.py": b"print(1)\n",
        }
    )
    staging = download_artifact(artifact, job_id="job-1", http_get=http_get)
    assert staging == staging_root("job-1")
    assert (staging / "SKILL.md").read_bytes() == skill
    assert (staging / "scripts/foo.py").read_bytes() == b"print(1)\n"


def test_download_sha256_mismatch_raises_exit4_code(isolated_data_dir):
    artifact = {
        "files": [
            {
                "relpath": "SKILL.md",
                "url": "https://test-bucket.s3.amazonaws.com/SKILL.md",
                "sha256": "wrong",
            }
        ]
    }
    http_get = _http_get_factory({"https://test-bucket.s3.amazonaws.com/SKILL.md": _VALID_SKILL_MD})
    with pytest.raises(InstallerError) as exc:
        download_artifact(artifact, job_id="job-1", http_get=http_get)
    assert exc.value.code == "sha256_mismatch"


def test_download_null_sha256_proceeds_with_warning(isolated_data_dir, caplog):
    """Per design plan §1B "Null-sha256 branch": null sha256 → log + proceed."""
    artifact = {
        "files": [
            {
                "relpath": "SKILL.md",
                "url": "https://test-bucket.s3.amazonaws.com/SKILL.md",
                "sha256": None,
            },
            {
                "relpath": "data.bin",
                "url": "https://test-bucket.s3.amazonaws.com/data.bin",
                "sha256": None,
            },
        ]
    }
    http_get = _http_get_factory(
        {
            "https://test-bucket.s3.amazonaws.com/SKILL.md": _VALID_SKILL_MD,
            "https://test-bucket.s3.amazonaws.com/data.bin": b"raw",
        }
    )
    with caplog.at_level(logging.WARNING):
        staging = download_artifact(artifact, job_id="job-1", http_get=http_get)
    assert (staging / "data.bin").read_bytes() == b"raw"
    warnings = [r for r in caplog.records if "skipping integrity check" in r.message]
    assert len(warnings) == 2  # one per null entry


def test_download_failed_request_raises(isolated_data_dir):
    artifact = {
        "files": [
            {
                "relpath": "SKILL.md",
                "url": "https://test-bucket.s3.amazonaws.com/SKILL.md",
                "sha256": None,
            }
        ]
    }
    http_get = _http_get_factory(
        {"https://test-bucket.s3.amazonaws.com/SKILL.md": _VALID_SKILL_MD},
        fail={"https://test-bucket.s3.amazonaws.com/SKILL.md": 500},
    )
    with pytest.raises(InstallerError) as exc:
        download_artifact(artifact, job_id="job-1", http_get=http_get)
    assert exc.value.code == "download_failed"


def test_download_empty_artifact_raises(isolated_data_dir):
    with pytest.raises(InstallerError) as exc:
        download_artifact({"files": []}, job_id="job-1", http_get=lambda u: None)
    assert exc.value.code == "missing_artifact"


# ---------------------------------------------------------------------------
# Frontmatter — design doc §5.6 contract
# ---------------------------------------------------------------------------


def test_validate_frontmatter_passes_on_valid_skill(tmp_path):
    (tmp_path / "SKILL.md").write_bytes(_VALID_SKILL_MD)
    validate_frontmatter(tmp_path)  # no raise


def test_validate_frontmatter_missing_metadata_raises(tmp_path):
    (tmp_path / "SKILL.md").write_text("---\ndescription: x\n---\n# body\n", encoding="utf-8")
    with pytest.raises(InstallerError) as exc:
        validate_frontmatter(tmp_path)
    assert exc.value.code == "invalid_frontmatter"


def test_validate_frontmatter_empty_tags_raises(tmp_path):
    (tmp_path / "SKILL.md").write_text(
        "---\nmetadata:\n  tags: []\n  roi: [a]\n---\n# body\n", encoding="utf-8"
    )
    with pytest.raises(InstallerError) as exc:
        validate_frontmatter(tmp_path)
    assert exc.value.code == "invalid_frontmatter"


def test_validate_frontmatter_missing_roi_raises(tmp_path):
    (tmp_path / "SKILL.md").write_text(
        "---\nmetadata:\n  tags: [a]\n---\n# body\n", encoding="utf-8"
    )
    with pytest.raises(InstallerError) as exc:
        validate_frontmatter(tmp_path)
    assert exc.value.code == "invalid_frontmatter"


def test_validate_frontmatter_missing_skill_md_raises(tmp_path):
    with pytest.raises(InstallerError) as exc:
        validate_frontmatter(tmp_path)
    assert exc.value.code == "invalid_frontmatter"


# ---------------------------------------------------------------------------
# install_to — atomic copytree, backup-on-existing
# ---------------------------------------------------------------------------


def test_install_to_fresh_destination(isolated_data_dir, tmp_path):
    staging = tmp_path / "staging"
    staging.mkdir()
    (staging / "SKILL.md").write_bytes(_VALID_SKILL_MD)
    dest = tmp_path / "dest" / "mySkill"
    installed = install_to(staging, destination=dest)
    assert installed == dest
    assert (dest / "SKILL.md").read_bytes() == _VALID_SKILL_MD


def test_install_to_existing_destination_backs_up(isolated_data_dir, tmp_path):
    """Reinstall over an existing skill → existing one is moved under
    ``{data_dir()}/enhancements/{ts}-backup/<skill>`` before copytree."""
    staging = tmp_path / "staging"
    staging.mkdir()
    (staging / "SKILL.md").write_bytes(_VALID_SKILL_MD)
    dest = tmp_path / "dest" / "mySkill"
    dest.mkdir(parents=True)
    (dest / "old.txt").write_text("legacy", encoding="utf-8")

    install_to(staging, destination=dest)
    assert (dest / "SKILL.md").exists()
    assert not (dest / "old.txt").exists()  # the old skill moved out

    # The backup lives somewhere under data_dir()/enhancements/<ts>-backup/<skill>.
    backups = list((isolated_data_dir / "enhancements").glob("*-backup/mySkill/old.txt"))
    assert len(backups) == 1
    assert backups[0].read_text(encoding="utf-8") == "legacy"
