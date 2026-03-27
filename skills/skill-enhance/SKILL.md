---
description: "Evaluate and enhance a mega-code skill using LLM-as-judge A/B testing with an iterative improvement loop."
argument-hint: "<skill-name>"
allowed-tools: Bash, Read, Write, Edit, AskUserQuestion
---

Run on-demand A/B evaluation of a mega-code skill, review results in an HTML viewer,
collect user feedback, and produce an enhanced version of the skill. The host agent (you)
handles test generation, grading, and enhancement; isolated A/B completions run via subprocess.

## Setup

```bash
MEGA_DIR="${CLAUDE_PLUGIN_ROOT:-$(cat ~/.local/share/mega-code/plugin-root 2>/dev/null)}"
set -a && . "$MEGA_DIR/.env" 2>/dev/null && set +a
uv run --directory "$MEGA_DIR" python -m mega_code.client.check_auth
```

If the auth check fails (non-zero exit), show the output to the user and stop.

**Detect which agent you are** — set `EVAL_AGENT` so the A/B runner uses the same agent:
- If you are Claude Code, set `EVAL_AGENT=claude`
- If you are Codex, set `EVAL_AGENT=codex`
- If unsure, omit it (auto-detection will be used)

All commands below assume `MEGA_DIR` is set.

## Phase 1 — Skill Selection & Workspace Setup

If a skill name was provided as an argument, use it directly.

If no skill name was provided, list available mega-code skills and ask the user to pick one:

```bash
export CLAUDE_PROJECT_DIR="$PWD" && \
uv run --directory "$MEGA_DIR" python -m mega_code.client.skill_enhance_helper list-skills 2>&1
```

Parse the JSON output and present the skills to the user using `AskUserQuestion`.
Only mega-code authored skills are shown. The author marker may be either
top-level `author` or nested `metadata.author`; both are supported.

Once a skill is selected, read its full SKILL.md content:

```bash
cat <skill-path>
```

Save the skill name, path, and content for later phases.

**Create iteration workspace:**

```bash
ITER_INFO=$(SKILL_NAME="$SKILL_NAME" uv run --directory "$MEGA_DIR" python -c "
import os
from mega_code.client.eval_workspace import create_iteration_dir
path, num = create_iteration_dir(os.environ['SKILL_NAME'])
print(f'{path}|{num}')
" 2>&1)
ITER_DIR=$(echo "$ITER_INFO" | tail -1 | cut -d'|' -f1)
ITERATION_NUM=$(echo "$ITER_INFO" | tail -1 | cut -d'|' -f2)
```

## Phase 2 — Generate Test Cases

You are the LLM. Generate exactly 4 realistic test cases to evaluate this skill.

Read the skill content carefully, then produce a JSON object with this exact schema:

```json
{
  "cases": [
    {
      "task": "A realistic coding task prompt that this skill should help with",
      "expectations": [
        {"text": "Specific, verifiable assertion about what a good response should include"},
        {"text": "Another specific assertion"},
        {"text": "A third assertion"}
      ]
    }
  ]
}
```

Guidelines for test cases:
- Each `task` should be a realistic coding request written as a real user would — concrete, specific, with enough detail to get a meaningful response
- Each test case should have 3-4 `expectations` — specific, verifiable natural language assertions
- Expectations should distinguish a response that follows the skill from one that doesn't
- Focus on observable behaviors and concrete outputs, not vague quality claims
- Good: "The commit message follows conventional commit format with a type prefix like feat: or fix:"
- Bad: "The response is good quality"

Write the JSON to the iteration workspace via Bash:

```bash
cat > "$ITER_DIR/test-cases.json" << 'TESTCASES_EOF'
<your generated JSON here>
TESTCASES_EOF
```

## Phase 3 — Security Scan

Run a static security audit before any A/B execution. This phase is a real gate.
It scans for red flags, classifies trust level, and sets the A/B policy.

Run the audit CLI:

```bash
uv run --directory "$MEGA_DIR" python -m mega_code.client.skill_security_audit \
    --skill-path "$SKILL_PATH" \
    --iteration-dir "$ITER_DIR" 2>&1
AUDIT_EXIT=$?
```

Read both:
- `$ITER_DIR/security-review.json`
- `$SKILL_PATH`

Use both inputs together. The JSON provides structured signals; the skill content provides the context.
Explain to the user, in plain language:
- why the skill was labeled `trusted` or `semitrusted`
- what evidence or red flags contributed to that label
- whether the A/B policy is `full_access`, `warn_and_continue`, or `skip_ab`

Interpret the `ab_policy` field as follows:
- `full_access` — continue to Phase 4
- `warn_and_continue` — show findings to the user, warn that the skill has security red flags, then continue to Phase 4 only after the user confirms
- `skip_ab` — do NOT run A/B testing. Tell the user the skill has high-severity red flags from a semitrusted source. Stop the evaluation loop unless they explicitly want a static-only review

If the `trust_level` is `semitrusted`, always ask the user whether to proceed before running Phase 4, even if `ab_policy` is `full_access`.

For `semitrusted`, your message to the user should explicitly include:
- the trust level
- the trust explanation from `security-review.json`
- any red flags found
- a direct question asking whether to proceed with A/B testing

This rollout is static audit only. Do NOT claim that Phase 4 has enforced read-only,
no-network, or sandbox isolation for semitrusted skills unless the runner actually supports it.

When reviewing the scan output, use this checklist to deepen the assessment:
- Credential & Secret Management
- Command Injection
- Prompt Injection & Instruction Override
- Data Exfiltration
- Supply Chain & Dependencies
- Principle of Least Privilege
- Dual-Layer Attack Detection
- Hook & Config Exploitation
- Indirect Prompt Injection via Content Processing

If the user chooses a static-only review after `skip_ab`, summarize the findings from
`security-review.json`, note that dynamic A/B execution was skipped for safety, and stop.

## Phase 4 — Run A/B Tests

Run the A/B test runner. This spawns isolated agent CLI completions — one with the skill as system prompt, one without — for each test case:

```bash
export CLAUDE_PROJECT_DIR="$PWD" && \
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

You are a strict evaluation grader. For each test case, grade BOTH the with-skill and baseline outputs against every expectation.

Read the A/B results from `$ITER_DIR/ab-results.json`. For each entry:
1. Read the `task`, `expectations`, `with_skill_output`, and `baseline_output`
2. Grade the **with-skill output** against each expectation
3. Grade the **baseline output** against each expectation
4. For each expectation: determine `passed` (true/false) and provide `evidence` (one sentence)

Be strict: only pass if the expectation is clearly and unambiguously met. If vague or partial, mark as failed.

Produce a JSON array with one grading object per test case:

```json
[
  {
    "with_skill_gradings": [
      {"expectation": "...", "passed": true, "evidence": "The output includes..."},
      {"expectation": "...", "passed": false, "evidence": "The output does not mention..."}
    ],
    "baseline_gradings": [
      {"expectation": "...", "passed": false, "evidence": "No evidence of..."},
      {"expectation": "...", "passed": true, "evidence": "The output does include..."}
    ]
  }
]
```

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
export CLAUDE_PROJECT_DIR="$PWD" && \
uv run --directory "$MEGA_DIR" python -m mega_code.client.skill_enhance_aggregator \
    --eval-data "$ITER_DIR/eval-full.json" \
    --skill-path <skill-path> \
    --iteration-dir "$ITER_DIR" 2>&1
```

Display the aggregation output to the user (per-test results + ROI metrics + verdict).

Launch the HTTP review server in the background. It opens the browser automatically
and handles feedback POSTs. Save the PID so we can kill it after the user is done:

```bash
PREV_WORKSPACE_ARG=""
if [ "$ITERATION_NUM" -gt 1 ]; then
    PREV_DIR="$(dirname "$ITER_DIR")/iteration-$((ITERATION_NUM - 1))"
    PREV_WORKSPACE_ARG="--previous-workspace $PREV_DIR"
fi

# Kill any leftover viewer on port 3117
lsof -ti :3117 2>/dev/null | xargs kill 2>/dev/null; sleep 0.5

uv run --directory "$MEGA_DIR" python -m mega_code.client.enhancement_viewer \
    "$ITER_DIR" \
    --skill-name "$SKILL_NAME" \
    --iteration "$ITERATION_NUM" \
    $PREV_WORKSPACE_ARG \
    > /dev/null 2>&1 &
```

Tell the user: "I've opened the evaluation results in your browser at http://localhost:3117. Review the outputs, leave feedback in the textboxes, and click 'Submit'. The feedback will be saved automatically. Let me know when you're done."

Wait for the user to confirm they've reviewed. Then stop the viewer and read feedback:

```bash
lsof -ti :3117 2>/dev/null | xargs kill 2>/dev/null
cat "$ITER_DIR/feedback.json" 2>/dev/null || echo '{"reviews": []}'
```

If the user says they have no feedback, that's fine — proceed with empty feedback.

## Phase 7 — Enhance Skill

You are the skill enhancer. Read the eval data, user feedback, and the current skill,
then produce an improved version following these 4 principles:

1. **Generalize from feedback** — Don't overfit to specific test cases. Use different
   metaphors or patterns rather than adding rigid constraints.
2. **Keep the prompt lean** — Read transcripts (not just outputs) to identify wasted
   effort. Remove instructions that aren't helping.
3. **Explain the why** — Avoid ALWAYS/NEVER in caps. Instead, explain reasoning so
   the model understands the intent. More humane and effective.
4. **Look for repeated work** — If all test runs independently wrote similar helper
   scripts, bundle that script into the skill's `scripts/` directory.

Read the following files:
- `$ITER_DIR/eval-full.json` — eval data with test cases, A/B outputs, and gradings
- `$ITER_DIR/feedback.json` — user feedback from the HTML viewer
- `<skill-path>` — the current SKILL.md being improved

Based on the eval results and feedback, produce an improved version of the SKILL.md.
Write ONLY the complete enhanced skill content (including frontmatter).

Write the enhanced content to `$ITER_DIR/enhanced-skill.md` via Bash:

```bash
cat > "$ITER_DIR/enhanced-skill.md" << 'ENHANCED_EOF'
<your enhanced SKILL.md content here>
ENHANCED_EOF
```

## Phase 8 — Store & Iterate

**Back up original, inject ROI, and replace with enhanced version:**

Read the benchmark data from `$ITER_DIR/benchmark.json` to extract the eval ROI.
Then pass it to `accept_enhanced_skill` so the ROI is injected into the enhanced
skill's frontmatter:

```bash
ITER_DIR="$ITER_DIR" SKILL_PATH="$SKILL_PATH" ITERATION_NUM="$ITERATION_NUM" \
uv run --directory "$MEGA_DIR" python -c "
import json, os
from pathlib import Path
from mega_code.client.skill_enhance_helper import accept_enhanced_skill

iter_dir = os.environ['ITER_DIR']
skill_path = os.environ['SKILL_PATH']
iteration_num = int(os.environ['ITERATION_NUM'])

benchmark = json.loads(Path(f'{iter_dir}/benchmark.json').read_text())
eval_roi = {
    'model': benchmark.get('model', 'host-agent'),
    'performance_increase': benchmark.get('performance_increase', 0),
    'token_savings': benchmark.get('token_savings', 0),
    'test_count': len(benchmark.get('test_results', [])),
    'with_skill_avg': benchmark.get('with_skill_avg', 0),
    'baseline_avg': benchmark.get('baseline_avg', 0),
}

accept_enhanced_skill(
    original_skill_path=Path(skill_path),
    enhanced_skill_path=Path(f'{iter_dir}/enhanced-skill.md'),
    iteration_dir=Path(iter_dir),
    iteration=iteration_num,
    eval_roi=eval_roi,
)
" 2>&1
```

This backs up the original to `$ITER_DIR/original-skill.md` and replaces `SKILL.md`
with the enhanced version (semantic version bumped, `generated_at` refreshed, ROI from eval added).

**Store on server** (creates a new DB row for the canonical enhanced skill name with the bumped semantic version and lineage metadata, while preserving the original pending-skill folder name as the lineage parent):

```bash
FINAL_SKILL_PATH=$(ITER_DIR="$ITER_DIR" SKILL_PATH="$SKILL_PATH" uv run --directory "$MEGA_DIR" python -c "
import json, os
from pathlib import Path

iter_dir = Path(os.environ['ITER_DIR'])
default_path = os.environ['SKILL_PATH']
identity_path = iter_dir / 'skill-identity.json'
if identity_path.exists():
    identity = json.loads(identity_path.read_text())
    canonical_name = identity.get('canonical_skill_name')
    if isinstance(canonical_name, str) and canonical_name.strip():
        pending_root = Path(default_path).resolve().parent.parent
        print(pending_root / canonical_name / 'SKILL.md')
    else:
        print(default_path)
else:
    print(default_path)
")
ENHANCED_CONTENT=$(cat "$FINAL_SKILL_PATH")
set -a && . "$MEGA_DIR/.env" 2>/dev/null && set +a
ITER_DIR="$ITER_DIR" SKILL_NAME="$SKILL_NAME" ITERATION_NUM="$ITERATION_NUM" \
uv run --directory "$MEGA_DIR" python -c "
import json, os, sys
from pathlib import Path
from mega_code.client.skill_enhance_helper import store_enhanced_skill_on_server

content = sys.stdin.read()
iter_dir = os.environ['ITER_DIR']
skill_name = os.environ['SKILL_NAME']
iteration_num = int(os.environ['ITERATION_NUM'])

benchmark = json.loads(Path(f'{iter_dir}/benchmark.json').read_text())
eval_roi = {
    'model': benchmark.get('model', 'host-agent'),
    'performance_increase': benchmark.get('performance_increase', 0),
    'token_savings': benchmark.get('token_savings', 0),
    'test_count': len(benchmark.get('test_results', [])),
    'with_skill_avg': benchmark.get('with_skill_avg', 0),
    'baseline_avg': benchmark.get('baseline_avg', 0),
}
store_enhanced_skill_on_server(
    skill_name,
    content,
    iteration_num,
    eval_roi=eval_roi,
    iteration_dir=Path(iter_dir),
)
" <<< "$ENHANCED_CONTENT" 2>&1
```

**Ask the user** if they want to iterate:

"The skill has been enhanced and saved. The original is backed up at `$ITER_DIR/original-skill.md`. Would you like to run another iteration to validate the enhancement?"

If the user wants another iteration:
1. Create a new iteration directory (increment automatically):
   ```bash
   ITER_INFO=$(SKILL_NAME="$SKILL_NAME" uv run --directory "$MEGA_DIR" python -c "
   import os
   from mega_code.client.eval_workspace import create_iteration_dir
   path, num = create_iteration_dir(os.environ['SKILL_NAME'])
   print(f'{path}|{num}')
   " 2>&1)
   ITER_DIR=$(echo "$ITER_INFO" | tail -1 | cut -d'|' -f1)
   ITERATION_NUM=$(echo "$ITER_INFO" | tail -1 | cut -d'|' -f2)
   ```
2. Go back to **Phase 2** — the skill being evaluated is now the enhanced version (already replaced in-place)

If the user is done, show a summary of what was done and where the files are.
