# mega-code plugin agent guide

This repo contains the MEGA-Code plugin surfaces:

- unified skills in `skills/` (serves both Claude Code and Codex CLI)
- lifecycle hooks in `hooks/`
- helper scripts in `scripts/`
- client/runtime code in `mega_code/`

Keep changes scoped to those areas. If a task requires core extraction logic,
prefer editing `mega_code/` rather than duplicating logic in skill docs or shell
scripts.

## Repo map

```text
skills/run/       -> /mega-code:run (Claude Code) / $mega-code-run (Codex)
skills/status/    -> /mega-code:status / $mega-code-status
skills/profile/   -> /mega-code:profile / $mega-code-profile
skills/login/     -> /mega-code:login / $mega-code-login
skills/help/      -> /mega-code:help / $mega-code-help

hooks/hooks.json   -> SessionStart / SessionEnd / UserPromptSubmit / Stop
scripts/           -> session-start.sh, check_pending_skills.py,
                      run_pipeline_async.py
```

## Non-negotiable runtime rules

### Resolve `MEGA_DIR` in skills

Every skill that runs `uv` must use the unified setup block:

```bash
if [ -n "$CLAUDE_PLUGIN_ROOT" ]; then
  MEGA_DIR="$CLAUDE_PLUGIN_ROOT"
else
  MEGA_DIR="$(cat ~/.local/share/mega-code/pkg-breadcrumb 2>/dev/null)"
fi
if [ -z "$MEGA_DIR" ] || [ ! -f "$MEGA_DIR/pyproject.toml" ]; then
  MEGA_DIR="$HOME/.local/share/mega-code/pkg"
  if [ ! -f "$MEGA_DIR/pyproject.toml" ]; then
    rm -rf "$MEGA_DIR"
    git clone --depth 1 "${MEGA_CODE_REPO_URL:-https://github.com/wisdomgraph/mega-code.git}" "$MEGA_DIR"
  fi
  bash "$MEGA_DIR/scripts/codex-bootstrap.sh" "$MEGA_DIR"
fi
```

- Claude Code: `CLAUDE_PLUGIN_ROOT` is set by the runtime, resolves immediately, bootstrap skipped.
- Codex (first run): env var unset, `pkg-breadcrumb` empty, clones + bootstraps, `codex-bootstrap.sh` writes `pkg-breadcrumb`.
- Codex (subsequent): `pkg-breadcrumb` resolves, bootstrap skipped.

Before any `uv run`, set the cache dir to avoid sandbox permission issues:

```bash
export UV_CACHE_DIR="${UV_CACHE_DIR:-$MEGA_DIR/.uv-cache}"
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

## Skill conventions

Skills live in `skills/*/SKILL.md` and serve both Claude Code and Codex CLI.

Required frontmatter:

- `name:` (used by Codex to register `$mega-code-*` commands)
- `description:`
- `allowed-tools:` (used by Claude Code)

Optional but expected when relevant:

- `argument-hint:`
- `disable-model-invocation: true` for skills that only orchestrate Bash

Authoring rules:

- Prefer the smallest `allowed-tools:` set that still works.
- Use `Bash, Read` by default; add `Write`, `Edit`, or `AskUserQuestion` only when needed.
- Keep command examples copy-pastable.
- Do not hardcode plugin install paths; use `${CLAUDE_PLUGIN_ROOT}` in hooks and `MEGA_DIR` in skills.
- If a skill invokes Python entry points, prefer existing modules in `mega_code.client` or scripts in `scripts/`.
- Use `python -m mega_code.client.*` module entry points (not `scripts/*.py`).
- When referencing commands in docs, show both syntaxes where helpful (`/mega-code:run` / `$mega-code-run`).

## Hook conventions

Hook config lives in `hooks/hooks.json`.

Required rules:

- Reference `${CLAUDE_PLUGIN_ROOT}` in every hook command.
- Every hook entry must include a `timeout`.
- Use at most `30` seconds for collection/data hooks and at most `5` seconds for quick checks.
- Supported events in this repo are `SessionStart`, `SessionEnd`, `UserPromptSubmit`, and `Stop`.

When editing hooks:

- Keep commands non-interactive.
- Prefer existing scripts/modules over inline shell.
- Preserve fast-path behavior for prompt-time hooks.

## Preferred implementation pattern

When adding or updating behavior:

1. Put reusable logic in `mega_code/` or `scripts/`.
2. Keep `SKILL.md` files focused on invocation workflow and operator guidance.
3. Each skill serves both platforms from a single file — no separate Codex variants needed.

## Consistency checks

Before finishing a change, verify:

- referenced files and commands actually exist in this repo
- skills use the unified `MEGA_DIR` setup block
- skills have both `name:` and `allowed-tools:` in frontmatter
- hook commands use `${CLAUDE_PLUGIN_ROOT}`
- new server-facing commands document the required auth/env assumptions
- instructions do not mention commands or skills that are absent from this repo
- `grep -r "codex-skills" .` returns no matches

## What to avoid

- Duplicating Python business logic in `SKILL.md`
- hardcoded absolute paths in hooks or skills
- maintaining separate skill files for Claude Code and Codex
- leaving stale references in docs after renaming files or commands
