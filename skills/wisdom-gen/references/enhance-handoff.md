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

Once the trigger check passes, use `AskUserQuestion` to ask **one**
question:

- **Question:** "Enhance the generated skill to better fit your needs?"
- **Options:** "Yes" / "No"

If **No**, end the wisdom-gen workflow with no extra output — this is a
**valid completion** of the MANDATORY handoff, not a skip. If **Yes**,
proceed to the Steps section below.

**Non-interactive environments.** You may only invoke the
default-to-No branch when there is an *observable* signal that the
environment cannot present an interactive prompt. Acceptable signals:
(a) you actually called `AskUserQuestion` and it returned an error
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
   - If multiple skills were generated, use `AskUserQuestion` to let the
     user pick one. Additionally offer an "All of them" option which
     enables **confirmation-gated sequential** mode (see step 3).

2. **Run skill-enhance (remote) for the chosen skill.** Follow
   `skills/skill-enhance/SKILL.md` with `SKILL_NAME` pre-set. The
   unified command defaults to the **remote** server flow; do **not**
   pass `--hitl` here. **Skip Phase 0 (argument dispatch — there is no
   `--hitl` flag) and skip Phase 2's interactive picker.** Begin from
   Phase 1 (setup & auth preflight), then in Phase 2 jump straight to
   the `resolve-skill --name "$SKILL_NAME"` call (the pre-supplied-name
   branch). `resolve-skill` repairs the canonical folder name and
   yields `ORIGINAL_SKILL_PATH` / `ORIGINAL_SCOPE`. From there, continue
   with Phases 3–5 in order (run module → exit-code branching →
   loop-or-exit).

3. **Confirmation-gated batch mode** (only if the user chose "All of
   them"): the gate fires *between* skills, after each skill has fully
   completed the remote run. The remote flow blocks until terminal
   status is reached and prompts the user once (install location and
   optional cross-scope cleanup), so by the time control returns the
   user has already interacted with that skill. The flow per skill is:

   ```
   Phase 1 (setup) → resolve-skill
     → Phase 3 (remote run, blocks until done)
     → Phase 4 (install-location prompt + install / terminal-no-install)
     → AskUserQuestion "Continue with next skill (<next-name>)?" → Yes / Stop
   ```

   On Stop, end cleanly with a summary of what was processed. Same-turn
   fully unattended sequential execution is **not** supported.

## Project directory handling

When wisdom-gen was invoked with `--project @name` (i.e., a project
other than `$PWD`), do **not** propagate that alias to the remote
skill-enhance flow. The remote skill resolves `<PROJECT_DIR>` from
`setup.sh` (captured as `pwd -P` before `uv run --directory` shifts
cwd); ensure the handoff runs from the user's actual working directory
so `resolve-skill` resolves relative to it rather than the project
alias.

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
- If the remote run reaches a terminal-no-install state (e.g.
  `failed`, `rejected`, `quarantined`, `enhancement_blocked`,
  `invariant_violation`) per Phase 4 of `skill-enhance`, surface the
  rejection detail (status, invariants, reason) to the user, end
  enhancement for that skill, and (in batch mode) continue with the
  next skill on user confirmation.
- If `skill-enhance` fails for any other reason on one skill in a batch,
  surface the error, ask whether to continue with the remaining skills,
  and do **not** roll back any prior install/archive actions.
