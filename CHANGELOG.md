# Changelog

Versioning policy: `.claude/rules/backward-compat-mega-code-client.md`.

## 2026-06-01 — v1.1.4b1

**Additive (beta).** New SkillBundle public surface for progressive-disclosure
skill packaging. Adds `SkillBundle`, `BundleFile`, `ResourcePlan`, and
`SkillBundleError` to `mega_code.client.api.protocol`, plus optional
`skill_bundle` / `skill_bundles` fields on `PendingSkillData` and
`OutputsResult`. Client B7 materialization writes bundle resources alongside
SKILL.md/injection/evidence/metadata when present.

Wire-safe both directions: new fields default to `None` / `[]` and no model
adds `extra="forbid"`, so old client → new server ignores bundle data and
new client → old server falls back to legacy single-file behaviour.

**Security.** `BundleFile.path` now rejects absolute paths and `..` segments
at the model boundary, and `SkillBundle.write_resources` raises
`E030_PATH_ESCAPE` if a resolved path escapes the target directory. Closes
the server→client path-traversal vector reachable via `save_outputs_to_pending`.

## 2026-05-18

**Breaking (beta).** Dropped `include_claude` / `include_codex` from
`PipelineRunRequest` and from the `--include-claude` / `--include-codex` /
`--include-all` CLI flags. Sync branch now selected by `MEGA_CODE_AGENT`
(`claude` / `codex` / unset → MEGA-Code).

Wire-safe both directions: server has no `extra="forbid"`, so old client →
new server silently ignores the keys; new client → old server falls back
to defaults. Lockstep recommended for behaviour, not required for HTTP.
