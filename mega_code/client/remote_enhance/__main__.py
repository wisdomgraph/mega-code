"""``python -m mega_code.client.remote_enhance`` — CLI entry point.

Three modes (mutually exclusive):

  1. **default**: package the skill folder, upload, poll, halt at
     "ready-to-install". Stdout is a single JSON envelope; SKILL.md
     prompts the user, then re-invokes mode 3.
  2. ``--poll-existing <job_id>``: skip package+upload, jump to poll,
     then halt at "ready-to-install".
  3. ``--install-existing <job_id> --install-location <choice>``:
     skip package+upload+poll; install from the staging dir written
     by a prior mode-1 or mode-2 invocation.

Exit codes:

  | code | meaning                                           |
  |------|---------------------------------------------------|
  | 0    | terminal status reached + install state resolved  |
  | 2    | 409 duplicate_content_hash (with existing_job_id) |
  | 3    | poll timeout                                      |
  | 4    | bad input / sha256 / frontmatter / install_loc    |
  | 5    | auth or network failure                           |

Stderr carries human-readable progress lines (filtered through
``SecretMasker`` so a Bearer token never leaks to a tee'd log).
Stdout is reserved for the final JSON envelope.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any

from mega_code.client.cli import get_env_path, load_env_file
from mega_code.client.dirs import data_dir
from mega_code.client.filters.secrets import SecretMasker
from mega_code.client.remote_enhance.client import (
    ApiError,
    AuthError,
    GatewayClient,
    NetworkError,
)
from mega_code.client.remote_enhance.installer import (
    InstallerError,
    download_artifact,
    install_to,
    staging_root,
    validate_frontmatter,
)
from mega_code.client.remote_enhance.packager import PackagerError, package_skill
from mega_code.client.remote_enhance.poller import PollTimeout
from mega_code.client.remote_enhance.poller import run as run_poller
from mega_code.client.utils.tracing import (
    get_tracer,
    set_span_attributes,
    setup_tracing,
    traced,
)

logger = logging.getLogger("mega_code.remote_enhance")

# Map of upstream error.code → CLI exit code for the 4xx pass-through
# inventory. Only ``duplicate_content_hash`` gets exit 2 (it's the
# resumable case the SKILL.md offers as "use the existing job"); everything
# else maps to exit 4 (caller-actionable bad input) or 5 (auth/network).
_EXIT_CODE_BY_API_CODE: dict[str, int] = {
    "duplicate_content_hash": 2,
    "queue_full": 5,
    # All other 4xx codes from the §1A inventory map to exit 4 (the default).
}

_masker = SecretMasker()


def _emit(envelope: dict[str, Any]) -> None:
    """Print the final JSON envelope to stdout. Caller exits immediately after."""
    sys.stdout.write(json.dumps(envelope, sort_keys=True))
    sys.stdout.write("\n")
    sys.stdout.flush()


def _progress(line: str) -> None:
    """Write a progress line to stderr, filtered for Bearer-token leakage."""
    sys.stderr.write(_masker.filter_text(line))
    sys.stderr.write("\n")
    sys.stderr.flush()


def _result_cache_path(job_id: str) -> Path:
    """Where the result envelope is cached after a successful download.

    **Sibling** of the staging dir (``{data_dir()}/enhancements/{job_id}.result.json``),
    not inside it, because anything inside the staging dir becomes part of
    the install payload — `install_to` copytree's the whole dir into
    ``~/.claude/skills/<skill>/``. A previous version of this code wrote the
    cache to ``staging/result.json``, which then got copied into the user's
    installed skill folder, then re-packaged and re-uploaded on the next
    enhance cycle, causing the upstream's source-carry-forward to publish
    ``result.json`` as part of the artifact in perpetuity. Sibling-file
    placement breaks that cycle: the cache stays scoped to the job (same
    name prefix, same parent dir, easy to reason about lifecycle) but never
    rides along with the install.
    """
    return data_dir() / "enhancements" / f"{job_id}.result.json"


def _save_result_cache(
    job_id: str,
    result_body: dict[str, Any],
    skill_name: str | None = None,
) -> None:
    """Persist the upstream ``JobResult`` envelope so install-existing can
    re-render the same ``result`` payload without a second network call.

    When ``skill_name`` is provided, it is stored as an extra top-level key
    alongside the upstream fields. Other readers use ``body.get(...)`` for
    upstream keys (``status``, ``artifact``, ``artifact_kind``, …) and ignore
    extras, so this is backwards-compatible. The field powers the
    ``--list-cached --skill-name <name>`` lookup that the slash command's
    ``prefix_exists`` exit-4 branch consumes.
    """
    cache_path = _result_cache_path(job_id)
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    payload = dict(result_body)
    if skill_name is not None:
        payload["skill_name"] = skill_name
    cache_path.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")


def _load_result_cache(job_id: str) -> dict[str, Any] | None:
    """Read the cached envelope. ``None`` if missing (caller falls back
    to a network fetch — covers the case where the user runs
    ``--install-existing`` from a fresh shell where ``data_dir()`` was wiped)."""
    cache_path = _result_cache_path(job_id)
    if not cache_path.is_file():
        return None
    try:
        return json.loads(cache_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        # A corrupted cache becomes a silent extra network round-trip otherwise.
        logger.warning("result cache at %s unreadable: %s", cache_path, exc)
        return None


def _resolve_destination(install_location: str, skill_name: str) -> Path:
    """Where the installed skill ends up on disk.

    ``project`` → ``<project-root>/.claude/skills/<skill-name>/`` so VCS captures it.
    ``global``  → ``~/.claude/skills/<skill-name>/`` so it follows the user.

    Delegates to ``skill_enhance_helper`` so the project-root resolution is
    session-aware (Claude session → mapped project dir, then env, then cwd) —
    matching every other client-side skill-discovery path. Hardcoding ``cwd``
    here would mis-locate the install when the user invokes from a sub-dir.
    """
    from mega_code.client.skill_enhance_helper import _project_skills_dir, _user_skills_dir

    if install_location == "project":
        return _project_skills_dir() / skill_name
    if install_location == "global":
        return _user_skills_dir() / skill_name
    # argparse already constrains to those two values, so this is unreachable
    # in practice — but the explicit raise documents the invariant.
    raise ValueError(f"unknown install_location: {install_location!r}")


def _resolve_skill_dir(skill_name: str) -> Path:
    """Locate the skill folder via ``skill_enhance_helper.resolve_skill``.

    The helper returns the path to the **SKILL.md file** (not its parent
    directory) — see [skill_enhance_helper.py:511](mega_code/client/skill_enhance_helper.py#L511)
    docstring: *"Returns: Tuple of (skill_name, skill_md_content, skill_path)"*
    where ``skill_path`` ends in ``.../<skill>/SKILL.md``. The packager
    needs the **directory**, so we take ``.parent`` here. Defer the import
    so unit tests of ``__main__``'s argparse don't pull the (heavy)
    discovery scanner into the import graph.
    """
    from mega_code.client.skill_enhance_helper import resolve_skill

    _name, _content, skill_md_path = resolve_skill(skill_name)
    return Path(skill_md_path).parent


def _api_error_envelope(api: ApiError) -> tuple[int, dict[str, Any]]:
    """Map an ``ApiError`` to (exit_code, stdout_envelope).

    ``duplicate_content_hash`` is the only code that produces a non-error
    sub-shape (exit 2 with the conflict envelope); everything else is the
    canonical exit-4/5 ``{"error": ...}`` envelope.
    """
    if api.code == "duplicate_content_hash":
        details = api.details or {}
        return 2, {
            "conflict": {
                "existing_job_id": details.get("existing_job_id"),
                # `content_hash` is client-side state — the caller provides
                # it (we re-emit it from the packager's pre-upload computation
                # in ``_run_default_mode``).
            }
        }
    exit_code = _EXIT_CODE_BY_API_CODE.get(api.code, 4)
    return exit_code, {
        "error": {
            "code": api.code,
            "message": api.message,
        }
    }


def _ready_to_install_envelope(job_id: str, body: dict[str, Any]) -> dict[str, Any]:
    """Stdout envelope for the "succeeded + enhanced + needs prompt" sub-state."""
    return {
        "status": body.get("status"),
        "artifact_kind": body.get("artifact_kind"),
        "result": body,
        "installed": False,
        "staging_dir": str(staging_root(job_id)),
        "needs_install_location": True,
    }


def _terminal_no_install_envelope(body: dict[str, Any]) -> dict[str, Any]:
    """Stdout envelope for non-succeeded terminals (failed / rejected / etc).

    Forwards the full upstream ``body`` under ``result`` (matching
    ``_ready_to_install_envelope`` and ``_installed_envelope``). The server
    populates ``body`` with rejection-specific fields like ``invariants``,
    ``evidence``, ``evaluation``, and ``reason`` — surfacing only ``reason``
    here used to drop the actual diagnostic detail and force the host agent
    to render a vague "terminal state, no install" message instead of the
    concrete fired-invariant list.
    """
    return {
        "status": body.get("status"),
        "artifact_kind": body.get("artifact_kind"),
        "result": body,
        "installed": False,
    }


def _installed_envelope(body: dict[str, Any], installed_path: Path) -> dict[str, Any]:
    """Stdout envelope for the post-copytree "installed" sub-state."""
    return {
        "status": body.get("status"),
        "artifact_kind": body.get("artifact_kind"),
        "result": body,
        "installed": True,
        "installed_path": str(installed_path),
    }


def _is_enhanced_succeeded(body: dict[str, Any]) -> bool:
    return body.get("status") == "succeeded" and body.get("artifact_kind") == "enhanced"


def _atomic_write_envelope(path: Path, envelope: dict[str, Any]) -> None:
    """Write the final JSON envelope to ``path`` atomically.

    Used by ``--result-json`` so the slash command can read the envelope by
    file path instead of tailing the log. The log mixes stdout and stderr,
    and a late-firing ``logger.warning`` after ``_emit`` would push the
    envelope off the last line — file-based delivery is deterministic.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_str = tempfile.mkstemp(prefix=path.name + ".", dir=str(path.parent))
    tmp = Path(tmp_str)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(envelope, fh, sort_keys=True)
        os.replace(tmp, path)
    except Exception:
        tmp.unlink(missing_ok=True)
        raise


def _canonical_skills_roots(project_dir: str) -> list[Path]:
    """Return the resolved skills-root directories a cleanup target must live under.

    Resolves ``~/.claude/skills`` and ``<project>/.claude/skills`` through
    ``Path.resolve()`` so a symlinked root normalizes to its real path. The
    caller then uses ``Path.is_relative_to`` against these resolved roots,
    which is symlink-safe in a way the previous bash ``case``-glob was not:
    a leaf-symlink-escape (``<skill>/SKILL.md -> /etc/passwd``) leaves the
    parent dir under ``~/.claude/skills`` but ``Path.resolve(strict=True)``
    on the parent walks the whole chain and would fail outside the root.
    """
    roots: list[Path] = []
    home_root = Path.home() / ".claude" / "skills"
    if home_root.is_dir():
        roots.append(home_root.resolve())
    project_root = (Path(project_dir) if project_dir else None) or Path.cwd()
    candidate = project_root / ".claude" / "skills"
    if candidate.is_dir():
        roots.append(candidate.resolve())
    return roots


@traced("client.remote_enhance.run_cleanup_original", kind="CLIENT", openinference_kind="TOOL")
def _run_cleanup_original(args: argparse.Namespace) -> tuple[int, dict[str, Any]]:
    """Safely delete the original skill directory after a cross-scope install.

    Replaces the SKILL.md guarded ``rm -rf`` block. ``Path.resolve(strict=True)``
    walks the full symlink chain (so a leaf-symlink-escape can't slip past),
    then ``is_relative_to`` confirms the resolved path is under either the
    user-scope or project-scope ``.claude/skills`` root. Refuses the skills
    root itself (deleting the root would clobber every installed skill).
    """
    set_span_attributes(mode="cleanup-original")
    target = args.cleanup_original
    if not target:
        return 4, {
            "error": {
                "code": "missing_cleanup_target",
                "message": "--cleanup-original requires a path argument",
            }
        }
    try:
        resolved = Path(target).resolve(strict=True)
    except (FileNotFoundError, RuntimeError) as exc:
        return 4, {
            "error": {
                "code": "cleanup_path_missing",
                "message": f"cannot resolve {target!r}: {exc}",
            }
        }
    roots = _canonical_skills_roots(args.project_dir)
    if not any(resolved == r or resolved.is_relative_to(r) for r in roots):
        return 4, {
            "error": {
                "code": "cleanup_unsafe_path",
                "message": (
                    f"refusing to delete {resolved} — not under a canonical "
                    "skills root (~/.claude/skills/ or <project>/.claude/skills/)"
                ),
            }
        }
    if any(resolved == r for r in roots):
        return 4, {
            "error": {
                "code": "cleanup_unsafe_path",
                "message": f"refusing to delete the skills root itself: {resolved}",
            }
        }
    try:
        shutil.rmtree(resolved)
    except OSError as exc:
        return 4, {
            "error": {
                "code": "cleanup_failed",
                "message": str(exc),
            }
        }
    set_span_attributes(removed_path=str(resolved))
    return 0, {"removed": str(resolved)}


@traced("client.remote_enhance.run_default_mode", kind="CLIENT", openinference_kind="TOOL")
def _run_default_mode(args: argparse.Namespace) -> tuple[int, dict[str, Any]]:
    """package → upload → poll. Halts at "ready-to-install"; install is mode 3."""
    set_span_attributes(skill_name=args.skill_name, mode="default")
    if not args.skill_name:
        return 4, {"error": {"code": "missing_skill_name", "message": "--skill-name is required"}}
    skill_dir = _resolve_skill_dir(args.skill_name)
    _progress(f"packaging {skill_dir}")
    try:
        bundle = package_skill(skill_dir)
    except PackagerError as exc:
        return 4, {"error": {"code": exc.code, "message": exc.message}}
    _progress(f"uploading {len(bundle.archive_bytes)} bytes (content_hash={bundle.content_hash})")
    try:
        with GatewayClient(timeout=args.poll_timeout or 1200.0) as client:
            try:
                upload_resp = client.upload(
                    archive_bytes=bundle.archive_bytes,
                    source="api",
                    skill_id=args.skill_name,
                )
            except ApiError as exc:
                exit_code, env = _api_error_envelope(exc)
                if exit_code == 2:
                    # Pair the upstream `existing_job_id` with the packager's
                    # locally-computed content_hash (the upstream `details`
                    # does not include content_hash).
                    env["conflict"]["content_hash"] = bundle.content_hash
                return exit_code, env
            job_id = str(upload_resp["job_id"])
            set_span_attributes(job_id=job_id)
            _progress(f"job_id={job_id}; polling")
            try:
                run_poller(
                    client=client,
                    job_id=job_id,
                    poll_timeout_s=args.poll_timeout,
                    on_progress=_progress,
                )
            except PollTimeout as exc:
                return 3, {
                    "timeout": {
                        "job_id": exc.job_id,
                        "elapsed_s": exc.elapsed_s,
                    }
                }
            try:
                result_body = client.get_result(job_id)
            except ApiError as exc:
                exit_code, env = _api_error_envelope(exc)
                return exit_code, env
    except AuthError as exc:
        return 5, {"error": {"code": "auth_failure", "message": str(exc)}}
    except NetworkError as exc:
        return 5, {"error": {"code": "network_failure", "message": str(exc)}}

    # Terminal-no-install path — non-succeeded or non-enhanced bundle. The
    # SKILL.md surfaces ``reason`` to the user and skips install entirely.
    if not _is_enhanced_succeeded(result_body):
        return 0, _terminal_no_install_envelope(result_body)

    # Succeeded + enhanced — download to staging, validate frontmatter,
    # halt for install_location prompt. Install itself is mode 3.
    artifact = result_body.get("artifact")
    if not artifact:
        return 4, {
            "error": {
                "code": "missing_artifact",
                "message": "succeeded result has no artifact bundle",
            }
        }
    try:
        staging = download_artifact(artifact, job_id=job_id)
        validate_frontmatter(staging)
    except InstallerError as exc:
        return 4, {"error": {"code": exc.code, "message": exc.message}}
    _save_result_cache(job_id, result_body, skill_name=args.skill_name)
    return 0, _ready_to_install_envelope(job_id, result_body)


@traced("client.remote_enhance.run_poll_existing", kind="CLIENT", openinference_kind="TOOL")
def _run_poll_existing(args: argparse.Namespace) -> tuple[int, dict[str, Any]]:
    job_id = args.poll_existing
    set_span_attributes(job_id=job_id, mode="poll-existing")
    try:
        with GatewayClient(timeout=args.poll_timeout or 1200.0) as client:
            try:
                run_poller(
                    client=client,
                    job_id=job_id,
                    poll_timeout_s=args.poll_timeout,
                    on_progress=_progress,
                )
            except PollTimeout as exc:
                return 3, {"timeout": {"job_id": exc.job_id, "elapsed_s": exc.elapsed_s}}
            try:
                result_body = client.get_result(job_id)
            except ApiError as exc:
                exit_code, env = _api_error_envelope(exc)
                return exit_code, env
    except AuthError as exc:
        return 5, {"error": {"code": "auth_failure", "message": str(exc)}}
    except NetworkError as exc:
        return 5, {"error": {"code": "network_failure", "message": str(exc)}}

    if not _is_enhanced_succeeded(result_body):
        return 0, _terminal_no_install_envelope(result_body)
    artifact = result_body.get("artifact")
    if not artifact:
        return 4, {
            "error": {
                "code": "missing_artifact",
                "message": "succeeded result has no artifact bundle",
            }
        }
    try:
        download_artifact(artifact, job_id=job_id)
        validate_frontmatter(staging_root(job_id))
    except InstallerError as exc:
        return 4, {"error": {"code": exc.code, "message": exc.message}}
    _save_result_cache(job_id, result_body, skill_name=args.skill_name)
    return 0, _ready_to_install_envelope(job_id, result_body)


@traced("client.remote_enhance.run_install_existing", kind="CLIENT", openinference_kind="TOOL")
def _run_install_existing(args: argparse.Namespace) -> tuple[int, dict[str, Any]]:
    set_span_attributes(
        job_id=args.install_existing,
        skill_name=args.skill_name,
        install_location=args.install_location,
        mode="install-existing",
    )
    if not args.install_location:
        return 4, {
            "error": {
                "code": "missing_install_location",
                "message": "--install-existing requires --install-location project|global",
            }
        }
    if not args.skill_name:
        return 4, {
            "error": {
                "code": "missing_skill_name",
                "message": "--install-existing requires --skill-name",
            }
        }
    job_id = args.install_existing
    staging = staging_root(job_id)
    if not staging.is_dir():
        return 4, {
            "error": {
                "code": "missing_staging",
                "message": f"no staging dir for job {job_id} at {staging}",
            }
        }
    # The result envelope was already shown to the user in mode 1 / mode 2;
    # mode 1/2 also caches it next to the staging dir, so we can re-render
    # the same payload without a second network round-trip on the install
    # path. Network fallback only fires if the cache is missing — covers a
    # fresh shell where ``data_dir()`` was wiped between mode-1 and mode-3.
    result_body = _load_result_cache(job_id)
    if result_body is None:
        try:
            with GatewayClient(timeout=args.poll_timeout or 1200.0) as client:
                try:
                    result_body = client.get_result(job_id)
                except ApiError as exc:
                    exit_code, env = _api_error_envelope(exc)
                    return exit_code, env
        except AuthError as exc:
            return 5, {"error": {"code": "auth_failure", "message": str(exc)}}
        except NetworkError as exc:
            return 5, {"error": {"code": "network_failure", "message": str(exc)}}

    destination = _resolve_destination(args.install_location, args.skill_name)
    try:
        install_to(staging, destination=destination)
    except OSError as exc:
        return 4, {"error": {"code": "install_failed", "message": str(exc)}}
    return 0, _installed_envelope(result_body, destination)


def _run_list_cached(args: argparse.Namespace) -> tuple[int, dict[str, Any]]:
    """Scan the local result cache for prior succeeded enhancements of a skill.

    Used by the slash command's ``prefix_exists`` exit-4 branch: when the
    server rejects an upload because a prior staging prefix is still in the
    bucket, the client offers to install a previously-enhanced version of the
    same skill from local cache instead of re-uploading.

    Pure local I/O — no network, no auth, no tracing. Always exit 0; an empty
    list (no matching cache entries) is a normal outcome the caller branches on.
    """
    if not args.skill_name:
        return 4, {
            "error": {
                "code": "missing_skill_name",
                "message": "--list-cached requires --skill-name",
            }
        }
    cache_dir = data_dir() / "enhancements"
    matches: list[dict[str, Any]] = []
    if cache_dir.is_dir():
        for entry in cache_dir.glob("*.result.json"):
            try:
                body = json.loads(entry.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                # A corrupt cache file is a noisy log line in other paths;
                # here it should silently not show up as an install candidate.
                continue
            if body.get("skill_name") != args.skill_name:
                continue
            if body.get("status") != "succeeded":
                continue
            if body.get("artifact_kind") != "enhanced":
                continue
            matches.append(
                {
                    "job_id": body.get("job_id") or entry.stem.removesuffix(".result"),
                    "completed_at": body.get("completed_at"),
                    "roi": body.get("roi"),
                }
            )
    # Newest first so the slash command can present the most recent enhancement
    # as the default install candidate.
    matches.sort(key=lambda m: m.get("completed_at") or "", reverse=True)
    return 0, {"cached": matches}


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m mega_code.client.remote_enhance",
        description="Skill enhancement client — package, upload, poll, install via the gateway.",
    )
    p.add_argument(
        "--skill-name",
        help="Skill name (resolved via skill_enhance_helper). Required for default mode "
        "and --install-existing.",
    )
    p.add_argument(
        "--poll-timeout",
        type=float,
        default=1200.0,
        help="Polling deadline in seconds; 0 means wait indefinitely. Default 1200.",
    )
    mode = p.add_mutually_exclusive_group()
    mode.add_argument(
        "--poll-existing",
        metavar="JOB_ID",
        help="Resume polling on an existing job (skip package + upload).",
    )
    mode.add_argument(
        "--install-existing",
        metavar="JOB_ID",
        help="Install from staging dir for an existing job (skip package + upload + poll). "
        "Requires --install-location.",
    )
    mode.add_argument(
        "--list-cached",
        action="store_true",
        help="List locally-cached succeeded enhancements for --skill-name. "
        "Pure local I/O; the slash command uses this on `prefix_exists` to offer "
        "install-from-cache as an alternative to re-uploading.",
    )
    mode.add_argument(
        "--cleanup-original",
        metavar="PATH",
        help="Safely delete an original skill directory after a cross-scope "
        "install. Resolves the path through the full symlink chain and "
        "refuses anything not under ~/.claude/skills/ or "
        "<project>/.claude/skills/.",
    )
    p.add_argument(
        "--install-location",
        choices=["project", "global"],
        help="Where to copy the enhanced skill. Required when --install-existing is set.",
    )
    p.add_argument(
        "--project-dir",
        default="",
        help="Override CLAUDE_PROJECT_DIR for this invocation. Set to the user's "
        "real project root when running from a plugin context (where cwd and "
        "CLAUDE_PROJECT_DIR may both point at the plugin cache).",
    )
    p.add_argument(
        "--result-json",
        metavar="PATH",
        default="",
        help="If set, the final JSON envelope is also written to this file "
        "atomically (mkstemp + os.replace). The slash command reads from "
        "this file instead of tailing the tee'd log, avoiding the "
        "'late stderr line shifted the JSON off the last line' failure.",
    )
    return p


def main(argv: list[str] | None = None) -> int:
    """Parse args, dispatch to one of the three modes, emit JSON envelope, return exit code.

    ``setup_tracing()`` runs first so the @traced decorators below export to
    the configured OTLP endpoint (Phoenix locally, Honeycomb in deploy).
    Without this call, decorated functions run with a no-op tracer provider.
    The whole dispatch is wrapped in a ``client.remote_enhance.main`` span so
    ``exit_code`` and ``mode`` are attached to one cohesive parent that
    contains all child spans (package / upload / poll / get_result / install).
    """
    # Load the canonical .env (same file check_auth.py loads). Without this,
    # MEGA_CODE_API_KEY / MEGA_CODE_SERVER_URL / MEGA_CODE_CLIENT_MODE are
    # only visible if the parent shell sourced .env first — which the slash
    # command's bash blocks do not always do across separate Bash tool calls.
    for _key, _value in load_env_file(get_env_path()).items():
        os.environ.setdefault(_key, _value)

    setup_tracing(service_name="mega-code-client")
    args = _build_parser().parse_args(argv)

    if getattr(args, "project_dir", ""):
        from mega_code.client.skill_enhance_helper import _apply_cli_project_dir

        _apply_cli_project_dir(args.project_dir)
    tracer = get_tracer(__name__)
    with tracer.start_as_current_span("client.remote_enhance.main"):
        if args.install_existing:
            mode = "install-existing"
            exit_code, envelope = _run_install_existing(args)
        elif args.poll_existing:
            mode = "poll-existing"
            exit_code, envelope = _run_poll_existing(args)
        elif args.list_cached:
            mode = "list-cached"
            exit_code, envelope = _run_list_cached(args)
        elif args.cleanup_original:
            mode = "cleanup-original"
            exit_code, envelope = _run_cleanup_original(args)
        else:
            mode = "default"
            exit_code, envelope = _run_default_mode(args)
        set_span_attributes(mode=mode, exit_code=exit_code)
        if args.result_json:
            # Best-effort: a write failure must not change the exit code, but
            # a failure to deliver the envelope is rare enough that the user
            # should see it on stderr.
            try:
                _atomic_write_envelope(Path(args.result_json), envelope)
            except OSError as exc:
                logger.warning("--result-json write failed: %s", exc)
        _emit(envelope)
        return exit_code


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
