"""Cross-language hash compatibility tests.

Golden values computed via Node.js using computeSkillFolderHash from
.local/skills/src/local-lock.ts to ensure Python produces identical hashes.
"""

from mega_code.client.update import _compute_hash

# -- Golden values (from Node.js) --

# Single file: SKILL.md = "---\nname: test\ndescription: test\n---\n# Test\n"
SINGLE_FILE_HASH = "05f84fd1e1dcd9efbe1fd9a6ea416e00e954f6bb2bc4faaf7591968a226e285c"

# Two files: SKILL.md = "root", sub/helper.md = "nested"
MULTI_FILE_HASH = "e06a3bc6924950c4b389e9106ad37c8ca8377f29ef02eb7c2d6efc00532f14ec"


def test_single_file_matches_typescript(tmp_path):
    skill_dir = tmp_path / "my-skill"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text("---\nname: test\ndescription: test\n---\n# Test\n")
    assert _compute_hash(skill_dir) == SINGLE_FILE_HASH


def test_multi_file_matches_typescript(tmp_path):
    skill_dir = tmp_path / "my-skill"
    (skill_dir / "sub").mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("root")
    (skill_dir / "sub" / "helper.md").write_text("nested")
    assert _compute_hash(skill_dir) == MULTI_FILE_HASH


def test_ignores_git_and_node_modules(tmp_path):
    skill_dir = tmp_path / "my-skill"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text("---\nname: test\ndescription: test\n---\n# Test\n")
    git_dir = skill_dir / ".git"
    git_dir.mkdir()
    (git_dir / "HEAD").write_text("ref: refs/heads/main")
    nm_dir = skill_dir / "node_modules" / "foo"
    nm_dir.mkdir(parents=True)
    (nm_dir / "index.js").write_text("noop")
    assert _compute_hash(skill_dir) == SINGLE_FILE_HASH


def test_deterministic(tmp_path):
    skill_dir = tmp_path / "my-skill"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text("content")
    assert _compute_hash(skill_dir) == _compute_hash(skill_dir)
