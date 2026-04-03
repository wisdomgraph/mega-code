---
name: mega-code-skill-enhance
description: "Evaluate and enhance a mega-code skill using LLM-as-judge A/B testing with an iterative improvement loop."
argument-hint: "<skill-name>"
allowed-tools: Bash, Read, Write, Edit
---

Run on-demand A/B evaluation of a mega-code skill, review results in an HTML viewer,
collect user feedback, and produce an enhanced version of the skill. The host agent (you)
handles test generation, grading, and enhancement; isolated A/B completions run via subprocess.

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
set -a && . "$MEGA_CODE_DATA_DIR/.env" 2>/dev/null && set +a
uv run --directory "$MEGA_DIR" python -m mega_code.client.check_auth
```

If the auth check fails (non-zero exit), show the output to the user and stop.

If any Python module is missing (e.g. `ModuleNotFoundError`), run `$mega-code-update` first to sync the local package.

**Detect which agent you are** — set `EVAL_AGENT` so the A/B runner uses the same agent:
- If you are Claude Code, set `EVAL_AGENT=claude`
- If you are Codex, set `EVAL_AGENT=codex`
- If unsure, omit it (auto-detection will be used)

All commands below assume `MEGA_DIR` is set.

## Phase 1 — Skill Selection & Workspace Setup

If a skill name was provided as an argument, use it directly regardless of authorship. The list-skills command only shows mega-code authored skills, but direct skill name arguments are not restricted by author.

If no skill name was provided, list available mega-code skills and ask the user to pick one:

```bash
PROJECT_DIR_CANDIDATE="${CLAUDE_PROJECT_DIR:-$(pwd -P)}"
case "$PROJECT_DIR_CANDIDATE" in
  *"/.claude/plugins/cache/"*|*"/.claude/plugins/marketplaces/"*)
    unset PROJECT_DIR_CANDIDATE
    ;;
esac
if [ -n "${PROJECT_DIR_CANDIDATE:-}" ]; then
  PROJECT_DIR_ARG=(--project-dir "$PROJECT_DIR_CANDIDATE")
else
  PROJECT_DIR_ARG=()
fi
uv run --directory "$MEGA_DIR" python -m mega_code.client.skill_enhance_helper list-skills "${PROJECT_DIR_ARG[@]}" 2>&1
```

Parse the JSON output and present the skills to the user using `AskUserQuestion`.
Only mega-code authored skills are shown. The author marker may be either
top-level `author` or nested `metadata.author`; both are supported.
If exactly one skill is returned, do not use `AskUserQuestion`; tell the user which
skill was found and proceed with it directly.

Once a skill is selected, resolve it immediately so the canonical folder name is repaired before any
later phases. Save the canonical skill name, path, and content for later phases:

```bash
PROJECT_DIR_CANDIDATE="${CLAUDE_PROJECT_DIR:-$(pwd -P)}"
case "$PROJECT_DIR_CANDIDATE" in
  *"/.claude/plugins/cache/"*|*"/.claude/plugins/marketplaces/"*)
    unset PROJECT_DIR_CANDIDATE
    ;;
esac
if [ -n "${PROJECT_DIR_CANDIDATE:-}" ]; then
  PROJECT_DIR_ARG=(--project-dir "$PROJECT_DIR_CANDIDATE")
else
  PROJECT_DIR_ARG=()
fi
RESOLVE_JSON=$(uv run --directory "$MEGA_DIR" python -m mega_code.client.skill_enhance_helper \
    resolve-skill --name "$SKILL_NAME" "${PROJECT_DIR_ARG[@]}" 2>&1)
read SKILL_NAME SKILL_PATH < <(echo "$RESOLVE_JSON" | tail -1 | python3 -c "import sys,json; d=json.load(sys.stdin); print(d['name'], d['path'])")
echo "$RESOLVE_JSON" | tail -1 | python3 -c "import sys,json; print(json.load(sys.stdin)['content'])"
```

**Create iteration workspace:**

```bash
ITER_JSON=$(uv run --directory "$MEGA_DIR" python -m mega_code.client.eval_workspace \
    create-iteration --skill-name "$SKILL_NAME" --skill-path "$SKILL_PATH" 2>&1)
read ITER_DIR ITERATION_NUM < <(echo "$ITER_JSON" | tail -1 | python3 -c "import sys,json; d=json.load(sys.stdin); print(d['path'], d['iteration'])")
```

## Phase 2 — Generate Test Cases

You are the LLM. Read the skill content carefully, then generate test cases following
the schema and guidelines in `references/test-case-schema.md`.

Write the JSON to the iteration workspace via Bash:

```bash
cat > "$ITER_DIR/test-cases.json" << 'TESTCASES_EOF'
<your generated JSON here>
TESTCASES_EOF
```

## Phase 3 — Security Scan

Run a static security audit before any A/B execution. This phase is a real gate.

```bash
uv run --directory "$MEGA_DIR" python -m mega_code.client.skill_security_audit \
    --skill-path "$SKILL_PATH" \
    --iteration-dir "$ITER_DIR" 2>&1
AUDIT_EXIT=$?
```

Read both `$ITER_DIR/security-review.json` and `$SKILL_PATH`. Use both inputs together —
the JSON provides structured signals; the skill content provides context.

Explain to the user in plain language: why the skill was labeled `trusted` or `semitrusted`,
what evidence or red flags contributed, and the resulting A/B policy. Treat trust as a
provenance hint, not as a reason to suppress dangerous findings.

Apply the trust policy from `references/trust-policy.md` to determine whether to proceed,
warn, or skip A/B testing.

Use the checklist in `references/security-checklist.md` to deepen the assessment.

## Phase 4 — Run A/B Tests

Run the A/B test runner. This spawns isolated agent CLI completions — one with the skill as system prompt, one without — for each test case:

```bash
uv run --directory "$MEGA_DIR" python -m mega_code.client.skill_enhance_runner \
    --test-cases "$ITER_DIR/test-cases.json" \
    --skill-md <skill-path> \
    --agent "${EVAL_AGENT}" \
    --output "$ITER_DIR/ab-results.json" 2>&1
```

The `--agent` flag ensures the A/B runner uses the same agent CLI as the host (you). If `EVAL_AGENT` is empty, omit the `--agent` flag entirely.

If this fails, show the error to the user and stop.

Read the A/B output JSON to use in the grading phase.

## Phase 5 — Grade Outputs

Read the A/B results from `$ITER_DIR/ab-results.json`. Grade both outputs for each test
case following the process and schema in `references/grading-schema.md`.

Write gradings to the iteration workspace:

```bash
cat > "$ITER_DIR/gradings.json" << 'GRADINGS_EOF'
<your generated gradings JSON here>
GRADINGS_EOF
```

## Phase 6 — HTML Review & User Feedback

Combine the test cases, A/B outputs, and gradings into a single JSON file:

```json
{
  "skill_name": "<skill-name>",
  "model": "<model from A/B output, e.g. with_skill_model field, or 'host-agent'>",
  "test_cases": <contents of test cases JSON>.cases,
  "ab_outputs": <contents of A/B output JSON>,
  "gradings": <contents of gradings JSON>
}
```

Write this combined file to `$ITER_DIR/eval-full.json`.

Run aggregation to compute metrics:

```bash
uv run --directory "$MEGA_DIR" python -m mega_code.client.skill_enhance_aggregator \
    --eval-data "$ITER_DIR/eval-full.json" \
    --iteration-dir "$ITER_DIR" 2>&1
```

Display the aggregation output to the user (per-test results + ROI metrics + verdict).

Launch the HTTP review server.

**If `EVAL_AGENT=codex`** — background processes are unreliable in this environment. Use
foreground mode: the script runs the viewer directly, blocks until the user submits
feedback, then exits and prints the feedback JSON to stdout. Do NOT call `stop-viewer.sh`.

```bash
FEEDBACK_JSON=$(bash "$MEGA_DIR/skills/skill-enhance/scripts/launch-viewer.sh" \
    "$MEGA_DIR" "$ITER_DIR" "$SKILL_NAME" "$ITERATION_NUM" --foreground)
```

The script will print the viewer URL to stderr before blocking. Tell the user:
"The evaluation viewer is running at the URL shown above. Review the outputs, leave feedback in the textboxes, and click 'Submit' — that will close the viewer and return control to me."
When the command returns, `$FEEDBACK_JSON` contains the feedback. Skip the stop-viewer step.

**Otherwise (Claude Code or other agents)** — the launch script uses `nohup` (and `setsid`
when available) to keep the viewer alive independent of this shell, saves the PID to
`$ITER_DIR/viewer.pid`, and waits until the server responds on its assigned port before returning:

```bash
bash "$MEGA_DIR/skills/skill-enhance/scripts/launch-viewer.sh" \
    "$MEGA_DIR" "$ITER_DIR" "$SKILL_NAME" "$ITERATION_NUM"
```

If the script exits non-zero the viewer failed to start. Show the error (including
`$ITER_DIR/viewer.log`) to the user and stop — do NOT tell them to open the URL.

On success, the script prints the actual URL (e.g. "Viewer is running on http://localhost:<PORT>") and opens the browser automatically.
Tell the user: "The evaluation viewer has opened in your browser. Review the outputs, leave feedback in the textboxes, and click 'Submit'. The feedback will be saved automatically. Let me know when you're done." Do NOT open the browser yourself or output the URL as a clickable link — the launch script handles browser opening.

Wait for the user to confirm they've reviewed. Then stop the viewer and read feedback:

```bash
export STOP_REASON=user_confirmed_in_chat
bash "$MEGA_DIR/skills/skill-enhance/scripts/stop-viewer.sh" "$ITER_DIR"
```

If the user says they have no feedback, that's fine — proceed with empty feedback.

## Phase 7 — Enhance Skill

Follow the principles in `references/enhancement-principles.md` to produce an improved
version of the skill.

Read the following files:
- `$ITER_DIR/eval-full.json` — eval data with test cases, A/B outputs, and gradings
- `$ITER_DIR/feedback.json` — user feedback from the HTML viewer
- `<skill-path>` — the current SKILL.md being improved

Based on the eval results and feedback, produce an improved version of the SKILL.md.
The enhanced skill MUST satisfy every item in this checklist:

1. **Frontmatter present** — starts with `---` / `---` YAML block
2. **`name`** and **`description`** — present and accurate
3. **`metadata.tags`** — REQUIRED. If the original skill has tags, copy them exactly.
   If not, generate at least 3 relevant tags from the skill's domain
   (e.g. `tags: [git, automation, devops]`)
4. **Skill body enhanced** — incorporates eval findings and user feedback

Write ONLY the complete enhanced skill content (frontmatter + body).

Write the enhanced content to `$ITER_DIR/enhanced-skill.md` via Bash:

```bash
cat > "$ITER_DIR/enhanced-skill.md" << 'ENHANCED_EOF'
<your enhanced SKILL.md content here>
ENHANCED_EOF
```

### Validate enhanced skill

Before proceeding to Phase 8, read `$ITER_DIR/enhanced-skill.md` and verify:
- Frontmatter contains `metadata.tags` as a list with at least 2 entries.
- If tags are missing, edit the file to add them before continuing.

## Phase 8 — Store & Iterate

**Back up original, inject ROI, and replace with enhanced version:**

Read the benchmark data from `$ITER_DIR/benchmark.json` to extract the eval ROI.
Then pass it to `accept_enhanced_skill` so the ROI is injected into the enhanced
skill's frontmatter:

```bash
uv run --directory "$MEGA_DIR" python -m mega_code.client.skill_enhance_helper \
    accept-skill \
    --skill-path "$SKILL_PATH" \
    --enhanced-path "$ITER_DIR/enhanced-skill.md" \
    --iteration-dir "$ITER_DIR" \
    --iteration "$ITERATION_NUM" \
    --benchmark "$ITER_DIR/benchmark.json" 2>&1
```

This backs up the original to `$ITER_DIR/original-skill.md` and replaces `SKILL.md`
with the enhanced version (semantic version bumped, `generated_at` refreshed, ROI from eval added).

**Store on server** (creates a new DB row for the canonical enhanced skill name with the bumped semantic version and lineage metadata, while preserving the original pending-skill folder name as the lineage parent):

```bash
set -a && . ~/.local/share/mega-code/.env 2>/dev/null && set +a
export MEGA_CODE_CLIENT_MODE=${MEGA_CODE_CLIENT_MODE:-remote}
uv run --directory "$MEGA_DIR" python -m mega_code.client.skill_enhance_helper \
    store-skill \
    --skill-name "$SKILL_NAME" \
    --iteration-dir "$ITER_DIR" \
    --iteration "$ITERATION_NUM" \
    --skill-path "$SKILL_PATH" \
    --benchmark "$ITER_DIR/benchmark.json" 2>&1
```

If the store-skill command succeeds, tell the user the skill was stored on the server. If it fails (non-zero exit), show the error output and warn that the enhanced skill was saved locally but not stored on the server.

**Ask the user** if they want to iterate:

"The skill has been enhanced and saved. The original is backed up at `$ITER_DIR/original-skill.md`. Would you like to run another iteration to validate the enhancement?"

If the user wants another iteration:
1. Create a new iteration directory (increment automatically):
   ```bash
   ITER_JSON=$(uv run --directory "$MEGA_DIR" python -m mega_code.client.eval_workspace \
       create-iteration --skill-name "$SKILL_NAME" --skill-path "$SKILL_PATH" 2>&1)
   read ITER_DIR ITERATION_NUM < <(echo "$ITER_JSON" | tail -1 | python3 -c "import sys,json; d=json.load(sys.stdin); print(d['path'], d['iteration'])")
   ```
2. Go back to **Phase 2** — the skill being evaluated is now the enhanced version (already replaced in-place)

If the user is done, show a summary of what was done and where the files are.
