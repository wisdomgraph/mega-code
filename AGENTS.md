# mega-code plugin

Codex plugin for session collection and skill extraction.
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

## Codex Skill Conventions

Codex CLI uses a different skill layout than Claude Code. Skills live in `codex-skills/`:

```
codex-skills/
├── mega-code-login/SKILL.md     # $mega-code-login
├── mega-code-run/SKILL.md       # $mega-code-run
├── mega-code-status/SKILL.md    # $mega-code-status
├── mega-code-profile/SKILL.md   # $mega-code-profile
└── mega-code-help/SKILL.md      # $mega-code-help
```

**Invocation syntax:** Codex uses `$mega-code-<name>` (dollar prefix) instead of
Claude Code's `/mega-code:skill-name` (slash + colon).

**Frontmatter:** Codex SKILL.md files use only `description:` in frontmatter.
Do not include `allowed-tools:`, `argument-hint:`, or `disable-model-invocation:`
— these are Claude Code-specific fields that Codex ignores.

**Bootstrap:** The `codex-bootstrap.sh` script installs Python dependencies via
`uv` on first run. It is called lazily from within skill scripts, not via hooks
(Codex CLI does not support lifecycle hooks).
