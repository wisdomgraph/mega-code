---
name: mega-code-wisdom-curate
description: "Curate a wisdom-backed workflow — retrieves relevant wisdoms from the PCR Wisdom Graph, curates skills, offers installation, and optionally runs the task."
argument-hint: "<problem description>"
allowed-tools: Bash, Read, Write, Glob, AskUserQuestion
---

# Wisdom Curate — PCR Skill Network

Retrieve relevant wisdoms from the knowledge graph, curate them into a
step-by-step workflow, install recommended skills, and optionally
execute the task.

## Setup

```bash
MEGA_DIR="$(cat ~/.local/share/mega-code/pkg-breadcrumb 2>/dev/null)"
if [ -z "$MEGA_DIR" ] || [ ! -f "$MEGA_DIR/pyproject.toml" ]; then
  MEGA_DIR="$HOME/.local/share/mega-code/pkg"
  if [ ! -f "$MEGA_DIR/pyproject.toml" ]; then
    rm -rf "$MEGA_DIR"
    git clone --depth 1 --branch codex "${MEGA_CODE_REPO_URL:-https://github.com/wisdomgraph/mega-code.git}" "$MEGA_DIR"
  fi
  bash "$MEGA_DIR/scripts/codex-bootstrap.sh" "$MEGA_DIR"
fi
export MEGA_CODE_DATA_DIR="$HOME/.local/share/mega-code"
export UV_CACHE_DIR="${UV_CACHE_DIR:-$MEGA_DIR/.uv-cache}"
uv run --directory "$MEGA_DIR" python -m mega_code.client.check_auth
```

If the auth check fails (non-zero exit), show the output to the user and stop.

### Data Directory

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

Each curation JSON contains: session_id, query, curation (markdown workflow),
token_count, cost_usd, created_at, status.

Key Python functions for skill/curation access:
- `mega_code.client.dirs.data_dir()` → data root path
- `mega_code.client.skill_installer.skills_dir()` → skills directory
- `mega_code.client.skill_installer.install_skills(skills)` → download + extract
- `mega_code.client.curation_store.save_curation(result)` → save to pending/
- `mega_code.client.curation_store.get_curation(session_id)` → load by ID
- `mega_code.client.curation_store.list_curations(status)` → list by status
- `mega_code.client.curation_store.update_curation_status(id, status)` → transition

## Step 1: Validate Input

If `$ARGUMENTS` is empty or blank, use `AskUserQuestion` to ask the user
what task or skill they need help with. Do NOT proceed until the user
provides a non-empty task description. Store the answer as `TASK_QUERY`.

If the user cancels or returns a blank response, **stop here** — do NOT proceed
to Step 2. This is the only bail-out point before side effects begin.

If `$ARGUMENTS` is provided, set `TASK_QUERY` to `$ARGUMENTS` and proceed.

## Step 2: Generate Session ID

```bash
SESSION_ID="${CODEX_THREAD_ID:-$(uuidgen 2>/dev/null || python3 -c 'import uuid;print(uuid.uuid4())')}"
echo "SESSION_ID=$SESSION_ID"
```

Remember this SESSION_ID — you will need it for feedback.

## Step 2b: Detect Project Context

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

**IMPORTANT**: Do NOT add any `echo` statements to this command.
The CLI prints JSON to stdout — any extra output will corrupt the JSON.

If `TASK_CONTEXT` is not empty, format the query as:
```
Task: <TASK_QUERY>, Task Context: <TASK_CONTEXT>
```

If `TASK_CONTEXT` is empty, use `TASK_QUERY` as-is.

```bash
uv run --directory "$MEGA_DIR" mega-code wisdom-curate \
  "$FORMATTED_QUERY" \
  --session-id "$SESSION_ID"
```

Where `FORMATTED_QUERY` is the value you composed above.

Parse the JSON output and store:
- `curation`: Markdown curation document (step-by-step workflow).
- `skills`: List of skill references, each with `name`, `path`, `url`.
- `wisdoms`: Underlying wisdom records.

## Step 4: Present Summary + Install Decision

Parse the `curation` field and present a structured summary:

```
Workflow: <title>
Overview: <1-2 sentence summary>
Steps:
1. <step title> — Skill: <skill-name>
2. <step title> — Skill: <skill-name>
3. <step title> — (no skill reference)

N skills recommended for this workflow.
```

Check which skills are already installed:

```bash
uv run --directory "$MEGA_DIR" mega-code skill-list
```

**IMPORTANT — BINARY INSTALL DECISION**: There are EXACTLY two outcomes.
- "Yes" → install ALL not-yet-installed skills (proceed to Step 5)
- "Skip" → install NOTHING (skip to Step 6)

Do NOT offer partial, selective, or subset installation in any form.

Show all skills with their status, then use `AskUserQuestion` to ask:

```
The following skills are recommended for this workflow:

1. python-pro — [Already installed]
2. fastapi — [Not installed]
3. d3-visualization — [Not installed]

Would you like to install the 2 new skills? (Yes / Skip)
```

- If **Yes**: install all not-yet-installed skills (proceed to Step 5).
- If **Skip**: skip to Step 6 (no installation).
- If the user responds with anything other than Yes: treat as Skip.
- If all skills are already installed: inform the user and skip to Step 6.

## Step 5: Install Skills

For each not-yet-installed skill, download and install from its presigned URL:

Write the JSON array of not-yet-installed skills from Step 3 to a temp file,
then pass the file path to the installer:

```bash
SKILLS_JSON_FILE="$(mktemp)"
cat > "$SKILLS_JSON_FILE" << 'SKILLS_EOF'
<JSON array of not-yet-installed skills from Step 3>
SKILLS_EOF

uv run --directory "$MEGA_DIR" mega-code skill-install \
  --skills-json "$SKILLS_JSON_FILE" \
  --session-id "$SESSION_ID"
```

Skills are extracted to `{data_dir()}/skills/{skill-name}/`.

Report per-skill status:
```
Installed: fastapi ✓
Installed: d3-visualization ✓
Skipped: python-pro (already installed)
```

## Step 6: Save Curation + Run Decision

Save the curate result for potential later resumption:

Write the full curate result JSON from Step 3 to a temp file,
then pass the file path:

```bash
CURATE_JSON_FILE="$(mktemp)"
cat > "$CURATE_JSON_FILE" << 'CURATE_EOF'
<full curate result JSON from Step 3>
CURATE_EOF

uv run --directory "$MEGA_DIR" mega-code curation-save \
  --result-json "$CURATE_JSON_FILE" \
  --session-id "$SESSION_ID"
```

Use `AskUserQuestion` to present the run decision:

- If the query is specific (actionable task), offer both options:
  ```
  Your task is ready to run. Would you like to:
  - Run now — execute the workflow with the installed skills
  - Later — end here, you can use the skills manually later
  ```
- If the query is vague, explain why and offer only **Later**.
- If the user cancels or returns a blank response, treat as **Later** and proceed to Step 8.
  Curation is already saved at this point; silently dropping the run avoids friction.

## Step 7: Run Now

If the user chooses **Run now**:

Update curation status:

```bash
uv run --directory "$MEGA_DIR" mega-code curation-status "$SESSION_ID" running
```

Follow the curation workflow. For each step:

1. Read the installed skill to get domain knowledge.
2. Adapt the step to the user's specific context.
3. Execute the step.

### Reading installed skills

When a step references a skill, read it from the installed skills directory:

```
Read("{data_dir()}/skills/{skill-name}/SKILL.md")
```

For specific sections referenced in the curation:
```
Reference: `python-pro/SKILL.md#Type Hints L42-78`
→ Read("{data_dir()}/skills/python-pro/SKILL.md", offset=42, limit=37)
```

After the workflow completes, mark as completed:

```bash
uv run --directory "$MEGA_DIR" mega-code curation-status "$SESSION_ID" completed
```

Proceed to Feedback.

## Step 8: Later

If the user chooses **Later**:

Show a brief summary:
```
Skills installed: python-pro, fastapi
Curation saved to: {data_dir()}/curations/pending/{SESSION_ID}.json
```

Then tell the user:

> "This workflow can be executed later in the same session. Type something like 'Run Now' or 'Execute Curate'."

**End the skill here. Do NOT proceed to Feedback** — Feedback evaluates execution results,
and no execution has occurred. Re-prompting for feedback now would be friction with no signal value.

### In-session resume rule

If the user later asks **in this same conversation** to run the saved curation (phrases like
"run it", "execute it", "let's do it now", "continue the curation"), do NOT re-invoke
`$mega-code:wisdom-curate`. Instead, **re-enter Step 7 (Run Now) with the same `SESSION_ID`**
still held in conversation context. The curation document, installed skills, and session id are
all still valid. After Step 7 completes, the Feedback section becomes **mandatory** — collect
and submit the full 7-field feedback exactly as if Run Now had been chosen at Step 6.

## Feedback (MANDATORY after Step 7 — Run Now or in-session resume)

**You MUST complete this step whenever Step 7 actually executed**, whether the user chose
Run Now at Step 6 or resumed a Later-saved curation later in the same conversation.
The Step 8 (Later) path before resumption ends the skill without feedback — no execution
result exists to evaluate in that branch.

Use the same `SESSION_ID` from Step 2.

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

```bash
uv run --directory "$MEGA_DIR" mega-code wisdom-feedback \
  --session-id "$SESSION_ID" \
  --feedback-text "<paste the composed feedback text here>"
```
