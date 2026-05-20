"""Skill-folder → zip-bundle packager with secret-leak prevention.

Walks a skill directory, refuses to include common credential filename
patterns (``.env``, ``*.key``, ``*.pem``, ``id_rsa*``, ``*credentials*``,
``secrets/``), enforces the 25 MB compressed cap, and computes a
``content_hash`` byte-compatible with the gateway's canonical hash so
the 409 dedup path is reproducible client-side.
"""

from __future__ import annotations

import fnmatch
import hashlib
import io
import json
import os
import zipfile
from collections.abc import Iterable
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from mega_code.client.utils.tracing import set_span_attributes, traced

# 25 MB matches the gateway's MAX_ARCHIVE_BYTES. Both compressed and
# uncompressed totals are capped at the same value upstream — we enforce
# the compressed cap here and let the upstream enforce the uncompressed
# cap (it has authoritative limits anyway).
MAX_ARCHIVE_BYTES = 25 * 1024 * 1024

# Names / paths that must never enter the bundle. Matched case-insensitively
# against either the basename or any path component. Each entry is one of:
#   - ``"name"``     — exact basename match
#   - ``"glob*"``    — fnmatch-style glob against the basename
#   - ``"dir/"``     — any path component equal to ``"dir"``
_FORBIDDEN_BASENAMES: tuple[str, ...] = (".env",)
_FORBIDDEN_GLOBS: tuple[str, ...] = (
    "*.key",
    "*.pem",
    "id_rsa*",
    "*credentials*",
    ".env.*",
)
_FORBIDDEN_DIRS: tuple[str, ...] = ("secrets",)

# Names / paths that are *silently filtered* from the bundle (not refused).
# Mirrors the upstream demo zip tool so the Python client and the bash
# demo produce equivalent archives. These are
# OS metadata, editor caches, VCS data, and build artifacts — never useful in a
# skill bundle, can blow the 25 MB cap (``node_modules``, ``.venv``), and can
# carry workspace-specific paths or unsanitised commit history. Refused-first
# semantics are preserved: if a path matches both _FORBIDDEN_* and _SKIPPED_*,
# the refusal wins (e.g. ``.vscode/credentials.json`` still raises rather than
# silently dropping the credential file).
_SKIPPED_BASENAMES: tuple[str, ...] = (
    ".ds_store",  # macOS Finder metadata
    "thumbs.db",  # Windows thumbnail cache
    "desktop.ini",  # Windows folder config
    ".gitignore",  # repo-level ignore rules; matches zip_skill.sh
    # wisdom-gen sidecars — internal pipeline artifacts that live next to
    # SKILL.md in pending/feedback folders. Read locally pre-upload (see
    # mega_code/client/pending.py); no upstream consumer reads them out
    # of the uploaded bundle. The filename `metadata.json` is also used
    # by unrelated skill ecosystems (marketplace catalogs, authored
    # metadata), so it is filtered by content shape instead — see
    # _is_wisdom_gen_metadata.
    "evidence.json",
    "injection.json",
)
_SKIPPED_GLOBS: tuple[str, ...] = (
    "*.pyc",  # Python bytecode
    "*.pyo",  # Python optimised bytecode
)
_SKIPPED_DIRS: tuple[str, ...] = (
    "__macosx",  # macOS zip resource forks
    "__pycache__",  # Python bytecode cache
    ".git",  # git data
    ".svn",  # subversion data
    ".hg",  # mercurial data
    ".vscode",  # VS Code workspace settings
    ".idea",  # JetBrains IDEs
    "node_modules",  # JS dependencies (huge)
    ".venv",  # Python virtualenv
    "venv",  # alternative virtualenv name
    ".tox",  # tox testing artifacts
    ".mypy_cache",  # mypy type-check cache
    ".pytest_cache",  # pytest cache
    ".ruff_cache",  # ruff cache
)


class PackagerError(Exception):
    """Raised on any packager precondition failure (size, secrets, missing SKILL.md)."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


class BundleFile(BaseModel):
    """One file in the bundle. Mirrors the gateway's BundleFile shape so
    ``compute_content_hash`` produces identical output."""

    model_config = ConfigDict(frozen=True)

    relpath: str  # uses '/' separators; relative to package root
    sha256: str
    size: int


class Bundle(BaseModel):
    """Result of packaging a skill folder."""

    model_config = ConfigDict(frozen=True)

    archive_bytes: bytes
    content_hash: str
    files: tuple[BundleFile, ...]


def _is_forbidden(relpath: str) -> bool:
    """Refuse on any credential-shaped filename or path component.

    Refusal is strict — the caller should surface a clear error and bail
    rather than silently dropping the file (silent drops are how secrets
    leak in adjacent products).
    """
    parts = relpath.split("/")
    basename = parts[-1].lower()
    if basename in _FORBIDDEN_BASENAMES:
        return True
    for pat in _FORBIDDEN_GLOBS:
        if fnmatch.fnmatch(basename, pat):
            return True
    lower_parts = [p.lower() for p in parts[:-1]]
    if any(p in _FORBIDDEN_DIRS for p in lower_parts):
        return True
    return False


def _is_skipped(relpath: str) -> bool:
    """Silently filter OS / editor / VCS / build noise from the bundle.

    Symmetric with ``_is_forbidden``: basename-or-glob match wins over a
    parent-directory match. ``_FORBIDDEN_*`` is checked first by the caller
    so a credential inside a skipped dir (e.g. ``.vscode/credentials.json``)
    still raises rather than silently dropping it.
    """
    parts = relpath.split("/")
    basename = parts[-1].lower()
    if basename in _SKIPPED_BASENAMES:
        return True
    for pat in _SKIPPED_GLOBS:
        if fnmatch.fnmatch(basename, pat):
            return True
    lower_parts = [p.lower() for p in parts[:-1]]
    if any(p in _SKIPPED_DIRS for p in lower_parts):
        return True
    return False


def _walk_skill(skill_dir: Path) -> Iterable[Path]:
    """Yield every regular file under ``skill_dir`` in deterministic order.

    Sorted at every level so ``compute_content_hash`` gets a stable input
    independent of filesystem listing order.

    Symlinks are refused — both files and directories. ``Path.rglob`` would
    otherwise descend into a symlinked dir and ``is_file()`` resolves
    symlinks, so e.g. ``evil -> ~/.aws`` inside a skill folder would bundle
    credentials whose in-skill relpath (``evil/credentials``) doesn't trip
    the credential-name filters. Refusing at walk time closes that hole.
    """
    skill_resolved = skill_dir.resolve()
    for dirpath, dirnames, filenames in os.walk(skill_dir, followlinks=False):
        dirnames.sort()
        dir_path = Path(dirpath)
        for name in sorted(dirnames):
            if (dir_path / name).is_symlink():
                raise PackagerError(
                    "symlink_rejected",
                    f"refusing symlinked directory in skill bundle: "
                    f"{(dir_path / name).relative_to(skill_dir)}",
                )
        for name in sorted(filenames):
            child = dir_path / name
            if child.is_symlink():
                raise PackagerError(
                    "symlink_rejected",
                    f"refusing symlinked file in skill bundle: {child.relative_to(skill_dir)}",
                )
            try:
                resolved = child.resolve(strict=True)
            except OSError as exc:
                raise PackagerError(
                    "missing_file",
                    f"cannot resolve {child.relative_to(skill_dir)}: {exc}",
                ) from exc
            if not resolved.is_relative_to(skill_resolved):
                raise PackagerError(
                    "symlink_rejected",
                    f"path escapes skill root: {child.relative_to(skill_dir)}",
                )
            if child.is_file():
                yield child


def _is_wisdom_gen_metadata(body: bytes) -> bool:
    """Return True if a ``metadata.json`` body looks like a wisdom-gen sidecar.

    The wisdom-gen pipeline writes ``metadata.json`` next to ``SKILL.md`` with
    a fixed shape (``skill_id``, ``run_id``, ``output_files``, ``generated_at``,
    ``roi``). Other skill ecosystems use the same filename for unrelated payloads
    (marketplace catalog entries with ``id``/``author``/``authorAvatar``;
    authored skill metadata with ``version``/``organization``/``abstract``;
    user content). Requiring both ``skill_id`` and ``run_id`` keys distinguishes
    our sidecar without false positives on those shapes.

    Malformed JSON or non-object roots → False (don't drop unknown files).
    """
    try:
        parsed = json.loads(body)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return False
    return isinstance(parsed, dict) and "skill_id" in parsed and "run_id" in parsed


def _file_sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def compute_content_hash(files: list[BundleFile]) -> str:
    """Reproduce the gateway's ``compute_content_hash`` byte-for-byte.

    Algorithm:
        manifest = {"files": [{"path", "sha256", "size"} sorted by relpath]}
        sha256(json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode())

    The packager's tests (``test_remote_enhance_packager.py``) drive this
    against ``content_hash_vectors.json`` from the upstream repo.
    """
    manifest = {
        "files": [
            {"path": f.relpath, "sha256": f.sha256, "size": f.size}
            for f in sorted(files, key=lambda f: f.relpath)
        ]
    }
    encoded = json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


@traced("client.remote_enhance.package_skill")
def package_skill(skill_dir: Path) -> Bundle:
    """Walk ``skill_dir``, build a zip in memory, return ``Bundle``.

    Raises ``PackagerError`` on:
      - missing or non-directory ``skill_dir``
      - missing ``SKILL.md`` at the root of ``skill_dir``
      - any forbidden basename/path component (``.env``, ``*.key``, etc.)
      - compressed archive size > 25 MB

    The bundle's ``content_hash`` is computed before any S3-side state
    exists, so the same hash will be re-derived on the upstream and used
    for the 409 dedup check.
    """
    if not skill_dir.is_dir():
        raise PackagerError(
            "missing_skill_dir",
            f"skill directory not found or not a directory: {skill_dir}",
        )
    skill_md = skill_dir / "SKILL.md"
    if not skill_md.is_file():
        raise PackagerError(
            "missing_skill_md",
            f"{skill_md} is required at the root of the skill folder",
        )

    files: list[BundleFile] = []
    file_bodies: list[tuple[str, bytes]] = []
    skipped_count = 0
    for path in _walk_skill(skill_dir):
        relpath = path.relative_to(skill_dir).as_posix()
        # Forbidden first (security wins over auto-clean): a credential file
        # inside a skipped dir must still raise, not silently drop.
        if _is_forbidden(relpath):
            raise PackagerError(
                "forbidden_path",
                f"refusing to bundle credential-shaped path: {relpath!r}",
            )
        if _is_skipped(relpath):
            skipped_count += 1
            continue
        body = path.read_bytes()
        if path.name == "metadata.json" and _is_wisdom_gen_metadata(body):
            skipped_count += 1
            continue
        files.append(BundleFile(relpath=relpath, sha256=_file_sha256(body), size=len(body)))
        file_bodies.append((relpath, body))

    if not any(f.relpath == "SKILL.md" for f in files):
        # Defensive — _walk_skill should have surfaced it. Raised here so
        # the upstream's 400 missing_skill_md never fires (faster feedback).
        raise PackagerError(
            "missing_skill_md",
            "SKILL.md must be at the archive root",
        )

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for relpath, body in file_bodies:
            zf.writestr(relpath, body)
    archive_bytes = buf.getvalue()

    if len(archive_bytes) > MAX_ARCHIVE_BYTES:
        raise PackagerError(
            "size_exceeded",
            f"compressed archive {len(archive_bytes)} bytes exceeds {MAX_ARCHIVE_BYTES} cap",
        )

    bundle = Bundle(
        archive_bytes=archive_bytes,
        content_hash=compute_content_hash(files),
        files=tuple(files),
    )
    set_span_attributes(
        skill_dir=str(skill_dir),
        file_count=len(files),
        skipped_count=skipped_count,
        archive_bytes=len(archive_bytes),
        content_hash=bundle.content_hash,
    )
    return bundle
