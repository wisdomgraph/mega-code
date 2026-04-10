# Enhance-Handoff Procedure

This reference is loaded from `skills/wisdom-gen/SKILL.md` after the
review sub-workflow returns. It owns the full handoff contract: the
trigger check, the binary Yes/No prompt, and the per-skill enhancement
flow. `SKILL.md` intentionally delegates all decision logic here.

## Trigger check (run this FIRST — do not skip)

The trigger is **generation**, not installation. Past runs have failed
because the agent gated on "were any skills installed?" — that is
**wrong**. Use this decision table:

| Skills generated this run?       | Installed? | Archived? | Run the handoff? |
|----------------------------------|------------|-----------|------------------|
| Yes                              | Yes        | —         | **YES**          |
| Yes                              | No         | Yes       | **YES**          |
| Yes                              | No         | No        | **YES**          |
| Partial (some generated, some failed) | any   | any       | **YES** — handoff applies to the successful subset |
| No (strategies / lessons only)   | —          | —         | No — end cleanly |

**Forbidden reasoning patterns** — when you catch yourself thinking
the line on the left, replace it with the line on the right and run
the handoff:

| ❌ Forbidden                                            | ✅ Correct reframing                                                            |
|---------------------------------------------------------|---------------------------------------------------------------------------------|
| "No skills were installed, so skipping enhance."        | "Installation is irrelevant — generated skills are eligible. Run the handoff." |
| "Nothing to enhance because everything was archived."   | "Archived skills are still generated skills. Run the handoff."                  |
| "User skipped installation, so ending workflow."        | "User skipping install ≠ user skipping enhance. Run the handoff and ask."       |
| "Pipeline had errors so I'll skip the handoff."         | "Partial generation still triggers the handoff for the successful subset."      |

An archived skill is still a generated skill and is still eligible for
enhancement. The only valid skip is a run that produced **zero skill
candidates** in the `pending review` printout (e.g. strategies or
lessons only). If unsure, default to running the handoff.

## Binary prompt

Once the trigger check passes, use `request_user_input` to ask **one**
question:

- **Question:** "Would you like to inject your secret sauce into the
  generated skill(s) and enhance their performance? Try skill-enhance."
- **Options:** "Yes" / "No"

If **No**, end the wisdom-gen workflow with no extra output — this is a
**valid completion** of the MANDATORY handoff, not a skip. If **Yes**,
proceed to the Steps section below.

**Non-interactive environments.** You may only invoke the
default-to-No branch when there is an *observable* signal that the
environment cannot present an interactive prompt. Acceptable signals:
(a) you actually called `request_user_input` and it returned an error
indicating no interactive session, or (b) the harness has explicitly
told you the session is non-interactive (e.g. via an environment hint
or system reminder). Do **not** classify the environment as
non-interactive based on guesswork, vibes, or "this looks like a
script" — when in doubt, attempt the prompt first. Once a valid signal
is observed, default to **No** and end cleanly: the trigger check ran,
the prompt could not be presented, and the safe default applied. This
is a valid completion of the handoff, not a skip.

## Source of truth for the skill list

Use the list of skill names you already have in conversation context —
i.e., the skills produced by this pipeline run as shown in the
`pending review` printout you just processed in the Post-Pipeline
Workflow. You also know which were installed vs archived from the
actions you just took. Do not re-derive this list via a separate CLI;
you are still in the same agent turn and already have the authoritative
information.

**Fallback if context is unclear or has been compressed mid-turn:**
re-derive the list deterministically by re-running the pending review
command from the Post-Pipeline Workflow:

```bash
uv run --directory "$MEGA_DIR" python -m mega_code.client.pending review \
  --run-id <RUN_ID> --project-id <PROJECT_ID>
```

Only ask the user to confirm skill names as a last resort, after both
in-context recall and the CLI re-derivation above have failed.

## Steps

1. **Pick a skill (or all of them).**
   - If exactly one skill was generated, use it directly as `SKILL_NAME`.
   - If multiple skills were generated, use `request_user_input` to let the
     user pick one. Additionally offer an "All of them" option which
     enables **confirmation-gated sequential** mode (see step 3).

2. **Run skill-enhance for the chosen skill.** Follow
   `skills/skill-enhance/SKILL.md` with `SKILL_NAME` pre-set. **Skip
   Phase 1's interactive picker entirely** and begin by running
   `resolve-skill --name "$SKILL_NAME"` directly. This is the
   pre-supplied-name branch of Phase 1, documented in
   `skills/skill-enhance/SKILL.md` under the rule *"If a skill name was
   provided as an argument, use it directly."* `resolve-skill` repairs
   the canonical folder name and yields `SKILL_PATH`. From there,
   continue with Phases 2–8 in order.

3. **Confirmation-gated batch mode** (only if the user chose "All of
   them"): the gate fires *between* skills, after each skill has fully
   completed Phase 8. Each skill independently goes through skill-enhance
   Phase 6, which opens an HTML viewer in the browser and blocks until
   the user submits feedback — so the user will already have interacted
   with each skill before reaching the "Continue with the next skill?"
   prompt. The flow per skill is:

   ```
   resolve-skill
     → Phase 2..6 (viewer)
     → Phase 7..8
     → request_user_input "Continue with next skill (<next-name>)?" → Yes / Stop
   ```

   On Stop, end cleanly with a summary of what was processed. Same-turn
   fully unattended sequential execution is **not** supported.

## Project directory handling

When wisdom-gen was invoked with `--project @name` (i.e., a project
other than `$PWD`), do **not** propagate that to skill-enhance's
`PROJECT_DIR_ARG` detection. Force `PROJECT_DIR_CANDIDATE="$PWD"` in
the inlined skill-enhance phases so that helper paths like `list-skills`
and `resolve-skill` resolve relative to the user's actual working
directory rather than the remote project alias.

## Failure handling

- If `resolve-skill` (Phase 1) fails — e.g., the archived run is scoped
  to a different project_id and isn't picked up by the current scan,
  the on-disk archive path is missing or unreadable, the canonical-name
  repair errors, or the name cannot be matched — surface the error
  verbatim, end enhancement for that skill, and (in batch mode)
  continue with the next skill on user confirmation. Do **not** retry
  with a guessed name; ask the user to confirm the intended skill name
  first. (Note: a normally-archived skill is *expected* to be found —
  `resolve_skill` scans archived runs via `_scan_archived_skills`. This
  failure path covers edge cases, not the normal archive flow.)
- If `skill-enhance` Phase 3 (security audit) returns a BLOCK / SKIP
  verdict, surface the verdict to the user, end enhancement for that
  skill, and (in batch mode) continue with the next skill on user
  confirmation.
- If `skill-enhance` fails for any other reason on one skill in a batch,
  surface the error, ask whether to continue with the remaining skills,
  and do **not** roll back any prior install/archive actions.
