"""Per-file presigned download → sha256 verify → atomic copytree installer.

Walks ``JobResult.artifact.files[]``, downloads each to
``{data_dir()}/enhancements/{job_id}/{relpath}``, verifies SHA256 when
non-null (skips + logs warning on null), validates frontmatter for
``artifact_kind=enhanced``, then atomically replaces the skill at the
install destination — backing up any existing skill there to
``{data_dir()}/enhancements/{ts}-backup/`` first.
"""

from __future__ import annotations

import hashlib
import ipaddress
import logging
import os
import shutil
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx

from mega_code.client.dirs import data_dir
from mega_code.client.skill_utils import parse_frontmatter
from mega_code.client.utils.tracing import set_span_attributes, traced

logger = logging.getLogger(__name__)


class InstallerError(Exception):
    """Raised on integrity / frontmatter / IO failures the installer can name.

    Carries an ``error.code`` from the exit-4 inventory:
    ``sha256_mismatch``, ``invalid_frontmatter``, ``download_failed``,
    ``missing_artifact``, ``invalid_path``. ``__main__`` reads ``code`` to
    compose the exit-4 stdout envelope.
    """

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def staging_root(job_id: str) -> Path:
    """Where downloaded files for one job live until install (or for inspection)."""
    return data_dir() / "enhancements" / job_id


def _backup_root(now: datetime | None = None) -> Path:
    ts = (now or datetime.now(UTC)).strftime("%Y%m%dT%H%M%SZ")
    return data_dir() / "enhancements" / f"{ts}-backup"


# Hard cap on a single artifact file. Mirrors the packager's compressed
# bundle cap (25 MB) so a malicious or compromised gateway cannot OOM the
# CLI by streaming a multi-GB body.
_MAX_ARTIFACT_BYTES = 25 * 1024 * 1024
_DOWNLOAD_CHUNK = 64 * 1024

# Default host suffix allowlist for artifact downloads. The design says
# artifacts are S3 presigned URLs; widen via env if a CDN is added.
_DEFAULT_HOST_SUFFIXES: tuple[str, ...] = (
    ".amazonaws.com",
    ".cloudfront.net",
)


def _allowed_host_suffixes() -> tuple[str, ...]:
    raw = os.environ.get("MEGA_CODE_ENHANCE_DOWNLOAD_HOST_SUFFIXES")
    if not raw:
        return _DEFAULT_HOST_SUFFIXES
    return tuple(s.strip().lower() for s in raw.split(",") if s.strip())


def _validate_artifact_url(url: str) -> None:
    """Reject any URL that is not https + a known artifact host.

    The artifact ``url`` is server-controlled. Without this check, a
    compromised gateway returning ``http://169.254.169.254/...`` (cloud
    IMDS) or ``http://localhost:5432/...`` would coerce the CLI into a
    confused-deputy SSRF probe — even though the download client carries
    no Authorization header, response bodies still land on disk under
    ``~/.claude/skills/``. Enforce: https scheme, hostname not an IP
    literal, hostname matches the configured suffix allowlist.
    """
    parsed = urlparse(url)
    if parsed.scheme != "https":
        raise InstallerError(
            "invalid_path", f"artifact url must be https, got scheme={parsed.scheme!r}"
        )
    host = (parsed.hostname or "").lower()
    if not host:
        raise InstallerError("invalid_path", "artifact url is missing hostname")
    try:
        ipaddress.ip_address(host)
    except ValueError:
        pass  # not an IP literal — good
    else:
        raise InstallerError(
            "invalid_path", f"artifact url hostname must not be an IP literal: {host!r}"
        )
    if host in {"localhost", "metadata.google.internal"}:
        raise InstallerError("invalid_path", f"artifact url host blocked: {host!r}")
    suffixes = _allowed_host_suffixes()
    if not any(host == s.lstrip(".") or host.endswith(s) for s in suffixes):
        raise InstallerError(
            "invalid_path",
            f"artifact url host {host!r} not in allowlist {suffixes}",
        )


def _stream_to_file(
    resp: httpx.Response,
    target: Path,
    *,
    expected_sha: str | None,
    relpath: str,
) -> None:
    """Stream ``resp`` to ``target`` with a hard byte cap and incremental sha256.

    Aborts as soon as the running total exceeds ``_MAX_ARTIFACT_BYTES``
    so a multi-GB body cannot exhaust memory or disk. The partial file
    is removed on any failure so a half-written artifact never lands at
    the install destination.
    """
    hasher = hashlib.sha256()
    total = 0
    try:
        with target.open("wb") as fh:
            for chunk in resp.iter_bytes(_DOWNLOAD_CHUNK):
                if not chunk:
                    continue
                total += len(chunk)
                if total > _MAX_ARTIFACT_BYTES:
                    raise InstallerError(
                        "download_failed",
                        f"{relpath!r} exceeds {_MAX_ARTIFACT_BYTES} byte cap",
                    )
                hasher.update(chunk)
                fh.write(chunk)
    except BaseException:
        target.unlink(missing_ok=True)
        raise
    if expected_sha is not None:
        actual = hasher.hexdigest()
        if actual != expected_sha:
            target.unlink(missing_ok=True)
            raise InstallerError(
                "sha256_mismatch",
                f"{relpath!r} sha256 mismatch: got {actual}, want {expected_sha}",
            )
    else:
        logger.warning(
            "server reported no sha256 for %r; skipping integrity check",
            relpath,
        )


def _safe_target(staging: Path, relpath: str) -> Path:
    """Resolve ``staging / relpath`` and require it stay within ``staging``.

    The upstream gateway is a network trust boundary: a malicious or
    compromised response that returns ``relpath`` containing ``..``
    segments, an absolute path, or backslashes would otherwise let the
    server pick where on disk we write, since ``Path("/foo") / "/etc/x"``
    silently discards the left operand and ``..`` is not normalized by
    ``__truediv__``. Reject before any mkdir/write.
    """
    if not relpath or not relpath.strip():
        raise InstallerError("invalid_path", "relpath is empty")
    if "\\" in relpath:
        raise InstallerError("invalid_path", f"relpath contains backslash: {relpath!r}")
    candidate = Path(relpath)
    if candidate.is_absolute() or any(part == ".." for part in candidate.parts):
        raise InstallerError("invalid_path", f"relpath escapes staging: {relpath!r}")
    staging_resolved = staging.resolve()
    target_resolved = (staging / candidate).resolve()
    if not target_resolved.is_relative_to(staging_resolved):
        raise InstallerError("invalid_path", f"relpath escapes staging: {relpath!r}")
    return target_resolved


@traced("client.remote_enhance.download_artifact")
def download_artifact(
    artifact: dict[str, Any],
    *,
    job_id: str,
    http_get: Callable[[str], httpx.Response] | None = None,  # injected for tests
) -> Path:
    """Download every file in ``artifact.files`` to the staging dir.

    Returns the staging root path. The caller is expected to next call
    ``validate_frontmatter`` (when ``artifact_kind=enhanced``) and then
    ``install_to``.

    Null-sha256 branch:
      - ``sha256`` non-null → verify, raise ``InstallerError(sha256_mismatch)`` on miss
      - ``sha256`` null → log warning, proceed (legacy / pre-seeded bytes)

    ``http_get`` defaults to a fresh ``httpx.Client`` GET so unit tests
    can drive the function with a stub without touching the network.
    """
    files = artifact.get("files") or []
    set_span_attributes(job_id=job_id, file_count=len(files))
    if not files:
        raise InstallerError("missing_artifact", "artifact contains no files to download")
    staging = staging_root(job_id)
    staging.mkdir(parents=True, exist_ok=True)

    owned_client: httpx.Client | None = None
    if http_get is None:
        owned_client = httpx.Client(timeout=120.0)

    try:
        for entry in files:
            relpath = str(entry["relpath"])
            url = str(entry["url"])
            expected_sha = entry.get("sha256")  # may be None
            _validate_artifact_url(url)
            target = _safe_target(staging, relpath)
            target.parent.mkdir(parents=True, exist_ok=True)

            try:
                if http_get is not None:
                    # Test-injected path — Response is already loaded but
                    # iter_bytes() still gives us per-chunk size enforcement.
                    resp = http_get(url)
                    resp.raise_for_status()
                    _stream_to_file(resp, target, expected_sha=expected_sha, relpath=relpath)
                else:
                    # Production path — true network streaming so the
                    # _MAX_ARTIFACT_BYTES cap prevents OOM before the body
                    # is buffered in memory.
                    assert owned_client is not None
                    with owned_client.stream("GET", url) as resp:
                        resp.raise_for_status()
                        _stream_to_file(resp, target, expected_sha=expected_sha, relpath=relpath)
            except (httpx.HTTPError, OSError) as exc:
                raise InstallerError(
                    "download_failed",
                    f"failed to download {relpath!r}: {exc}",
                ) from exc
    finally:
        if owned_client is not None:
            owned_client.close()

    return staging


@traced("client.remote_enhance.validate_frontmatter")
def validate_frontmatter(staging: Path) -> None:
    """Validate the design-doc §5.6 frontmatter contract for ``enhanced`` bundles.

    Required:
      1. ``metadata`` block present (under the YAML frontmatter)
      2. ``metadata.tags`` is a non-empty list
      3. ``metadata.roi`` is a non-empty list

    Failure → ``InstallerError(invalid_frontmatter, ...)`` and the bundle
    is preserved at ``staging`` for inspection. No client-side stamping.
    """
    skill_md = staging / "SKILL.md"
    if not skill_md.is_file():
        raise InstallerError("invalid_frontmatter", "downloaded bundle is missing SKILL.md")
    fm = parse_frontmatter(skill_md.read_text(encoding="utf-8"))
    metadata = fm.get("metadata")
    if not isinstance(metadata, dict):
        raise InstallerError("invalid_frontmatter", "SKILL.md missing metadata block")
    tags = metadata.get("tags")
    if not isinstance(tags, list) or not tags:
        raise InstallerError("invalid_frontmatter", "metadata.tags must be a non-empty list")
    roi = metadata.get("roi")
    if not isinstance(roi, list) or not roi:
        raise InstallerError("invalid_frontmatter", "metadata.roi must be a non-empty list")


@traced("client.remote_enhance.install_to", kind="CLIENT", openinference_kind="TOOL")
def install_to(staging: Path, *, destination: Path) -> Path:
    """Atomically replace ``destination`` with ``staging``'s contents.

    Sibling-tmp + rename pattern so the swap is genuinely atomic:

      1. ``shutil.copytree(staging → tmp_install)`` to a sibling under
         ``destination.parent`` (same filesystem → rename is atomic). On
         failure here, ``destination`` is untouched.
      2. If ``destination`` exists, ``shutil.move`` it under
         ``{data_dir()}/enhancements/{ts}-backup/<name>`` — atomic on POSIX
         when source and dest share a filesystem.
      3. ``os.replace(tmp_install → destination)`` — atomic rename.

    A crash between steps 2 and 3 leaves ``tmp_install`` on disk and the
    backup in place; recoverable by hand. ``shutil.copytree`` directly
    onto ``destination`` (the previous implementation) was *not* atomic:
    a mid-copy failure would leave a half-populated destination with the
    original already moved out.
    """
    set_span_attributes(staging=str(staging), destination=str(destination))
    destination.parent.mkdir(parents=True, exist_ok=True)

    # UUID-suffixed sibling tmp avoids collisions with concurrent installs
    # and keeps the rename on the same filesystem as ``destination``.
    suffix = uuid.uuid4().hex[:8]
    tmp_install = destination.parent / f".{destination.name}.tmp.{suffix}"
    # Stage the backup as a sibling of ``destination`` first so the
    # initial rename is atomic on the same filesystem (cross-fs
    # ``shutil.move`` degrades to copy+delete and is non-atomic). After
    # the swap completes we relocate it to the canonical _backup_root().
    sibling_backup: Path | None = None
    backup: Path | None = None
    try:
        shutil.copytree(str(staging), str(tmp_install))
        if destination.exists():
            sibling_backup = destination.parent / f".{destination.name}.bak.{suffix}"
            os.rename(str(destination), str(sibling_backup))
        try:
            os.replace(str(tmp_install), str(destination))
        except BaseException:
            # The rename failed after we already moved the original out
            # of the way. Put it back before propagating so the user's
            # previously-working skill is not silently destroyed.
            if sibling_backup is not None and sibling_backup.exists():
                try:
                    os.rename(str(sibling_backup), str(destination))
                except OSError as restore_exc:
                    logger.error(
                        "failed to restore backup after install failure; "
                        "previous skill is at %s — recover by hand. cause: %s",
                        sibling_backup,
                        restore_exc,
                    )
                    raise
            raise
        if sibling_backup is not None:
            backup = _backup_root() / destination.name
            backup.parent.mkdir(parents=True, exist_ok=True)
            try:
                shutil.move(str(sibling_backup), str(backup))
            except OSError as exc:
                # Install already succeeded — keep the sibling backup
                # in place and surface the path so the user can clean
                # up. Do not fail the install for a relocation glitch.
                logger.warning(
                    "install succeeded but could not move backup to %s (left at %s): %s",
                    backup,
                    sibling_backup,
                    exc,
                )
                backup = sibling_backup
            logger.info("backed up existing skill: %s -> %s", destination, backup)
            set_span_attributes(backed_up_to=str(backup))
    except BaseException:
        if tmp_install.exists():
            shutil.rmtree(tmp_install, ignore_errors=True)
        raise
    return destination
