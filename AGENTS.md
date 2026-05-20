# mega-code plugin agent guide

This repo contains the MEGA-Code plugin surfaces:

- Claude Code skills in `skills/`
- lifecycle hooks in `hooks/`
- helper scripts in `scripts/`
- client/runtime code in `mega_code/`

Keep changes scoped to those areas. If a task requires core extraction logic,
prefer editing `mega_code/` rather than duplicating logic in skill docs or shell
scripts.

## Repo map

```text
skills/wisdom-gen/ -> /mega-code:wisdom-gen
skills/status/     -> /mega-code:status
skills/profile/    -> /mega-code:profile
skills/login/      -> /mega-code:login
skills/help/       -> /mega-code:help
```

Session ingestion is hook-less: `wisdom-gen` triggers an on-demand sync
that scans `~/.claude/projects/` directly, filters by the user's cwd,
and uploads matching transcripts. State lives in
`~/.local/share/mega-code/projects/<folder>/claude-sync-ledger.json`.

## Non-negotiable runtime rules

### Resolve `MEGA_DIR` in Claude-facing skills

Every Claude skill that runs `uv` must set:

```bash
MEGA_DIR="${CLAUDE_PLUGIN_ROOT:-$(cat ~/.local/share/mega-code/plugin-root 2>/dev/null)}"
```

Every `uv run` command must include:

```bash
--directory "$MEGA_DIR"
```

### Load environment before Python commands in skills/scripts

Before Python commands that depend on credentials or server config, load `.env`:

```bash
set -a && . "$MEGA_DIR/.env" 2>/dev/null && set +a
```

If a command talks to the MEGA-Code server, check `MEGA_CODE_API_KEY` first and
fail with a clear message when it is missing.

### Keep related shell steps in one Bash block

If a skill depends on variables such as `MEGA_DIR`, `LOG`, or exported project
context, keep the commands in one Bash block so state is preserved.

## Claude Code skill conventions

Claude skills live in `skills/*/SKILL.md`.

Required frontmatter:

- `description:`
- `allowed-tools:`

Optional but expected when relevant:

- `argument-hint:`
- `disable-model-invocation: true` for skills that only orchestrate Bash

Authoring rules:

- Prefer the smallest `allowed-tools:` set that still works.
- Use `Bash, Read` by default; add `Write`, `Edit`, or `AskUserQuestion` only when needed.
- Keep command examples copy-pastable.
- Do not hardcode plugin install paths; use `${CLAUDE_PLUGIN_ROOT}` in hooks and `MEGA_DIR` in skills.
- If a skill invokes Python entry points, prefer existing modules in `mega_code.client` or scripts in `scripts/`.

## Preferred implementation pattern

When adding or updating behavior:

1. Put reusable logic in `mega_code/` or `scripts/`.
2. Keep `SKILL.md` files focused on invocation workflow and operator guidance.
3. Reuse existing commands and paths where possible.

## Consistency checks

Before finishing a change, verify:

- referenced files and commands actually exist in this repo
- Claude skills use the `MEGA_DIR` pattern when calling `uv`
- hook commands use `${CLAUDE_PLUGIN_ROOT}`
- new server-facing commands document the required auth/env assumptions
- instructions do not mention commands or skills that are absent from this repo

## What to avoid

- Duplicating Python business logic in `SKILL.md`
- hardcoded absolute paths in hooks or skills
- leaving stale references in docs after renaming files or commands
