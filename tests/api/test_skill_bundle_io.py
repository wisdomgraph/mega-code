"""Tests for SkillBundle disk + ZIP I/O and the PendingSkillData adapter (A3+A6).

Coverage:
- to_disk / from_disk round-trip identity (empty + populated).
- run_id parameter creates the run-scoped path layout.
- Empty optional sub-directories are never created.
- ZIP round-trip with flat-rooted namelist assertion.
- MQ5 three-shape matrix on from_disk (no plan / no plan + subdirs / plan present).
- from_pending_skill_data adapter on a legacy PendingSkillData fixture.
"""

from __future__ import annotations

import zipfile

import pytest

from mega_code.client.api.protocol import (
    _SYSTEM_FILES,
    BundleFile,
    PendingSkillData,
    ResourcePlan,
    SkillBundle,
    SkillBundleError,
)

# -- helpers -----------------------------------------------------------------


def _empty_bundle(slug: str = "my-skill") -> SkillBundle:
    return SkillBundle(
        skill_slug=slug,
        skill_md=f"---\nname: {slug}\ndescription: x\n---\nbody\n",
        injection_rules='{"foo": 1}',
        evidence="[]",
        metadata='{"k": "v"}',
    )


def _populated_bundle(slug: str = "my-skill") -> SkillBundle:
    return SkillBundle(
        skill_slug=slug,
        skill_md=(
            f"---\nname: {slug}\ndescription: x\n---\nSee `references/a.md` and `scripts/run.sh`.\n"
        ),
        injection_rules='{"foo": 1}',
        evidence="[]",
        metadata='{"k": "v"}',
        bundle_files=[
            BundleFile(path="references/a.md", kind="reference", content="ref a"),
            BundleFile(path="scripts/run.sh", kind="script", content="echo hi\n"),
        ],
        resource_plan=ResourcePlan(
            create_references=True,
            create_scripts=True,
            rationale="see ev:001",
            planned_files=[],
        ),
        tags=["a", "b"],
        author="alice",
    )


# -- to_disk / from_disk round-trip -----------------------------------------


def test_to_disk_writes_system_files_and_returns_bundle_root(tmp_path):
    bundle = _empty_bundle()
    root = bundle.to_disk(tmp_path)
    assert root == tmp_path / "my-skill"
    assert (root / "SKILL.md").is_file()
    assert (root / "injection.json").is_file()
    assert (root / "evidence.json").is_file()
    assert (root / "metadata.json").is_file()
    assert (root / "resource_plan.json").is_file()


def test_to_disk_writes_exactly_skill_md_plus_system_files(tmp_path):
    """Sync guard: an empty bundle's root holds SKILL.md + exactly the names in
    _SYSTEM_FILES — nothing more, nothing missing. Fails loudly if a writer
    drifts from the _SYSTEM_FILES single source of truth."""
    bundle = _empty_bundle()
    root = bundle.to_disk(tmp_path)
    written = {p.name for p in root.iterdir() if p.is_file()}
    assert written == {"SKILL.md", *_SYSTEM_FILES}


def test_from_disk_defaults_absent_json_system_files(tmp_path):
    """The _JSON_SYSTEM_FILES read defaults fill in for absent sidecars."""
    bundle_dir = tmp_path / "sparse-skill"
    bundle_dir.mkdir()
    (bundle_dir / "SKILL.md").write_text("---\nname: sparse-skill\n---\nbody\n")
    # No injection.json / evidence.json / metadata.json on disk.

    loaded = SkillBundle.from_disk(bundle_dir)
    assert loaded.injection_rules == "{}"
    assert loaded.evidence == "[]"
    assert loaded.metadata == "{}"


def test_empty_bundle_does_not_create_optional_subdirs(tmp_path):
    bundle = _empty_bundle()
    root = bundle.to_disk(tmp_path)
    for sub in ("references", "scripts", "assets"):
        assert not (root / sub).exists(), f"{sub}/ should not have been created"


def test_to_disk_with_run_id_creates_nested_path(tmp_path):
    bundle = _empty_bundle()
    root = bundle.to_disk(tmp_path, run_id="run-123")
    assert root == tmp_path / "run-123" / "my-skill"
    assert (root / "SKILL.md").is_file()


def test_round_trip_empty_bundle(tmp_path):
    original = _empty_bundle()
    original.to_disk(tmp_path)
    loaded = SkillBundle.from_disk(tmp_path / "my-skill")
    # bundle_files ordering not asserted; equality covers everything else.
    assert loaded.skill_slug == original.skill_slug
    assert loaded.skill_md == original.skill_md
    assert loaded.injection_rules == original.injection_rules
    assert loaded.evidence == original.evidence
    assert loaded.metadata == original.metadata
    assert loaded.resource_plan == original.resource_plan
    assert loaded.bundle_files == []


def test_round_trip_populated_bundle(tmp_path):
    original = _populated_bundle()
    original.to_disk(tmp_path)
    loaded = SkillBundle.from_disk(tmp_path / "my-skill")
    assert loaded.skill_md == original.skill_md
    assert loaded.resource_plan == original.resource_plan
    # Compare bundle_files as sets (order-insensitive per A3 acceptance).
    assert {(bf.path, bf.kind, bf.content) for bf in loaded.bundle_files} == {
        (bf.path, bf.kind, bf.content) for bf in original.bundle_files
    }


def test_from_disk_raises_when_skill_md_missing(tmp_path):
    bundle_dir = tmp_path / "broken"
    bundle_dir.mkdir()
    with pytest.raises(SkillBundleError) as exc:
        SkillBundle.from_disk(bundle_dir)
    assert exc.value.code == "E020_MISSING_SKILL_MD"


# -- MQ5 three-shape matrix --------------------------------------------------


def test_mq5_no_plan_no_subdirs_loads_with_default_plan(tmp_path):
    """Pre-migration archive shape: no resource_plan.json, no subdirs → OK."""
    bundle_dir = tmp_path / "legacy-skill"
    bundle_dir.mkdir()
    (bundle_dir / "SKILL.md").write_text("---\nname: legacy-skill\n---\nbody\n")
    (bundle_dir / "injection.json").write_text("{}")
    (bundle_dir / "evidence.json").write_text("[]")
    (bundle_dir / "metadata.json").write_text("{}")
    # Note: no resource_plan.json, no references/scripts/assets dirs.

    loaded = SkillBundle.from_disk(bundle_dir)
    assert loaded.resource_plan == ResourcePlan()
    assert loaded.bundle_files == []


def test_mq5_no_plan_but_subdirs_present_raises_e010(tmp_path):
    """Corrupted shape: subdirs present but no resource_plan.json → E010."""
    bundle_dir = tmp_path / "corrupted"
    bundle_dir.mkdir()
    (bundle_dir / "SKILL.md").write_text("---\nname: corrupted\n---\nbody\n")
    (bundle_dir / "references").mkdir()
    (bundle_dir / "references" / "x.md").write_text("x")

    with pytest.raises(SkillBundleError) as exc:
        SkillBundle.from_disk(bundle_dir)
    assert exc.value.code == "E010_MISSING_RESOURCE_PLAN"


def test_mq5_plan_present_loads_normally(tmp_path):
    """Standard shape: resource_plan.json present → normal load path."""
    bundle = _populated_bundle()
    bundle.to_disk(tmp_path)
    loaded = SkillBundle.from_disk(tmp_path / "my-skill")
    assert loaded.resource_plan.create_references is True
    assert len(loaded.bundle_files) == 2


# -- ZIP I/O -----------------------------------------------------------------


def test_zip_round_trip_flat_rooted(tmp_path):
    bundle = _populated_bundle()
    zip_path = tmp_path / "my-skill.zip"
    bundle.to_zip(zip_path)

    # Flat-rooted: no `<slug>/` wrapper in any entry.
    with zipfile.ZipFile(zip_path, "r") as zf:
        names = zf.namelist()
    assert "SKILL.md" in names
    assert "resource_plan.json" in names
    assert "references/a.md" in names
    assert "scripts/run.sh" in names
    for name in names:
        assert not name.startswith("my-skill/"), f"unexpected slug-wrapper in {name!r}"

    loaded = SkillBundle.from_zip(zip_path)
    assert loaded.skill_md == bundle.skill_md
    assert loaded.resource_plan == bundle.resource_plan
    assert {(bf.path, bf.kind) for bf in loaded.bundle_files} == {
        (bf.path, bf.kind) for bf in bundle.bundle_files
    }


def test_from_zip_uses_filename_stem_as_slug_by_default(tmp_path):
    bundle = _empty_bundle("filename-derived")
    zip_path = tmp_path / "filename-derived.zip"
    bundle.to_zip(zip_path)
    loaded = SkillBundle.from_zip(zip_path)
    assert loaded.skill_slug == "filename-derived"


def test_from_zip_honours_explicit_skill_slug(tmp_path):
    bundle = _empty_bundle("anything")
    zip_path = tmp_path / "anything.zip"
    bundle.to_zip(zip_path)
    loaded = SkillBundle.from_zip(zip_path, skill_slug="overridden")
    assert loaded.skill_slug == "overridden"


def test_from_zip_raises_when_skill_md_missing(tmp_path):
    zip_path = tmp_path / "broken.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("metadata.json", "{}")
    with pytest.raises(SkillBundleError) as exc:
        SkillBundle.from_zip(zip_path)
    assert exc.value.code == "E020_MISSING_SKILL_MD"


# -- from_pending_skill_data adapter ----------------------------------------


def test_from_pending_skill_data_maps_fields_and_empties_resources():
    pend = PendingSkillData(
        skill_name="My Skill",
        skill_md="---\nname: my-skill\n---\nbody",
        injection_rules='{"rules": []}',
        evidence='[{"id": 1}]',
        metadata='{"k": "v"}',
        author="alice",
        version="1.2.3",
        tags=["py", "test"],
        installed=False,
        approved=False,
    )
    bundle = SkillBundle.from_pending_skill_data(pend)
    assert bundle.skill_slug == "my-skill"
    assert bundle.version == "1.2.3"
    assert bundle.skill_md == pend.skill_md
    assert bundle.injection_rules == pend.injection_rules
    assert bundle.evidence == pend.evidence
    assert bundle.metadata == pend.metadata
    assert bundle.author == "alice"
    assert bundle.tags == ["py", "test"]
    assert bundle.bundle_files == []
    assert bundle.resource_plan == ResourcePlan()


def test_from_pending_skill_data_defaults_version_when_empty():
    pend = PendingSkillData(
        skill_name="x",
        skill_md="body",
        injection_rules="{}",
        evidence="[]",
        metadata="{}",
        version="",
    )
    bundle = SkillBundle.from_pending_skill_data(pend)
    assert bundle.version == "1.0.0"


def test_from_pending_skill_data_prefers_frontmatter_name():
    """canonical_skill_name reads frontmatter `name` when present."""
    pend = PendingSkillData(
        skill_name="ignored name",
        skill_md="---\nname: frontmatter-name\n---\nbody",
        injection_rules="{}",
        evidence="[]",
        metadata="{}",
    )
    bundle = SkillBundle.from_pending_skill_data(pend)
    assert bundle.skill_slug == "frontmatter-name"


# -- security: BundleFile.path traversal guard ------------------------------


@pytest.mark.parametrize(
    "bad_path",
    [
        "/etc/cron.d/evil",
        "../../../../tmp/escaped.txt",
        "references/../../../tmp/zipslip.txt",
        "..",
        "/abs/path",
    ],
)
def test_bundle_file_rejects_escaping_path(bad_path):
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        BundleFile(path=bad_path, kind="reference", content="x")


def test_write_resources_backstop_blocks_symlink_escape(tmp_path, monkeypatch):
    """Even if a BundleFile.path slipped past the field validator, the
    resolve()/is_relative_to backstop in write_resources must refuse to write
    outside the target directory."""
    bundle = SkillBundle(
        skill_slug="s",
        skill_md="---\nname: s\n---\nbody",
        injection_rules="{}",
        evidence="[]",
        metadata="{}",
    )
    # Bypass the field validator (frozen + validate_assignment off) by
    # constructing a BundleFile then mutating via object.__setattr__ — the
    # backstop is the defence-in-depth layer this test exercises.
    bf = BundleFile(path="references/ok.md", kind="reference", content="x")
    object.__setattr__(bf, "path", "../escaped.txt")
    object.__setattr__(bundle, "bundle_files", [bf])

    target = tmp_path / "skill_root"
    target.mkdir()
    with pytest.raises(SkillBundleError) as exc:
        bundle.write_resources(target)
    assert exc.value.code == "E030_PATH_ESCAPE"
    assert not (tmp_path / "escaped.txt").exists()


def test_from_zip_rejects_zipslip_entry(tmp_path):
    """references/../foo passes the startswith('references/') filter but must
    be refused by BundleFile.path validation before any disk write."""
    from pydantic import ValidationError

    zip_path = tmp_path / "evil.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("SKILL.md", "---\nname: s\n---\nbody")
        zf.writestr("resource_plan.json", "{}")
        zf.writestr("references/../../escaped.txt", "pwned")
    with pytest.raises(ValidationError):
        SkillBundle.from_zip(zip_path)
