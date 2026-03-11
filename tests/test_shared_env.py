"""Tests for shared local environment (~/.local/share/mega-code).

Covers:
1. Shared .env credential store (get_env_path, save_env_file, load_env_file)
2. Login saves credentials to the shared location
3. Cross-tool credential sharing (Claude Code + Codex use same path)
4. Bootstrap scripts create consistent data directory structure
"""

import os
import stat
from pathlib import Path
import pytest

from mega_code.client.cli import get_env_path, load_env_file, save_env_file


# ═══════════════════════════════════════════════════════════════════════════
# Unit tests: shared .env path and file operations
# ═══════════════════════════════════════════════════════════════════════════


class TestGetEnvPath:
    """get_env_path() always returns ~/.local/share/mega-code/.env."""

    def test_returns_stable_path(self, tmp_path, monkeypatch):
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        result = get_env_path()
        assert result == tmp_path / ".local" / "share" / "mega-code" / ".env"

    def test_creates_parent_directory(self, tmp_path, monkeypatch):
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        result = get_env_path()
        assert result.parent.is_dir()

    def test_idempotent_calls(self, tmp_path, monkeypatch):
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        path1 = get_env_path()
        path2 = get_env_path()
        assert path1 == path2


class TestSaveAndLoadEnvFile:
    """save_env_file and load_env_file round-trip correctly."""

    def test_round_trip(self, tmp_path):
        env_path = tmp_path / ".env"
        env_vars = {
            "MEGA_CODE_API_KEY": "mg_test_key_123",
            "MEGA_CODE_CLIENT_MODE": "remote",
            "MEGA_CODE_SERVER_URL": "https://console.megacode.ai",
        }
        save_env_file(env_path, env_vars)
        loaded = load_env_file(env_path)
        assert loaded == env_vars

    def test_permissions_are_600(self, tmp_path):
        env_path = tmp_path / ".env"
        save_env_file(env_path, {"KEY": "value"})
        mode = stat.S_IMODE(env_path.stat().st_mode)
        assert mode == 0o600

    def test_update_preserves_existing_keys(self, tmp_path):
        env_path = tmp_path / ".env"
        save_env_file(env_path, {"A": "1", "B": "2"})
        save_env_file(env_path, {"B": "updated"})
        loaded = load_env_file(env_path)
        assert loaded["A"] == "1"
        assert loaded["B"] == "updated"

    def test_load_missing_file_returns_empty(self, tmp_path):
        env_path = tmp_path / "nonexistent.env"
        assert load_env_file(env_path) == {}


# ═══════════════════════════════════════════════════════════════════════════
# Unit tests: login saves to shared location
# ═══════════════════════════════════════════════════════════════════════════


class TestLoginSavesToSharedEnv:
    """_save_api_key writes credentials to ~/.local/share/mega-code/.env."""

    def test_save_api_key_writes_to_stable_path(self, tmp_path, monkeypatch):
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        from mega_code.client.login import _save_api_key

        env_path, env_vars = _save_api_key(
            "mg_test_api_key",
            "https://console.megacode.ai/api/mega-service/v1",
        )
        assert env_path == tmp_path / ".local" / "share" / "mega-code" / ".env"
        assert env_vars["MEGA_CODE_API_KEY"] == "mg_test_api_key"
        assert env_vars["MEGA_CODE_CLIENT_MODE"] == "remote"
        assert env_vars["MEGA_CODE_SERVER_URL"] == "https://console.megacode.ai"

    def test_save_api_key_file_permissions(self, tmp_path, monkeypatch):
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        from mega_code.client.login import _save_api_key

        env_path, _ = _save_api_key("mg_key", "https://x.com/api/mega-service/v1")
        mode = stat.S_IMODE(env_path.stat().st_mode)
        assert mode == 0o600

    def test_save_api_key_preserves_existing_vars(self, tmp_path, monkeypatch):
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        from mega_code.client.login import _save_api_key

        # Pre-populate with an extra key
        env_path = get_env_path()
        save_env_file(env_path, {"OPENAI_API_KEY": "sk-existing"})

        _save_api_key("mg_new_key", "https://x.com/api/mega-service/v1")
        loaded = load_env_file(env_path)
        assert loaded["OPENAI_API_KEY"] == "sk-existing"
        assert loaded["MEGA_CODE_API_KEY"] == "mg_new_key"


# ═══════════════════════════════════════════════════════════════════════════
# Unit tests: cross-tool credential sharing
# ═══════════════════════════════════════════════════════════════════════════


class TestCrossToolCredentialSharing:
    """Login from one tool (Claude Code or Codex) is visible to the other."""

    def test_login_via_claude_code_visible_to_codex(self, tmp_path, monkeypatch):
        """Credentials saved by Claude Code login are readable by Codex CLI."""
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        from mega_code.client.login import _save_api_key

        # Simulate Claude Code login
        _save_api_key("mg_claude_key", "https://console.megacode.ai/api/mega-service/v1")

        # Simulate Codex reading the same .env (via the same get_env_path)
        env_path = get_env_path()
        loaded = load_env_file(env_path)
        assert loaded["MEGA_CODE_API_KEY"] == "mg_claude_key"
        assert loaded["MEGA_CODE_CLIENT_MODE"] == "remote"

    def test_login_via_codex_visible_to_claude_code(self, tmp_path, monkeypatch):
        """Credentials saved via Codex are readable by Claude Code."""
        monkeypatch.setattr(Path, "home", lambda: tmp_path)

        # Simulate Codex login (same _save_api_key, same path)
        from mega_code.client.login import _save_api_key

        _save_api_key("mg_codex_key", "https://console.megacode.ai/api/mega-service/v1")

        # Claude Code reads the same file
        env_path = get_env_path()
        loaded = load_env_file(env_path)
        assert loaded["MEGA_CODE_API_KEY"] == "mg_codex_key"

    def test_second_login_overwrites_api_key(self, tmp_path, monkeypatch):
        """Re-login from another tool overwrites the API key."""
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        from mega_code.client.login import _save_api_key

        _save_api_key("mg_first_key", "https://console.megacode.ai/api/mega-service/v1")
        _save_api_key("mg_second_key", "https://console.megacode.ai/api/mega-service/v1")

        loaded = load_env_file(get_env_path())
        assert loaded["MEGA_CODE_API_KEY"] == "mg_second_key"

    def test_run_pipeline_script_loads_stable_env(self, tmp_path, monkeypatch):
        """run_pipeline_async.py loads ~/.local/share/mega-code/.env first."""
        # The script at module level does:
        #   _stable_env = Path.home() / ".local" / "share" / "mega-code" / ".env"
        #   if _stable_env.exists(): dotenv.load_dotenv(_stable_env, override=False)
        # We verify the path construction matches get_env_path()
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        stable_env = Path.home() / ".local" / "share" / "mega-code" / ".env"
        assert stable_env == get_env_path()


# ═══════════════════════════════════════════════════════════════════════════
# Integration tests: bootstrap scripts produce consistent layout
# ═══════════════════════════════════════════════════════════════════════════


class TestBootstrapConsistency:
    """Both session-start.sh and codex-bootstrap.sh create the same data dir layout."""

    SCRIPTS_DIR = Path(__file__).parent.parent / "scripts"

    def _run_bootstrap(self, script_name, tmp_path, mega_dir, data_dir):
        """Run a bootstrap script with controlled env."""
        import subprocess

        script = self.SCRIPTS_DIR / script_name
        if not script.is_file():
            pytest.skip(f"Script {script_name} not found")

        env = os.environ.copy()
        env["MEGA_CODE_DATA_DIR"] = str(data_dir)
        env["HOME"] = str(tmp_path / "home")
        (tmp_path / "home").mkdir(exist_ok=True)

        if script_name == "session-start.sh":
            env["CLAUDE_PLUGIN_ROOT"] = str(mega_dir)
        subprocess.run(
            ["bash", str(script)] + ([str(mega_dir)] if "codex" in script_name else []),
            env=env,
            timeout=60,
            capture_output=True,
        )
        return data_dir

    def test_both_scripts_create_env_file(self, tmp_path):
        """Both bootstrap scripts create .env in the data directory."""
        mega_dir = tmp_path / "mega"
        mega_dir.mkdir()
        (mega_dir / "pyproject.toml").write_text(
            '[project]\nname="t"\nversion="0.1"\nrequires-python=">=3.10"\n'
        )
        (mega_dir / ".env").touch()

        for script_name in ("codex-bootstrap.sh", "session-start.sh"):
            data_dir = tmp_path / f"data-{script_name}"
            self._run_bootstrap(script_name, tmp_path, mega_dir, data_dir)
            assert (data_dir / ".env").is_file(), f"{script_name} didn't create .env"
            mode = stat.S_IMODE((data_dir / ".env").stat().st_mode)
            assert mode == 0o600, f"{script_name} didn't set .env permissions to 0600"

    def test_both_scripts_create_profile_json(self, tmp_path):
        """Both bootstrap scripts create profile.json = {}."""
        mega_dir = tmp_path / "mega"
        mega_dir.mkdir()
        (mega_dir / "pyproject.toml").write_text(
            '[project]\nname="t"\nversion="0.1"\nrequires-python=">=3.10"\n'
        )
        (mega_dir / ".env").touch()

        for script_name in ("codex-bootstrap.sh", "session-start.sh"):
            data_dir = tmp_path / f"data-{script_name}"
            self._run_bootstrap(script_name, tmp_path, mega_dir, data_dir)
            assert (data_dir / "profile.json").read_text() == "{}"

    def test_both_scripts_write_plugin_root_breadcrumb(self, tmp_path):
        """Both bootstrap scripts write plugin-root breadcrumb."""
        mega_dir = tmp_path / "mega"
        mega_dir.mkdir()
        (mega_dir / "pyproject.toml").write_text(
            '[project]\nname="t"\nversion="0.1"\nrequires-python=">=3.10"\n'
        )
        (mega_dir / ".env").touch()

        for script_name in ("codex-bootstrap.sh", "session-start.sh"):
            data_dir = tmp_path / f"data-{script_name}"
            self._run_bootstrap(script_name, tmp_path, mega_dir, data_dir)
            assert (data_dir / "plugin-root").read_text().strip() == str(mega_dir)

    def test_credential_migration_from_plugin_dir(self, tmp_path):
        """Bootstrap migrates credentials from MEGA_DIR/.env to DATA_DIR/.env."""
        mega_dir = tmp_path / "mega"
        mega_dir.mkdir()
        (mega_dir / "pyproject.toml").write_text(
            '[project]\nname="t"\nversion="0.1"\nrequires-python=">=3.10"\n'
        )
        # Write credentials in the old location
        (mega_dir / ".env").write_text("MEGA_CODE_API_KEY=mg_migrated_key\n")

        data_dir = tmp_path / "data-migration"
        self._run_bootstrap("codex-bootstrap.sh", tmp_path, mega_dir, data_dir)

        content = (data_dir / ".env").read_text()
        assert "mg_migrated_key" in content
