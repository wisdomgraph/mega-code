---
description: Provide feedback on generated skills and strategies from previous pipeline runs. Discovers archived runs and collects structured ratings.
argument-hint: [--run-id <id>] [--project <id>]
allowed-tools: Bash, Read, AskUserQuestion
disable-model-invocation: true
---

# MEGA-Code Feedback

Provide feedback on generated skills and strategies.

## Setup

```bash
MEGA_DIR="${CLAUDE_PLUGIN_ROOT:-$(cat ~/.local/mega-code/plugin-root 2>/dev/null)}"
```

## Usage

- **Without --run-id**: Finds the most recent run without feedback
- **With --run-id + --project**: Feedback for a specific archived run

## Workflow

1. **Discover runs**: Find archived runs in `~/.local/mega-code/data/feedback/{project_id}/`
2. **Show items**: List skills and strategies with their install/skip status
3. **Collect feedback**: Ask type-specific rating questions per item
4. **Save**: Write feedback.json alongside the archived run data

```bash
# List recent runs and their feedback status
set -a && . "$MEGA_DIR/.env" 2>/dev/null && set +a && \
  uv run --directory "$MEGA_DIR" python -c "
from mega_code.client.feedback import get_recent_runs
for r in get_recent_runs(limit=5):
    fb = '✓ has feedback' if r.has_feedback else '✗ no feedback'
    items = len(r.skills) + len(r.strategies)
    print(f'  {r.run_id} [{r.project_id}]: {items} items ({fb})')
"
```

If runs are found without feedback, present items and use AskUserQuestion
with type-specific questions:

**For each SKILL item**, ask:
- Focus (1-5): How focused on a single tool/workflow?
- Accuracy (1-5): Are instructions and examples correct?
- Completeness (1-5): Does it cover key use cases?
- Conciseness (1-5): Appropriately concise?
- Clarity (1-5): Clear and well-structured?
- Useful? (yes/no/maybe): Would you use this skill?
- Reason: Why or why not?

**For each STRATEGY item**, ask:
- Accuracy (1-5): Is this rule/preference correct?
- Relevance (1-5): Relevant to your workflow?
- Specificity (1-5): Specific enough to be actionable?
- Useful? (yes/no/maybe): Would you use this strategy?
- Reason: Why or why not?
- Correction (optional): How should it be corrected?

Save feedback:

```bash
set -a && . "$MEGA_DIR/.env" 2>/dev/null && set +a && \
  uv run --directory "$MEGA_DIR" python -m mega_code.client.feedback_cli \
  --run-id <RUN_ID> --project <PROJECT_ID> \
  --overall-quality <quality> --item-ratings '<JSON_RATINGS>' \
  --comments "<text or empty>"
```
