---
name: mega-code-profile
description: "View or update your MEGA-Code developer profile (language, level, style) to personalise skill extraction."
---

# Developer Profile

Set up your developer profile to personalise skill extraction. Profile determines
which skills are too basic for your experience level.

## Setup

```bash
MEGA_DIR="$HOME/.local/share/mega-code/pkg"
if [ ! -f "$MEGA_DIR/pyproject.toml" ]; then
  git clone --depth 1 https://github.com/wisdomgraph/mega-code.git "$MEGA_DIR"
fi
bash "$MEGA_DIR/scripts/codex-bootstrap.sh" "$MEGA_DIR"
```

## Interactive Setup (Recommended)

Ask the user for their profile with these fields:

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
  Requires a valid API key (run `$mega-code-login` first).
- **Local mirror** `~/.local/share/mega-code/profile.json` — written only after a successful remote save.
