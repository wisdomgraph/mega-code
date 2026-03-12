---
name: mega-code-status
description: "Show MEGA-Code status including pending skills, strategies, and recent pipeline runs."
---

# MEGA-Code Status

Show current MEGA-Code status and pending items.

## Setup

```bash
MEGA_DIR="$HOME/.local/mega-code/pkg"
if [ ! -f "$MEGA_DIR/pyproject.toml" ]; then
  git clone --depth 1 https://github.com/wisdomgraph/mega-code.git "$MEGA_DIR"
fi
bash "$MEGA_DIR/scripts/codex-bootstrap.sh" "$MEGA_DIR"
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
| Skills | `~/.local/mega-code/data/pending-skills/{name}/` | `.agents/skills/{name}/SKILL.md` |
| Strategies | `~/.local/mega-code/data/pending-strategies/{name}.md` | `.agents/rules/mega-code/{name}.md` |
