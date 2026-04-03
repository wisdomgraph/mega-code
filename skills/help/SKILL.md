---
name: mega-code-help
description: "Show MEGA-Code help — available commands, output locations, and usage tips."
allowed-tools: Read
---

# MEGA-Code Help

## Available Commands

| Command | Description |
|---------|-------------|
| `$mega-code-login` | Sign in via GitHub or Google OAuth |
| `$mega-code-wisdom-gen` | Run skill extraction pipeline |
| `$mega-code-status` | Show pending items and status |
| `$mega-code-profile` | View or update developer profile |
| `$mega-code-stop` | Stop a running pipeline |
| `$mega-code-skill-enhance` | Evaluate and enhance a skill with A/B testing |
| `$mega-code-update` | Sync installed skills with latest repo version and install new ones |
| `$mega-code-help` | Show this help |

## Output Locations

| Type | Pending Location | Installed Location |
|------|------------------|--------------------|
| Skills | `~/.local/share/mega-code/data/pending-skills/{name}/` | `.agents/skills/{name}/SKILL.md` |
| Strategies | `~/.local/share/mega-code/data/pending-strategies/{name}.md` | `.agents/rules/mega-code/{name}.md` + referenced in `AGENTS.md` |

## Skill Structure

Generated skills follow this structure:

```
~/.local/share/mega-code/data/pending-skills/{skill-name}/
├── SKILL.md        # Main skill content
├── injection.json  # Auto-trigger rules
├── evidence.json   # Source evidence
└── metadata.json   # Generation info
```

## Strategy Structure

Strategies are modular rules saved as:

```markdown
---
paths: **/*.py
---

# Strategy Title

Clear statement of the preference or convention.
```

When installed, each strategy is written to `.agents/rules/mega-code/{name}.md` and a reference is added to `AGENTS.md` so Codex automatically loads the strategy when its described context applies to the current task.

## Model Options

The `--model` flag for `$mega-code-wisdom-gen` accepts any model alias supported by the LLM module:

| Alias | Provider |
|-------|----------|
| `gemini-3-flash` | Google |
| `gpt-5-mini` | OpenAI |

When no model is specified, the server selects the best model based on your configured LLM keys.

## Tips

- Run `$mega-code-wisdom-gen` after significant coding sessions
- Run `$mega-code-skill-enhance <skill>` to improve an existing skill and review ROI
- Use `--project` to analyze multiple sessions for stronger patterns
- Use `@name` to run on a different project without switching directories
- Skills with more evidence (from multiple sessions) are higher quality
- Review and edit skills before installing for best results
- Run `$mega-code-update` to sync installed skills with the latest repo version and install new ones
