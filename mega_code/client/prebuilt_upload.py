"""Upload an accepted enhanced-skill bundle to the gateway's prebuilt route.

Phase 8 of ``/mega-code:skill-enhance`` calls this CLI after the local
``accept-skill`` and remote ``store-skill`` steps succeed. The bundle (zip
of ``SKILL.md`` + adjacent ``references/`` / ``scripts/`` / ``assets/`` /
``metadata.json``) is POSTed to
``/api/megacode/v1/skill-enhance/uploads/prebuilt`` and written to the
shared S3 prefix on the upstream side.

Idempotency: a deterministic key derived from ``(skill_name, iteration)``
is sent on every call so a retry of the same iteration replays the prior
row instead of producing a duplicate. The upstream's unique index is
``(uploaded_by_user_id, idempotency_key)`` — per-user namespacing comes
from the row identity, not the hashed value, so ``user_id`` is
intentionally not in the hash (the client doesn't know it; the gateway
injects ``X-Mega-User-Id`` server-side from ``VerifiedUser``).

Stdout on success is two lines: a ``SUCCESS: prebuilt-upload …`` summary
(grepped by the SKILL.md to detect the success path) and the full
``PrebuiltUploadResponse`` JSON envelope (for telemetry / E2E assertions).

Exit codes:
    0 — 201 Created (fresh or replayed)
    1 — argparse / arg validation
    2 — packager refused (forbidden path, size cap, missing SKILL.md)
    3 — gateway/upstream 4xx (``error.code`` printed to stderr)
    4 — auth (401/403) or persistent network failure
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from mega_code.client.cli import get_env_path, load_env_file
from mega_code.client.remote_enhance.client import (
    ApiError,
    AuthError,
    GatewayClient,
    NetworkError,
)
from mega_code.client.remote_enhance.packager import PackagerError, package_skill
from mega_code.client.utils.tracing import setup_tracing

_IDEMPOTENCY_KEY_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


class PrebuiltFile(BaseModel):
    """One file entry in the upstream ``PrebuiltUploadResponse.files`` array."""

    model_config = ConfigDict(frozen=True)

    relpath: str
    sha256: str
    size: int


class PrebuiltUploadResponse(BaseModel):
    """Mirrors the upstream ``PrebuiltUploadResponse`` shape verbatim.

    The ``replayed`` field is composed client-side from the
    ``Idempotent-Replayed`` response header (not part of the upstream JSON
    body) so the caller can branch on it without reading raw headers.
    """

    model_config = ConfigDict(frozen=True)

    id: str
    skill_id: str
    s3_prefix: str
    content_hash: str
    file_count: int
    uploaded_bytes: int
    source_revision: str
    files: tuple[PrebuiltFile, ...]
    replayed: bool = False


def _compute_idempotency_key(skill_name: str, iteration: int) -> str:
    """Deterministic key per ``(skill_name, iteration)``.

    Deliberately does **not** accept ``user_id``: upstream's unique index is
    ``(uploaded_by_user_id, idempotency_key)`` and the gateway derives
    ``user_id`` from the bearer token server-side, so the hash doesn't need
    per-user entropy. See design doc §2B.
    """
    payload = f"skill-enhance:{skill_name}:{iteration}".encode()
    return hashlib.sha256(payload).hexdigest()[:32]


def upload_prebuilt_bundle(
    *,
    skill_id: str,
    bundle_dir: Path,
    idempotency_key: str | None = None,
) -> PrebuiltUploadResponse:
    """Package ``bundle_dir`` and POST to the gateway prebuilt route.

    Raises ``PackagerError`` on bundle-level refusals. Raises ``AuthError`` /
    ``ApiError`` on auth or 4xx. Raises ``NetworkError`` on transient
    failures (post-retry-budget) or persistent 5xx — the CLI maps each to
    its own exit code.
    """
    bundle = package_skill(bundle_dir)
    with GatewayClient() as client:
        body, replayed = client.upload_prebuilt(
            archive_bytes=bundle.archive_bytes,
            skill_id=skill_id,
            idempotency_key=idempotency_key,
        )
    return PrebuiltUploadResponse.model_validate({**body, "replayed": replayed})


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="mega_code.client.prebuilt_upload",
        description="Upload an enhanced-skill bundle to the prebuilt S3 sink.",
    )
    parser.add_argument("--skill-id", required=True, help="canonical skill name")
    parser.add_argument(
        "--bundle-dir",
        required=True,
        type=Path,
        help="folder containing SKILL.md (typically dirname($SKILL_PATH))",
    )
    key_group = parser.add_mutually_exclusive_group(required=True)
    key_group.add_argument(
        "--iteration",
        type=int,
        help="iteration number — used to derive the deterministic idempotency key",
    )
    key_group.add_argument(
        "--idempotency-key",
        help="override the derived key (matches ^[A-Za-z0-9_-]{1,64}$)",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    # Load .env so MEGA_CODE_API_KEY / OTEL_EXPORTER_OTLP_* are visible when
    # the slash command's bash blocks don't source it across separate Bash
    # tool calls. Same pattern as remote_enhance/__main__.py.
    for _key, _value in load_env_file(get_env_path()).items():
        os.environ.setdefault(_key, _value)
    # Install the OTLP exporter so the @traced decorators on GatewayClient
    # actually export spans. Without this, decorated functions run under a
    # no-op tracer and nothing reaches Phoenix / Honeycomb.
    setup_tracing(service_name="mega-code-client")

    args = _parse_args(argv)

    if args.idempotency_key is not None:
        if not _IDEMPOTENCY_KEY_PATTERN.fullmatch(args.idempotency_key):
            print(
                f"error: --idempotency-key must match {_IDEMPOTENCY_KEY_PATTERN.pattern}",
                file=sys.stderr,
            )
            return 1
        idempotency_key = args.idempotency_key
    else:
        if args.iteration < 0:
            print("error: --iteration must be a non-negative integer", file=sys.stderr)
            return 1
        idempotency_key = _compute_idempotency_key(args.skill_id, args.iteration)

    try:
        response = upload_prebuilt_bundle(
            skill_id=args.skill_id,
            bundle_dir=args.bundle_dir,
            idempotency_key=idempotency_key,
        )
    except PackagerError as exc:
        print(f"error: {exc.code}: {exc.message}", file=sys.stderr)
        return 2
    except ApiError as exc:
        print(f"error: {exc.code}: {exc.message}", file=sys.stderr)
        return 3
    except AuthError as exc:
        print(f"error: auth: {exc}", file=sys.stderr)
        return 4
    except NetworkError as exc:
        print(f"error: network: {exc}", file=sys.stderr)
        return 4

    print(
        f"SUCCESS: prebuilt-upload s3_prefix={response.s3_prefix} "
        f"replayed={str(response.replayed).lower()}"
    )
    print(json.dumps(response.model_dump()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
