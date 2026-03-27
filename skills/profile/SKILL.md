---
name: mega-code-profile
description: "View or update your developer profile. Usage: [--language <lang>] [--level Beginner|Intermediate|Expert] [--style Mentor|Formal|Concise] [--reset]"
allowed-tools: Bash
---

# Developer Profile

Set up your developer profile to personalise skill extraction. Profile determines
which skills are too basic for your experience level.

## Setup

```bash
MEGA_DIR="$(cat ~/.local/share/mega-code/pkg-breadcrumb 2>/dev/null)"
if [ -z "$MEGA_DIR" ] || [ ! -f "$MEGA_DIR/pyproject.toml" ]; then
  MEGA_DIR="$HOME/.local/share/mega-code/pkg"
  if [ ! -f "$MEGA_DIR/pyproject.toml" ]; then
    rm -rf "$MEGA_DIR"
    git clone --depth 1 --branch codex "${MEGA_CODE_REPO_URL:-https://github.com/wisdomgraph/mega-code.git}" "$MEGA_DIR"
  fi
  bash "$MEGA_DIR/scripts/codex-bootstrap.sh" "$MEGA_DIR"
fi
export MEGA_CODE_DATA_DIR="$HOME/.local/share/mega-code"
export UV_CACHE_DIR="${UV_CACHE_DIR:-$MEGA_DIR/.uv-cache}"
uv run --directory "$MEGA_DIR" python -m mega_code.client.check_auth
```

If the auth check fails (non-zero exit), show the output to the user and stop.

## Interactive Setup (Recommended)

Use `request_user_input` with all three questions in a single call:

1. "Preferred language?" — options: `English`, `Korean`, `Thai`
   (user can type a custom language via the auto-added "Other" option)
2. "Experience level?" — options: `Beginner`, `Intermediate`, `Expert`
3. "Communication style?" — options: `Mentor`, `Formal`, `Concise`

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
  Requires a valid API key (run `$mega-code-login` first).
- **Local mirror** `~/.local/share/mega-code/profile.json` — written only after a successful remote save.
