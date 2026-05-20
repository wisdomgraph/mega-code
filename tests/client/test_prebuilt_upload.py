"""Unit tests for ``mega_code.client.prebuilt_upload``.

Covers the design-doc §2E matrix:
  - packager reuse
  - happy path (fresh) / replay path (`Idempotent-Replayed: true`)
  - deterministic key + negative regression guard (no ``user_id`` param)
  - error mapping for each upstream error code
  - metadata.json travels in the bundle
  - --iteration validation, --idempotency-key validation, mutual exclusion
  - HTTP retry / transport translation (NetworkError, AuthError)
"""

from __future__ import annotations

import inspect
import io
import json
import re
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import MagicMock, patch

import httpx
import pytest
from tenacity import wait_none

from mega_code.client import prebuilt_upload as cli
from mega_code.client.remote_enhance.client import GatewayClient
from mega_code.client.remote_enhance.packager import Bundle, PackagerError


@pytest.fixture(autouse=True)
def _no_retry_wait(monkeypatch):
    """Drop tenacity's exponential backoff in tests — keeps the suite fast."""
    monkeypatch.setattr(GatewayClient.upload_prebuilt.retry, "wait", wait_none())


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _api_key(monkeypatch):
    """``GatewayClient.__init__`` requires MEGA_CODE_API_KEY."""
    monkeypatch.setenv("MEGA_CODE_API_KEY", "test-key")
    monkeypatch.setenv("MEGA_CODE_SERVER_URL", "http://gateway.test")


def _canonical_body(skill_id: str = "headline-scorer") -> dict:
    return {
        "id": "8c4d2e1a-1111-2222-3333-444455556666",
        "skill_id": skill_id,
        "s3_prefix": f"enhanced/8c4d2e1a-1111-2222-3333-444455556666/{skill_id}/",
        "content_hash": "13420e8c" + "0" * 56,
        "file_count": 2,
        "uploaded_bytes": 18432,
        "source_revision": "v-abc123",
        "files": [
            {"relpath": "SKILL.md", "sha256": "a" * 64, "size": 824},
            {"relpath": "references/x.md", "sha256": "b" * 64, "size": 5120},
        ],
    }


def _bundle() -> Bundle:
    return Bundle(archive_bytes=b"PK\x03\x04zip", content_hash="ch-deadbeef", files=())


def _run_cli(argv: list[str]) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        rc = cli.main(argv)
    return rc, out.getvalue(), err.getvalue()


def _mock_response(
    body: dict,
    *,
    status: int = 201,
    replayed: bool = False,
) -> MagicMock:
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = status
    resp.headers = {"Idempotent-Replayed": "true"} if replayed else {}
    resp.json.return_value = body
    resp.text = json.dumps(body)
    resp.reason_phrase = "Created" if status == 201 else "Error"
    return resp


# ---------------------------------------------------------------------------
# Deterministic key
# ---------------------------------------------------------------------------


def test_compute_idempotency_key_deterministic():
    a = cli._compute_idempotency_key("foo", 3)
    b = cli._compute_idempotency_key("foo", 3)
    assert a == b
    assert len(a) == 32
    assert re.fullmatch(r"^[A-Za-z0-9_-]{1,64}$", a)


def test_compute_idempotency_key_changes_with_args():
    base = cli._compute_idempotency_key("foo", 3)
    assert cli._compute_idempotency_key("bar", 3) != base
    assert cli._compute_idempotency_key("foo", 4) != base


def test_compute_idempotency_key_signature_has_no_user_id():
    """Negative regression guard — earlier drafts included user_id in the hash."""
    sig = inspect.signature(cli._compute_idempotency_key)
    assert "user_id" not in sig.parameters
    assert list(sig.parameters) == ["skill_name", "iteration"]


# ---------------------------------------------------------------------------
# Packager reuse + happy/replay paths
# ---------------------------------------------------------------------------


def test_happy_path_calls_packager_and_emits_success(tmp_path, monkeypatch):
    bundle_dir = tmp_path / "myskill"
    bundle_dir.mkdir()
    pkg_calls: list[Path] = []

    def fake_package(d: Path) -> Bundle:
        pkg_calls.append(d)
        return _bundle()

    monkeypatch.setattr(cli, "package_skill", fake_package)

    body = _canonical_body()
    with patch("httpx.Client.post", return_value=_mock_response(body)) as post:
        rc, out, err = _run_cli(
            [
                "--skill-id",
                "headline-scorer",
                "--bundle-dir",
                str(bundle_dir),
                "--iteration",
                "3",
            ]
        )

    assert rc == 0, err
    assert pkg_calls == [bundle_dir]
    # Verify archive_bytes flowed through verbatim
    _, kwargs = post.call_args
    assert kwargs["files"]["archive"][1] == b"PK\x03\x04zip"
    assert kwargs["data"] == {"skill_id": "headline-scorer"}
    assert kwargs["headers"]["Idempotency-Key"] == cli._compute_idempotency_key(
        "headline-scorer", 3
    )
    assert "SUCCESS: prebuilt-upload" in out
    assert "replayed=false" in out
    # Last stdout line is the JSON envelope
    envelope = json.loads(out.strip().splitlines()[-1])
    assert envelope["s3_prefix"] == body["s3_prefix"]
    assert envelope["replayed"] is False


def test_replay_path_sets_replayed_true(tmp_path, monkeypatch):
    bundle_dir = tmp_path / "myskill"
    bundle_dir.mkdir()
    monkeypatch.setattr(cli, "package_skill", lambda d: _bundle())

    body = _canonical_body()
    with patch("httpx.Client.post", return_value=_mock_response(body, replayed=True)):
        rc, out, _ = _run_cli(
            [
                "--skill-id",
                "headline-scorer",
                "--bundle-dir",
                str(bundle_dir),
                "--iteration",
                "3",
            ]
        )

    assert rc == 0
    assert "replayed=true" in out
    envelope = json.loads(out.strip().splitlines()[-1])
    assert envelope["replayed"] is True
    assert envelope["s3_prefix"] == body["s3_prefix"]


# ---------------------------------------------------------------------------
# Argparse / arg validation
# ---------------------------------------------------------------------------


def test_iteration_negative_exits_1(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "package_skill", lambda d: _bundle())
    with patch("httpx.Client.post") as post:
        rc, _, err = _run_cli(
            [
                "--skill-id",
                "s",
                "--bundle-dir",
                str(tmp_path),
                "--iteration",
                "-1",
            ]
        )
    assert rc == 1
    assert "non-negative" in err
    post.assert_not_called()


def test_iteration_non_integer_exits_2_from_argparse(tmp_path):
    """argparse rejects non-int → SystemExit(2) before our code runs."""
    with pytest.raises(SystemExit):
        _run_cli(
            [
                "--skill-id",
                "s",
                "--bundle-dir",
                str(tmp_path),
                "--iteration",
                "not-an-int",
            ]
        )


def test_iteration_and_idempotency_key_mutually_exclusive(tmp_path):
    with pytest.raises(SystemExit):
        _run_cli(
            [
                "--skill-id",
                "s",
                "--bundle-dir",
                str(tmp_path),
                "--iteration",
                "1",
                "--idempotency-key",
                "abc",
            ]
        )


def test_neither_iteration_nor_key_required(tmp_path):
    with pytest.raises(SystemExit):
        _run_cli(["--skill-id", "s", "--bundle-dir", str(tmp_path)])


def test_explicit_idempotency_key_passes_through(tmp_path, monkeypatch):
    bundle_dir = tmp_path / "x"
    bundle_dir.mkdir()
    monkeypatch.setattr(cli, "package_skill", lambda d: _bundle())
    with patch("httpx.Client.post", return_value=_mock_response(_canonical_body())) as post:
        rc, _, _ = _run_cli(
            [
                "--skill-id",
                "s",
                "--bundle-dir",
                str(bundle_dir),
                "--idempotency-key",
                "deadbeef-cafe-1234",
            ]
        )
    assert rc == 0
    assert post.call_args.kwargs["headers"]["Idempotency-Key"] == "deadbeef-cafe-1234"


def test_idempotency_key_pattern_rejected(tmp_path):
    rc, _, err = _run_cli(
        [
            "--skill-id",
            "s",
            "--bundle-dir",
            str(tmp_path),
            "--idempotency-key",
            "has spaces",
        ]
    )
    assert rc == 1
    assert "idempotency-key" in err


# ---------------------------------------------------------------------------
# Error mapping
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "code,status",
    [
        ("invalid_skill_id", 400),
        ("invalid_archive", 400),
        ("skill_md_not_at_root", 400),
        ("body_too_large", 413),
        ("invalid_user_id", 400),
        ("missing_user_id", 400),
        ("invalid_idempotency_key", 400),
    ],
)
def test_upstream_error_codes_exit_3(code, status, tmp_path, monkeypatch):
    bundle_dir = tmp_path / "x"
    bundle_dir.mkdir()
    monkeypatch.setattr(cli, "package_skill", lambda d: _bundle())
    body = {"error": {"code": code, "message": f"upstream said {code}"}}
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = status
    resp.headers = {}
    resp.json.return_value = body
    resp.text = json.dumps(body)
    resp.reason_phrase = "Bad Request"
    with patch("httpx.Client.post", return_value=resp):
        rc, _, err = _run_cli(
            [
                "--skill-id",
                "s",
                "--bundle-dir",
                str(bundle_dir),
                "--iteration",
                "1",
            ]
        )
    assert rc == 3
    assert code in err


def test_auth_error_exits_4(tmp_path, monkeypatch):
    bundle_dir = tmp_path / "x"
    bundle_dir.mkdir()
    monkeypatch.setattr(cli, "package_skill", lambda d: _bundle())
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = 401
    resp.headers = {}
    resp.reason_phrase = "Unauthorized"
    with patch("httpx.Client.post", return_value=resp):
        rc, _, err = _run_cli(
            [
                "--skill-id",
                "s",
                "--bundle-dir",
                str(bundle_dir),
                "--iteration",
                "1",
            ]
        )
    assert rc == 4
    assert "auth" in err


def test_packager_error_exits_2(tmp_path, monkeypatch):
    bundle_dir = tmp_path / "x"
    bundle_dir.mkdir()

    def boom(d: Path) -> Bundle:
        raise PackagerError("forbidden_path", "refusing .env")

    monkeypatch.setattr(cli, "package_skill", boom)
    rc, _, err = _run_cli(
        [
            "--skill-id",
            "s",
            "--bundle-dir",
            str(bundle_dir),
            "--iteration",
            "1",
        ]
    )
    assert rc == 2
    assert "forbidden_path" in err


def test_network_error_exits_4(tmp_path, monkeypatch):
    bundle_dir = tmp_path / "x"
    bundle_dir.mkdir()
    monkeypatch.setattr(cli, "package_skill", lambda d: _bundle())

    def raise_connect(*a, **kw):
        raise httpx.ConnectError("nope")

    with patch("httpx.Client.post", side_effect=raise_connect):
        rc, _, err = _run_cli(
            [
                "--skill-id",
                "s",
                "--bundle-dir",
                str(bundle_dir),
                "--iteration",
                "1",
            ]
        )
    assert rc == 4
    assert "network" in err


# ---------------------------------------------------------------------------
# HTTP retry — 5xx retries via tenacity; 4xx does not.
# ---------------------------------------------------------------------------


def _bad_5xx(status: int = 503) -> MagicMock:
    bad = MagicMock(spec=httpx.Response)
    bad.status_code = status
    bad.headers = {}
    bad.reason_phrase = "Service Unavailable"
    bad.text = "unavail"
    bad.raise_for_status.side_effect = httpx.HTTPStatusError(
        f"{status}", request=MagicMock(), response=bad
    )
    return bad


def test_retry_on_503_then_success(tmp_path, monkeypatch):
    """Two transient 503s, then 201 → succeeds; exactly 3 POSTs made."""
    bundle_dir = tmp_path / "x"
    bundle_dir.mkdir()
    monkeypatch.setattr(cli, "package_skill", lambda d: _bundle())

    good = _mock_response(_canonical_body())
    with patch(
        "httpx.Client.post",
        side_effect=[_bad_5xx(), _bad_5xx(), good],
    ) as post:
        rc, out, err = _run_cli(
            [
                "--skill-id",
                "s",
                "--bundle-dir",
                str(bundle_dir),
                "--iteration",
                "1",
            ]
        )
    assert rc == 0, err
    assert post.call_count == 3
    assert "SUCCESS" in out


def test_persistent_503_exits_4(tmp_path, monkeypatch):
    """All 5 attempts return 503 → NetworkError → exit 4."""
    bundle_dir = tmp_path / "x"
    bundle_dir.mkdir()
    monkeypatch.setattr(cli, "package_skill", lambda d: _bundle())

    with patch("httpx.Client.post", return_value=_bad_5xx()) as post:
        rc, _, err = _run_cli(
            [
                "--skill-id",
                "s",
                "--bundle-dir",
                str(bundle_dir),
                "--iteration",
                "1",
            ]
        )
    assert rc == 4
    assert "network" in err
    assert post.call_count == 5  # tenacity's stop_after_attempt(5)


def test_no_retry_on_4xx(tmp_path, monkeypatch):
    """4xx is not retryable — exactly one POST, exit 3 with the upstream code."""
    bundle_dir = tmp_path / "x"
    bundle_dir.mkdir()
    monkeypatch.setattr(cli, "package_skill", lambda d: _bundle())

    body = {"error": {"code": "invalid_skill_id", "message": "bad id"}}
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = 400
    resp.headers = {}
    resp.json.return_value = body
    resp.text = json.dumps(body)
    resp.reason_phrase = "Bad Request"
    with patch("httpx.Client.post", return_value=resp) as post:
        rc, _, err = _run_cli(
            [
                "--skill-id",
                "s",
                "--bundle-dir",
                str(bundle_dir),
                "--iteration",
                "1",
            ]
        )
    assert rc == 3
    assert "invalid_skill_id" in err
    assert post.call_count == 1


# ---------------------------------------------------------------------------
# Bundle contents: wisdom-gen sidecars are silently filtered out
# ---------------------------------------------------------------------------


def _bundle_names_after_upload(bundle_dir, tmp_path):
    """Run the CLI against ``bundle_dir`` with a mocked gateway, return the
    set of names that ended up in the zip."""
    captured: dict = {}

    def capture(*args, **kwargs):
        captured["files"] = kwargs.get("files")
        return _mock_response(_canonical_body())

    with patch("httpx.Client.post", side_effect=capture):
        rc, _, err = _run_cli(
            [
                "--skill-id",
                "skill",
                "--bundle-dir",
                str(bundle_dir),
                "--iteration",
                "1",
            ]
        )
    assert rc == 0, err

    import zipfile

    archive_bytes = captured["files"]["archive"][1]
    with zipfile.ZipFile(io.BytesIO(archive_bytes)) as zf:
        return set(zf.namelist())


def test_wisdom_gen_sidecars_excluded_from_bundle(tmp_path):
    """End-to-end through the *real* packager — evidence/injection and
    wisdom-gen-shaped metadata.json are filtered; SKILL.md and user-authored
    files travel."""
    bundle_dir = tmp_path / "skill"
    bundle_dir.mkdir()
    (bundle_dir / "SKILL.md").write_text("---\nname: x\n---\nbody\n")
    (bundle_dir / "metadata.json").write_text(
        '{"skill_id": "x", "run_id": "abc", "output_files": [], "roi": {}}'
    )
    (bundle_dir / "evidence.json").write_text("{}")
    (bundle_dir / "injection.json").write_text("{}")
    (bundle_dir / "references").mkdir()
    (bundle_dir / "references" / "x.md").write_text("ref\n")

    names = _bundle_names_after_upload(bundle_dir, tmp_path)
    assert names == {"SKILL.md", "references/x.md"}


def test_foreign_metadata_json_is_kept_in_bundle(tmp_path):
    """Regression guard: third-party skill ecosystems use the same filename
    `metadata.json` for unrelated payloads (marketplace catalogs, authored
    skill metadata, user content). Those must NOT be silently dropped — only
    the wisdom-gen shape (skill_id + run_id keys) is filtered."""
    bundle_dir = tmp_path / "skill"
    bundle_dir.mkdir()
    (bundle_dir / "SKILL.md").write_text("---\nname: x\n---\nbody\n")
    # Marketplace-catalog shape — present in many third-party skills under
    # /Users/.../SKILLS/ (clojure-write, gpt5-consultant, etc.).
    (bundle_dir / "metadata.json").write_text(
        '{"id": "foo", "name": "x", "author": "bar", "authorAvatar": "https://..."}'
    )

    names = _bundle_names_after_upload(bundle_dir, tmp_path)
    assert names == {"SKILL.md", "metadata.json"}
