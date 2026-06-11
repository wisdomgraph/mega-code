"""Tests for the approve_pending_skill helper (A7).

Covers:
- Returns the validated SkillBundle on a clean pending dir.
- Raises PendingApprovalError on any severity == "error" finding.
- Warning-severity findings do not raise (F006 third-person warning).
- Frontmatter is normalised before validation (allowed-tools backfill makes a
  missing-allowed-tools bundle pass runtime validation).
- E010 from from_disk propagates as SkillBundleError (not PendingApprovalError).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from mega_code.client.api.protocol import (
    ResourcePlan,
    SkillBundle,
    SkillBundleError,
)
from mega_code.client.pending import (
    PendingApprovalError,
    approve_pending_skill,
)

# -- helpers -----------------------------------------------------------------


def _write_bundle(tmp_path: Path, *, skill_md: str, metadata: str = "{}") -> Path:
    bundle = SkillBundle(
        skill_slug="my-skill",
        skill_md=skill_md,
        injection_rules="{}",
        evidence="[]",
        metadata=metadata,
    )
    return bundle.to_disk(tmp_path)


def _runtime_md(*, description: str = "Helps the user.", allowed_tools: str | None = "Read") -> str:
    lines = ["---", "description: " + description]
    if allowed_tools is not None:
        lines.append(f'allowed-tools: "{allowed_tools}"')
    lines += ["---", "", "body"]
    return "\n".join(lines) + "\n"


# -- happy path --------------------------------------------------------------


def test_returns_validated_bundle_on_clean_pending_dir(tmp_path: Path):
    bundle_dir = _write_bundle(tmp_path, skill_md=_runtime_md())
    bundle = approve_pending_skill(bundle_dir)
    assert isinstance(bundle, SkillBundle)
    assert bundle.skill_slug == "my-skill"


def test_warning_only_findings_do_not_raise(tmp_path: Path):
    """F006 (description-wrong-person) is warn-only — must not block approval."""
    bundle_dir = _write_bundle(
        tmp_path,
        skill_md=_runtime_md(description="I do a thing for you."),
    )
    bundle = approve_pending_skill(bundle_dir)
    assert bundle is not None


# -- error path --------------------------------------------------------------


def test_raises_on_error_severity_finding(tmp_path: Path):
    """A forbidden top-level key at runtime stage triggers F003 — must raise."""
    skill_md = (
        "---\n"
        "description: Helps the user.\n"
        'allowed-tools: "Read"\n'
        "source_run_id: abc\n"  # forbidden at runtime
        "---\n\nbody\n"
    )
    bundle_dir = _write_bundle(tmp_path, skill_md=skill_md)
    with pytest.raises(PendingApprovalError) as exc:
        approve_pending_skill(bundle_dir)
    codes = [f.code for f in exc.value.findings if f.severity == "error"]
    assert "F003_FORBIDDEN_KEY" in codes


def test_pending_approval_error_carries_all_findings(tmp_path: Path):
    """Multiple findings are surfaced together rather than one-at-a-time."""
    # Empty body, no allowed-tools — but allowed-tools gets backfilled, so
    # we provoke multiple errors via an empty bundle_files mismatch.
    bundle = SkillBundle(
        skill_slug="my-skill",
        skill_md="---\ndescription: hi\n---\nbody\n",
        injection_rules="{}",
        evidence="[]",
        metadata="{}",
        resource_plan=ResourcePlan(create_references=True, rationale=""),  # P002 + E008
    )
    bundle_dir = bundle.to_disk(tmp_path)

    with pytest.raises(PendingApprovalError) as exc:
        approve_pending_skill(bundle_dir)
    codes = [f.code for f in exc.value.findings if f.severity == "error"]
    assert "P002_EMPTY_RATIONALE" in codes
    assert "E008_BUNDLE_FLAG_FILES_MISMATCH" in codes


# -- frontmatter normalisation before validation -----------------------------


def test_allowed_tools_backfill_runs_before_validation(tmp_path: Path):
    """A pending bundle missing `allowed-tools` is normalised first → passes runtime."""
    skill_md = "---\ndescription: Helps the user.\n---\n\nbody\n"
    metadata = '{"allowed_tools": "Read, Grep"}'
    bundle_dir = _write_bundle(tmp_path, skill_md=skill_md, metadata=metadata)

    bundle = approve_pending_skill(bundle_dir)
    # Normalisation injected the metadata-sourced value, so the returned
    # bundle's SKILL.md now has `allowed-tools` populated.
    assert "allowed-tools:" in bundle.skill_md
    assert "Read, Grep" in bundle.skill_md


def test_allowed_tools_default_used_when_metadata_silent(tmp_path: Path):
    from mega_code.client.skill_utils import DEFAULT_ALLOWED_TOOLS

    skill_md = "---\ndescription: Helps the user.\n---\n\nbody\n"
    bundle_dir = _write_bundle(tmp_path, skill_md=skill_md, metadata="{}")

    bundle = approve_pending_skill(bundle_dir)
    assert DEFAULT_ALLOWED_TOOLS in bundle.skill_md


# -- E010 from from_disk propagates -----------------------------------------


def test_e010_from_from_disk_propagates_as_skill_bundle_error(tmp_path: Path):
    """from_disk's MQ5 invariant fires before validate_bundle — wrong exception type."""
    bundle_dir = tmp_path / "corrupted"
    bundle_dir.mkdir()
    (bundle_dir / "SKILL.md").write_text("---\nname: corrupted\n---\nbody\n")
    (bundle_dir / "references").mkdir()
    (bundle_dir / "references" / "x.md").write_text("x")

    with pytest.raises(SkillBundleError) as exc:
        approve_pending_skill(bundle_dir)
    assert exc.value.code == "E010_MISSING_RESOURCE_PLAN"
