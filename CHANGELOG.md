# Changelog

Versioning policy: `.claude/rules/backward-compat-mega-code-client.md`.

## 2026-05-18

**Breaking (beta).** Dropped `include_claude` / `include_codex` from
`PipelineRunRequest` and from the `--include-claude` / `--include-codex` /
`--include-all` CLI flags. Sync branch now selected by `MEGA_CODE_AGENT`
(`claude` / `codex` / unset → MEGA-Code).

Wire-safe both directions: server has no `extra="forbid"`, so old client →
new server silently ignores the keys; new client → old server falls back
to defaults. Lockstep recommended for behaviour, not required for HTTP.
