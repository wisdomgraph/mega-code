---
description: Run the MEGA-Code skill extraction pipeline to analyze Claude Code sessions and generate reusable skills and strategies.
argument-hint: [--project [@<name>]] [--model <model>] [--poll-timeout <seconds>]
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
MEGA_DIR="${CLAUDE_PLUGIN_ROOT:-$(cat ~/.local/mega-code/plugin-root 2>/dev/null)}"
```

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

**Project argument formats** (all equivalent):
`@mega-code` · `mega-code` · `mega-code_b39e0992` · `/path/to/project`

## Running the Pipeline

All variables must be in **one single Bash call** so `$LOG` and `$MEGA_DIR` stay in scope:

```bash
LOG="/tmp/mega-code-run-$(date +%Y%m%d-%H%M%S).log" && \
  echo "Pipeline log: $LOG" && \
  export CLAUDE_PROJECT_DIR="$PWD" && \
  [ -f "${HOME}/.local/mega-code/.env" ] && set -a && . "${HOME}/.local/mega-code/.env" && set +a ; \
  set -a && . "$MEGA_DIR/.env" 2>/dev/null && set +a && \
  uv run --directory "$MEGA_DIR" python scripts/run_pipeline_async.py [FLAGS] 2>&1 | tee "$LOG"
```

Replace `[FLAGS]` with desired combination from the table above.
Tell the user the log path so they can monitor with `tail -f` or check after completion.

## Model Options

| Alias | Provider |
|-------|----------|
| `gemini-3-flash` | Google |
| `gpt-5-mini` | OpenAI |

When omitted, server selects based on configured LLM keys (priority: OpenAI > Anthropic > Gemini). Falls back to `gemini-3-flash`.

## Pipeline Outputs

1. **Skills & Strategies** — saved to pending dirs for review/install
2. **Lesson Learned documents** — saved to `~/.local/mega-code/data/feedback/{project_id}/{run_id}/lessons/` (from sessions tagged lesson_learn)

## Post-Pipeline Workflow (MANDATORY)

The pipeline prints a JSON object with `additionalContext` on completion.
You MUST parse and follow the embedded workflow immediately — do NOT just report "pipeline complete".

### Steps:

1. **Read & Analyze** — Read each pending item with the Read tool. Analyze quality, clarity, completeness silently.
   For **lessons**: read each `.md` file at the path shown in the notification and display the markdown content directly to the user.

2. **Present Review** — Show each item with summary, quality assessment, and enhanced version if needed.

3. **Ask User** — Use AskUserQuestion:
   - Which items to install (multiSelect: true) — skills and strategies only
   - Which version (Enhanced/Original) — for skills/strategies
   - Location: project (`.claude/skills/`) or user (`~/.claude/skills/`)

4. **Install** — Write approved items:
   - Skills → `<location>/skills/<name>/SKILL.md`
   - Strategies → `.claude/rules/mega-code/<name>.md`
   - Lessons are already saved to the run folder — no install step needed.

5. **Clean Up** — Clear all pending items after installation:

```bash
set -a && . "$MEGA_DIR/.env" 2>/dev/null && set +a && \
  uv run --directory "$MEGA_DIR" python -c "
from mega_code.client.pending import clear_pending
cleared = clear_pending()
print(f'Cleared {cleared} pending items')
"
```
