# mega-code plugin

Gemini CLI extension for session collection and skill extraction.
All Python logic lives in `mega_code/` (the parent repo). This directory owns
only slash commands, lifecycle hooks, and plugin configuration.

## Structure

```
skills/run/       → /mega-code:run      trigger extraction pipeline
skills/status/    → /mega-code:status   show pending items
skills/profile/   → /mega-code:profile  set language/level/style
skills/login/     → /mega-code:login    OAuth flow
skills/help/      → /mega-code:help     list available commands
skills/stop/      → /mega-code:stop     stop running pipeline
commands/mega-code/ → TOML command wrappers for the above
hooks/hooks.json  → SessionStart / SessionEnd lifecycle hooks
```

## MEGA_DIR Setup (required in every command that calls uv run)

```bash
MEGA_DIR="$(cat ~/.local/share/mega-code/plugin-root 2>/dev/null)"
if [ -z "$MEGA_DIR" ] || [ ! -f "$MEGA_DIR/pyproject.toml" ]; then
  MEGA_DIR="$HOME/.local/share/mega-code/pkg"
fi
```

All `uv run` commands must use `--directory "$MEGA_DIR"`.
Before any `uv run`, set the cache dir to avoid sandbox permission issues:

```bash
export UV_CACHE_DIR="${UV_CACHE_DIR:-$MEGA_DIR/.uv-cache}"
```

## Environment Loading

```bash
set -a && . "$MEGA_DIR/.env" 2>/dev/null && set +a
```

Always source `.env` before any Python command. Check `MEGA_CODE_API_KEY` is set
before making server calls.

## Skill Conventions

- Every `SKILL.md` must have `description:` in its frontmatter
- Skills are activated contextually by the model when relevant
- All commands in one Bash block so variables stay in scope across steps

## Getting Started

1. Run `/mega-code:login` to authenticate via GitHub or Google OAuth
2. Run `/mega-code:profile` to set your developer profile
3. Code as normal — sessions are collected automatically
4. Run `/mega-code:run` to extract skills from your coding sessions
5. Run `/mega-code:status` to check pending items
