"""save_outputs_to_pending must materialize a SkillBundle to disk.

Remote-mode runs previously wrote only SKILL.md + injection/evidence/metadata,
dropping resource_plan.json and the bundle files even though the response
carried a SkillBundle. These tests pin the resource-bearing write and the
invariant that the normalized SKILL.md is NOT clobbered by bundle content.
"""

from __future__ import annotations

import json
from pathlib import Path

from mega_code.client import pending as pending_mod
from mega_code.client.api.protocol import (
    BundleFile,
    OutputsResult,
    PendingSkillData,
    PipelineStatusResult,
    ResourcePlan,
    SkillBundle,
)
from mega_code.client.pending import save_outputs_to_pending

_NORMALIZED_MD = "---\nname: my-skill\ndescription: A demo skill.\n---\n\n# My Skill\n"

_RAW_BUNDLE_MD = "---\nname: my-skill\n---\n\nRAW bundle markdown — must NOT overwrite disk.\n"


def _status_with_bundle(bundle: SkillBundle | None) -> PipelineStatusResult:
    skill = PendingSkillData(
        skill_name="my-skill",
        skill_md=_NORMALIZED_MD,
        injection_rules='{"skill_id": "my-skill"}',
        evidence="[]",
        metadata='{"skill_id": "my-skill"}',
        skill_bundle=bundle,
    )
    return PipelineStatusResult(
        run_id="r1",
        project_id="p1",
        status="completed",
        outputs=OutputsResult(pending_skills=[skill]),
    )


def test_bundle_writes_resource_plan_and_files(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(pending_mod, "PENDING_SKILLS_DIR", tmp_path / "pending-skills")
    bundle = SkillBundle(
        skill_slug="my-skill",
        skill_md=_RAW_BUNDLE_MD,
        injection_rules="{}",
        evidence="[]",
        metadata="{}",
        bundle_files=[
            BundleFile(path="references/examples.md", kind="reference", content="# Examples\n"),
            BundleFile(path="scripts/run.sh", kind="script", content="#!/bin/sh\necho hi\n"),
        ],
        resource_plan=ResourcePlan(create_references=True, create_scripts=True, rationale="why"),
    )

    save_outputs_to_pending(_status_with_bundle(bundle))

    skill_dir = tmp_path / "pending-skills" / "my-skill"
    plan_path = skill_dir / "resource_plan.json"
    assert plan_path.exists()
    plan = json.loads(plan_path.read_text())
    assert plan["create_references"] is True
    assert plan["create_scripts"] is True

    assert (skill_dir / "references/examples.md").read_text() == "# Examples\n"
    assert (skill_dir / "scripts/run.sh").read_text() == "#!/bin/sh\necho hi\n"

    # The normalized SKILL.md from the remote path must survive — to_disk's raw
    # bundle markdown must never clobber it.
    skill_md = (skill_dir / "SKILL.md").read_text()
    assert "RAW bundle markdown" not in skill_md
    assert "# My Skill" in skill_md


def test_no_bundle_writes_no_resource_plan(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(pending_mod, "PENDING_SKILLS_DIR", tmp_path / "pending-skills")

    save_outputs_to_pending(_status_with_bundle(None))

    skill_dir = tmp_path / "pending-skills" / "my-skill"
    assert (skill_dir / "SKILL.md").exists()
    assert not (skill_dir / "resource_plan.json").exists()
    assert not (skill_dir / "references").exists()
