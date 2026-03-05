# mega-code plugin

Claude Code plugin for session collection and skill extraction.
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
```

## MEGA_DIR Setup (required in every skill that calls uv run)

```bash
MEGA_DIR="${CLAUDE_PLUGIN_ROOT:-$(cat ~/.local/mega-code/plugin-root 2>/dev/null)}"
```

All `uv run` commands must use `--directory "$MEGA_DIR"`.

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
