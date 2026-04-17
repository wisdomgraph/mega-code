"""Sync installed skills from a skills-lock.json file.

Usage:
    # Sync existing skills + detect new ones:
    python -m mega_code.client.update \
        --userdir /home/user --project_dir /path/to/project --mega_dir /path/to/pkg

    # Install specific new skills:
    python -m mega_code.client.update \
        --userdir /home/user --project_dir /path/to/project --mega_dir /path/to/pkg \
        --install-skills mega-code-foo,mega-code-bar
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import sys
from pathlib import Path

from mega_code.client.utils.tracing import get_tracer, setup_tracing


def _tracer():
    return get_tracer(__name__)


def _parse_skill_name(skill_md: Path) -> str | None:
    """Extract the name: field from a SKILL.md frontmatter."""
    try:
        text = skill_md.read_text()
    except OSError:
        return None
    m = re.search(r"^name:\s*(.+)$", text, re.MULTILINE)
    if not m:
        return None
    return m.group(1).strip().strip("\"'")


def _build_skill_map(mega_dir: Path) -> dict[str, Path]:
    """Map skill name (from frontmatter) -> skill directory in the repo."""
    with _tracer().start_as_current_span("update.build_skill_map") as span:
        span.set_attribute("update.mega_dir", str(mega_dir))
        skills_root = mega_dir / "skills"
        mapping: dict[str, Path] = {}
        if not skills_root.is_dir():
            span.set_attribute("update.skill_map_count", 0)
            span.set_attribute("update.skill_map_names", "")
            return mapping
        for skill_dir in sorted(skills_root.iterdir()):
            skill_md = skill_dir / "SKILL.md"
            if skill_md.is_file():
                name = _parse_skill_name(skill_md)
                if name:
                    mapping[name] = skill_dir
        span.set_attribute("update.skill_map_count", len(mapping))
        span.set_attribute("update.skill_map_names", ",".join(sorted(mapping.keys())))
        return mapping


def _load_lock(path: Path) -> dict | None:
    """Load and return a skills-lock.json, or None if missing/invalid."""
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text())
        if "skills" not in data:
            raise ValueError("missing 'skills' key")
        return data
    except (json.JSONDecodeError, ValueError) as exc:
        raise SystemExit(f"Error: invalid lock file {path}: {exc}") from exc


def _compute_hash(skill_dir: Path) -> str:
    """Compute SHA-256 matching the TypeScript computeSkillFolderHash.

    Recursively collects all files (excluding .git/ and node_modules/),
    sorts by POSIX relative path, and hashes path+content pairs.
    """
    files: list[tuple[str, bytes]] = []
    for path in skill_dir.rglob("*"):
        if not path.is_file():
            continue
        parts = path.relative_to(skill_dir).parts
        if ".git" in parts or "node_modules" in parts:
            continue
        rel = path.relative_to(skill_dir).as_posix()
        files.append((rel, path.read_bytes()))

    files.sort(key=lambda f: f[0])

    h = hashlib.sha256()
    for rel_path, content in files:
        h.update(rel_path.encode())
        h.update(content)
    return h.hexdigest()


def _resolve_lock_and_target(project_dir: Path, userdir: Path) -> tuple[dict, Path, Path] | None:
    """Find the lock file and corresponding target dir.

    Returns (lock_data, lock_path, target_dir) or None if no lock exists.
    """
    with _tracer().start_as_current_span("update.resolve_lock") as span:
        project_lock_path = project_dir / "skills-lock.json"
        user_lock_path = userdir / ".agents" / ".skill-lock.json"

        lock = _load_lock(project_lock_path)
        if lock is not None:
            span.set_attribute("update.lock_path", str(project_lock_path))
            span.set_attribute("update.lock_scope", "project")
            span.set_attribute("update.lock_skill_count", len(lock["skills"]))
            return lock, project_lock_path, project_dir / ".agents" / "skills"

        lock = _load_lock(user_lock_path)
        if lock is not None:
            span.set_attribute("update.lock_path", str(user_lock_path))
            span.set_attribute("update.lock_scope", "user")
            span.set_attribute("update.lock_skill_count", len(lock["skills"]))
            return lock, user_lock_path, userdir / ".agents" / "skills"

        span.set_attribute("update.lock_scope", "none")
        return None


def _sync(lock: dict, lock_path: Path, target_dir: Path, skill_map: dict[str, Path]) -> None:
    """Sync existing skills and detect new ones."""
    with _tracer().start_as_current_span("update.sync") as span:
        span.set_attribute("update.lock_path", str(lock_path))
        span.set_attribute("update.target_dir", str(target_dir))

        updated: list[str] = []
        removed: list[str] = []

        print(f"Using lock: {lock_path}")

        for skill_name in lock["skills"]:
            if not skill_name.startswith("mega-code-"):
                span.add_event(
                    "update.sync.skill",
                    {"skill_name": skill_name, "action": "skipped_non_mega"},
                )
                continue
            dest = target_dir / skill_name
            if skill_name in skill_map:
                dest.mkdir(parents=True, exist_ok=True)
                shutil.copytree(skill_map[skill_name], dest, dirs_exist_ok=True)
                updated.append(skill_name)
                span.add_event(
                    "update.sync.skill",
                    {"skill_name": skill_name, "action": "updated"},
                )
            else:
                if dest.is_dir():
                    shutil.rmtree(dest)
                    removed.append(skill_name)
                    span.add_event(
                        "update.sync.skill",
                        {"skill_name": skill_name, "action": "removed"},
                    )
                else:
                    removed.append(f"{skill_name} (already absent)")
                    span.add_event(
                        "update.sync.skill",
                        {"skill_name": skill_name, "action": "skipped_absent"},
                    )

        # Clean up orphan mega-code-* dirs not tracked by the lock file.
        # These can linger from previous installs and cause loading warnings
        # (e.g. stale SKILL.md missing required fields).
        if target_dir.is_dir():
            lock_names = set(lock["skills"].keys())
            for child in sorted(target_dir.iterdir()):
                if (
                    child.is_dir()
                    and child.name.startswith("mega-code-")
                    and child.name not in lock_names
                    and child.name not in skill_map
                ):
                    shutil.rmtree(child)
                    removed.append(child.name)
                    span.add_event(
                        "update.sync.skill",
                        {"skill_name": child.name, "action": "removed_orphan"},
                    )

        if updated:
            print(f"\nUpdated ({len(updated)}):")
            for name in updated:
                print(f"  + {name}")
        if removed:
            print(f"\nRemoved ({len(removed)}):")
            for name in removed:
                print(f"  - {name}")
        if not updated and not removed:
            print("\nNothing to sync.")

        # Detect new skills: in repo, not in lock, and not already installed
        not_in_lock = set(skill_map.keys()) - set(lock["skills"].keys())
        new_skills = sorted(
            name for name in not_in_lock if not (target_dir / name / "SKILL.md").is_file()
        )
        if new_skills:
            print(f"\nNEW_SKILLS:{json.dumps(new_skills)}")

        span.set_attribute("update.updated_count", len(updated))
        span.set_attribute("update.removed_count", len(removed))
        span.set_attribute("update.new_skills_count", len(new_skills))
        span.set_attribute("update.updated_names", ",".join(updated))
        span.set_attribute("update.removed_names", ",".join(removed))
        span.set_attribute("update.new_skill_names", ",".join(new_skills))


def _install(
    skill_names: list[str],
    lock: dict,
    lock_path: Path,
    target_dir: Path,
    skill_map: dict[str, Path],
    mega_dir: Path,
) -> int:
    """Install selected new skills: copy files and update lock."""
    with _tracer().start_as_current_span("update.install") as span:
        span.set_attribute("update.lock_path", str(lock_path))
        span.set_attribute("update.target_dir", str(target_dir))
        span.set_attribute("update.requested_names", ",".join(skill_names))

        installed: list[str] = []
        skipped: list[str] = []
        remote = _get_repo_remote(mega_dir)

        for name in skill_names:
            if name not in skill_map:
                print(f"Warning: skill '{name}' not found in repo, skipping")
                skipped.append(name)
                span.add_event(
                    "update.install.skill",
                    {"skill_name": name, "action": "not_found"},
                )
                continue
            src = skill_map[name]
            dest = target_dir / name
            dest.mkdir(parents=True, exist_ok=True)
            shutil.copytree(src, dest, dirs_exist_ok=True)

            computed_hash = _compute_hash(src)
            lock["skills"][name] = {
                "source": remote,
                "sourceType": "git",
                "computedHash": computed_hash,
            }
            installed.append(name)
            span.add_event(
                "update.install.skill",
                {
                    "skill_name": name,
                    "action": "installed",
                    "computed_hash": computed_hash,
                    "source": remote,
                },
            )

        if installed:
            lock_path.write_text(json.dumps(lock, indent=2) + "\n")
            print(f"\nInstalled ({len(installed)}):")
            for name in installed:
                print(f"  + {name}")
        else:
            print("\nNo skills installed.")

        span.set_attribute("update.installed_count", len(installed))
        span.set_attribute("update.skipped_count", len(skipped))
        span.set_attribute("update.installed_names", ",".join(installed))

        return 0


def _migrate_legacy_user_skills(userdir: Path) -> None:
    """Migrate mega-code skills/rules from ~/.codex/ to ~/.agents/ (one-time, idempotent).

    Users who installed skills before the path change have files under
    ~/.codex/skills/mega-code-*/ and ~/.codex/rules/mega-code/.  Codex CLI
    no longer loads from those locations, so we move the files and delete the
    originals.  Non-mega-code content in ~/.codex/ is never touched.
    """
    with _tracer().start_as_current_span("update.migrate_legacy") as span:
        moved_skills: list[str] = []
        moved_rules: list[str] = []

        # --- skills: ~/.codex/skills/mega-code-* → ~/.agents/skills/ ---
        legacy_skills_dir = userdir / ".codex" / "skills"
        new_skills_dir = userdir / ".agents" / "skills"
        if legacy_skills_dir.is_dir():
            for child in sorted(legacy_skills_dir.iterdir()):
                if child.is_dir() and child.name.startswith("mega-code-"):
                    dest = new_skills_dir / child.name
                    if not dest.exists():
                        dest.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copytree(child, dest)
                    shutil.rmtree(child)
                    moved_skills.append(child.name)
                    span.add_event(
                        "migrate.skill",
                        {"skill_name": child.name, "src": str(child), "dst": str(dest)},
                    )

        # --- rules: ~/.codex/rules/mega-code/ → ~/.agents/rules/mega-code/ ---
        legacy_rules_dir = userdir / ".codex" / "rules" / "mega-code"
        new_rules_dir = userdir / ".agents" / "rules" / "mega-code"
        if legacy_rules_dir.is_dir():
            for child in sorted(legacy_rules_dir.iterdir()):
                if child.is_file():
                    dest = new_rules_dir / child.name
                    if not dest.exists():
                        dest.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(child, dest)
                    child.unlink()
                    moved_rules.append(child.name)
                    span.add_event(
                        "migrate.rule",
                        {"rule_name": child.name, "src": str(child), "dst": str(dest)},
                    )
            # Remove empty legacy dir
            try:
                legacy_rules_dir.rmdir()
            except OSError:
                pass

        # --- AGENTS.md: warn if user still has strategy refs there ---
        legacy_agents_md = userdir / ".codex" / "AGENTS.md"
        if legacy_agents_md.is_file():
            text = legacy_agents_md.read_text()
            if "mega-code:strategies" in text:
                print(
                    "\nNotice: mega-code strategy references found in ~/.codex/AGENTS.md.\n"
                    "Please move the <!-- mega-code:strategies:start/end --> block\n"
                    "to ~/.agents/AGENTS.md so Codex can load them."
                )

        if moved_skills:
            print(f"\nMigrated from ~/.codex/ to ~/.agents/ ({len(moved_skills)} skills):")
            for name in moved_skills:
                print(f"  {name}")
        if moved_rules:
            print(f"\nMigrated rules ({len(moved_rules)}):")
            for name in moved_rules:
                print(f"  {name}")

        span.set_attribute("migrate.moved_skills_count", len(moved_skills))
        span.set_attribute("migrate.moved_rules_count", len(moved_rules))
        span.set_attribute("migrate.moved_skills_names", ",".join(moved_skills))
        span.set_attribute("migrate.moved_rules_names", ",".join(moved_rules))


def _get_repo_remote(mega_dir: Path) -> str:
    """Read the git remote URL from the repo config file (no subprocess)."""
    config = mega_dir / ".git" / "config"
    if not config.is_file():
        return "unknown"
    try:
        text = config.read_text()
        m = re.search(r'\[remote "origin"\][^\[]*url\s*=\s*(.+)', text)
        return m.group(1).strip() if m else "unknown"
    except OSError:
        return "unknown"


def main() -> int:
    parser = argparse.ArgumentParser(description="mega-code update — sync skills from lock file")
    parser.add_argument(
        "--userdir", required=True, help="User home directory. Always $HOME in SKILL.md invocation."
    )
    parser.add_argument(
        "--project_dir",
        required=True,
        help="Current working directory (the project root). Always $(pwd).",
    )
    parser.add_argument(
        "--mega_dir",
        required=True,
        help=(
            "Path to the checked-out mega-code package directory. "
            "Resolved from the pkg-breadcrumb file or defaulted to ~/.local/share/mega-code/pkg."
        ),
    )
    parser.add_argument(
        "--install-skills",
        default=None,
        help=(
            "Comma-separated skill names to install (e.g. mega-code-foo,mega-code-bar). "
            "When present, switches to install mode; skips sync and new-skills detection."
        ),
    )
    args = parser.parse_args()

    setup_tracing(service_name="mega-code-client")

    userdir = Path(args.userdir)
    project_dir = Path(args.project_dir)
    mega_dir = Path(args.mega_dir)

    mode = "install" if args.install_skills else "sync"

    with _tracer().start_as_current_span("update") as root_span:
        root_span.set_attribute("update.userdir", str(userdir))
        root_span.set_attribute("update.project_dir", str(project_dir))
        root_span.set_attribute("update.mega_dir", str(mega_dir))
        root_span.set_attribute("update.mode", mode)

        try:
            # Migrate any skills/rules from the legacy ~/.codex/ location
            _migrate_legacy_user_skills(userdir)

            # Build skill map
            skill_map = _build_skill_map(mega_dir)

            # Resolve lock file and target
            result = _resolve_lock_and_target(project_dir, userdir)
            if result is None:
                print("No skills-lock.json found. Nothing to do.")
                root_span.set_attribute("update.result", "no_lock_file")
                return 0
            lock, lock_path, target_dir = result

            if args.install_skills:
                names = [n.strip() for n in args.install_skills.split(",") if n.strip()]
                rc = _install(names, lock, lock_path, target_dir, skill_map, mega_dir)
                root_span.set_attribute("update.result", "install_done")
                return rc
            else:
                _sync(lock, lock_path, target_dir, skill_map)
                root_span.set_attribute("update.result", "sync_done")
                return 0
        except Exception as exc:
            root_span.record_exception(exc)
            raise
        finally:
            from mega_code.client.utils.ndjson_tracing import export_traces
            from mega_code.client.utils.tracing import get_span_writer

            export_traces(writer=get_span_writer())


if __name__ == "__main__":
    sys.exit(main())
