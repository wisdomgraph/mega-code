---
description: Show MEGA-Code help — available commands, output locations, skill and strategy structure, and usage tips.
argument-hint: ""
allowed-tools: Read
---

# MEGA-Code Help

## Available Commands

| Command | Description |
|---------|-------------|
| `/mega-code:run` | Run skill extraction pipeline |
| `/mega-code:status` | Show pending items and status |
| `/mega-code:feedback` | Provide feedback on generated items |
| `/mega-code:manage upload` | Upload session data to server |
| `/mega-code:manage update` | Update to latest version |
| `/mega-code:manage config` | View/modify configuration |
| `/mega-code:manage profile` | Set up developer profile |
| `/mega-code:manage uninstall` | Remove MEGA-Code |
| `/mega-code:help` | Show this help |

## Output Locations

| Type | Pending Location | Installed Location |
|------|------------------|-------------------|
| Skills | `~/.local/mega-code/data/pending-skills/{name}/` | `.claude/skills/{name}/SKILL.md` |
| Strategies | `~/.local/mega-code/data/pending-strategies/{name}.md` | `.claude/rules/mega-code/{name}.md` |

## Skill Structure

Generated skills follow this structure:

```
~/.local/mega-code/data/pending-skills/{skill-name}/
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

The `--model` flag for `/mega-code:run` accepts any model alias supported by the LLM module:

| Alias | Provider |
|-------|----------|
| `gemini-3-flash` | Google (default) |
| `gpt-5-mini` | OpenAI |
| `claude-sonnet-4-5` | Anthropic |

## Tips

- Run `/mega-code:run` after significant coding sessions
- Use `--project` to analyze multiple sessions for stronger patterns
- Use `@name` to run on a different project without switching directories
- Skills with more evidence (from multiple sessions) are higher quality
- Review and edit skills before installing for best results
- Use `/mega-code:manage upload` to share session data for server-side processing
- Run `/mega-code:manage update` periodically to get the latest pipeline improvements
- Use `/mega-code:manage config` to set up credentials before first upload
