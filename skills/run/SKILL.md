---
description: Run the MEGA-Code skill extraction pipeline to analyze Claude Code sessions and generate reusable skills and strategies.
argument-hint: [--project [@<name>]] [--model <model>] [--poll-timeout <seconds>] [--include-claude]
allowed-tools: Bash, Read, Write, Edit, AskUserQuestion
disable-model-invocation: true
---

# Run Skill Extraction Pipeline

Extract reusable skills and coding strategies from your Claude Code sessions.

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
MEGA_DIR="${CLAUDE_PLUGIN_ROOT:-$(cat ~/.local/share/mega-code/plugin-root 2>/dev/null)}"
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
| `--include-claude` | Include related Claude Code sessions from the project |

**Project argument formats** (all equivalent):
`@mega-code` · `mega-code` · `mega-code_b39e0992` · `/path/to/project`

## Running the Pipeline

All variables must be in **one single Bash call** so `$LOG` and `$MEGA_DIR` stay in scope:

```bash
LOG="/tmp/mega-code-run-$(date +%Y%m%d-%H%M%S).log" && \
  echo "Pipeline log: $LOG" && \
  export CLAUDE_PROJECT_DIR="$PWD" && \
  uv run --directory "$MEGA_DIR" python -m mega_code.client.run_pipeline [FLAGS] 2>&1 | tee "$LOG"
```

Replace `[FLAGS]` with desired combination from the table above.
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
