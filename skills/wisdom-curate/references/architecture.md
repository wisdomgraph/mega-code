# wisdom-curate architecture reference

Background reference for the wisdom-curate skill. Read on demand when you
need to understand where curations and skills are stored, or which Python
helpers are available.

## Data directory

The mega-code data directory is returned by `mega_code.client.dirs.data_dir()`.
Use this function to resolve the path — never hardcode it.

Skills and curations are stored under this directory:

```
{data_dir()}/skills/{skill-name}/             ← installed skill directories
  SKILL.md                                     ← main skill file
  scripts/                                     ← optional
  references/                                  ← optional

{data_dir()}/curations/pending/               ← curated, not yet executed
  {session_id}.json
{data_dir()}/curations/running/               ← currently executing
  {session_id}.json
{data_dir()}/curations/completed/             ← finished
  {session_id}.json
```

Each curation JSON contains: `session_id`, `query`, `curation` (markdown
workflow), `token_count`, `cost_usd`, `created_at`, `status`. The `skills`
and `wisdoms` fields from the live `WisdomCurateResult` are intentionally
excluded: `skills` carries pre-signed download URLs that expire, so
re-using them on resume would 403; `wisdoms` is omitted because the
`curation` markdown already embeds the skill references it depends on.

## Key Python functions

- `mega_code.client.dirs.data_dir()` → data root path
- `mega_code.client.skill_installer.skills_dir()` → skills directory
- `mega_code.client.skill_installer.install_skills(skills)` → download + extract
- `mega_code.client.curation_store.save_curation(result)` → save to pending/
- `mega_code.client.curation_store.get_curation(session_id)` → load by ID
- `mega_code.client.curation_store.list_curations(status)` → list by status
- `mega_code.client.curation_store.update_curation_status(id, status)` → transition
