---
description: Show MEGA-Code status including pending skills, strategies, and recent pipeline runs.
argument-hint: ""
allowed-tools: Bash, Read
---

# MEGA-Code Status

Show current MEGA-Code status and pending items.

## Finding the MEGA-Code Directory

```bash
# Discover mega-code root (marketplace or symlink install)
MEGA_DIR="${CLAUDE_PLUGIN_ROOT:-$(cat ~/.local/mega-code/plugin-root 2>/dev/null)}"
```

## Quick Status

Show current MEGA-Code status:
- Number of pending skills and strategies
- Recent pipeline runs
- Installation status

```bash
# Check pending items
ls -la ~/.local/mega-code/data/pending-skills/ ~/.local/mega-code/data/pending-strategies/ 2>/dev/null || echo "No pending items"
```

## Detailed Pending Items

List all pending skills with descriptions and all pending strategies with categories:

```bash
# Show pending skills
echo "=== Pending Skills ===" && \
for dir in ~/.local/mega-code/data/pending-skills/*/; do
  if [ -d "$dir" ]; then
    name=$(basename "$dir")
    desc=$(grep -m1 "description:" "$dir/SKILL.md" 2>/dev/null | cut -d: -f2- | head -c 60)
    echo "  $name: $desc..."
  fi
done

# Show pending strategies
echo "=== Pending Strategies ===" && \
for file in ~/.local/mega-code/data/pending-strategies/*.md 2>/dev/null; do
  if [ -f "$file" ]; then
    name=$(basename "$file" .md)
    desc=$(grep -m1 "^# " "$file" | cut -c3- | head -c 60)
    echo "  $name: $desc..."
  fi
done
```

## Output Locations

| Type | Pending Location | Installed Location |
|------|------------------|-------------------|
| Skills | `~/.local/mega-code/data/pending-skills/{name}/` | `.claude/skills/{name}/SKILL.md` |
| Strategies | `~/.local/mega-code/data/pending-strategies/{name}.md` | `.claude/rules/mega-code/{name}.md` |
