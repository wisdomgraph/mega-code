# Add user email to generated SKILL.md frontmatter (key: `creator`)

**Product**: MEGA-Code-Server Claude Code, Codex plugin  
**Version**: 1.0  
**Author**: Senior Software Architect  
**Date**: 2026-04-07
**Status**: Ready for implementation

---

## Goal

Generated SKILL.md files carry `metadata.creator` (value: user's email)
attributing them to the
logged-in user. The gate runs **after** `run_pipeline` completes and
**only** when at least one skill was written to the pending directory.
No skills generated → silent no-op, no prompt.

## Constraints

- **No re-login.** `login.py` untouched; existing API keys stay valid.
- **Server prerequisite.** `GET /profile` must return `email`. Lives in
  the private MEGA repo. Until shipped, users hit the prompt fallback.
- **Idempotent injection.** `ensure_skill_frontmatter` guards the field
  with `if email and "creator" not in metadata` (mirrors existing
  `author`/`version`/`tags`, NOT the `setdefault` path reserved for
  `extra_frontmatter`). Re-runs are byte-identical.
- **Gate runs BEFORE pending review.** Review copies files out of the
  pending dir, so patching must happen first.
- **Gate runs only on pipeline success.** Inside the Post-Pipeline
  Workflow success branch — never at file level. See §6 exit-code matrix.

## Architecture

```
run_pipeline → pending/pending-skills/<slug>/SKILL.md (no email)
                                │
                                ▼
            ensure_user_email --resolve-and-apply
                ├─ pending dir empty?            → exit 0
                ├─ env MEGA_CODE_USER_EMAIL set? → apply, exit 0
                ├─ profile.email available?     → cache + apply, exit 0
                └─ otherwise                     → exit 2 EMAIL_REQUIRED
                                │
        Claude reads exit 2 ──▶ AskUserQuestion
                  enter ──▶ --set-from-env && --apply-all-pending
                  skip  ──▶ proceed
                                │
                                ▼
                    pending review sub-workflow
```

## Implementation

### 1. `mega_code/client/skill_utils.py`

Three edits to teach the existing frontmatter plumbing about `email`.
`pending.py` is NOT modified — the pipeline write path stays email-free;
the post-pipeline CLI patches files via `ensure_skill_frontmatter`, and
the whitelist update lets `skill-enhance` preserve the field (§7).

**Line 24** — add `"creator"` to `SKILL_METADATA_KEYS`:
```python
SKILL_METADATA_KEYS = ("author", "creator", "version", "tags", "generated_at", "roi")
```

**Line 71 `_quote_skill_metadata_fields`** — add `"creator"` to the
quoted-value loop (email values contain `@` and need quoting):
```python
for key in ("version", "generated_at", "creator"):
```

**`ensure_skill_frontmatter` at line 478** — add `email: str = ""`
kwarg (the **value** is the user's email; the **frontmatter key** is
`creator`). Inject in two branches mirroring `author` (explicit guard,
NOT `setdefault`).

```python
def ensure_skill_frontmatter(
    skill_md: str,
    skill_name: str,
    *,
    author: str = "",
    email: str = "",                    # new — written as metadata["creator"]
    version: str = "",
    generated_at: str = "",
    tags: list[str] | None = None,
    extra_frontmatter: dict | None = None,
) -> str:
```

Insert in both `has_nested_metadata` branch (after `author`, line 519+)
and fresh-build branch (after `author`, line 565+):
```python
if email and "creator" not in metadata:
    metadata["creator"] = email
```

**Skip the `has_legacy_metadata` branch (line 549)** — it returns
unchanged without injecting `author` either; documented in Known
Limitations.

**`normalize_pending_skill_markdown` — do not modify.** §5 calls
`ensure_skill_frontmatter` directly.

### 2. `mega_code/client/api/protocol.py`

**Line 163 `UserProfile`** — add optional read-only email field:
```python
email: str | None = Field(
    None,
    description="Authenticated user's email. Server-populated, read-only on client.",
)
```
Pydantic v2 defaults to `extra="ignore"`, so older servers parse as `None`.

### 3. `mega_code/client/api/remote.py`

**Line 291** (`save_profile`) — strip `email` from PUT payload:
```python
payload = profile.model_dump(by_alias=True, exclude={"email"})
```

### 4. `mega_code/client/profile.py`

**Line 40** — exclude `email` from local JSON mirror:
```python
content = json.dumps(
    profile.model_dump(by_alias=True, exclude={"email"}), indent=2
)
```

**Atomic commit requirement.** §2 + §3 + §4 MUST land in a single commit.
§2 alone causes `/mega-code:profile` edits to echo cached email back to
the server AND leak it into `~/.local/share/mega-code/profile.json`.

### 5. `mega_code/client/ensure_user_email.py` (new)

Argparse CLI. Imports env helpers from `mega_code.client.cli`
(`get_env_path`/`load_env_file`/`save_env_file`). Uses `create_client`
from `mega_code.client.api` (same factory as `check_auth.py`).

Modes:
- `--resolve-and-apply [--non-interactive]` — pending dir empty → exit 0;
  resolve email (env cache → profile API → exit 2, or silent skip if
  `--non-interactive`); patch each pending SKILL.md.
- `--apply-all-pending` — apply cached email without resolving.
- `--set-from-env` — read `MEGA_CODE_EMAIL_INPUT`, validate, cache.
- `--show` — print cached email.

**Key behaviors** (full file is straightforward argparse — not reproduced):

```python
# Permissive regex: rejects whitespace. save_env_file writes bare
# KEY=value (cli.py:87,96), so quoted local-parts never appear.
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

def _load_env_into_os() -> None:
    """Mirror check_auth.py:31-32 so create_client() finds the API key."""
    for k, v in load_env_file(get_env_path()).items():
        os.environ.setdefault(k, v)

def _iter_pending_skill_files() -> list[Path]:
    from mega_code.client.pending import PENDING_SKILLS_DIR
    if not PENDING_SKILLS_DIR.exists():
        return []
    return [
        d / "SKILL.md" for d in PENDING_SKILLS_DIR.iterdir()
        if d.is_dir() and (d / "SKILL.md").is_file()
    ]

def _apply_to(paths: list[Path], email: str) -> int:
    """Idempotent via ensure_skill_frontmatter's `creator not in metadata` guard."""
    from mega_code.client.skill_utils import ensure_skill_frontmatter
    touched = 0
    for path in paths:
        content = path.read_text(encoding="utf-8")
        updated = ensure_skill_frontmatter(content, skill_name=path.parent.name, email=email)
        if updated != content:
            path.write_text(updated, encoding="utf-8")
            touched += 1
    print(f"ensure_user_email: applied email to {touched}/{len(paths)} skill(s)")
    return 0

def _resolve_and_apply(non_interactive: bool) -> int:
    paths = _iter_pending_skill_files()
    if not paths:
        return 0  # silent no-op when nothing was generated
    email = _load_cached() or _try_resolve_from_profile()
    if not email:
        if non_interactive:
            return 0  # references/enhance-handoff.md §55-60
        print("EMAIL_REQUIRED", file=sys.stderr)
        return 2
    return _apply_to(paths, email)
```

`_load_cached` / `_save_cached` wrap `load_env_file`/`save_env_file`
around `MEGA_CODE_USER_EMAIL`. `_try_resolve_from_profile` calls
`_load_env_into_os()` then `create_client().load_profile()`, caches on
success, returns `""` on any exception. `_set_from_env` reads
`MEGA_CODE_EMAIL_INPUT`, validates against `EMAIL_RE` (exit 1 on
invalid), caches. `_apply_all_pending` requires cache (exit 1 if empty).

**Security.** `--set-from-env` reads from an env var, never positional
shell args. The skill-side invocation MUST single-quote with `'\''`
escaping (§6).

### 6. `skills/wisdom-gen/SKILL.md` — post-pipeline gate

Insert a new **numbered list item** inside `## Post-Pipeline Workflow`
→ `### Steps:` (line 133), **after** item 1 (parse `run_id`/`project_id`)
and **before** the current item 2 (review sub-workflow command).
Renumber the existing items 2 → 3, 3 → 4. Do NOT add a new `###`
sub-heading; the gate is an item in the existing numbered list.

**Exit-code placement matrix.** The gate runs **only in success paths**:

| Pipeline exit | Path                                | Gate? |
|---------------|-------------------------------------|-------|
| 0             | normal success                      | ✅    |
| 2             | "wait for existing run to finish"   | ✅ (after wait) |
| 2             | "stop and start new"                | ✅ (on retry) |
| 2             | "leave running, exit"               | ❌    |
| 3             | "run again"                         | ✅ (on retry) |
| 3             | "do nothing"                        | ❌    |

Implementer MUST guard the gate behind the success condition, never
file-level.

````markdown
2. **Inject User Email** — after parsing `run_id` / `project_id`, run
   the gate before the review sub-workflow.

```bash
MEGA_DIR="$(cd "${CLAUDE_SKILL_DIR}/../.." && pwd)"
uv run --directory "$MEGA_DIR" python -m mega_code.client.ensure_user_email --resolve-and-apply
EMAIL_STATUS=$?
echo "email_status=$EMAIL_STATUS"
```

- `email_status=0` → cached, resolved, or no skills to patch. Continue.
- `email_status=2` → use `AskUserQuestion`:
  - **Question:** "Email attribution is not cached and the server profile
    didn't return one. Enter your email to tag the generated skill(s),
    or skip."
  - **Options:** `Enter my email` (free-text via "Other"),
    `Skip for this run`

On "Enter my email" — pass via single-quoted env var with `'\''`
escaping applied unconditionally:
```bash
MEGA_DIR="$(cd "${CLAUDE_SKILL_DIR}/../.." && pwd)"
MEGA_CODE_EMAIL_INPUT='<escaped-email>' \
  uv run --directory "$MEGA_DIR" python -m mega_code.client.ensure_user_email --set-from-env \
  && uv run --directory "$MEGA_DIR" python -m mega_code.client.ensure_user_email --apply-all-pending
```

If chain exits 1, `--set-from-env` rejected the input (stderr:
`invalid email format: '<rejected>'`); `&&` short-circuits the apply.
Surface stderr and re-prompt with a **retry-specific** message —
do NOT reuse the original prompt verbatim, or the user may loop on the
same bad value:
- **Question:** "The previous email `<rejected>` was rejected (must
  contain `@`, a domain, no whitespace). Enter a valid email, or skip."

On "Skip" — proceed without injection. **Do NOT write any sentinel
value to `MEGA_CODE_USER_EMAIL`** — any non-empty string would be
written verbatim into every subsequent skill's `metadata.creator`. See
Known Limitations.

**Non-interactive harnesses** (hook, CI, batch):
```bash
uv run --directory "$MEGA_DIR" python -m mega_code.client.ensure_user_email \
  --resolve-and-apply --non-interactive
```
Silent exit 0 on failure, matching `references/enhance-handoff.md` §55-60.
````

### 7. `mega_code/client/skill_enhance_helper.py` — B1 fix

**Line 605** (inside `_build_accepted_skill`, after
`metadata["author"] = DEFAULT_AUTHOR`, before `metadata.pop("roi", None)`):
```python
if "creator" not in metadata and "creator" in original_meta:
    metadata["creator"] = original_meta["creator"]
```
The whitelist preserves what's present but cannot invent what the LLM
draft drops.

### 8. Tests

**`tests/client/test_skill_utils.py`** (new):
- `email="a@b.com"` covers nested + fresh-build branches
- `email=""` → field omitted
- Idempotency: second call is byte-identical
- Regression: `normalize_skill_frontmatter` preserves `metadata.creator`

**`tests/client/test_ensure_user_email.py`** (new):
- empty pending dir → exit 0, no profile call
- pending + cache → exit 0, mutated
- pending + no cache + mocked profile → exit 0, cached, mutated
- pending + no cache + profile returns `None` → exit 2
- `--non-interactive` no email → exit 0, unchanged
- `--set-from-env` valid → 0; invalid → 1
- `--apply-all-pending` no cache → 1
- Monkeypatch `get_env_path`, `PENDING_SKILLS_DIR`

**`tests/client/test_skill_enhance_helper.py`** (update):
- B1: original has `creator`, draft drops it → final retains it

`test_pending.py`, `test_skill_security_audit.py` unchanged.

## Known Limitations

- **Server endpoint prerequisite.** `GET /profile` email field lives in
  private MEGA repo. Until shipped, users hit the prompt fallback once
  per machine.
- **Legacy flat-metadata skills** (top-level `author:`) hit
  `ensure_skill_frontmatter:549` and return unchanged. Only used by
  `test_skill_security_audit.py:36`. Deferred.
- **Already-installed skills** are not back-filled. Re-generate via
  wisdom-gen for attribution.
- **Multi-run pending overlap.** `--apply-all-pending` patches every
  pending SKILL.md, including stale leftovers. Email is correct (same
  user), but the gate does NOT install/archive them — user still walks
  the normal review.
- **No "never ask again" sentinel.** Setting `MEGA_CODE_USER_EMAIL=disabled`
  would write `disabled` verbatim into every skill's `metadata.creator`. Either set a real
  email or choose "Skip" each time. Sentinel handling deferred.
- **Exit code split.** `--resolve-and-apply` returns exit 2 for both
  network failure and "profile returned no email". May split later.
- **YAML byte-stability.** `render_frontmatter` uses
  `yaml.dump(..., sort_keys=False)` on a `deepcopy`; the injection guard
  prevents mutation on second call, so `updated == content` holds.
- **Email regex** intentionally permissive (whitespace-free).
- **`author` YAML quoting inconsistency** is pre-existing; not fixed.

## Follow-ups

- ~~Document in repo `CLAUDE.md` that `~/.local/share/mega-code/.env` is
  the stable credential store; `$MEGA_DIR/.env` is dev overlay only.~~
  Resolved: the dev overlay was retired. `$MEGA_CODE_DATA_DIR/.env` is the
  single source of truth; see `CLAUDE.md` §"Environment Loading".
- ~~Wrap the env-bootstrap + `create_client()` pattern (duplicated in
  `check_auth.py:30-32` and `ensure_user_email.py:_load_env_into_os`) in
  a shared helper.~~
  Resolved: `mega_code.client.cli.load_credentials()` is the shared helper.
  `create_client()` calls it at entry, so no other module needs to load.

## Verification

```bash
# Unit tests
uv run --directory . pytest \
  tests/client/test_skill_utils.py \
  tests/client/test_ensure_user_email.py \
  tests/client/test_skill_enhance_helper.py -v

# E2E dry run — clear cache first
grep -v '^MEGA_CODE_USER_EMAIL=' ~/.local/share/mega-code/.env > /tmp/env.tmp \
  && mv /tmp/env.tmp ~/.local/share/mega-code/.env

# (a) No pending → self-skip (exit 0)
uv run --directory . python -m mega_code.client.ensure_user_email --resolve-and-apply

# (b) Pending + no cache → exit 2
uv run --directory . python -m mega_code.client.ensure_user_email --resolve-and-apply

# (c) Provide via env, then apply
MEGA_CODE_EMAIL_INPUT='reeyan@example.com' \
  uv run --directory . python -m mega_code.client.ensure_user_email --set-from-env
uv run --directory . python -m mega_code.client.ensure_user_email --apply-all-pending

# (d) Non-interactive → silent skip (exit 0)
uv run --directory . python -m mega_code.client.ensure_user_email \
  --resolve-and-apply --non-interactive

# Pre-commit
uvx pre-commit run --files \
  mega_code/client/skill_utils.py \
  mega_code/client/api/protocol.py \
  mega_code/client/api/remote.py \
  mega_code/client/profile.py \
  mega_code/client/ensure_user_email.py \
  mega_code/client/skill_enhance_helper.py \
  tests/client/test_skill_utils.py \
  tests/client/test_ensure_user_email.py \
  skills/wisdom-gen/SKILL.md
```

**Manual E2E:**
1. Clean state (remove `MEGA_CODE_USER_EMAIL` from env).
2. `/mega-code:wisdom-gen` on a session that generates a skill →
   gate prompts → cached → SKILL.md gains `metadata.creator`.
3. `/mega-code:wisdom-gen` again → cached hit, silent injection.
4. `/mega-code:wisdom-gen` on a strategies-only session → gate self-skips.
5. `/mega-code:skill-enhance` on the skill from step 2 → confirm
   `metadata.creator` survives Phase 8 (exercises §7 fallback).
