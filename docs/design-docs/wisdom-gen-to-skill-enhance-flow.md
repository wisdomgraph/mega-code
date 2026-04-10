# Wisdom-Gen → Skill-Enhance In-Flow Handoff

**Status:** Implemented · **Owner:** Reeyan Lee · **Updated:** 2026-04-06
**Skills:** `skills/wisdom-gen/SKILL.md`, `skills/skill-enhance/SKILL.md`

## Problem

After `/mega-code:wisdom-gen` finishes its review/install/archive workflow,
running `skill-enhance` against just-generated skills requires a separate
turn and manual context re-establishment. This friction breaks the
*generate → evaluate → enhance* loop.

## Goal

Add an in-flow handoff at the end of `wisdom-gen` that:

1. Detects whether any skills were *generated* this run (not installed).
2. Asks one binary Yes/No to enhance them now.
3. On Yes, runs `skill-enhance` against the chosen skill(s) in the same turn.
4. Works identically whether skills were installed or archived.

## Non-goals

- Restructuring the existing review/install/archive workflow
- Forcing enhancement (opt-in, default No)
- Changing `skill-enhance` itself
- Persisting an "enhance later" queue across sessions

## Existing surfaces (relevant)

| Component | Path | Note |
|---|---|---|
| `wisdom-gen` skill | `skills/wisdom-gen/SKILL.md` | Previously ended at `pending review` |
| `skill-enhance` skill | `skills/skill-enhance/SKILL.md` | Phase 1 already accepts a pre-supplied skill name |
| `pending review` CLI | `mega_code/client/pending.py` | Prints generated skill names + workflow instructions |
| `resolve_skill` | `mega_code/client/skill_enhance_helper.py` | Resolves a name across pending / installed / archived — no install/archive branching needed in the handoff |

## Flow

```
/mega-code:wisdom-gen
  ├── Setup / auth
  ├── Run pipeline → pending skills
  ├── Post-Pipeline Workflow (MANDATORY)
  │     └── pending review → install / archive
  └── MANDATORY — Enhance Generated Skills (post-review handoff)
        ├── Trigger check (decision table — generation-gated)
        ├── If skip-eligible (zero skills) → exit
        ├── AskUserQuestion Yes/No
        │     ├── No → valid completion, exit
        │     └── non-interactive (signal observed) → default No, exit
        └── Yes
              ├── 1 skill → use it directly
              ├── N skills → picker ("one" | "All of them")
              └── For each chosen skill:
                    → skill-enhance Phase 1 (pre-supplied-name branch):
                          resolve-skill --name "$SKILL_NAME"
                    → skill-enhance Phases 2..8
                    → (batch, after Phase 8) AskUserQuestion "Continue with next?"
```

## Source of truth for the skill list

The handoff runs **inside the same agent turn** that produced and reviewed
the skills. The agent already has:

1. Pipeline output JSON (`run_id`, `project_id`)
2. The `pending review` printout (skill names, descriptions, paths)
3. Its own install/archive actions

**Decision:** conversation context is the source of truth. No new helper CLI.

**Fallback** if context is unclear or has been compressed mid-turn:
re-run the same `pending review --run-id <id> --project-id <id>` command
the agent already used in the Post-Pipeline Workflow. Asking the user is
the last resort.

A `list-run-skills` helper was considered and rejected — it would solve no
problem the agent's context does not, and would itself have to fall back to
a pending-dir scan in the pre-archive window.

## Hardening (added during code review)

- **Decision table** with explicit rows for installed / archived / partial
  generation. The "partial" row directs the handoff at the successful subset.
  The full 5-row trigger-check table lives in
  [`skills/wisdom-gen/references/enhance-handoff.md` § Trigger check](../../skills/wisdom-gen/references/enhance-handoff.md);
  this doc intentionally does not duplicate it to keep a single source of truth.
- **Forbidden-reasoning self-correction table** maps each known
  rationalization (e.g. "nothing installed, so skipping") to a positive
  reframing the agent should substitute.
- **Observable-signal gating** for the non-interactive default-to-No branch.
  Agent may only invoke it when (a) `AskUserQuestion` returned a
  no-interactive-session error, or (b) the harness explicitly indicated
  non-interactivity. No guesswork. When in doubt, attempt the prompt first.
- **Phase 1 (`resolve-skill`) failure handling** added to the failure
  section: surface error verbatim, do not retry with a guessed name, batch
  continues on confirmation.
- **"Run the handoff" ≠ "perform an enhancement".** A `No` answer or a
  validly-detected non-interactive default-to-No is a *valid completion*.

## Edge cases

| Case | Behavior |
|---|---|
| Zero skills (strategies/lessons only) | Skip the prompt entirely |
| All skills archived | `resolve_skill` scans archived runs (via `_scan_archived_skills`) and normally finds them; enhancement proceeds. If the archived run is scoped to a different project_id or its on-disk path is missing, falls through to the Phase 1 failure branch below |
| Multiple skills | Picker with confirmation-gated "All of them" mode |
| Pipeline partially failed | Handoff applies to the successful subset |
| Phase 3 security audit BLOCK/SKIP | Surface verdict, end that skill, batch continues on confirmation |
| Phase 1 resolve-skill failure | Surface error, no name guessing, batch continues on confirmation |
| Non-interactive (signal observed) | Default No, exit cleanly |
| `--include-claude` skills (Claude-authored) | Phase 1 accepts any name; handoff still works |

## Security

- Skill names flow into `resolve-skill --name "$SKILL_NAME"` (lookup, not
  exec) — no shell injection surface.
- `skill-enhance` runs its own Phase 3 security audit before any A/B run;
  malicious skills remain gated.
- Handoff bypasses no existing trust policy.

## Resolved questions

- **Q1 — Source of truth:** conversation context. Helper CLI rejected.
- **Q2 — Multi-skill batch model:** confirmation-gated sequential. Phase 6
  HTML viewer makes unattended sequential infeasible.
- **Q3 — `--no-enhance-prompt` flag:** rejected. Non-interactive default-No
  already covers automation.
