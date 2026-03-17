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
hooks/hooks.json  → SessionStart / SessionEnd / UserPromptSubmit / Stop / BeforeAgent
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

`hooks/hooks.json` is a single file read by both Claude Code and Gemini CLI.
Each CLI silently skips event names it doesn't recognise, so both sets coexist.

- Hook commands use the fallback pattern
  `CC=${CLAUDE_PLUGIN_ROOT} GEM=${extensionPath} bash -c 'D="${CC:-$GEM}"; ...'`
  so the correct root directory resolves regardless of which CLI runs the hook
- Every hook entry must have a `timeout` field in **milliseconds** (max 30000
  for data hooks, 5000 for checks). Claude Code interprets these as seconds,
  making the values meaninglessly large — but hooks complete in under 1 s so the
  timeout is never hit. Gemini CLI interprets them as milliseconds (correct).
  Using millisecond values is the only direction that works for both CLIs.
- Claude Code events: `SessionStart`, `SessionEnd`, `UserPromptSubmit`, `Stop`
- Gemini CLI events: `SessionStart`, `SessionEnd`, `BeforeAgent`
- `BeforeAgent` mirrors `UserPromptSubmit` — both entries should carry the same
  command list so behaviour is consistent across CLIs
