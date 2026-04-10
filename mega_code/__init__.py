"""MEGA-Code: Open-source Codex CLI plugin for AI optimization.

Collects interaction data, extracts reusable skills, and optimizes
AI workflows via the MEGA framework.
"""

from importlib.metadata import version as _pkg_version

try:
    __version__ = _pkg_version("mega-code")
except Exception:
    __version__ = "0.0.0-dev"
