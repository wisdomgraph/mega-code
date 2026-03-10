"""Tests for documentation contracts — Codex CLI coverage (Phase 5)."""

from pathlib import Path

ROOT = Path(__file__).parent.parent.parent


class TestReadmeCodexSection:
    """Cycle 1: README.md documents Codex CLI usage."""

    def test_readme_has_codex_cli_heading(self):
        content = (ROOT / "README.md").read_text()
        assert "## Codex CLI" in content or "### Codex CLI" in content

    def test_readme_codex_section_has_dollar_commands(self):
        content = (ROOT / "README.md").read_text()
        assert "$mega-code-run" in content
        assert "$mega-code-login" in content

    def test_readme_codex_section_has_agents_skills_path(self):
        content = (ROOT / "README.md").read_text()
        assert ".agents/skills/" in content

    def test_readme_mentions_include_codex_flag(self):
        content = (ROOT / "README.md").read_text()
        assert "--include-codex" in content


class TestAgentsMdCodexConventions:
    """Cycle 2: AGENTS.md documents Codex skill conventions."""

    def test_agents_md_documents_codex_skills_dir(self):
        content = (ROOT / "AGENTS.md").read_text()
        assert "codex-skills/" in content

    def test_agents_md_documents_dollar_invocation(self):
        content = (ROOT / "AGENTS.md").read_text()
        assert "$mega-code-" in content

    def test_agents_md_has_codex_heading(self):
        content = (ROOT / "AGENTS.md").read_text()
        lines = content.split("\n")
        heading_lines = [l for l in lines if l.startswith("#") and "odex" in l]
        assert len(heading_lines) >= 1


class TestReadmeProjectStructure:
    """Cycle 3: README project tree includes Codex entries."""

    def test_readme_project_tree_has_codex_skills(self):
        content = (ROOT / "README.md").read_text()
        assert "codex-skills/" in content

    def test_readme_project_tree_has_codex_bootstrap(self):
        content = (ROOT / "README.md").read_text()
        assert "codex-bootstrap.sh" in content
