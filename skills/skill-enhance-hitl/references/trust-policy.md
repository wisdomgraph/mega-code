# Trust Level & A/B Policy

## Interpreting ab_policy

- `full_access` — continue to Phase 4
- `warn_and_continue` — show findings to the user, warn that the skill has security red flags, then continue to Phase 4 only after the user confirms
- `skip_ab` — do NOT run A/B testing. Tell the user the skill has high-severity red flags from a semitrusted source. Stop the evaluation loop unless they explicitly want a static-only review

Trust labels describe provenance confidence only. They do not suppress red flags. A `trusted`
skill with findings can still require `warn_and_continue`.

## Semitrusted Skills

If the `trust_level` is `semitrusted`, always ask the user whether to proceed before running Phase 4, even if `ab_policy` is `full_access`.

For `semitrusted`, your message to the user should explicitly include:
- the trust level
- the trust explanation from `security-review.json`
- any red flags found
- a direct question asking whether to proceed with A/B testing

This rollout is static audit only. Do NOT claim that Phase 4 has enforced read-only,
no-network, or sandbox isolation for semitrusted skills unless the runner actually supports it.

## Ignore Patterns

`security_review.ignore_patterns` is only advisory. The audit may honor a limited safe subset
of known noisy patterns, but it must not suppress high-risk findings such as remote shell
execution, credential access, prompt override, or config persistence.

## Static-Only Review

If the user chooses a static-only review after `skip_ab`, summarize the findings from
`security-review.json`, note that dynamic A/B execution was skipped for safety, and stop.
