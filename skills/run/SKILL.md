---
name: mega-code-run
description: "Run the MEGA-Code skill extraction pipeline to analyze coding sessions and generate reusable skills and strategies."
argument-hint: "[--project [@<name>]] [--model <model>] [--poll-timeout <seconds>] [--include-claude] [--include-codex]"
allowed-tools: Bash, Read, Write, Edit, AskUserQuestion
disable-model-invocation: true
---

# Run Skill Extraction Pipeline

Extract reusable skills and coding strategies from your coding sessions.

## ⚠️ Important: Pipeline is Long-Running

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
if [ -n "$CLAUDE_PLUGIN_ROOT" ]; then
  MEGA_DIR="$CLAUDE_PLUGIN_ROOT"
  MEGA_CODE_AGENT="claude-code"
else
  MEGA_DIR="$(cat ~/.local/share/mega-code/pkg-breadcrumb 2>/dev/null)"
  MEGA_CODE_AGENT="codex"
fi
if [ -z "$MEGA_DIR" ] || [ ! -f "$MEGA_DIR/pyproject.toml" ]; then
  MEGA_DIR="$HOME/.local/share/mega-code/pkg"
  if [ ! -f "$MEGA_DIR/pyproject.toml" ]; then
    rm -rf "$MEGA_DIR"
    git clone --depth 1 "${MEGA_CODE_REPO_URL:-https://github.com/wisdomgraph/mega-code.git}" "$MEGA_DIR"
  fi
  bash "$MEGA_DIR/scripts/codex-bootstrap.sh" "$MEGA_DIR"
  MEGA_CODE_AGENT="codex"
fi
export MEGA_CODE_DATA_DIR="$HOME/.local/share/mega-code"
export MEGA_CODE_AGENT
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
| `--include-claude` | Include Claude Code sessions (cross-agent opt-in) |
| `--include-codex` | Include Codex sessions (cross-agent opt-in) |

**Project argument formats** (all equivalent):
`@mega-code` · `mega-code` · `mega-code_b39e0992` · `/path/to/project`

## Running the Pipeline

Check for pending items first, then run the pipeline. All variables must be in
**one single Bash call** so `$LOG` and `$MEGA_DIR` stay in scope:

```bash
uv run --directory "$MEGA_DIR" python -m mega_code.client.pending review < /dev/null 2>/dev/null || true
AGENT_FLAG=""
if [ "$MEGA_CODE_AGENT" = "codex" ]; then
  AGENT_FLAG="--include-codex"
elif [ "$MEGA_CODE_AGENT" = "claude-code" ]; then
  AGENT_FLAG="--include-claude"
fi
LOG="/tmp/mega-code-run-$(date +%Y%m%d-%H%M%S).log" && \
  echo "Pipeline log: $LOG" && \
  export MEGA_CODE_PROJECT_DIR="$PWD" && \
  uv run --directory "$MEGA_DIR" python -m mega_code.client.run_pipeline $AGENT_FLAG [FLAGS] 2>&1 | tee "$LOG"
```

Replace `[FLAGS]` with any additional flags from the table above.
`$AGENT_FLAG` is set automatically based on the detected coding agent.
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

Use the `AskUserQuestion` tool to present these options:

**Question:** "A pipeline is already running for this project (run_id: {run_id}). What would you like to do?"

**Options:**
1. "Stop it and start a new one"
2. "Wait for the existing run to finish"
3. "Leave it running — exit without action"

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

Use the `AskUserQuestion` tool to present these options:

**Question:** "The pipeline timed out on the server ({error message}). What would you like to do?"

**Options:**
1. "Run again — start a fresh pipeline run"
2. "Do nothing — exit without action"

**Option 1 — Run again:**
Re-execute the pipeline command from "Running the Pipeline" section.

**Option 2 — Do nothing:**
Return immediately. Do not print anything or ask further questions.

## Post-Pipeline Workflow (MANDATORY)

The pipeline prints a JSON object with `additionalContext` on completion.
You MUST parse and follow the embedded workflow immediately — do NOT just report "pipeline complete".

### Steps:

1. Parse `run_id` and `project_id` from the pipeline output JSON (`additionalContext`).

2. Run this command to get the detailed review workflow instructions:

```bash
uv run --directory "$MEGA_DIR" python -m mega_code.client.pending review \
  --run-id <RUN_ID> --project-id <PROJECT_ID>
```

3. Follow the printed instructions **exactly** — they contain the full review, install, and archive workflow.

## Tips

- Run the pipeline after significant coding sessions (`/mega-code:run` in Claude Code, `$mega-code-run` in Codex)
- Use `--project` to analyze multiple sessions for stronger patterns
- Use `@name` to run on a different project without switching directories
- Skills with more evidence (from multiple sessions) are higher quality
- Review and edit skills before installing for best results
