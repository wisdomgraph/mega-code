---
description: Show MEGA-Code status including pending skills, strategies, and recent pipeline runs.
argument-hint: ""
allowed-tools: Bash, Read
---

# MEGA-Code Status

Show current MEGA-Code status and pending items.

## Setup

```bash
MEGA_DIR="${CLAUDE_PLUGIN_ROOT:-$(cat ~/.local/mega-code/plugin-root 2>/dev/null)}"
```

## Quick Status

```bash
ls -la ~/.local/mega-code/data/pending-skills/ ~/.local/mega-code/data/pending-strategies/ 2>/dev/null || echo "No pending items"
```

## Detailed Pending Items

Uses `ls` checks to avoid zsh glob errors on empty directories.

```bash
SKILLS_DIR="$HOME/.local/mega-code/data/pending-skills"
STRATS_DIR="$HOME/.local/mega-code/data/pending-strategies"

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
| Skills | `~/.local/mega-code/data/pending-skills/{name}/` | `.claude/skills/{name}/SKILL.md` |
| Strategies | `~/.local/mega-code/data/pending-strategies/{name}.md` | `.claude/rules/mega-code/{name}.md` |
