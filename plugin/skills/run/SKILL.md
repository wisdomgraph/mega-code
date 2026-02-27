---
description: Run the MEGA-Code skill extraction pipeline to analyze Claude Code sessions and generate reusable skills and strategies.
argument-hint: [--project [@<name>]] [--model <model>] [--include-claude] [--include-codex] [--include-all] [--poll-timeout <seconds>]
allowed-tools: Bash, Read, Write, Edit, AskUserQuestion
disable-model-invocation: true
---

# Run Skill Extraction Pipeline

Extract reusable skills and coding strategies from your Claude Code sessions.
MEGA-Code analyzes your interactions to identify patterns, best practices,
and learned rules that can be saved as reusable skills.

## ⚠️ Important: Pipeline is Long-Running

The pipeline command runs as a **background process and may take a long time**:

**DO NOT terminate or interrupt the background command.** It will print results
when done. Silence with no new output for several minutes is **completely normal**
during LLM inference (especially with `gpt-5-mini` or other reasoning models).

The default poll timeout is **20 minutes**. For longer runs, use `--poll-timeout`:
- `--poll-timeout 3600` — wait up to 1 hour
- `--poll-timeout 0` — wait indefinitely (no timeout)

## Finding the MEGA-Code Directory

```bash
# Discover mega-code root (marketplace or symlink install)
MEGA_DIR="${CLAUDE_PLUGIN_ROOT:-$(cat ~/.local/mega-code/plugin-root 2>/dev/null)}"
```

Then use `uv run --directory "$MEGA_DIR"` for all subsequent commands.

## Usage

- **Without --project**: Process current session only
- **With --project** (no value): Process all sessions in current project
- **With --project @name**: Process a specific project by name prefix
- **With --model**: Specify LLM model (server selects best model based on your configured LLM keys)
- **With --include-claude**: Include claude.jsonl conversation files
- **With --include-codex**: Include opencode.jsonl conversation files
- **With --include-all**: Include all available conversion sources
- **With --poll-timeout SECONDS**: Max seconds to wait for completion (default: 1200 = 20 min; 0 = indefinite)
- **Note**: Source flags can be combined (e.g., `--include-claude --include-codex`)

**Project argument formats** (all equivalent for the same project):
- `@mega-code` — name prefix with @ (Claude Code autocomplete friendly)
- `mega-code` — name prefix without @
- `mega-code_b39e0992` — exact folder name with hash
- `/path/to/project` — filesystem path

```bash
# IMPORTANT: All run commands must export CLAUDE_PROJECT_DIR=$PWD
# so the pipeline knows which project to process.
# Discover the mega-code directory first:
MEGA_DIR="${CLAUDE_PLUGIN_ROOT:-$(cat ~/.local/mega-code/plugin-root 2>/dev/null)}"

# Run on current session
LOG="/tmp/mega-code-run-$(date +%Y%m%d-%H%M%S).log"
echo "MEGA-Code pipeline log: $LOG"
export CLAUDE_PROJECT_DIR="$PWD" && uv run --directory "$MEGA_DIR" python scripts/run_pipeline_async.py 2>&1 | tee "$LOG"

# Run on all current project sessions
LOG="/tmp/mega-code-run-$(date +%Y%m%d-%H%M%S).log"
echo "MEGA-Code pipeline log: $LOG"
export CLAUDE_PROJECT_DIR="$PWD" && uv run --directory "$MEGA_DIR" python scripts/run_pipeline_async.py --project 2>&1 | tee "$LOG"

# Run on a specific project by name
LOG="/tmp/mega-code-run-$(date +%Y%m%d-%H%M%S).log"
echo "MEGA-Code pipeline log: $LOG"
export CLAUDE_PROJECT_DIR="$PWD" && uv run --directory "$MEGA_DIR" python scripts/run_pipeline_async.py --project @mega-code 2>&1 | tee "$LOG"

# Run with a specific model
LOG="/tmp/mega-code-run-$(date +%Y%m%d-%H%M%S).log"
echo "MEGA-Code pipeline log: $LOG"
export CLAUDE_PROJECT_DIR="$PWD" && uv run --directory "$MEGA_DIR" python scripts/run_pipeline_async.py --project --model gpt-5-mini 2>&1 | tee "$LOG"

# Run on a specific session
LOG="/tmp/mega-code-run-$(date +%Y%m%d-%H%M%S).log"
echo "MEGA-Code pipeline log: $LOG"
export CLAUDE_PROJECT_DIR="$PWD" && cd "$MEGA_DIR" && . ./.env && uv run python scripts/run_pipeline_async.py --session-id <uuid> 2>&1 | tee "$LOG"

# Run on current project (Claude conversations only)
LOG="/tmp/mega-code-run-$(date +%Y%m%d-%H%M%S).log"
echo "MEGA-Code pipeline log: $LOG"
export CLAUDE_PROJECT_DIR="$PWD" && cd "$MEGA_DIR" && . ./.env && uv run python scripts/run_pipeline_async.py --project --include-claude 2>&1 | tee "$LOG"

# Run on current project (Codex conversations only)
LOG="/tmp/mega-code-run-$(date +%Y%m%d-%H%M%S).log"
echo "MEGA-Code pipeline log: $LOG"
export CLAUDE_PROJECT_DIR="$PWD" && cd "$MEGA_DIR" && . ./.env && uv run python scripts/run_pipeline_async.py --project --include-codex 2>&1 | tee "$LOG"

# Run on current project (both Claude and Codex)
LOG="/tmp/mega-code-run-$(date +%Y%m%d-%H%M%S).log"
echo "MEGA-Code pipeline log: $LOG"
export CLAUDE_PROJECT_DIR="$PWD" && cd "$MEGA_DIR" && . ./.env && uv run python scripts/run_pipeline_async.py --project --include-claude --include-codex 2>&1 | tee "$LOG"

# Run on current project (all sources)
LOG="/tmp/mega-code-run-$(date +%Y%m%d-%H%M%S).log"
echo "MEGA-Code pipeline log: $LOG"
export CLAUDE_PROJECT_DIR="$PWD" && cd "$MEGA_DIR" && . ./.env && uv run python scripts/run_pipeline_async.py --project --include-all 2>&1 | tee "$LOG"
```

**Log file**: All output goes to `/tmp/mega-code-run-YYYYMMDD-HHMMSS.log`.
Tell the user the log path so they can monitor progress with `tail -f <log>` or
check results after completion.

## Model Options

The `--model` flag accepts any model alias supported by the LLM module:

| Alias | Provider |
|-------|----------|
| `gemini-3-flash` | Google |
| `gpt-5-mini` | OpenAI |

When `--model` is omitted, the server selects the best model based on your configured LLM keys (priority: OpenAI > Anthropic > Gemini). If no user keys are configured, defaults to `gemini-3-flash`.

## Pipeline Outputs

The pipeline generates two types of output:

1. **Skills & Strategies** — saved to pending dirs for review/install
2. **Lesson Learned documents** — personalized learning docs saved to
   `~/.local/mega-code/lessons-learned/` (generated from sessions tagged lesson_learn)

## Post-Pipeline Workflow (MANDATORY)

When the pipeline command completes, it prints a JSON object to stdout:
```json
{"additionalContext": "...notification with mandatory workflow..."}
```

You MUST parse the `additionalContext` value and follow the embedded workflow
IMMEDIATELY. The workflow instructs you to review and install pending items.
Do NOT just report "pipeline complete" — you must execute the full workflow.

### Step-by-step:

1. **Read & Analyze** — Use Read tool to read each pending item listed in the
   notification. Analyze quality, clarity, completeness. Do this silently.

2. **Present Review** — Show each item to the user with:
   - Summary, quality assessment, suggested improvements
   - Enhanced version if improvements are needed

3. **Ask User** — Use AskUserQuestion with multiple questions:
   - Which items to install (multiSelect: true)
   - Which version (Enhanced/Original)
   - Installation location: project level (`.claude/skills/`) or user level (`~/.claude/skills/`)

4. **Install** — Write approved items to chosen locations:
   - Skills → `<location>/skills/<name>/SKILL.md`
   - Strategies → `.claude/rules/mega-code/<name>.md`

5. **Archive** — Run this command to archive (not delete) pending items:

```bash
MEGA_DIR="${CLAUDE_PLUGIN_ROOT:-$(cat ~/.local/mega-code/plugin-root 2>/dev/null)}"
cd "$MEGA_DIR" && set -a && . ./.env && set +a && uv run python -c "
from mega_code.client.feedback import archive_pending_items
from mega_code.client.pending import get_pending_skills, get_pending_strategies
skills = get_pending_skills()
strategies = get_pending_strategies()
installed_names = set()  # <-- fill with names of installed items
run_id = archive_pending_items(
    run_id='<RUN_ID>',
    project_id='<PROJECT_ID>',
    installed_skills=[s for s in skills if s.name in installed_names],
    skipped_skills=[s for s in skills if s.name not in installed_names],
    installed_strategies=[s for s in strategies if s.name in installed_names],
    skipped_strategies=[s for s in strategies if s.name not in installed_names],
)
print(f'ARCHIVED_RUN_ID={run_id}')
"
```

6. **Collect Feedback** — Use AskUserQuestion to ask:
   - Quality rating (Excellent/Good/Mixed/Poor)
   - What could be improved (multiSelect)
   - Additional comments

   Then save feedback:
```bash
MEGA_DIR="${CLAUDE_PLUGIN_ROOT:-$(cat ~/.local/mega-code/plugin-root 2>/dev/null)}"
cd "$MEGA_DIR" && set -a && . ./.env && set +a && \
  uv run python -m mega_code.client.feedback_cli \
  --run-id '<RUN_ID>' \
  --project '<PROJECT_ID>' \
  --overall-quality <quality> \
  --comments "<text or empty>"
```
