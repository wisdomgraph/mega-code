# mega-code plugin agent guide

This repo contains the MEGA-Code plugin surfaces for Codex CLI:

- skills in `skills/`
- helper scripts in `scripts/`
- client/runtime code in `mega_code/`

Keep changes scoped to those areas. If a task requires core extraction logic,
prefer editing `mega_code/` rather than duplicating logic in skill docs or shell
scripts.

## Repo map

```text
skills/wisdom-gen/ -> $mega-code-wisdom-gen
skills/status/     -> $mega-code-status
skills/profile/    -> $mega-code-profile
skills/login/      -> $mega-code-login
skills/update/     -> $mega-code-update
skills/help/       -> $mega-code-help

scripts/          -> codex-bootstrap.sh
```

## Non-negotiable runtime rules

### Resolve `MEGA_DIR` in skills

Every skill that runs `uv` must use the Codex setup block:

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
```

- Codex (first run): `pkg-breadcrumb` empty, clones + bootstraps, `codex-bootstrap.sh` writes `pkg-breadcrumb`.
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

Skills live in `skills/*/SKILL.md` and are installed via Codex CLI.

Required frontmatter:

- `name:` (used by Codex to register `$mega-code-*` commands)
- `description:`
- `allowed-tools:`

Optional but expected when relevant:

- `argument-hint:`
- `disable-model-invocation: true` for skills that only orchestrate Bash

Authoring rules:

- Prefer the smallest `allowed-tools:` set that still works.
- Use `Bash, Read` by default; add `Write` or `Edit` only when needed.
- `request_user_input` is a Codex CLI built-in — it does NOT need to be listed in `allowed-tools`.
- Keep command examples copy-pastable.
- Use `MEGA_DIR` in skills for the plugin root path.
- If a skill invokes Python entry points, prefer existing modules in `mega_code.client` or scripts in `scripts/`.
- Use `python -m mega_code.client.*` module entry points (not `scripts/*.py`).

## Preferred implementation pattern

When adding or updating behavior:

1. Put reusable logic in `mega_code/` or `scripts/`.
2. Keep `SKILL.md` files focused on invocation workflow and operator guidance.

## Consistency checks

Before finishing a change, verify:

- referenced files and commands actually exist in this repo
- skills use the Codex `MEGA_DIR` setup block
- skills have both `name:` and `allowed-tools:` in frontmatter
- new server-facing commands document the required auth/env assumptions
- instructions do not mention commands or skills that are absent from this repo

## What to avoid

- Duplicating Python business logic in `SKILL.md`
- hardcoded absolute paths in skills
- leaving stale references in docs after renaming files or commands
