# Email Attribution Gate

This reference is loaded from `skills/wisdom-gen/SKILL.md` step 2
(Post-Pipeline Workflow). It owns the full email injection contract:
the initial resolve-and-apply, the interactive fallback prompt, retry
on invalid input, and the non-interactive skip path.

## Resolve and Apply

Run the gate after parsing `run_id` / `project_id`, before the review
sub-workflow:

```bash
MEGA_DIR="$(cd "${CLAUDE_SKILL_DIR}/../.." && pwd)"
uv run --directory "$MEGA_DIR" python -m mega_code.client.ensure_user_email --resolve-and-apply
EMAIL_STATUS=$?
echo "email_status=$EMAIL_STATUS"
```

## Exit-Code Handling

- `email_status=0` → cached, resolved, or no skills to patch. Continue.
- `email_status=2` → use `AskUserQuestion`:
  - **Question:** "Email attribution is not cached and the server profile
    didn't return one. Enter your email to tag the generated skill(s),
    or skip."
  - **Options:** `Enter my email` (free-text via "Other"),
    `Skip for this run`

## On "Enter my email"

Pass via single-quoted env var with `'\''` escaping applied
unconditionally:

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

## On "Skip"

Proceed without injection. **Do NOT write any sentinel value to
`MEGA_CODE_USER_EMAIL`** — any non-empty string would be written
verbatim into every subsequent skill's `metadata.creator`.

## Non-Interactive Harnesses (hook, CI, batch)

```bash
uv run --directory "$MEGA_DIR" python -m mega_code.client.ensure_user_email \
  --resolve-and-apply --non-interactive
```

Silent exit 0 on failure, matching `references/enhance-handoff.md` §55-60.
