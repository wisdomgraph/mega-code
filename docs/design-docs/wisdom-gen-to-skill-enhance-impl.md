# Wisdom-Gen → Skill-Enhance In-Flow Handoff — Implementation

**Status:** Implemented · **Owner:** Reeyan Lee · **Updated:** 2026-04-06
**Design:** [`wisdom-gen-to-skill-enhance-flow.md`](./wisdom-gen-to-skill-enhance-flow.md)

## Scope

Generation-gated handoff from `wisdom-gen` to `skill-enhance` in the same
agent turn. Two files; no Python, no CLI, no changes to `skills/skill-enhance/`.

## Files

| File | Change |
|---|---|
| `skills/wisdom-gen/SKILL.md` | (a) Sub-workflow boundary anchor on step 3 of "Post-Pipeline Workflow" forces the agent back into wisdom-gen after review/install/archive. (b) New "MANDATORY — Enhance Generated Skills (post-review handoff)" section that delegates all decision logic to the reference. |
| `skills/wisdom-gen/references/enhance-handoff.md` | New. Owns trigger check, decision table, forbidden-reasoning self-correction table, binary prompt, observable-signal gating for non-interactive default-to-No, context-compression CLI fallback, per-skill steps, batch mode, project-dir handling, failure handling for Phase 1 (`resolve-skill`) and Phase 3 (security audit). |

## Key decisions

- **Generation-gated, not install-gated.** Trigger fires whenever a skill
  was generated. Install/archive status is irrelevant. Only valid skip:
  zero skill candidates. Forbidden-reasoning patterns are listed with
  positive reframings to harden against the prior failure mode.
- **Sub-workflow boundary anchor in `SKILL.md` step 3.** The
  `pending review` printout — assembled by
  `format_review_notification()` in `mega_code/client/pending.py`, which
  appends the workflow block read from
  `mega_code/client/config.yaml:review_notification.workflow_template` as
  the *final* section of the output — ends with a strong terminal marker.
  Without the anchor, the agent treated that marker as the end of
  wisdom-gen and never returned to run the handoff. The fix lives in
  `SKILL.md` (orchestration boundary), not in `pending.py` /
  `config.yaml` (generic sub-routine that other commands also reuse).
- **MANDATORY framing, distinct from "perform an enhancement".**
  "Run the handoff" = trigger check + binary prompt. A user `No` or a
  validly-detected non-interactive default-to-No counts as a *valid
  completion*, not a skip.
- **Observable-signal gating for non-interactive default-to-No.** Agent
  may only invoke the default-to-No branch when (a) `AskUserQuestion`
  actually returned a no-interactive-session error, or (b) the harness
  explicitly indicated non-interactivity. No guesswork. When in doubt,
  attempt the prompt first.
- **`SKILL.md` stays lean.** Decision tables, forbidden patterns, and
  per-skill steps live in the reference, mirroring the
  `skills/skill-enhance/` references pattern.
- **Trust in-turn context.** Skill list comes from the conversation
  context (the `pending review` printout already processed). Fallback
  on context compression: re-run the same `pending review` command
  rather than asking the user.
- **Pre-supplied-name branch of skill-enhance Phase 1.** Skip the
  interactive picker; call `resolve-skill --name "$SKILL_NAME"` directly,
  then continue Phases 2–8 in order.
- **Batch mode is confirmation-gated between skills**, not unattended
  (skill-enhance Phase 6 viewer requires per-skill user interaction).
- **`--project @name` forces `PROJECT_DIR_CANDIDATE="$PWD"`** so
  `resolve-skill` / `list-skills` resolve against the working directory.

## Not changed

- `skills/wisdom-gen/SKILL.md` frontmatter
- `skills/skill-enhance/` (entirely)
- `mega_code/client/config.yaml` review notification template
- Hooks, scripts, plugin configuration

## Manual verification

| # | Setup | Action | Expected |
|---|---|---|---|
| 0 | ≥1 skill generated | Archive all, install none | Handoff prompt **still fires** (regression test for the boundary anchor) |
| 1 | ≥1 skill generated | Install, answer **No** | Clean exit, characterized as "valid completion" |
| 2 | ≥1 skill generated | Install, answer **Yes** | Phase 1 picker skipped, `resolve-skill` runs immediately, Phases 2–8 complete |
| 3 | Skill archived (not installed) | Answer **Yes** | `resolve-skill` finds the archived skill; enhancement completes |
| 4 | 2+ skills | Pick "All of them" | Per-skill Phase 8 → "continue?" gate; **Stop** mid-batch exits with summary |
| 5 | Strategies/lessons only (zero skills) | — | Handoff skipped entirely |
| 6 | Pipeline partially failed (some skills, some failures) | — | Handoff fires for the successful subset |
| 7 | No TTY / harness signals non-interactive | — | Default-No path; no blocking prompt |
| 8 | Agent guesses non-interactive without signal | — | Must attempt prompt first; no preemptive skip |
| 9 | Phase 1 `resolve-skill` failure (e.g., archived folder missing) | — | Error surfaced verbatim; no name guessing; batch continues on confirmation |
| 10 | Phase 3 BLOCK/SKIP verdict | Answer **Yes** | Verdict surfaced; that skill ends; batch continues on confirmation |
| 11 | `--project @other` | Answer **Yes** | `PROJECT_DIR_CANDIDATE` forced to `$PWD` |

Tests 0, 1, 2, 3, 5, 6 are the minimum bar.

## Rollback

```bash
git checkout HEAD~1 -- skills/wisdom-gen/SKILL.md
git rm skills/wisdom-gen/references/enhance-handoff.md
```

## Out of scope

- `list-run-skills` helper CLI (rejected — flow doc § Source of truth for the skill list, § Resolved questions Q1)
- Any change to `skills/skill-enhance/SKILL.md`
- `--no-enhance-prompt` flag (rejected — non-interactive default-No covers automation)
- Persisting an "enhance later" queue across sessions
