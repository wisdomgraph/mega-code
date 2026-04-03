# Trust Level & A/B Policy

## Interpreting ab_policy

- `full_access` — continue to Phase 4
- `warn_and_continue` — show findings to the user, warn that the skill has security red flags, then present options using `request_user_input` (see below)
- `skip_ab` — do NOT run A/B testing. Tell the user the skill has high-severity red flags from a semitrusted source, then present options using `request_user_input` (see below)

Trust labels describe provenance confidence only. They do not suppress red flags. A `trusted`
skill with findings can still require `warn_and_continue`.

## Semitrusted Skills

If the `trust_level` is `semitrusted`, always ask the user whether to proceed before running Phase 4, even if `ab_policy` is `full_access`.

For `semitrusted`, your message to the user should explicitly include:
- the trust level
- the trust explanation from `security-review.json`
- any red flags found

Then present options using `request_user_input` (see below).

This rollout is static audit only. Do NOT claim that Phase 4 has enforced read-only,
no-network, or sandbox isolation for semitrusted skills unless the runner actually supports it.

## Ignore Patterns

`security_review.ignore_patterns` is only advisory. The audit may honor a limited safe subset
of known noisy patterns, but it must not suppress high-risk findings such as remote shell
execution, credential access, prompt override, or config persistence.

## Decision Prompts

### warn_and_continue / semitrusted prompt

Use `request_user_input` to present these options:

**Question:** "This skill has security findings. How would you like to proceed?"

**Options:**
1. "Continue to A/B testing"
2. "Static review only"
3. "Stop"

**Option 1 — Continue to A/B testing:**
Proceed to Phase 4 as normal.

**Option 2 — Static review only:**
Summarize the findings from `security-review.json`, note that dynamic A/B execution
was skipped for safety, and stop.

**Option 3 — Stop:**
Return immediately. Do not print anything or ask further questions.

### skip_ab prompt

Use `request_user_input` to present these options:

**Question:** "This skill has high-severity red flags and A/B testing is blocked. How would you like to proceed?"

**Options:**
1. "Static review only"
2. "Stop"

**Option 1 — Static review only:**
Summarize the findings from `security-review.json`, note that dynamic A/B execution
was skipped for safety, and stop.

**Option 2 — Stop:**
Return immediately. Do not print anything or ask further questions.
