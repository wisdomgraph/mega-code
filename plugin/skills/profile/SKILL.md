---
description: View or update your MEGA-Code developer profile (language, level, style).
argument-hint: [--language <lang>] [--level Beginner|Intermediate|Expert] [--style Mentor|Formal|Concise] [--reset]
allowed-tools: Bash
---

# MEGA-Code Profile

View or update your developer profile. The profile personalises MEGA-Code output
(lessons, skills, strategies) to your language, experience level, and communication style.

## Finding the MEGA-Code Directory

```bash
MEGA_DIR="$(cat ~/.local/mega-code/plugin-root 2>/dev/null || echo $HOME/.claude/mega-code)"
```

## Show Current Profile

```bash
MEGA_DIR="$(cat ~/.local/mega-code/plugin-root 2>/dev/null || echo $HOME/.claude/mega-code)"
set -a && . "$MEGA_DIR/.env" 2>/dev/null && set +a && \
  uv run --directory "$MEGA_DIR" python -m mega_code.client.cli profile
```

## Update Profile Fields

Set any combination of the three personalisation fields:

```bash
MEGA_DIR="$(cat ~/.local/mega-code/plugin-root 2>/dev/null || echo $HOME/.claude/mega-code)"
set -a && . "$MEGA_DIR/.env" 2>/dev/null && set +a && \
  uv run --directory "$MEGA_DIR" python -m mega_code.client.cli profile \
    --language "English" \
    --level "Expert" \
    --style "Concise"
```

### Available Options

| Field | Options |
|-------|---------|
| `--language` | Any language string, e.g. `English`, `Thai`, `Japanese` |
| `--level` | `Beginner`, `Intermediate`, `Expert` |
| `--style` | `Mentor`, `Formal`, `Concise` |

You can pass any subset — only the specified fields are updated; others are kept.

## Reset Profile

Remove all profile settings and revert to defaults:

```bash
MEGA_DIR="$(cat ~/.local/mega-code/plugin-root 2>/dev/null || echo $HOME/.claude/mega-code)"
set -a && . "$MEGA_DIR/.env" 2>/dev/null && set +a && \
  uv run --directory "$MEGA_DIR" python -m mega_code.client.cli profile --reset
```

## Profile Storage

Profile is stored at: `~/.local/mega-code/profile.json`

The profile is applied automatically during pipeline runs to personalise
generated lessons, skills, and strategies.
