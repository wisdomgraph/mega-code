"""__main__ tests — argparse dispatch, exit codes, stdout JSON envelopes.

Covers each exit-code branch (0/2/3/4/5) and each exit-0 sub-shape
(ready-to-install / installed / terminal-no-install). Stubs out the
gateway client + packager + installer so no network or filesystem state
outside ``isolated_data_dir`` leaks across tests.
"""

from __future__ import annotations

import io
import json
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from mega_code.client.remote_enhance import __main__ as cli
from mega_code.client.remote_enhance.client import ApiError, AuthError, NetworkError
from mega_code.client.remote_enhance.packager import Bundle, PackagerError
from mega_code.client.remote_enhance.poller import PollResult, PollTimeout


@pytest.fixture
def isolated_data_dir(tmp_path, monkeypatch) -> Path:
    monkeypatch.setenv("MEGA_CODE_DATA_DIR", str(tmp_path))
    return tmp_path


def _run_cli(argv: list[str]) -> tuple[int, dict]:
    """Run ``main(argv)`` while capturing stdout JSON; return (exit_code, parsed_envelope)."""
    out = io.StringIO()
    err = io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        rc = cli.main(argv)
    line = out.getvalue().strip().splitlines()[-1]  # last line is the envelope
    return rc, json.loads(line)


def _bundle(content_hash: str = "ch-deadbeef") -> Bundle:
    return Bundle(archive_bytes=b"PK\x03\x04zip", content_hash=content_hash, files=())


# ---------------------------------------------------------------------------
# Argparse — missing required flags
# ---------------------------------------------------------------------------


def test_install_existing_without_install_location_exit_4(isolated_data_dir):
    rc, env = _run_cli(["--install-existing", "job-1"])
    assert rc == 4
    assert env["error"]["code"] == "missing_install_location"


def test_install_existing_without_skill_name_exit_4(isolated_data_dir):
    rc, env = _run_cli(["--install-existing", "job-1", "--install-location", "global"])
    assert rc == 4
    assert env["error"]["code"] == "missing_skill_name"


def test_default_mode_requires_skill_name(isolated_data_dir):
    rc, env = _run_cli([])
    assert rc == 4
    assert env["error"]["code"] == "missing_skill_name"


# ---------------------------------------------------------------------------
# Default mode — happy path → exit 0 ready-to-install
# ---------------------------------------------------------------------------


def test_default_mode_succeeded_emits_ready_to_install(isolated_data_dir, tmp_path, monkeypatch):
    skill_dir = tmp_path / "mySkill"
    skill_dir.mkdir()
    monkeypatch.setattr(cli, "_resolve_skill_dir", lambda name: skill_dir)
    monkeypatch.setattr(cli, "package_skill", lambda d: _bundle())

    fake_client = MagicMock()
    fake_client.upload.return_value = {"job_id": "job-1"}
    fake_client.get_result.return_value = {
        "status": "succeeded",
        "artifact_kind": "enhanced",
        "artifact": {
            "files": [
                {
                    "relpath": "SKILL.md",
                    "url": "https://test-bucket.s3.amazonaws.com/s",
                    "sha256": None,
                }
            ]
        },
        "reason": {"status": "succeeded"},
        "usage_snapshot": {},
        "roi": [],
    }

    monkeypatch.setattr(cli, "GatewayClient", lambda **kw: _ctx(fake_client))
    monkeypatch.setattr(
        cli,
        "run_poller",
        lambda **kw: PollResult(
            job_id="job-1", status="succeeded", last_job_detail={"status": "succeeded"}
        ),
    )
    monkeypatch.setattr(
        cli,
        "download_artifact",
        lambda artifact, *, job_id: Path(isolated_data_dir / "enhancements" / job_id),
    )
    monkeypatch.setattr(cli, "validate_frontmatter", lambda staging: None)

    rc, env = _run_cli(["--skill-name", "mySkill", "--poll-timeout", "60"])
    assert rc == 0
    assert env["status"] == "succeeded"
    assert env["artifact_kind"] == "enhanced"
    assert env["installed"] is False
    assert env["needs_install_location"] is True
    assert "staging_dir" in env


# ---------------------------------------------------------------------------
# Default mode — 409 duplicate_content_hash → exit 2 with content_hash from local
# ---------------------------------------------------------------------------


def test_default_mode_duplicate_content_hash_emits_exit_2_with_local_hash(
    isolated_data_dir, tmp_path, monkeypatch
):
    """Per design plan §1B: ``content_hash`` in the exit-2 envelope comes from
    the packager's pre-upload computation (the upstream 409 ``details`` does
    NOT carry it — skill-enhance-server §3.10.1)."""
    skill_dir = tmp_path / "mySkill"
    skill_dir.mkdir()
    monkeypatch.setattr(cli, "_resolve_skill_dir", lambda name: skill_dir)
    monkeypatch.setattr(cli, "package_skill", lambda d: _bundle("local-hash-abc"))

    fake_client = MagicMock()
    fake_client.upload.side_effect = ApiError(
        status=409,
        code="duplicate_content_hash",
        message="dup",
        details={"existing_job_id": "11111111-1111-1111-1111-111111111111"},
    )
    monkeypatch.setattr(cli, "GatewayClient", lambda **kw: _ctx(fake_client))

    rc, env = _run_cli(["--skill-name", "mySkill"])
    assert rc == 2
    assert env["conflict"]["existing_job_id"] == "11111111-1111-1111-1111-111111111111"
    assert env["conflict"]["content_hash"] == "local-hash-abc"


# ---------------------------------------------------------------------------
# Default mode — poll timeout → exit 3
# ---------------------------------------------------------------------------


def test_default_mode_poll_timeout_emits_exit_3(isolated_data_dir, tmp_path, monkeypatch):
    skill_dir = tmp_path / "mySkill"
    skill_dir.mkdir()
    monkeypatch.setattr(cli, "_resolve_skill_dir", lambda name: skill_dir)
    monkeypatch.setattr(cli, "package_skill", lambda d: _bundle())

    fake_client = MagicMock()
    fake_client.upload.return_value = {"job_id": "job-1"}
    monkeypatch.setattr(cli, "GatewayClient", lambda **kw: _ctx(fake_client))

    def _raise_timeout(**_kw):
        raise PollTimeout(job_id="job-1", elapsed_s=600)

    monkeypatch.setattr(cli, "run_poller", _raise_timeout)

    rc, env = _run_cli(["--skill-name", "mySkill", "--poll-timeout", "600"])
    assert rc == 3
    assert env["timeout"]["job_id"] == "job-1"
    assert env["timeout"]["elapsed_s"] == 600


# ---------------------------------------------------------------------------
# Default mode — packager refusal → exit 4
# ---------------------------------------------------------------------------


def test_default_mode_packager_refusal_emits_exit_4(isolated_data_dir, tmp_path, monkeypatch):
    skill_dir = tmp_path / "mySkill"
    skill_dir.mkdir()
    monkeypatch.setattr(cli, "_resolve_skill_dir", lambda name: skill_dir)

    def _raise(d):
        raise PackagerError("forbidden_path", "refusing .env")

    monkeypatch.setattr(cli, "package_skill", _raise)

    rc, env = _run_cli(["--skill-name", "mySkill"])
    assert rc == 4
    assert env["error"]["code"] == "forbidden_path"


# ---------------------------------------------------------------------------
# Default mode — network failure → exit 5
# ---------------------------------------------------------------------------


def test_default_mode_network_failure_emits_exit_5(isolated_data_dir, tmp_path, monkeypatch):
    skill_dir = tmp_path / "mySkill"
    skill_dir.mkdir()
    monkeypatch.setattr(cli, "_resolve_skill_dir", lambda name: skill_dir)
    monkeypatch.setattr(cli, "package_skill", lambda d: _bundle())

    def _ctx_raises(**_kw):
        raise NetworkError("upstream unreachable")

    monkeypatch.setattr(cli, "GatewayClient", _ctx_raises)

    rc, env = _run_cli(["--skill-name", "mySkill"])
    assert rc == 5
    assert env["error"]["code"] == "network_failure"


def test_default_mode_auth_failure_emits_exit_5(isolated_data_dir, tmp_path, monkeypatch):
    skill_dir = tmp_path / "mySkill"
    skill_dir.mkdir()
    monkeypatch.setattr(cli, "_resolve_skill_dir", lambda name: skill_dir)
    monkeypatch.setattr(cli, "package_skill", lambda d: _bundle())

    def _ctx_raises(**_kw):
        raise AuthError("MEGA_CODE_API_KEY not set")

    monkeypatch.setattr(cli, "GatewayClient", _ctx_raises)

    rc, env = _run_cli(["--skill-name", "mySkill"])
    assert rc == 5
    assert env["error"]["code"] == "auth_failure"


# ---------------------------------------------------------------------------
# Default mode — terminal-no-install (failed/quarantined/etc) → exit 0 sub-shape
# ---------------------------------------------------------------------------


def test_default_mode_failed_terminal_emits_no_install_envelope(
    isolated_data_dir, tmp_path, monkeypatch
):
    skill_dir = tmp_path / "mySkill"
    skill_dir.mkdir()
    monkeypatch.setattr(cli, "_resolve_skill_dir", lambda name: skill_dir)
    monkeypatch.setattr(cli, "package_skill", lambda d: _bundle())

    fake_client = MagicMock()
    fake_client.upload.return_value = {"job_id": "job-1"}
    fake_client.get_result.return_value = {
        "status": "failed",
        "artifact_kind": "none",
        "artifact": None,
        "reason": {"status": "failed", "failure_reason": "max_iterations"},
    }
    monkeypatch.setattr(cli, "GatewayClient", lambda **kw: _ctx(fake_client))
    monkeypatch.setattr(
        cli,
        "run_poller",
        lambda **kw: PollResult(
            job_id="job-1", status="failed", last_job_detail={"status": "failed"}
        ),
    )

    rc, env = _run_cli(["--skill-name", "mySkill"])
    assert rc == 0
    assert env["status"] == "failed"
    assert env["installed"] is False
    assert "needs_install_location" not in env
    assert "staging_dir" not in env
    assert env["result"]["reason"]["failure_reason"] == "max_iterations"


# ---------------------------------------------------------------------------
# install-existing mode — happy path → exit 0 installed
# ---------------------------------------------------------------------------


def test_install_existing_happy_path_emits_installed(isolated_data_dir, tmp_path, monkeypatch):
    # Pre-create the staging dir so install_to has something to copytree.
    job_id = "job-1"
    staging = isolated_data_dir / "enhancements" / job_id
    staging.mkdir(parents=True)
    (staging / "SKILL.md").write_text("# x\n", encoding="utf-8")

    fake_client = MagicMock()
    fake_client.get_result.return_value = {
        "status": "succeeded",
        "artifact_kind": "enhanced",
        "reason": {"status": "succeeded"},
    }
    monkeypatch.setattr(cli, "GatewayClient", lambda **kw: _ctx(fake_client))

    # Force install destination into tmp.
    monkeypatch.setattr(
        cli,
        "_resolve_destination",
        lambda loc, name: tmp_path / loc / name,
    )

    rc, env = _run_cli(
        [
            "--install-existing",
            job_id,
            "--install-location",
            "global",
            "--skill-name",
            "mySkill",
        ]
    )
    assert rc == 0
    assert env["installed"] is True
    assert env["installed_path"].endswith("global/mySkill")


def test_install_existing_missing_staging_emits_exit_4(isolated_data_dir, monkeypatch):
    fake_client = MagicMock()
    monkeypatch.setattr(cli, "GatewayClient", lambda **kw: _ctx(fake_client))
    rc, env = _run_cli(
        [
            "--install-existing",
            "no-such-job",
            "--install-location",
            "global",
            "--skill-name",
            "mySkill",
        ]
    )
    assert rc == 4
    assert env["error"]["code"] == "missing_staging"


def test_install_existing_uses_cached_result_skips_network(
    isolated_data_dir, tmp_path, monkeypatch
):
    """Per fix #5 (revised): when ``{job_id}.result.json`` is cached **as a
    sibling** of the staging dir by a prior mode-1/mode-2 run, install-existing
    reads it back and skips the ``GET /result`` network round-trip entirely.
    The cache is a sibling — not inside the staging dir — because anything
    inside staging becomes part of the install payload.
    """
    job_id = "job-1"
    staging = isolated_data_dir / "enhancements" / job_id
    staging.mkdir(parents=True)
    (staging / "SKILL.md").write_text("# x\n", encoding="utf-8")
    cached = {
        "status": "succeeded",
        "artifact_kind": "enhanced",
        "reason": {"status": "succeeded"},
        "roi": [{"category": "speed"}],
    }
    cache_sibling = isolated_data_dir / "enhancements" / f"{job_id}.result.json"
    cache_sibling.write_text(json.dumps(cached), encoding="utf-8")

    fake_client = MagicMock()
    monkeypatch.setattr(cli, "GatewayClient", lambda **kw: _ctx(fake_client))
    monkeypatch.setattr(
        cli,
        "_resolve_destination",
        lambda loc, name: tmp_path / loc / name,
    )

    rc, env = _run_cli(
        [
            "--install-existing",
            job_id,
            "--install-location",
            "global",
            "--skill-name",
            "mySkill",
        ]
    )
    assert rc == 0
    assert env["installed"] is True
    fake_client.get_result.assert_not_called()  # cache hit — no network call


def test_result_cache_path_is_sibling_of_staging_not_inside_it(isolated_data_dir):
    """Pin the cycle-prevention invariant: the result-cache file must live
    *outside* the staging dir. If it ever moves back inside, the install's
    copytree (`install_to`) would copy it into ``~/.claude/skills/<skill>/``,
    where the next ``package_skill`` run would re-bundle it, the upstream's
    ``finalize_succeeded`` would carry it forward to ``enhanced/<skill>/``,
    and the cycle would re-perpetuate ``result.json`` as artifact noise.
    """
    job_id = "job-abc"
    cache_path = cli._result_cache_path(job_id)
    staging = cli.staging_root(job_id)

    # Same parent (both under enhancements/), but the cache must NOT be inside
    # the staging dir — that's the whole point of this invariant.
    assert cache_path.parent == staging.parent, "cache must share a parent with staging"
    assert not cache_path.is_relative_to(staging), (
        f"cache {cache_path} must NOT be inside staging {staging} — "
        f"would be copied into the install destination by install_to"
    )
    # And the filename must collide with nothing reasonable a skill author
    # would name their file (the upstream-published artifact convention).
    assert cache_path.name == f"{job_id}.result.json"


# ---------------------------------------------------------------------------
# Error-code passthrough sweep — plan §1C: the client's stdout `error.code`
# equals the upstream `error.code` verbatim for every entry in the §1A 4xx
# inventory. `duplicate_content_hash` is excluded (different envelope shape;
# covered by the dedicated test above).
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "upstream_status,upstream_code,expected_exit",
    [
        (400, "invalid_source", 4),
        (400, "invalid_skill_id", 4),
        (400, "invalid_user_id", 4),
        (400, "path_traversal", 4),
        (400, "invalid_archive", 4),
        (400, "empty_archive", 4),
        (400, "size_exceeded", 4),
        (400, "missing_skill_md", 4),
        (400, "nested_package_not_supported", 4),
        (400, "skill_md_not_at_root", 4),
        (400, "invalid_skill_md", 4),
        (409, "prefix_exists", 4),
        (409, "not_terminal", 4),
        (413, "body_too_large", 4),
        (429, "queue_full", 5),
    ],
)
def test_client_passes_through_upstream_error_code_verbatim(
    isolated_data_dir, tmp_path, monkeypatch, upstream_status, upstream_code, expected_exit
):
    """For each upstream `error.code` from the §1A pass-through inventory,
    the client's stdout `error.code` field must equal the upstream value
    byte-for-byte (no string rewriting on the client side either)."""
    skill_dir = tmp_path / "mySkill"
    skill_dir.mkdir()
    monkeypatch.setattr(cli, "_resolve_skill_dir", lambda name: skill_dir)
    monkeypatch.setattr(cli, "package_skill", lambda d: _bundle())

    fake_client = MagicMock()
    fake_client.upload.side_effect = ApiError(
        status=upstream_status,
        code=upstream_code,
        message="upstream said no",
        details=None,
    )
    monkeypatch.setattr(cli, "GatewayClient", lambda **kw: _ctx(fake_client))

    rc, env = _run_cli(["--skill-name", "mySkill"])
    assert rc == expected_exit, f"expected exit {expected_exit} for {upstream_code}, got {rc}"
    assert env["error"]["code"] == upstream_code, (
        f"client must pass {upstream_code!r} through verbatim, got {env['error']['code']!r}"
    )


# ---------------------------------------------------------------------------
# Regression: _resolve_skill_dir must return the *directory* containing
# SKILL.md, not the SKILL.md file itself. `skill_enhance_helper.resolve_skill`
# returns the path to the SKILL.md file (`.../<skill>/SKILL.md`); a previous
# bug treated that as the skill directory and the packager rejected it with
# `missing_skill_dir`. The fix takes `.parent` before handing it to the
# packager — this test pins that contract.
# ---------------------------------------------------------------------------


def test_resolve_skill_dir_returns_parent_directory_of_skill_md(tmp_path, monkeypatch):
    skill_dir = tmp_path / "myskill"
    skill_dir.mkdir()
    skill_md = skill_dir / "SKILL.md"
    skill_md.write_text("# x\n", encoding="utf-8")

    def _fake_resolve(name):
        # Mirrors the real helper: returns (name, content, path-to-SKILL.md).
        return (name, "# x\n", str(skill_md))

    monkeypatch.setattr("mega_code.client.skill_enhance_helper.resolve_skill", _fake_resolve)

    resolved = cli._resolve_skill_dir("myskill")
    assert resolved == skill_dir, (
        f"_resolve_skill_dir must return the directory containing SKILL.md, "
        f"got {resolved} (a {'file' if resolved.is_file() else 'directory'})"
    )
    assert resolved.is_dir()


# ---------------------------------------------------------------------------
# Helper: context-manager-shaped fake that returns the same client on __enter__
# ---------------------------------------------------------------------------


class _ContextManagerWrapper:
    def __init__(self, inner):
        self._inner = inner

    def __enter__(self):
        return self._inner

    def __exit__(self, *_exc):
        return None


def _ctx(inner):
    return _ContextManagerWrapper(inner)


# ---------------------------------------------------------------------------
# --list-cached — local cache scan for prefix_exists fallback
# ---------------------------------------------------------------------------


def _write_cache_entry(
    cache_dir: Path,
    *,
    job_id: str,
    skill_name: str | None,
    status: str = "succeeded",
    artifact_kind: str = "enhanced",
    completed_at: str = "2026-04-30T00:00:00Z",
    roi: dict | None = None,
) -> None:
    cache_dir.mkdir(parents=True, exist_ok=True)
    body: dict = {
        "job_id": job_id,
        "status": status,
        "artifact_kind": artifact_kind,
        "completed_at": completed_at,
        "roi": roi,
    }
    if skill_name is not None:
        body["skill_name"] = skill_name
    (cache_dir / f"{job_id}.result.json").write_text(json.dumps(body))


def test_list_cached_requires_skill_name(isolated_data_dir):
    rc, env = _run_cli(["--list-cached"])
    assert rc == 4
    assert env["error"]["code"] == "missing_skill_name"


def test_list_cached_returns_only_matching_skill_succeeded_enhanced(isolated_data_dir):
    cache = isolated_data_dir / "enhancements"
    # match
    _write_cache_entry(
        cache,
        job_id="job-A",
        skill_name="python-ruff-lint",
        completed_at="2026-04-29T10:00:00Z",
        roi={"performance_increase": "5%"},
    )
    # match — newer
    _write_cache_entry(
        cache,
        job_id="job-B",
        skill_name="python-ruff-lint",
        completed_at="2026-04-30T10:00:00Z",
        roi={"performance_increase": "8%"},
    )
    # different skill
    _write_cache_entry(cache, job_id="job-C", skill_name="other-skill")
    # right skill but not enhanced
    _write_cache_entry(
        cache,
        job_id="job-D",
        skill_name="python-ruff-lint",
        artifact_kind="rejected",
    )
    # right skill but not succeeded
    _write_cache_entry(
        cache,
        job_id="job-E",
        skill_name="python-ruff-lint",
        status="failed",
    )
    # legacy entry without skill_name field — must not match
    _write_cache_entry(cache, job_id="job-F", skill_name=None)

    rc, env = _run_cli(["--list-cached", "--skill-name", "python-ruff-lint"])
    assert rc == 0
    job_ids = [c["job_id"] for c in env["cached"]]
    assert job_ids == ["job-B", "job-A"], "newest-first by completed_at"
    assert env["cached"][0]["roi"] == {"performance_increase": "8%"}


def test_list_cached_no_matches_returns_empty_list(isolated_data_dir):
    rc, env = _run_cli(["--list-cached", "--skill-name", "nothing-here"])
    assert rc == 0
    assert env == {"cached": []}


def test_list_cached_skips_corrupt_cache_files(isolated_data_dir):
    cache = isolated_data_dir / "enhancements"
    cache.mkdir(parents=True)
    (cache / "broken.result.json").write_text("{not valid json")
    _write_cache_entry(cache, job_id="job-ok", skill_name="my-skill")

    rc, env = _run_cli(["--list-cached", "--skill-name", "my-skill"])
    assert rc == 0
    assert [c["job_id"] for c in env["cached"]] == ["job-ok"]


def test_save_result_cache_writes_skill_name(isolated_data_dir):
    cli._save_result_cache(
        "job-xyz",
        {"status": "succeeded", "artifact_kind": "enhanced", "job_id": "job-xyz"},
        skill_name="python-ruff-lint",
    )
    body = json.loads((isolated_data_dir / "enhancements" / "job-xyz.result.json").read_text())
    assert body["skill_name"] == "python-ruff-lint"
    # upstream fields preserved
    assert body["status"] == "succeeded"
    assert body["artifact_kind"] == "enhanced"


def test_save_result_cache_omits_skill_name_when_none(isolated_data_dir):
    cli._save_result_cache(
        "job-legacy",
        {"status": "succeeded", "artifact_kind": "enhanced", "job_id": "job-legacy"},
    )
    body = json.loads((isolated_data_dir / "enhancements" / "job-legacy.result.json").read_text())
    assert "skill_name" not in body


# ---------------------------------------------------------------------------
# --result-json — atomic envelope file (avoids tail-the-log flake)
# ---------------------------------------------------------------------------


def test_result_json_writes_envelope_to_file(isolated_data_dir, tmp_path):
    """SKILL.md Phase 4 reads from this file instead of tailing the log."""
    target = tmp_path / "out.result.json"
    rc, env = _run_cli(
        ["--list-cached", "--skill-name", "doesNotExist", "--result-json", str(target)]
    )
    assert rc == 0
    assert env == {"cached": []}
    assert target.is_file()
    on_disk = json.loads(target.read_text())
    assert on_disk == env


def test_result_json_write_failure_does_not_change_exit_code(isolated_data_dir, tmp_path):
    """A failed --result-json write must not perturb the exit code — the
    envelope is still emitted to stdout, which the slash command can fall
    back to. The harness logs the failure but the contract holds."""
    # Point --result-json at a path whose parent is a regular file (so mkdir fails).
    blocker = tmp_path / "blocker"
    blocker.write_text("not a dir")
    bad_path = blocker / "out.result.json"
    rc, env = _run_cli(["--list-cached", "--skill-name", "x", "--result-json", str(bad_path)])
    assert rc == 0
    assert env == {"cached": []}


# ---------------------------------------------------------------------------
# _terminal_no_install_envelope — full upstream body forwarding
# ---------------------------------------------------------------------------


def test_terminal_no_install_envelope_forwards_invariants_and_evidence():
    """Regression: previous shape was ``{"reason": body.get("reason")}`` which
    silently dropped invariants/evidence. SKILL.md Phase 4 sub-shape 3 walks
    the full body to render rejection detail; the envelope must carry it."""
    body = {
        "status": "invariant_violation",
        "artifact_kind": None,
        "invariants": ["frontmatter.author missing", "tools list malformed"],
        "evidence": {"validator": "structural", "fired": 2},
        "reason": "two structural invariants tripped",
        "extra_unknown_field": "should still survive",
    }
    env = cli._terminal_no_install_envelope(body)
    assert env["status"] == "invariant_violation"
    assert env["installed"] is False
    # Full body forwarded — every original key is reachable via env["result"].
    assert env["result"] is body or env["result"] == body
    assert env["result"]["invariants"] == ["frontmatter.author missing", "tools list malformed"]
    assert env["result"]["evidence"] == {"validator": "structural", "fired": 2}
    assert env["result"]["reason"] == "two structural invariants tripped"
    assert env["result"]["extra_unknown_field"] == "should still survive"


# ---------------------------------------------------------------------------
# --cleanup-original — guarded delete with symlink-resolution
# ---------------------------------------------------------------------------


def test_cleanup_original_removes_dir_under_skills_root(isolated_data_dir, tmp_path, monkeypatch):
    """Happy path: the dir is under ``<project>/.claude/skills/`` so deletion
    succeeds and a structured envelope is emitted."""
    skills_root = tmp_path / ".claude" / "skills"
    skills_root.mkdir(parents=True)
    target = skills_root / "mySkill"
    target.mkdir()
    (target / "SKILL.md").write_text("---\nname: mySkill\n---\n")

    rc, env = _run_cli(["--cleanup-original", str(target), "--project-dir", str(tmp_path)])
    assert rc == 0
    assert env == {"removed": str(target.resolve())}
    assert not target.exists()


def test_cleanup_original_refuses_path_outside_skills_root(isolated_data_dir, tmp_path):
    """Defense in depth: a path that exists but lives outside any canonical
    skills root must be refused with cleanup_unsafe_path, not deleted."""
    rogue = tmp_path / "rogue"
    rogue.mkdir()
    (rogue / "marker").write_text("important")

    rc, env = _run_cli(["--cleanup-original", str(rogue), "--project-dir", str(tmp_path)])
    assert rc == 4
    assert env["error"]["code"] == "cleanup_unsafe_path"
    assert rogue.exists()  # NOT deleted
    assert (rogue / "marker").read_text() == "important"


def test_cleanup_original_refuses_nonexistent_path(isolated_data_dir, tmp_path):
    """A bogus path returns cleanup_path_missing (exit 4) — the slash command
    surfaces the error.code so the user knows the original was already gone."""
    rc, env = _run_cli(
        [
            "--cleanup-original",
            str(tmp_path / "does-not-exist"),
            "--project-dir",
            str(tmp_path),
        ]
    )
    assert rc == 4
    assert env["error"]["code"] == "cleanup_path_missing"


def test_cleanup_original_refuses_skills_root_itself(isolated_data_dir, tmp_path):
    """Catastrophic input: deleting the skills root would clobber every
    installed skill. Guarded delete refuses with cleanup_unsafe_path."""
    skills_root = tmp_path / ".claude" / "skills"
    skills_root.mkdir(parents=True)
    (skills_root / "keep" / "SKILL.md").parent.mkdir()
    (skills_root / "keep" / "SKILL.md").write_text("placeholder")

    rc, env = _run_cli(["--cleanup-original", str(skills_root), "--project-dir", str(tmp_path)])
    assert rc == 4
    assert env["error"]["code"] == "cleanup_unsafe_path"
    assert skills_root.exists()
    assert (skills_root / "keep" / "SKILL.md").exists()
