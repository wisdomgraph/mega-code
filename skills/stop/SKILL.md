---
name: mega-code-stop
description: "Stop a running MEGA-Code pipeline."
argument-hint: "[run-id]"
allowed-tools: Bash, Read, AskUserQuestion
---

# Stop Pipeline

Stop a currently running MEGA-Code skill extraction pipeline.

## Setup

```bash
MEGA_DIR="${CLAUDE_PLUGIN_ROOT:-$(cat ~/.local/share/mega-code/plugin-root 2>/dev/null)}"
uv run --directory "$MEGA_DIR" python -m mega_code.client.check_auth
```

If the auth check fails (non-zero exit), show the output to the user and stop.

## Workflow

### If run-id argument is provided

Skip to the **Stop** step below using the provided run-id.

### If no run-id argument

**Step 1 — List active runs:**

```bash
uv run --directory "$MEGA_DIR" mega-code pipeline-status
```

If the output says "No active pipeline runs.", tell the user and stop.

**Step 2 — Ask user to confirm:**

Use the `AskUserQuestion` tool to present the active runs and let the user choose.
Always include a cancel option — even if there is only one active run.

Format the question like:

```
Active pipeline runs:

1. {run_id} | project: {project_id} | status: {status}
   Phase: {current_phase} ({sessions_processed}/{sessions_total})

Which run would you like to stop? Select a number, or 0 to cancel.
```

If user selects 0 or cancels, say "Cancelled." and stop.

### Stop

```bash
uv run --directory "$MEGA_DIR" mega-code pipeline-stop --run-id <SELECTED_RUN_ID>
```

Report the result to the user. If successful, confirm:
"Pipeline {run_id} has been stopped."
