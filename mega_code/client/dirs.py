"""Platform-aware directory resolution for mega-code.

Follows XDG Base Directory Specification on Linux and macOS (CLI convention).
Migration from the legacy path (~/.local/mega-code) is handled by
session-start.sh, not here — this module is pure path resolution.

See docs/design-docs/2026-xdg-data-dirs.md for rationale.
"""

import os
from pathlib import Path


def data_dir() -> Path:
    """User data root: sessions, pipeline output, skills, config.

    Resolution order:
    1. MEGA_CODE_DATA_DIR env var (explicit override)
    2. XDG_DATA_HOME/mega-code (default: ~/.local/share/mega-code)
    """
    if env := os.environ.get("MEGA_CODE_DATA_DIR"):
        p = Path(env)
        p.mkdir(parents=True, exist_ok=True)
        return p

    xdg_data = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
    new_path = xdg_data / "mega-code"
    new_path.mkdir(parents=True, exist_ok=True)
    return new_path
