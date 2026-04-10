# mega_code/client/eval_workspace.py
"""Iteration workspace management for skill-enhance enhancement loop.

Manages persistent workspace directories for each skill evaluation,
replacing the old /tmp-based approach with structured iteration dirs.

Directory structure::

    ~/.local/share/mega-code/data/skill-enhance/{skill-name}/
      iteration-1/
        original-skill.md     # backup of SKILL.md before enhancement
        test-cases.json
        ab-results.json
        gradings.json
        eval-full.json
        benchmark.json
        feedback.json          # written by HTTP server from browser POST
        draft-skill.md         # written by host agent (pre-bump draft)
        enhanced-skill.md      # written by accept-skill (final accepted version)
      iteration-2/
        ...
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path

from mega_code.client.dirs import data_dir
from mega_code.client.skill_utils import canonical_skill_name

logger = logging.getLogger(__name__)


def _validate_path_component(name: str, label: str = "name") -> None:
    """Reject path components that could escape their parent directory."""
    if not name or ".." in name or "/" in name or "\x00" in name:
        raise ValueError(f"Invalid {label}: {name!r}")


def workspace_root(skill_name: str) -> Path:
    """Return workspace root for a skill: <data_dir>/data/skill-enhance/<skill_name>/."""
    _validate_path_component(skill_name, "skill_name")
    return data_dir() / "data" / "skill-enhance" / skill_name


def resolve_workspace_skill_name(skill_name: str, skill_path: str = "") -> str:
    """Return the canonical skill slug for workspace storage."""
    fallback_name = canonical_skill_name(skill_name)
    if not skill_path:
        return fallback_name

    path = Path(skill_path)
    if path.is_dir():
        path = path / "SKILL.md"
    if not path.exists():
        return fallback_name

    try:
        skill_md = path.read_text(encoding="utf-8")
    except OSError:
        return fallback_name
    return canonical_skill_name(skill_name, skill_md)


def _max_iteration(root: Path) -> int:
    """Find the highest iteration-N number under *root*, or 0 if none exist."""
    if not root.exists():
        return 0
    max_iter = 0
    for child in root.iterdir():
        if child.is_dir():
            match = re.match(r"^iteration-(\d+)$", child.name)
            if match:
                max_iter = max(max_iter, int(match.group(1)))
    return max_iter


def create_iteration_dir(skill_name: str, skill_path: str = "") -> tuple[Path, int]:
    """Create the next iteration directory for a skill evaluation.

    Scans existing iteration-N dirs and creates iteration-(N+1).

    Returns:
        Tuple of (iteration_dir_path, iteration_number).
    """
    root = workspace_root(resolve_workspace_skill_name(skill_name, skill_path))
    root.mkdir(parents=True, exist_ok=True)

    max_iter = _max_iteration(root)

    next_iter = max_iter + 1
    iteration_dir = root / f"iteration-{next_iter}"
    iteration_dir.mkdir(parents=True, exist_ok=True)

    logger.info("Created iteration dir: %s", iteration_dir)
    return iteration_dir, next_iter


def save_artifact(iteration_dir: Path, name: str, data: dict) -> Path:
    """Save a JSON artifact to the iteration directory.

    Args:
        iteration_dir: Path to the iteration-N directory.
        name: Artifact filename (e.g., "test-cases.json").
        data: Dictionary to serialize as JSON.

    Returns:
        Path to the saved file.
    """
    if not name.endswith(".json"):
        name = f"{name}.json"
    path = iteration_dir / name
    if not path.resolve().is_relative_to(iteration_dir.resolve()):
        raise ValueError(f"Artifact path escapes iteration dir: {name!r}")
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    logger.info("Saved artifact: %s", path)
    return path


def load_artifact(iteration_dir: Path, name: str) -> dict | None:
    """Load a JSON artifact from the iteration directory.

    Returns:
        Parsed dict, or None if the file doesn't exist.
    """
    if not name.endswith(".json"):
        name = f"{name}.json"
    path = iteration_dir / name
    if not path.resolve().is_relative_to(iteration_dir.resolve()):
        raise ValueError(f"Artifact path escapes iteration dir: {name!r}")
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def load_previous_iteration(skill_name: str, iteration: int) -> dict | None:
    """Load eval-full.json from the previous iteration for comparison.

    Args:
        skill_name: Name of the skill.
        iteration: Current iteration number (loads iteration - 1).

    Returns:
        Parsed eval-full dict, or None if no previous iteration exists.
    """
    if iteration <= 1:
        return None
    prev_dir = workspace_root(skill_name) / f"iteration-{iteration - 1}"
    return load_artifact(prev_dir, "eval-full.json")


def get_latest_iteration(skill_name: str) -> int:
    """Get the highest iteration number for a skill, or 0 if none exist."""
    return _max_iteration(workspace_root(skill_name))


def save_text_artifact(iteration_dir: Path, name: str, content: str) -> Path:
    """Save a text artifact (e.g., enhanced-skill.md, review.html).

    Args:
        iteration_dir: Path to the iteration-N directory.
        name: Artifact filename.
        content: Text content to write.

    Returns:
        Path to the saved file.
    """
    path = iteration_dir / name
    if not path.resolve().is_relative_to(iteration_dir.resolve()):
        raise ValueError(f"Artifact path escapes iteration dir: {name!r}")
    path.write_text(content, encoding="utf-8")
    logger.info("Saved text artifact: %s", path)
    return path


def main() -> None:
    """CLI entry point for eval_workspace.

    Subcommands:
        create-iteration  Create the next iteration directory for a skill.
    """
    import argparse

    parser = argparse.ArgumentParser(
        prog="mega_code.client.eval_workspace",
        description="Iteration workspace management for skill-enhance.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    create_iter = subparsers.add_parser(
        "create-iteration",
        help="Create the next iteration directory for a skill evaluation",
    )
    create_iter.add_argument(
        "--skill-name", required=True, help="Name of the skill to create an iteration for"
    )
    create_iter.add_argument(
        "--skill-path",
        default="",
        help="Optional path to SKILL.md used to derive the canonical workspace slug",
    )

    args = parser.parse_args()

    if args.command == "create-iteration":
        path, num = create_iteration_dir(args.skill_name, args.skill_path)
        print(json.dumps({"path": str(path), "iteration": num}))


if __name__ == "__main__":
    main()
