---
description: View or update your MEGA-Code developer profile (language, level, style) to personalise skill extraction.
argument-hint: [--language <lang>] [--level Beginner|Intermediate|Expert] [--style Mentor|Formal|Concise] [--reset]
allowed-tools: Bash, AskUserQuestion
---

# Developer Profile

Set up your developer profile to personalise skill extraction. Profile determines
which skills are too basic for your experience level.

## Finding the MEGA-Code Directory

```bash
MEGA_DIR="${CLAUDE_PLUGIN_ROOT:-$(cat ~/.local/mega-code/plugin-root 2>/dev/null)}"
```

## Interactive Setup (Recommended)

Ask the user for their profile using `AskUserQuestion` with these fields:

- **language**: Preferred communication language — options: `English`, `Korean`, `Thai`
  (user can also type a custom language via "Other")
- **level**: `Beginner`, `Intermediate`, or `Expert`
- **style**: `Mentor`, `Formal`, or `Concise` (reserved for future use)

After collecting answers, save with:

```bash
MEGA_DIR="${CLAUDE_PLUGIN_ROOT:-$(cat ~/.local/mega-code/plugin-root 2>/dev/null)}"
uv run --directory "$MEGA_DIR" mega-code profile --language "<language>" --level <level> --style <style>
```

## Show Current Profile

```bash
MEGA_DIR="${CLAUDE_PLUGIN_ROOT:-$(cat ~/.local/mega-code/plugin-root 2>/dev/null)}"
uv run --directory "$MEGA_DIR" mega-code profile
```

## Reset Profile

```bash
MEGA_DIR="${CLAUDE_PLUGIN_ROOT:-$(cat ~/.local/mega-code/plugin-root 2>/dev/null)}"
uv run --directory "$MEGA_DIR" mega-code profile --reset
```

## Profile Storage

Profile is saved in two places:

- **Remote database** (Postgres via mega-service) — authoritative source, persists across machines.
  Requires a valid API key (run `/mega-code:login` first).
- **Local mirror** `~/.local/mega-code/profile.json` — written only after a successful remote save.
  Used by the local pipeline to personalise skill and lesson generation without a network call.

If the remote save fails (e.g. no API key configured), the local file is **not** updated.
