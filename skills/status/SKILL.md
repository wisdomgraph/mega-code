---
name: mega-code-status
description: "Show MEGA-Code status including pending skills, strategies, and recent pipeline runs."
argument-hint: ""
allowed-tools: Bash, Read
---

# MEGA-Code Status

Show current MEGA-Code status and pending items.

## Setup

```bash
MEGA_DIR="${MEGA_CODE_PLUGIN_ROOT:-$(cat ~/.local/share/mega-code/pkg-breadcrumb 2>/dev/null)}"
if [ -z "$MEGA_DIR" ] || [ ! -f "$MEGA_DIR/pyproject.toml" ]; then
  MEGA_DIR="$HOME/.local/share/mega-code/pkg"
  if [ ! -f "$MEGA_DIR/pyproject.toml" ]; then
    rm -rf "$MEGA_DIR"
    git clone --depth 1 "${MEGA_CODE_REPO_URL:-https://github.com/wisdomgraph/mega-code.git}" "$MEGA_DIR"
  fi
  bash "$MEGA_DIR/scripts/codex-bootstrap.sh" "$MEGA_DIR"
fi
export MEGA_CODE_DATA_DIR="$HOME/.local/share/mega-code"
uv run --directory "$MEGA_DIR" python -m mega_code.client.check_auth
```

If the auth check fails (non-zero exit), show the output to the user and stop.

## Pipeline Status

```bash
uv run --directory "$MEGA_DIR" mega-code pipeline-status 2>/dev/null || true
```

## Quick Status

```bash
ls -la ~/.local/share/mega-code/data/pending-skills/ ~/.local/share/mega-code/data/pending-strategies/ 2>/dev/null || echo "No pending items"
```

## Detailed Pending Items

Uses `ls` checks to avoid zsh glob errors on empty directories.

```bash
SKILLS_DIR="$HOME/.local/share/mega-code/data/pending-skills"
STRATS_DIR="$HOME/.local/share/mega-code/data/pending-strategies"

echo "=== Pending Skills ==="
if [ -d "$SKILLS_DIR" ] && [ "$(ls -A "$SKILLS_DIR" 2>/dev/null)" ]; then
  for dir in "$SKILLS_DIR"/*/; do
    name=$(basename "$dir")
    desc=$(grep -m1 "description:" "$dir/SKILL.md" 2>/dev/null | cut -d: -f2- | head -c 60)
    echo "  $name:$desc"
  done
else
  echo "  (none)"
fi

echo "=== Pending Strategies ==="
if [ -d "$STRATS_DIR" ] && [ "$(ls -A "$STRATS_DIR" 2>/dev/null)" ]; then
  for file in "$STRATS_DIR"/*.md; do
    name=$(basename "$file" .md)
    desc=$(grep -m1 "^# " "$file" | cut -c3- | head -c 60)
    echo "  $name: $desc"
  done
else
  echo "  (none)"
fi
```

## Output Locations

| Type | Pending Location | Installed Location |
|------|------------------|-------------------|
| Skills | `~/.local/share/mega-code/data/pending-skills/{name}/` | `.claude/skills/{name}/SKILL.md` |
| Strategies | `~/.local/share/mega-code/data/pending-strategies/{name}.md` | `.claude/rules/mega-code/{name}.md` |
