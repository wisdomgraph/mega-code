---
description: Curate a step-by-step plan with installable skills before writing code — use when the user asks to plan, design, or scope an engineering approach before implementing it.
argument-hint: "<problem description>"
allowed-tools: Bash, Read, Write, Glob, AskUserQuestion
---

# Wisdom Curate

Retrieve relevant wisdoms from the knowledge graph, curate them into a
step-by-step workflow, install recommended skills, and optionally
execute the task.

## Setup

```bash
bash "${CLAUDE_SKILL_DIR}/scripts/setup.sh"
```

The script verifies auth, generates a `SESSION_ID`, and resolves dir
paths. If it exits non-zero (auth failure), show the output and stop.

It prints four lines on stdout: `MEGA_DIR`, `SESSION_ID`, `DATA_DIR`,
`SKILLS_DIR`. **Remember all four literal values** — each later bash
block, Read path, or user-facing message below uses them as placeholders
(`<MEGA_DIR>`, `<SESSION_ID>`, `<DATA_DIR>`, `<SKILLS_DIR>`) that you
MUST replace with the printed values before running. Bash tool calls
start fresh shells, so these are not available as shell variables — only
as literals you substitute at write time.

Read `references/architecture.md` (with the Read tool) when you need the
data directory layout, curation JSON shape, or the Python helper
functions used by Steps 5–7.

**Failure rule for every bash block below**: if any `uv run` command
exits non-zero, surface its stderr to the user and stop the workflow —
do not silently proceed to the next step.

## Step 1: Resolve Task

Capture `$ARGUMENTS` as `TASK_QUERY`:

```bash
TASK_QUERY="${ARGUMENTS:-}"
```

- If `TASK_QUERY` is non-empty, proceed to Step 2.
- If `TASK_QUERY` is empty, use `AskUserQuestion` to ask the user to
  describe the task they want planned. Store the response as `TASK_QUERY`,
  then proceed to Step 2.
- If the user cancels the `AskUserQuestion` or returns a blank response,
  **stop here**: do NOT proceed to Step 2 and do NOT run the Feedback
  section. Output nothing further. This is the only bail-out point
  before side effects begin.

## Step 2: Detect Project Context

Identify the project's tech stack using your own knowledge. Do NOT use a script.
Store the result in `TASK_CONTEXT` (separate from `TASK_QUERY`).

1. Use `Glob` to find manifest/config files in the project root:
   ```
   Glob("*") in the current working directory
   ```
   Look for any recognizable manifest — package.json, pyproject.toml, go.mod,
   Cargo.toml, pom.xml, build.gradle, Gemfile, composer.json, *.csproj, etc.
   This is not an exhaustive list — recognize any manifest you encounter.

2. `Read` the first manifest file found (limit to 50 lines). From its contents,
   determine the primary language, version (if visible), and key frameworks/libraries.

3. Compose `TASK_CONTEXT` as a descriptive sentence covering language,
   version (when visible), and key frameworks. See
   `references/task-context-examples.md` for example strings and the rules
   for handling partial or unrecognizable project types.

## Step 3: Curate Skills

Show a brief acknowledgment to the user:

> Analyzing task... Curating skills...

Compose `FORMATTED_QUERY` from the values held in conversation context:
- If `TASK_CONTEXT` is non-empty: `Task: <TASK_QUERY>, Task Context: <TASK_CONTEXT>`
- Otherwise: just `<TASK_QUERY>`

**Before running the bash block below, perform these substitutions:**
- Replace `<MEGA_DIR>` and `<SESSION_ID>` with the literal values printed
  by Setup.
- Replace `<the composed query string>` with the composed query verbatim.
  The `'WC_QUERY_EOF'` heredoc is quote-sealed, so single quotes, double
  quotes, `$`, and backticks in the query pass through unescaped — do
  not pre-escape them.
- Do NOT add any `echo` statements inside this block — the CLI prints
  JSON to stdout and extra output corrupts the parse.

```bash
FORMATTED_QUERY=$(cat << 'WC_QUERY_EOF'
<the composed query string>
WC_QUERY_EOF
)
uv run --directory "<MEGA_DIR>" mega-code wisdom-curate \
  "$FORMATTED_QUERY" \
  --session-id "<SESSION_ID>"
```

Parse the JSON output and store:
- `curation`: Markdown curation document (step-by-step workflow).
- `skills`: List of skill references, each with `name` and `path` (`url`
  is empty — the CLI already downloaded every skill to `<SKILLS_DIR>`).
- `wisdoms`: Underlying wisdom records.

The CLI downloads the full skill folder (SKILL.md, scripts/, references/,
etc.) for every recommended skill to `<SKILLS_DIR>/{name}/` as a side
effect of the curate call, so skill content is immediately available for
Step 7 without any further network access.

## Step 4: Present Summary + Permanent Install Decision

Parse the `curation` field and present a structured summary:

```
Workflow: <title>
Overview: <1-2 sentence summary>
Steps:
1. <step title> — Skill: <skill-name>
2. <step title> — Skill: <skill-name>
3. <step title> — (no skill reference)

N skills downloaded and ready.
```

Check which skills are already permanently installed in Claude's skill
directories:

```bash
ls ~/.claude/skills 2>/dev/null || echo "(none)"
ls .claude/skills 2>/dev/null || echo "(none)"
```

**Binary decision only** — never offer partial or selective installs.
Show all skills with their status, then use `AskUserQuestion`:

```
The following skills are ready for this workflow:

1. python-pro — [Already permanently installed]
2. fastapi — [Downloaded, not yet permanently installed]
3. d3-visualization — [Downloaded, not yet permanently installed]

Would you like to permanently install the 2 new skills for future use? (Yes / Skip)
```

- **Yes** → permanently install ALL not-yet-installed skills (Step 5).
- **Skip** or any other response → skip permanent install, go to Step 6.
  Skills are still available in `<SKILLS_DIR>` for this workflow.
- If all skills are already permanently installed, inform the user and go to Step 6.

## Step 5: Permanently Install Skills

Install the whole skill folder (SKILL.md and all supporting files) from
the local cache to Claude Code's permanent skills directory.

Substitute `<MEGA_DIR>` and list the not-yet-permanently-installed skill
names as positional arguments. Add `--scope project` to install into the
current project's `.claude/skills/` instead of the default user scope
(`~/.claude/skills/`).

```bash
uv run --directory "<MEGA_DIR>" mega-code skill-install \
  <skill-name-1> <skill-name-2> ...
```

The command exits non-zero if a skill name is not found in the local
cache (e.g. it was not returned by Step 3). In that case, report the
failure to the user and stop before Step 6.

Report per-skill status:
```
fastapi: installed
d3-visualization: installed
```

## Step 6: Save Curation + Run Decision

Pipe the full curate result JSON from Step 3 into the save script over
stdin.

Substitute `<MEGA_DIR>` and `<SESSION_ID>`. Replace
`<full curate result JSON from Step 3>` with the entire JSON object
returned by `mega-code wisdom-curate` in Step 3 (include `session_id`,
`query`, `curation`, `skills`, `wisdoms`, etc.). The session id is
passed inline as `WC_SESSION_ID` so the script can assert that the
server echoed the expected id.

```bash
WC_SESSION_ID="<SESSION_ID>" uv run --directory "<MEGA_DIR>" python \
  "${CLAUDE_SKILL_DIR}/scripts/save_curation.py" << 'CURATE_EOF'
<full curate result JSON from Step 3>
CURATE_EOF
```

Use `AskUserQuestion` to present the run decision:

- If the query is specific (actionable task), offer both options:
  ```
  Your task is ready to run. Would you like to:
  - Run now — execute the workflow with the installed skills
  - Later — end here, you can use the skills manually later
  ```
- If the query is vague, explain why and offer only **Later**.
- If the user cancels the `AskUserQuestion` or returns a blank response,
  treat it as **Later** and proceed to Step 8. Curation is already saved
  at this point, so silently dropping the run is safe; re-prompting would
  just be friction.

## Step 7: Run Now

If the user chooses **Run now**:

Update curation status (substitute `<MEGA_DIR>` and `<SESSION_ID>`):

```bash
uv run --directory "<MEGA_DIR>" python \
  "${CLAUDE_SKILL_DIR}/scripts/update_curation_status.py" "<SESSION_ID>" running
```

Follow the curation workflow. For each step:

1. Read the installed skill to get domain knowledge.
2. Adapt the step to the user's specific context.
3. Execute the step.

### Reading installed skills

When a step references a skill, read it from the installed skills directory.
Use the literal `DATA_DIR` value printed in Setup (e.g. `/Users/you/.mega-code/data`)
as the prefix — the `Read` tool requires a real absolute path.

```
Read("<DATA_DIR>/skills/<skill-name>/SKILL.md")
```

For specific sections referenced in the curation:
```
Reference: `python-pro/SKILL.md#Type Hints L42-78`
→ Read("<DATA_DIR>/skills/python-pro/SKILL.md", offset=42, limit=37)
```

After the workflow completes, mark as completed:

```bash
uv run --directory "<MEGA_DIR>" python \
  "${CLAUDE_SKILL_DIR}/scripts/update_curation_status.py" "<SESSION_ID>" completed
```

Proceed to Feedback.

## Step 8: Later

If the user chooses **Later**:

Show a brief summary (substitute the literal `DATA_DIR` and `SESSION_ID`
values printed in Setup):
```
Skills installed: python-pro, fastapi
Curation saved to: <DATA_DIR>/curations/pending/<SESSION_ID>.json

You can ask me to run this workflow later **in this same conversation** —
just say "run it now", "execute the curation", or similar, and I will
resume from where we left off (with mandatory feedback after execution).
After this conversation ends the curation file remains saved, but
automatic resumption is not yet supported.
```

End the skill here. **Do NOT proceed to Feedback now** — feedback exists
to evaluate install + run results, and the workflow was not executed.

### In-session resume rule (for Claude)

If the user later asks **in this same conversation** to run the saved
curation (phrases like "run it", "execute it", "let's do it now",
"continue the curation"), do NOT re-invoke `/mega-code:wisdom-curate`.
Instead, **re-enter Step 7 (Run Now) with the same `<SESSION_ID>`,
`<DATA_DIR>`, `<MEGA_DIR>` literals** still held in conversation context
from this Setup. The curation document, installed skills, and session id
are all still valid. After Step 7 completes, the Feedback section
becomes **mandatory** — collect and submit the 7-field feedback exactly
as if Run Now had been chosen at Step 6.

## Feedback (MANDATORY after Step 7 — Run Now or in-session resume)

**You MUST complete this step whenever Step 7 actually executed**, whether
the user chose Run Now at Step 6 or resumed a Later-saved curation later
in the same conversation. The Step 8 (Later) path before resumption and
the Step 1 cancel path both end the skill without running Feedback — no
execution result exists to evaluate in those branches.

Use the same `<SESSION_ID>` literal from Setup.

### Evidence-based execution

The curation document contains **Evidence annotations** for each wisdom:
- **Evidence: Strong** (N positive, M negative) — apply directly with confidence
- **Evidence: Weak** (N positive, M negative) — verify before applying
- **Evidence: Limited** (N sessions) — treat as suggestion, validate independently
- **Evidence: None** — no prior feedback, use your own judgment

The portfolio-level blockquote (> **Evidence: Strong/Mixed/Limited**) indicates
overall workflow reliability. Adjust your execution confidence accordingly:
- Strong → follow steps closely
- Mixed → follow structure but validate weak-evidence steps
- Limited → treat as starting point, verify each step

### Evaluation rigor

Rate contribution by verified effect, not apparent relevance or effort:
- Assign `direct` only if you can identify a specific observed result caused or materially influenced by this contribution.
- Assign `none` if no measurable outcome changed, even if the contribution appears relevant.
- Quantify lift, savings, or improvement only when supported by observed evidence.
- Use the full rating scale without hesitation. Low scores are correct when impact is weak, evidence is thin, or attribution is unclear. If the impact is good, high scores are correct.

### Feedback content (per-step, then per-wisdom)

Write per-wisdom feedback. For each wisdom in the workflow, cover:

1. **Contribution**: `direct` (clearly helpful), `ambient` (partially helpful), or `none`
2. **Accuracy impact**: quality improvement estimate (-1.0 to 1.0, negative = harmful)
3. **Efficiency impact**: time/token savings estimate (-1.0 to 1.0, negative = overhead)
4. **Reason**: why it contributed or not
5. **Step rating**: how well the step performed (0-5, 0 = not applicable)
6. **Recommendation**: improvement suggestion (if any)
7. **Update**: factual correction for outdated content (if any)

### Feedback text template

Compose the feedback text using this template. Repeat the
`Step N (...)` block once per step in the curated workflow.

```
Step 1 (<step name>): <rating>/5
- <wisdom>: <direct|ambient|none>. Lift: <-1.0 to 1.0>, savings: <-1.0 to 1.0>. <reason>.
  Recommendation: <improvement suggestion>
  Update: <factual correction>

Step 2 (<step name>): <rating>/5
- <wisdom>: <direct|ambient|none>. Lift: <-1.0 to 1.0>, savings: <-1.0 to 1.0>. <reason>.
  Recommendation: <improvement suggestion>
  Update: <factual correction>
```

### Submit feedback

**Before running the bash block below, perform these substitutions:**
- Replace `<MEGA_DIR>` and `<SESSION_ID>` with the literal values from Setup.
- Replace `<paste the composed feedback text here>` with the composed
  feedback text verbatim. The `'WC_FEEDBACK_EOF'` heredoc is quote-sealed,
  so no escaping is needed.

```bash
FEEDBACK_TEXT=$(cat << 'WC_FEEDBACK_EOF'
<paste the composed feedback text here>
WC_FEEDBACK_EOF
)
uv run --directory "<MEGA_DIR>" mega-code wisdom-feedback \
  --session-id "<SESSION_ID>" \
  --feedback-text "$FEEDBACK_TEXT"
```
