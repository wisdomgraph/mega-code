---
name: mega-code-profile
description: "View or update your MEGA-Code developer profile (language, level, style) to personalise skill extraction."
argument-hint: "[--language <lang>] [--level Beginner|Intermediate|Expert] [--style Mentor|Formal|Concise] [--reset]"
allowed-tools: Bash, AskUserQuestion
---

# Developer Profile

Set up your developer profile to personalise skill extraction. Profile determines
which skills are too basic for your experience level.

## Setup

```bash
MEGA_DIR="${CLAUDE_PLUGIN_ROOT:-$(cat ~/.local/share/mega-code/pkg-breadcrumb 2>/dev/null)}"
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

## Interactive Setup (Recommended)

Ask the user for their profile using `AskUserQuestion` with these fields:

- **language**: Preferred communication language — options: `English`, `Korean`, `Thai`
  (user can also type a custom language via "Other")
- **level**: `Beginner`, `Intermediate`, or `Expert`
- **style**: `Mentor`, `Formal`, or `Concise` (reserved for future use)

Save with:

```bash
uv run --directory "$MEGA_DIR" mega-code profile --language "<language>" --level <level> --style <style>
```

## Show Current Profile

```bash
uv run --directory "$MEGA_DIR" mega-code profile
```

## Reset Profile

```bash
uv run --directory "$MEGA_DIR" mega-code profile --reset
```

## Profile Storage

Profile is saved in two places:

- **Remote server** — authoritative source, persists across machines.
  Requires a valid API key (run `/mega-code:login` or `$mega-code-login` first).
- **Local mirror** `~/.local/share/mega-code/profile.json` — written only after a successful remote save.
