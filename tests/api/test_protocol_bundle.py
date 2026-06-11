"""Tests for SkillBundle wire types (A1).

Covers:
- Round-trip via model_dump() / model_validate() for each new model.
- BundleFile frozen-equality (mutation raises ValidationError).
- Regression guard: every pre-A1 symbol in protocol.__all__ still imports.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from mega_code.client.api import protocol
from mega_code.client.api.protocol import (
    BundleFile,
    OutputsResult,
    PendingSkillData,
    ResourcePlan,
    SkillBundle,
    ValidationFinding,
)

# -- BundleFile ---------------------------------------------------------------


def test_bundle_file_round_trip():
    bf = BundleFile(path="references/examples.md", kind="reference", content="body")
    assert BundleFile.model_validate(bf.model_dump()) == bf


def test_bundle_file_is_frozen():
    bf = BundleFile(path="references/a.md", kind="reference", content="x")
    with pytest.raises(ValidationError):
        bf.path = "references/b.md"  # type: ignore[misc]


def test_bundle_file_rejects_unknown_kind():
    with pytest.raises(ValidationError):
        BundleFile(path="references/x.md", kind="docs", content="x")  # type: ignore[arg-type]


# -- ResourcePlan -------------------------------------------------------------


def test_resource_plan_defaults_empty():
    rp = ResourcePlan()
    assert rp.create_references is False
    assert rp.create_scripts is False
    assert rp.create_assets is False
    assert rp.rationale == ""
    assert rp.planned_files == []


def test_resource_plan_round_trip_with_planned_files():
    rp = ResourcePlan(
        create_references=True,
        rationale="Body > 300 lines (R1, L1-450)",
        planned_files=[BundleFile(path="references/a.md", kind="reference", content="x")],
    )
    assert ResourcePlan.model_validate(rp.model_dump()) == rp


# -- SkillBundle --------------------------------------------------------------


def _minimal_bundle(**overrides) -> SkillBundle:
    defaults = {
        "skill_slug": "my-skill",
        "skill_md": "---\nname: my-skill\n---\nbody",
        "injection_rules": "{}",
        "evidence": "[]",
        "metadata": "{}",
    }
    defaults.update(overrides)
    return SkillBundle(**defaults)


def test_skill_bundle_minimal_defaults():
    b = _minimal_bundle()
    assert b.version == "1.0.0"
    assert b.bundle_files == []
    assert b.resource_plan == ResourcePlan()
    assert b.installed is False
    assert b.approved is False
    assert b.tags == []


def test_skill_bundle_round_trip_full():
    b = _minimal_bundle(
        bundle_files=[BundleFile(path="references/a.md", kind="reference", content="x")],
        resource_plan=ResourcePlan(create_references=True, rationale="see ev:001"),
        tags=["python", "testing"],
        author="alice",
    )
    assert SkillBundle.model_validate(b.model_dump()) == b


def test_skill_bundle_requires_identity():
    with pytest.raises(ValidationError):
        SkillBundle(
            skill_md="x",
            injection_rules="{}",
            evidence="[]",
            metadata="{}",
        )  # type: ignore[call-arg]


# -- ValidationFinding --------------------------------------------------------


def test_validation_finding_round_trip():
    vf = ValidationFinding(
        code="E002_SKILL_MD_TOO_LONG",
        severity="error",
        message="Body exceeds 800 lines",
        path=None,
    )
    assert ValidationFinding.model_validate(vf.model_dump()) == vf


def test_validation_finding_rejects_unknown_severity():
    with pytest.raises(ValidationError):
        ValidationFinding(
            code="X",
            severity="info",  # type: ignore[arg-type]
            message="m",
        )


def test_validation_finding_path_optional():
    vf = ValidationFinding(code="W001_SKILL_MD_LONG", severity="warning", message="m")
    assert vf.path is None


# -- Regression: pre-A1 public surface still importable ----------------------


_PRE_A1_SYMBOLS = (
    "ACTIVE_STATUSES",
    "ERROR_STATUSES",
    "TERMINAL_STATUSES",
    "ActivePipelineItem",
    "ActivePipelinesResult",
    "EnhanceSkillResult",
    "MegaCodeBaseClient",
    "OutputsResult",
    "PendingLessonData",
    "PendingSkillData",
    "PendingStrategyData",
    "PipelineStatusResult",
    "PipelineStopResult",
    "ProfileResult",
    "SkillArtifactData",
    "SkillRefItem",
    "TriggerPipelineResult",
    "UploadResult",
    "UserProfile",
)


def test_pre_a1_symbols_still_exported():
    """A1 is additive — every symbol from the pre-A1 __all__ must still resolve."""
    missing = [name for name in _PRE_A1_SYMBOLS if not hasattr(protocol, name)]
    assert not missing, f"A1 dropped pre-existing symbols: {missing}"


def test_new_symbols_in_all():
    for name in ("BundleFile", "ResourcePlan", "SkillBundle", "ValidationFinding"):
        assert name in protocol.__all__


# -- B7: SkillBundle wire-carrier additions ----------------------------------


def _make_bundle(slug: str = "foo") -> SkillBundle:
    return SkillBundle(
        skill_slug=slug,
        skill_md=f"# {slug}\n",
        injection_rules="{}",
        evidence="[]",
        metadata="{}",
        bundle_files=[BundleFile(path="references/a.md", kind="reference", content="hello")],
    )


def test_pending_skill_data_skill_bundle_defaults_none():
    """Pre-bundle rows (and legacy callers) load with skill_bundle=None."""
    p = PendingSkillData(
        skill_name="x", skill_md="m", injection_rules="{}", evidence="[]", metadata="{}"
    )
    assert p.skill_bundle is None
    # JSON round-trip preserves None.
    assert PendingSkillData.model_validate_json(p.model_dump_json()).skill_bundle is None


def test_pending_skill_data_skill_bundle_round_trip():
    """Populated skill_bundle survives JSON round-trip with bundle_files intact."""
    b = _make_bundle("foo")
    p = PendingSkillData(
        skill_name="foo",
        skill_md="# foo",
        injection_rules="{}",
        evidence="[]",
        metadata="{}",
        skill_bundle=b,
    )
    p2 = PendingSkillData.model_validate_json(p.model_dump_json())
    assert p2.skill_bundle is not None
    assert p2.skill_bundle.skill_slug == "foo"
    assert [bf.path for bf in p2.skill_bundle.bundle_files] == ["references/a.md"]


def test_outputs_result_skill_bundles_defaults_empty():
    """Pre-B7 callers (and pre-bundle runs) see an empty list, not missing key."""
    o = OutputsResult()
    assert o.skill_bundles == []
    assert OutputsResult.model_validate_json(o.model_dump_json()).skill_bundles == []


def test_outputs_result_skill_bundles_round_trip():
    o = OutputsResult(skill_bundles=[_make_bundle("a"), _make_bundle("b")])
    o2 = OutputsResult.model_validate_json(o.model_dump_json())
    assert [b.skill_slug for b in o2.skill_bundles] == ["a", "b"]


def test_outputs_result_old_payload_still_parses():
    """Wire-additive: a JSON payload without skill_bundles still validates."""
    legacy = (
        '{"skill_artifacts": [], "pending_skills": [], '
        '"pending_strategies": [], "pending_lessons": []}'
    )
    o = OutputsResult.model_validate_json(legacy)
    assert o.skill_bundles == []
