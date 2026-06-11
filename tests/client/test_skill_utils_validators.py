"""Tests for validate_frontmatter + validate_bundle (A2).

Coverage matrix:
- F001-F007 frontmatter codes (positive + negative).
- W001 / E002 SKILL.md body size.
- E003 path-safety matrix (traversal, absolute, backslash, regex).
- E004 wrong root subdir.
- E005 extension violation.
- E006 per-file size.
- E007 total bundle size.
- E008 flag-files consistency.
- E009 missing citation (forward).
- E012 dangling citation (reverse).
- P001-P004 planner rules.
- validate_bundle concatenates frontmatter findings (shared type).
"""

from __future__ import annotations

import pytest

from mega_code.client.api.protocol import (
    BundleFile,
    ResourcePlan,
    SkillBundle,
)
from mega_code.client.skill_utils import (
    iter_cited_bundle_paths,
    validate_bundle,
    validate_frontmatter,
)

# -- helpers ----------------------------------------------------------------


def _codes(findings) -> list[str]:
    return [f.code for f in findings]


def _runtime_md(
    *,
    description: str = "Does a thing.",
    allowed_tools: str = "Read, Grep",
    extra: str = "",
) -> str:
    return (
        f'---\ndescription: {description}\nallowed-tools: "{allowed_tools}"\n{extra}---\n\nbody\n'
    )


def _extracted_md(
    *,
    name: str = "my-skill",
    description: str = "Does a thing.",
    extra: str = "",
) -> str:
    return f"---\nname: {name}\ndescription: {description}\n{extra}---\n\nbody\n"


def _bundle(
    *,
    skill_slug: str = "my-skill",
    skill_md: str | None = None,
    bundle_files: list[BundleFile] | None = None,
    resource_plan: ResourcePlan | None = None,
) -> SkillBundle:
    return SkillBundle(
        skill_slug=skill_slug,
        skill_md=skill_md if skill_md is not None else _extracted_md(name=skill_slug),
        injection_rules="{}",
        evidence="[]",
        metadata="{}",
        bundle_files=bundle_files or [],
        resource_plan=resource_plan or ResourcePlan(),
    )


# -- validate_frontmatter: F001-F007 ----------------------------------------


def test_runtime_valid_frontmatter_returns_no_errors():
    findings = validate_frontmatter(_runtime_md(), stage="runtime")
    errors = [f for f in findings if f.severity == "error"]
    assert errors == []


def test_extracted_valid_frontmatter_returns_no_errors():
    findings = validate_frontmatter(_extracted_md(), stage="extracted")
    errors = [f for f in findings if f.severity == "error"]
    assert errors == []


def test_f001_missing_required_key_runtime():
    md = "---\ndescription: hi\n---\nbody"
    codes = _codes(validate_frontmatter(md, stage="runtime"))
    assert "F001_MISSING_REQUIRED_KEY" in codes


def test_f001_missing_required_key_extracted():
    md = "---\ndescription: hi\n---\nbody"
    codes = _codes(validate_frontmatter(md, stage="extracted"))
    assert "F001_MISSING_REQUIRED_KEY" in codes


def test_f002_empty_value():
    md = '---\ndescription: ""\nallowed-tools: "Read"\n---\nbody'
    codes = _codes(validate_frontmatter(md, stage="runtime"))
    assert "F002_EMPTY_VALUE" in codes


def test_f003_forbidden_key_runtime_only():
    md = _runtime_md(extra="source_run_id: abc\n")
    codes = _codes(validate_frontmatter(md, stage="runtime"))
    assert "F003_FORBIDDEN_KEY" in codes


def test_f003_not_emitted_at_extracted():
    md = _extracted_md(extra="source_run_id: abc\n")
    codes = _codes(validate_frontmatter(md, stage="extracted"))
    assert "F003_FORBIDDEN_KEY" not in codes


def test_f004_bad_kebab_case():
    md = _extracted_md(name="My_Skill")
    codes = _codes(validate_frontmatter(md, stage="extracted"))
    assert "F004_BAD_KEBAB_CASE" in codes


def test_f005_name_dir_mismatch_when_expected_slug_supplied():
    md = _extracted_md(name="other-name")
    codes = _codes(validate_frontmatter(md, stage="extracted", expected_slug="my-skill"))
    assert "F005_NAME_DIR_MISMATCH" in codes


def test_f005_skipped_when_expected_slug_missing():
    md = _extracted_md(name="anything")
    codes = _codes(validate_frontmatter(md, stage="extracted"))
    assert "F005_NAME_DIR_MISMATCH" not in codes


def test_f006_warn_only_on_first_person():
    md = _runtime_md(description="I do a thing for you.")
    findings = validate_frontmatter(md, stage="runtime")
    f006 = [f for f in findings if f.code == "F006_DESCRIPTION_WRONG_PERSON"]
    assert len(f006) == 1
    assert f006[0].severity == "warning"


def test_f006_not_emitted_on_third_person():
    md = _runtime_md(description="Helps the user do a thing.")
    codes = _codes(validate_frontmatter(md, stage="runtime"))
    assert "F006_DESCRIPTION_WRONG_PERSON" not in codes


def test_f007_allowed_tools_empty_list():
    md = "---\ndescription: hi\nallowed-tools: []\n---\nbody"
    codes = _codes(validate_frontmatter(md, stage="runtime"))
    assert "F007_ALLOWED_TOOLS_EMPTY_LIST" in codes


# -- validate_bundle: body size (W001 / E002) -------------------------------


def test_w001_skill_md_long_emits_warning():
    body = "\n".join(["line"] * 600)
    bundle = _bundle(skill_md=_extracted_md() + body)
    findings = validate_bundle(bundle, stage="extracted")
    w001 = [f for f in findings if f.code == "W001_SKILL_MD_LONG"]
    assert len(w001) == 1
    assert w001[0].severity == "warning"


def test_e002_skill_md_too_long_emits_error():
    body = "\n".join(["line"] * 900)
    bundle = _bundle(skill_md=_extracted_md() + body)
    codes = _codes(validate_bundle(bundle, stage="extracted"))
    assert "E002_SKILL_MD_TOO_LONG" in codes


def test_short_body_no_size_findings():
    bundle = _bundle()
    codes = _codes(validate_bundle(bundle, stage="extracted"))
    assert "W001_SKILL_MD_LONG" not in codes
    assert "E002_SKILL_MD_TOO_LONG" not in codes


# -- validate_bundle: path safety matrix (E003 / E004 / E005) --------------


def _bundle_with_path(path: str, kind: str = "reference", content: str = "x") -> SkillBundle:
    return _bundle(
        bundle_files=[BundleFile(path=path, kind=kind, content=content)],  # type: ignore[arg-type]
        resource_plan=ResourcePlan(
            create_references=(kind == "reference"),
            create_scripts=(kind == "script"),
            create_assets=(kind == "asset"),
            rationale="see ev:001",
        ),
        skill_md=_extracted_md() + f"\n`{path}`\n",
    )


def test_e003_path_traversal_refused_at_model_boundary():
    """``..`` segments are blocked by ``BundleFile`` field validation — the
    bundle never reaches ``validate_bundle``. Defence-in-depth: E003 remains
    in place for any path the model would still accept (see backslash test)."""
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        _bundle_with_path("references/../x.md")


def test_e003_path_absolute_refused_at_model_boundary():
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        _bundle_with_path("/abs/x.md")


def test_e003_path_backslash():
    codes = _codes(validate_bundle(_bundle_with_path("references\\x.md"), stage="extracted"))
    assert "E003_BUNDLE_PATH_UNSAFE" in codes


def test_e004_path_wrong_root():
    codes = _codes(validate_bundle(_bundle_with_path("badroot/x.md"), stage="extracted"))
    assert "E004_BUNDLE_PATH_WHITELIST" in codes


def test_e005_extension_violation_reference_must_be_md():
    codes = _codes(
        validate_bundle(_bundle_with_path("references/x.py", kind="reference"), stage="extracted")
    )
    assert "E005_BUNDLE_EXTENSION" in codes


def test_valid_path_no_path_safety_findings():
    findings = validate_bundle(_bundle_with_path("references/x.md"), stage="extracted")
    for code in (
        "E003_BUNDLE_PATH_UNSAFE",
        "E004_BUNDLE_PATH_WHITELIST",
        "E005_BUNDLE_EXTENSION",
    ):
        assert code not in _codes(findings), code


# -- validate_bundle: size caps (E006 / E007) ------------------------------


def test_e006_per_file_size_cap():
    big = "x" * (32 * 1024 + 1)
    bundle = _bundle(
        bundle_files=[BundleFile(path="references/big.md", kind="reference", content=big)],
        resource_plan=ResourcePlan(create_references=True, rationale="see ev:001"),
        skill_md=_extracted_md() + "\n`references/big.md`\n",
    )
    codes = _codes(validate_bundle(bundle, stage="extracted"))
    assert "E006_BUNDLE_FILE_TOO_LARGE" in codes


def test_e007_total_bundle_size_cap():
    # 9 files at 30 KiB each = 270 KiB > 256 KiB total.
    files = [
        BundleFile(path=f"references/f{i}.md", kind="reference", content="x" * (30 * 1024))
        for i in range(9)
    ]
    citations = "\n".join(f"`references/f{i}.md`" for i in range(9))
    bundle = _bundle(
        bundle_files=files,
        resource_plan=ResourcePlan(create_references=True, rationale="see ev:001"),
        skill_md=_extracted_md() + "\n" + citations + "\n",
    )
    codes = _codes(validate_bundle(bundle, stage="extracted"))
    assert "E007_BUNDLE_TOTAL_TOO_LARGE" in codes


# -- validate_bundle: consistency (E008) -----------------------------------


def test_e008_flag_set_but_no_files():
    bundle = _bundle(
        bundle_files=[],
        resource_plan=ResourcePlan(create_references=True, rationale="see ev:001"),
    )
    codes = _codes(validate_bundle(bundle, stage="extracted"))
    assert "E008_BUNDLE_FLAG_FILES_MISMATCH" in codes


def test_e008_files_present_but_flag_off():
    bundle = _bundle(
        bundle_files=[BundleFile(path="references/x.md", kind="reference", content="x")],
        resource_plan=ResourcePlan(create_references=False),
        skill_md=_extracted_md() + "\n`references/x.md`\n",
    )
    codes = _codes(validate_bundle(bundle, stage="extracted"))
    assert "E008_BUNDLE_FLAG_FILES_MISMATCH" in codes


# -- validate_bundle: citations (E009 / E012) ------------------------------


def test_e009_missing_citation():
    bundle = _bundle(
        bundle_files=[BundleFile(path="references/x.md", kind="reference", content="x")],
        resource_plan=ResourcePlan(create_references=True, rationale="see ev:001"),
        skill_md=_extracted_md(),  # no citation in body
    )
    codes = _codes(validate_bundle(bundle, stage="extracted"))
    assert "E009_BUNDLE_CITATION_MISSING" in codes


def test_e012_dangling_citation():
    bundle = _bundle(
        bundle_files=[BundleFile(path="references/real.md", kind="reference", content="x")],
        resource_plan=ResourcePlan(create_references=True, rationale="see ev:001"),
        skill_md=_extracted_md() + "\n`references/real.md` and `references/ghost.md`\n",
    )
    codes = _codes(validate_bundle(bundle, stage="extracted"))
    assert "E012_BUNDLE_DANGLING_CITATION" in codes


def test_citation_recognised_via_markdown_link():
    bundle = _bundle(
        bundle_files=[BundleFile(path="references/x.md", kind="reference", content="x")],
        resource_plan=ResourcePlan(create_references=True, rationale="see ev:001"),
        skill_md=_extracted_md() + "\nSee [docs](references/x.md).\n",
    )
    codes = _codes(validate_bundle(bundle, stage="extracted"))
    assert "E009_BUNDLE_CITATION_MISSING" not in codes


def test_citation_recognised_via_load_directive():
    bundle = _bundle(
        bundle_files=[BundleFile(path="references/x.md", kind="reference", content="x")],
        resource_plan=ResourcePlan(create_references=True, rationale="see ev:001"),
        skill_md=_extracted_md() + "\n/load references/x.md\n",
    )
    codes = _codes(validate_bundle(bundle, stage="extracted"))
    assert "E009_BUNDLE_CITATION_MISSING" not in codes


def test_citation_recognised_via_shell_string():
    bundle = _bundle(
        bundle_files=[BundleFile(path="references/x.md", kind="reference", content="x")],
        resource_plan=ResourcePlan(create_references=True, rationale="see ev:001"),
        skill_md=_extracted_md() + '\nLoad "${CLAUDE_SKILL_DIR}/references/x.md"\n',
    )
    codes = _codes(validate_bundle(bundle, stage="extracted"))
    assert "E009_BUNDLE_CITATION_MISSING" not in codes


# -- iter_cited_bundle_paths (public helper) -------------------------------


def test_iter_cited_bundle_paths_empty_when_no_citations():
    assert iter_cited_bundle_paths(_extracted_md()) == set()


def test_iter_cited_bundle_paths_backtick_form():
    md = _extracted_md() + "\n`references/x.md` and `scripts/run.sh`\n"
    assert iter_cited_bundle_paths(md) == {"references/x.md", "scripts/run.sh"}


def test_iter_cited_bundle_paths_markdown_link_form():
    md = _extracted_md() + "\nSee [docs](references/x.md) and [a](assets/data.json).\n"
    assert iter_cited_bundle_paths(md) == {"references/x.md", "assets/data.json"}


def test_iter_cited_bundle_paths_load_directive_form():
    md = _extracted_md() + "\n/load references/x.md\n"
    assert iter_cited_bundle_paths(md) == {"references/x.md"}


def test_iter_cited_bundle_paths_dedupes_repeated_citations():
    md = _extracted_md() + "\n`references/x.md` here and `references/x.md` again\n"
    assert iter_cited_bundle_paths(md) == {"references/x.md"}


def test_iter_cited_bundle_paths_ignores_non_bundle_paths():
    md = _extracted_md() + "\n`src/main.py` and `docs/README.md`\n"
    assert iter_cited_bundle_paths(md) == set()


# -- validate_bundle: planner P001-P004 ------------------------------------


def test_p001_create_assets_forbidden():
    bundle = _bundle(
        resource_plan=ResourcePlan(create_assets=True, rationale="see ev:001"),
    )
    codes = _codes(validate_bundle(bundle, stage="extracted"))
    assert "P001_CREATE_ASSETS_FORBIDDEN_IN_PHASE_3" in codes


def test_p002_empty_rationale_when_create_flag_set():
    bundle = _bundle(
        bundle_files=[BundleFile(path="references/x.md", kind="reference", content="x")],
        resource_plan=ResourcePlan(create_references=True, rationale=""),
        skill_md=_extracted_md() + "\n`references/x.md`\n",
    )
    codes = _codes(validate_bundle(bundle, stage="extracted"))
    assert "P002_EMPTY_RATIONALE" in codes


def test_p003_rationale_lacks_citation():
    bundle = _bundle(
        bundle_files=[BundleFile(path="references/x.md", kind="reference", content="x")],
        resource_plan=ResourcePlan(
            create_references=True, rationale="The body is long, extracting examples."
        ),
        skill_md=_extracted_md() + "\n`references/x.md`\n",
    )
    codes = _codes(validate_bundle(bundle, stage="extracted"))
    assert "P003_RATIONALE_LACKS_CITATION" in codes


def test_p003_satisfied_with_line_range_citation():
    bundle = _bundle(
        bundle_files=[BundleFile(path="references/x.md", kind="reference", content="x")],
        resource_plan=ResourcePlan(create_references=True, rationale="Body L300-450 is long."),
        skill_md=_extracted_md() + "\n`references/x.md`\n",
    )
    codes = _codes(validate_bundle(bundle, stage="extracted"))
    assert "P003_RATIONALE_LACKS_CITATION" not in codes


def test_p004_rationale_extract_flag_mismatch():
    bundle = _bundle(
        resource_plan=ResourcePlan(
            create_references=False,
            create_scripts=False,
            create_assets=False,
            rationale="We should extract the examples (ev:001).",
        ),
    )
    codes = _codes(validate_bundle(bundle, stage="extracted"))
    assert "P004_RATIONALE_EXTRACT_FLAG_MISMATCH" in codes


# -- validate_bundle: concatenates frontmatter findings --------------------


def test_validate_bundle_concatenates_frontmatter_findings():
    bundle = _bundle(skill_md="---\n---\nbody")  # missing description + name
    findings = validate_bundle(bundle, stage="extracted")
    codes = _codes(findings)
    assert "F001_MISSING_REQUIRED_KEY" in codes


def test_validate_bundle_forwards_expected_slug_to_frontmatter():
    bundle = _bundle(
        skill_slug="my-skill",
        skill_md=_extracted_md(name="drifted-name"),
    )
    codes = _codes(validate_bundle(bundle, stage="extracted"))
    assert "F005_NAME_DIR_MISMATCH" in codes


def test_valid_bundle_returns_only_warnings_or_empty():
    bundle = _bundle()
    findings = validate_bundle(bundle, stage="extracted")
    errors = [f for f in findings if f.severity == "error"]
    assert errors == []
