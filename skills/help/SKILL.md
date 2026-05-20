---
description: Show MEGA-Code help — available commands, output locations, skill and strategy structure, and usage tips.
argument-hint: ""
allowed-tools: Read
---

# MEGA-Code Help

## Available Commands

| Command | Description |
|---------|-------------|
| `/mega-code:login` | Sign in via GitHub or Google OAuth |
| `/mega-code:wisdom-gen` | Run skill extraction pipeline |
| `/mega-code:skill-enhance` | Enhance a skill (remote server by default; pass `--hitl` for local human-in-the-loop A/B) |
| `/mega-code:wisdom-curate` | Curate a wisdom-backed workflow with skill installation |
| `/mega-code:status` | Show pending items and status |
| `/mega-code:stop` | Stop a running pipeline |
| `/mega-code:profile` | View or update your developer profile |
| `/mega-code:help` | Show this help |

## Output Locations

| Type | Pending Location | Installed Location |
|------|------------------|-------------------|
| Skills | `~/.local/share/mega-code/data/pending-skills/{name}/` | `.claude/skills/{name}/SKILL.md` |
| Strategies | `~/.local/share/mega-code/data/pending-strategies/{name}.md` | `.claude/rules/mega-code/{name}.md` |
| Curated Skills | — | `{data_dir}/skills/{name}/SKILL.md` |
| Curations | `{data_dir}/curations/pending/{session_id}.json` | `{data_dir}/curations/completed/{session_id}.json` |

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

## Model Options

The `--model` flag for `/mega-code:wisdom-gen` accepts any model alias supported by the LLM module:

| Alias | Provider |
|-------|----------|
| `gemini-3-flash` | Google |
| `gpt-5-mini` | OpenAI |

When no model is specified, the server selects the best model based on your configured LLM keys.


## Tips

  - Run /mega-code:wisdom-gen after significant coding sessions
  - Run /mega-code:skill-enhance <skill> to enhance a skill via the remote server (requires `MEGA_CODE_CLIENT_MODE=remote`); add `--hitl` to use the local human-in-the-loop A/B flow instead
  - Use --project to analyze multiple sessions for stronger patterns
  - Skills with more evidence (from multiple sessions) are higher quality
  - Review and edit skills before installing for best results
  - Run /mega-code:wisdom-curate <task description> to get a curated workflow with relevant skills
  - Curated workflows can be executed immediately or saved for later resumption
  - For plugin updates, use `/plugin marketplace update mind-ai-mega-code`
