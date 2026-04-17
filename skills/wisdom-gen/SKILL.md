---
name: mega-code-wisdom-gen
description: "Run the MEGA-Code extraction pipeline. Usage: [--project [@name]] [--model <alias>] [--poll-timeout <sec>] [--include-codex]"
allowed-tools: Bash, Read, Write, Edit
disable-model-invocation: true
---

# Run Skill Extraction Pipeline

Extract reusable skills and coding strategies from your coding sessions.

## Important: Pipeline is Long-Running

The pipeline command **blocks until the server finishes processing**. The server
runs the pipeline asynchronously and this client polls for completion.

**DO NOT interrupt the command (Ctrl+C) while it is running.** Silence with no
new output for several minutes is **completely normal** during LLM inference
(especially with `gpt-5-mini` or other reasoning models).

The default poll timeout is **20 minutes**. For longer runs, use `--poll-timeout`:
- `--poll-timeout 3600` — wait up to 1 hour
- `--poll-timeout 0` — wait indefinitely (no timeout)

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
export MEGA_CODE_AGENT="codex"
export MEGA_CODE_DATA_DIR="$HOME/.local/share/mega-code"
export UV_CACHE_DIR="${UV_CACHE_DIR:-$MEGA_DIR/.uv-cache}"
uv run --directory "$MEGA_DIR" python -m mega_code.client.check_auth
```

If the auth check fails (non-zero exit), show the output to the user and stop.

All commands below assume `MEGA_DIR` is set.

## Flags

| Flag | Behavior |
|------|----------|
| *(none)* | Process current session only |
| `--project` | All sessions in current project |
| `--project @name` | Specific project by name prefix |
| `--session-id <uuid>` | Specific session |
| `--model <alias>` | LLM model (default: server picks best) |
| `--poll-timeout <seconds>` | Max seconds to poll for completion (default: 1200 = 20 min; 0 = indefinite) |
| `--include-codex` | Include Codex sessions (default for Codex agent) |

**Project argument formats** (all equivalent):
`@mega-code` · `mega-code` · `mega-code_b39e0992` · `/path/to/project`

## Running the Pipeline

Check for pending items first, then run the pipeline. All variables must be in
**one single Bash call** so `$LOG` and `$MEGA_DIR` stay in scope:

```bash
LOG="/tmp/mega-code-wisdom-gen-$(date +%Y%m%d-%H%M%S).log" && \
  echo "Pipeline log: $LOG" && \
  export MEGA_CODE_PROJECT_DIR="$PWD" && \
  uv run --directory "$MEGA_DIR" python -m mega_code.client.run_pipeline --include-codex [FLAGS] 2>&1 | tee "$LOG"
```

Replace `[FLAGS]` with any additional flags from the table above.
Tell the user the log path so they can monitor with `tail -f` or check after completion.

## Model Options

| Alias | Provider |
|-------|----------|
| `gemini-3-flash` | Google |
| `gpt-5-mini` | OpenAI |

When omitted, server selects based on configured LLM keys (priority: Gemini > OpenAI). Falls back to `gemini-3-flash`.

## Pipeline Outputs

1. **Skills & Strategies** — saved to pending dirs for review/install
2. **Lesson Learned documents** — saved to `~/.local/share/mega-code/data/feedback/{project_id}/{run_id}/lessons/` (from sessions tagged `lesson_learn`)

## Handling Active Pipeline (Exit Code 2)

If the pipeline command exits with code **2**, a pipeline is already running.
Parse the JSON output to get `conflict.run_id` and `conflict.project_id`.

Use `request_user_input` to present these options:

**Question:** "A pipeline is already running for this project (run_id: {run_id}). What would you like to do?"

**Options:**
1. "Stop & restart"
2. "Wait for it"
3. "Leave running"

**Option 1 — Stop and restart:**
```bash
uv run --directory "$MEGA_DIR" python -m mega_code.client.cli pipeline-stop --run-id <RUN_ID>
```
Then re-run the pipeline command from "Running the Pipeline" section.

**Option 2 — Wait for existing run:**
```bash
uv run --directory "$MEGA_DIR" python -m mega_code.client.run_pipeline \
  --poll-existing <RUN_ID> --project <PROJECT_ID> [--poll-timeout <seconds>] 2>&1 | tee "$LOG"
```
Then follow the Post-Pipeline Workflow as normal.

**Option 3 — Leave it running:**
Return immediately. Do not print anything or ask further questions.

## Handling Server Timeout (Exit Code 3)

If the pipeline command exits with code **3**, the pipeline exceeded the server's
max runtime and was terminated. Parse the JSON output for `timeout.error` details.

Use `request_user_input` to present these options:

**Question:** "The pipeline timed out on the server ({error message}). What would you like to do?"

**Options:**
1. "Run again"
2. "Do nothing"

**Option 1 — Run again:**
Re-execute the pipeline command from "Running the Pipeline" section.

**Option 2 — Do nothing:**
Return immediately. Do not print anything or ask further questions.

## Post-Pipeline Workflow (MANDATORY)

The pipeline prints a JSON object with `additionalContext` on completion.
You MUST parse and follow the embedded workflow immediately — do NOT just report "pipeline complete".

### Steps:

1. Parse `run_id` and `project_id` from the pipeline output JSON (`additionalContext`).

2. **Inject User Email** — run the email attribution gate.
   Read `references/email-gate.md` and follow it. Continue to step 3
   regardless of outcome (cached, skipped, or applied).

3. Run this command to get the detailed review workflow instructions:

```bash
uv run --directory "$MEGA_DIR" python -m mega_code.client.pending review \
  --run-id <RUN_ID> --project-id <PROJECT_ID>
```

4. Follow the printed instructions **exactly** for the review, install,
   and archive steps. Those instructions are a **sub-workflow**, not the
   end of wisdom-gen. When the sub-workflow completes — whether you
   installed items, archived them, or skipped everything — you MUST
   return here and continue with the "MANDATORY — Enhance Generated
   Skills (post-review handoff)" section below. Do NOT terminate the
   wisdom-gen workflow at the archive step.

## MANDATORY — Enhance Generated Skills (post-review handoff)

After the review sub-workflow returns, you MUST **run the enhance
handoff** whenever this pipeline run **generated** any skills.
Install/archive status is **irrelevant** — an archived skill is still a
generated skill and is still eligible. The only valid skip is a run
that produced zero skill candidates (strategies/lessons-only). **If
unsure, default to running the handoff.**

"Run the handoff" means execute the trigger check and (when applicable)
the binary prompt defined in the reference — *not* "always perform an
enhancement". A user "No" answer, or a non-interactive default-to-No,
is a **valid completion** of the handoff, not a skip.

You MUST now read `references/enhance-handoff.md` and follow it
end-to-end before terminating wisdom-gen. It owns the trigger check,
the binary Yes/No prompt, and the per-skill enhancement flow. Do not
re-implement the decision logic here.

## Tips

- Run the pipeline after significant coding sessions (`$mega-code-wisdom-gen`)
- Use `--project` to analyze multiple sessions for stronger patterns
- Use `@name` to run on a different project without switching directories
- Skills with more evidence (from multiple sessions) are higher quality
- Review and edit skills before installing for best results
- For updates, run: `npx skills add https://github.com/wisdomgraph/mega-code/tree/codex -a codex`
