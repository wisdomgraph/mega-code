# mega-code plugin

Multi-CLI plugin (Claude Code + Gemini CLI) for session collection and skill extraction.
All Python logic lives in `mega_code/` (the parent repo). This directory owns
only slash commands, lifecycle hooks, and plugin configuration.

## Structure

```
skills/run/       → /mega-code:run      trigger extraction pipeline
skills/status/    → /mega-code:status   show pending items
skills/profile/   → /mega-code:profile  set language/level/style
skills/login/     → /mega-code:login    OAuth flow
skills/help/      → /mega-code:help     list available commands
hooks/hooks.json  → SessionStart / SessionEnd / UserPromptSubmit / Stop
scripts/          → session-start.sh, check_pending_skills.py, run_pipeline_async.py

gemini-extension.json → Gemini CLI extension manifest
GEMINI.md             → Gemini CLI system instructions
```

## Installation

This repo can be installed as a plugin/extension via multiple channels:

- **Claude Code** — marketplace (`/plugin marketplace install mind-ai-mega-code`)
- **Gemini CLI** — marketplace (`gemini extensions install wisdomgraph/mega-code`)
- **Codex** — `npx skills add wisdomgraph/mega-code -a codex`

Skills in `skills/` and hooks in `hooks/hooks.json` are shared across all CLIs.
Hook commands use `${CLAUDE_PLUGIN_ROOT:-${extensionPath}}` so the correct
template variable is expanded regardless of which CLI runs the hook.

## MEGA_DIR Setup (required in every skill that calls uv run)

```bash
if [ -n "$CLAUDE_PLUGIN_ROOT" ]; then
  MEGA_DIR="$CLAUDE_PLUGIN_ROOT"            # Claude Code → env var
else
  MEGA_DIR="$(cat ~/.local/share/mega-code/pkg-breadcrumb 2>/dev/null)"  # Codex → pkg-breadcrumb
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

- Every `SKILL.md` must have `description:` and `allowed-tools:` frontmatter
- Use `disable-model-invocation: true` for skills that only run Bash commands
- Allowed tools should be minimal — prefer `Bash, Read` over unrestricted sets
- All commands in one Bash block so variables stay in scope across steps

## Hook Conventions

- All hook commands reference `${CLAUDE_PLUGIN_ROOT}` — never hardcode paths
- Every hook entry must have a `timeout` field (max 30s for data hooks, 5s for checks)
- Required events: `SessionStart`, `SessionEnd`, `UserPromptSubmit`, `Stop`
