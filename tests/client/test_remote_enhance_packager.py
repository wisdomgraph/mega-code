"""Packager tests — content_hash parity + secret-leak prevention + size cap.

Drives ``compute_content_hash`` and ``package_skill`` against the canonical
``content_hash_vectors.json`` (vendored from skill-enhance-server) so a
divergence between the two algorithms surfaces immediately. Without this
parity the upstream's 409 ``duplicate_content_hash`` would fire on the
client's first re-upload, with no way for the client to tell which side
drifted.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from mega_code.client.remote_enhance.packager import (
    MAX_ARCHIVE_BYTES,
    BundleFile,
    PackagerError,
    compute_content_hash,
    package_skill,
)

_VECTORS_PATH = Path(__file__).resolve().parents[1] / "fixtures" / "content_hash_vectors.json"


def _load_vectors() -> list[dict]:
    return json.loads(_VECTORS_PATH.read_text(encoding="utf-8"))["vectors"]


# ---------------------------------------------------------------------------
# Content-hash parity (precondition #4 — fixture vendored from upstream)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "vector",
    _load_vectors(),
    ids=lambda v: v["name"],
)
def test_compute_content_hash_matches_canonical_vector(vector):
    """Every vector hashes byte-for-byte identical to skill-enhance-server.

    The vectors were generated from
    ``server.storage.bundle.compute_content_hash`` — divergence here means
    the gateway 409 dedup will fire on legitimate re-uploads.
    """
    files = [
        BundleFile(relpath=f["relpath"], sha256=f["sha256"], size=f["size"])
        for f in vector["files"]
    ]
    assert compute_content_hash(files) == vector["content_hash"]


@pytest.mark.parametrize(
    "vector",
    _load_vectors(),
    ids=lambda v: v["name"],
)
def test_package_skill_produces_canonical_hash(vector, tmp_path):
    """End-to-end: write vector files to disk, package, assert hash parity."""
    skill_dir = tmp_path / "skill"
    skill_dir.mkdir()
    for f in vector["files"]:
        target = skill_dir / f["relpath"]
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(f["utf8_text"], encoding="utf-8")
    bundle = package_skill(skill_dir)
    assert bundle.content_hash == vector["content_hash"], f"hash drift on vector {vector['name']!r}"


# ---------------------------------------------------------------------------
# Secret-leak prevention — design doc §1B forbids these patterns
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "filename,body",
    [
        (".env", "API_KEY=abc\n"),
        ("creds.key", "-----BEGIN ...\n"),
        ("private.pem", "-----BEGIN PRIVATE KEY-----\n"),
        ("id_rsa", "ssh key\n"),
        ("id_rsa.pub", "pub key\n"),
        ("aws_credentials.txt", "AKIA...\n"),
        ("secrets/db.json", "{}\n"),
    ],
)
def test_refuses_credential_shaped_paths(tmp_path, filename, body):
    skill_dir = tmp_path / "skill"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text("# x\n", encoding="utf-8")
    target = skill_dir / filename
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(body, encoding="utf-8")
    with pytest.raises(PackagerError) as exc_info:
        package_skill(skill_dir)
    assert exc_info.value.code == "forbidden_path"


def test_missing_skill_md_raises(tmp_path):
    skill_dir = tmp_path / "skill"
    skill_dir.mkdir()
    (skill_dir / "README.md").write_text("# r\n", encoding="utf-8")
    with pytest.raises(PackagerError) as exc_info:
        package_skill(skill_dir)
    assert exc_info.value.code == "missing_skill_md"


def test_missing_skill_dir_raises(tmp_path):
    with pytest.raises(PackagerError) as exc_info:
        package_skill(tmp_path / "does-not-exist")
    assert exc_info.value.code == "missing_skill_dir"


# ---------------------------------------------------------------------------
# Size cap — fail before the bytes ever leave the host
# ---------------------------------------------------------------------------


def test_compressed_archive_exceeds_25mb_cap_raises(tmp_path):
    """A blob of incompressible random bytes pushes past the 25 MB cap."""
    import os as _os

    skill_dir = tmp_path / "skill"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text("# x\n", encoding="utf-8")
    # Random bytes don't compress — write 26 MB so the deflate output stays >25 MB.
    (skill_dir / "blob.bin").write_bytes(_os.urandom(26 * 1024 * 1024))
    with pytest.raises(PackagerError) as exc_info:
        package_skill(skill_dir)
    assert exc_info.value.code == "size_exceeded"


def test_max_archive_bytes_constant_matches_upstream():
    """The cap is documented at 25 MB on both sides; if upstream changes, this breaks."""
    assert MAX_ARCHIVE_BYTES == 25 * 1024 * 1024


# ---------------------------------------------------------------------------
# Skip behavior — silently filter OS / editor / VCS / build noise. Mirrors the
# upstream demo tool ``skill-enhance-server/tests_skill/zip_skill.sh`` so the
# Python client and the bash tool produce equivalent archives.
# ---------------------------------------------------------------------------


def _bundle_relpaths(bundle) -> set[str]:
    return {f.relpath for f in bundle.files}


@pytest.mark.parametrize(
    "noise_relpath",
    [
        ".DS_Store",  # macOS metadata at root
        "subdir/.DS_Store",  # macOS metadata nested
        "Thumbs.db",  # Windows thumbnail cache
        "desktop.ini",  # Windows folder config
        ".gitignore",  # informational; matches zip_skill.sh
        "module/__pycache__/foo.cpython-311.pyc",
        "src/foo.pyc",  # pyc anywhere
        "src/foo.pyo",  # pyo anywhere
        ".git/HEAD",  # git data
        ".git/objects/ab/cdef",  # nested git data
        ".svn/entries",  # svn data
        ".hg/store/data",  # mercurial data
        ".vscode/settings.json",  # editor settings
        ".idea/workspace.xml",  # JetBrains
        "node_modules/foo/index.js",  # JS deps
        ".venv/bin/python",  # Python venv
        "venv/lib/python3.11/site-packages/x.py",
        ".tox/py311/log",  # tox
        ".mypy_cache/3.11/cache",
        ".pytest_cache/v/cache",
        ".ruff_cache/0.1.0/cache",
        "__MACOSX/foo",  # macOS resource forks
    ],
)
def test_skips_noise_silently(tmp_path, noise_relpath):
    """Each noise path is silently filtered, never refused. Bundle still
    contains SKILL.md and only SKILL.md (the noise file is dropped)."""
    skill_dir = tmp_path / "skill"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text("# x\n", encoding="utf-8")
    target = skill_dir / noise_relpath
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(b"noise")

    bundle = package_skill(skill_dir)
    assert _bundle_relpaths(bundle) == {"SKILL.md"}, (
        f"noise path {noise_relpath!r} should be silently skipped, got {_bundle_relpaths(bundle)}"
    )


def test_useful_files_alongside_noise_still_bundled(tmp_path):
    """Skip filter does not over-fire on legitimate skill content."""
    skill_dir = tmp_path / "skill"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text("# x\n", encoding="utf-8")
    (skill_dir / "scripts").mkdir()
    (skill_dir / "scripts" / "run.py").write_text("print(1)\n", encoding="utf-8")
    (skill_dir / "references").mkdir()
    (skill_dir / "references" / "best-practices.md").write_text("# bp\n", encoding="utf-8")
    # And some noise that should be filtered.
    (skill_dir / ".DS_Store").write_bytes(b"\x00\x00")
    (skill_dir / "scripts" / "__pycache__").mkdir()
    (skill_dir / "scripts" / "__pycache__" / "run.cpython-311.pyc").write_bytes(b"bc")

    bundle = package_skill(skill_dir)
    assert _bundle_relpaths(bundle) == {
        "SKILL.md",
        "scripts/run.py",
        "references/best-practices.md",
    }


def test_credential_inside_skipped_dir_still_refused(tmp_path):
    """Forbidden-first precedence: a credential file inside a skipped
    directory must still raise, never silently drop. ``_FORBIDDEN_*`` wins."""
    skill_dir = tmp_path / "skill"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text("# x\n", encoding="utf-8")
    (skill_dir / ".vscode").mkdir()
    (skill_dir / ".vscode" / "credentials.json").write_text('{"token": "abc"}\n', encoding="utf-8")

    with pytest.raises(PackagerError) as exc_info:
        package_skill(skill_dir)
    assert exc_info.value.code == "forbidden_path"


def test_dotenv_in_git_dir_still_refused(tmp_path):
    """Same precedence rule for ``.env`` in ``.git/``."""
    skill_dir = tmp_path / "skill"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text("# x\n", encoding="utf-8")
    (skill_dir / ".git").mkdir()
    (skill_dir / ".git" / ".env").write_text("API_KEY=abc\n", encoding="utf-8")

    with pytest.raises(PackagerError) as exc_info:
        package_skill(skill_dir)
    assert exc_info.value.code == "forbidden_path"


def test_skipped_files_do_not_affect_content_hash(tmp_path):
    """Two bundles — one with noise, one without — must hash identically.
    The skip filter runs *before* hashing, so the canonical content_hash is
    stable across users with different OS/editor footprints."""
    base = tmp_path / "base"
    base.mkdir()
    (base / "SKILL.md").write_text("# x\n", encoding="utf-8")

    noisy = tmp_path / "noisy"
    noisy.mkdir()
    (noisy / "SKILL.md").write_text("# x\n", encoding="utf-8")
    (noisy / ".DS_Store").write_bytes(b"\x00")
    (noisy / "__pycache__").mkdir()
    (noisy / "__pycache__" / "x.pyc").write_bytes(b"bc")

    h_base = package_skill(base).content_hash
    h_noisy = package_skill(noisy).content_hash
    assert h_base == h_noisy


# ---------------------------------------------------------------------------
# Symlink rejection — the credential-exfil hole
# ---------------------------------------------------------------------------


def test_symlinked_file_is_rejected(tmp_path):
    """A symlinked file inside the skill folder must NOT be packaged.

    Without this guard, ``evil -> ~/.aws/credentials`` inside a skill folder
    would be bundled because ``Path.is_file()`` resolves through symlinks.
    The packager refuses at walk time so the user's secrets never enter the
    bundle, never get hashed, never get uploaded.
    """
    skill_dir = tmp_path / "leaky"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text("# ok\n", encoding="utf-8")

    secret = tmp_path / "outside-secret"
    secret.write_text("AWS_SECRET_ACCESS_KEY=hunter2\n", encoding="utf-8")
    (skill_dir / "evil").symlink_to(secret)

    with pytest.raises(PackagerError) as exc_info:
        package_skill(skill_dir)
    assert exc_info.value.code == "symlink_rejected"


def test_symlinked_dir_is_rejected(tmp_path):
    """A symlinked directory inside the skill folder must also be refused.

    ``os.walk(followlinks=False)`` won't descend, but the symlink entry
    itself still appears in ``dirnames`` — the packager raises explicitly
    so the user gets a clear error instead of a silently-empty subtree.
    """
    skill_dir = tmp_path / "leaky-dir"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text("# ok\n", encoding="utf-8")

    outside = tmp_path / "outside-tree"
    outside.mkdir()
    (outside / "data").write_text("payload", encoding="utf-8")
    (skill_dir / "linked").symlink_to(outside, target_is_directory=True)

    with pytest.raises(PackagerError) as exc_info:
        package_skill(skill_dir)
    assert exc_info.value.code == "symlink_rejected"
