# MEGA-Code (Open Source Edition)

An open-source Claude Code plugin that collects interaction data, extracts
reusable skills, and optimizes AI workflows.

## Quick Start

### Install via Claude Code Plugin Marketplace

Inside a Claude Code session, run:

```
/plugin marketplace add git@bitbucket.org:mindai/mega-code-oss.git
/plugin install mega-code@wisdomgraph-mega-code
```

That's it — the plugin's slash commands are immediately available.

### Update to Latest Version

```
/plugin marketplace update wisdomgraph-mega-code
```

### First Steps After Install

Sign in to MEGA-Code to get an API key:

```
/mega-code:login
```

This opens a browser-based OAuth flow (GitHub or Google). Once signed in,
your API key is saved automatically.

### Available Slash Commands

| Command | Description |
|---------|-------------|
| `/mega-code:login` | Sign in via GitHub or Google OAuth |
| `/mega-code:run` | Run skill extraction pipeline |
| `/mega-code:status` | Show pending items and status |
| `/mega-code:feedback` | Provide feedback on generated items |
| `/mega-code:help` | Show help and reference |

### Example Usage

```
# In a Claude Code session:
/mega-code:login                  # Sign in and get API key (first time)
/mega-code:run --project          # Extract skills from all project sessions
/mega-code:status                 # See what was generated
/mega-code:feedback               # Rate the generated skills
```

## Development Setup (from main repo)

If you are developing from the main `mega-code` repository (which includes this
as a submodule), use the sync script to test changes without committing:

```bash
# From the main mega-code repo root:
bash scripts/setup-oss-test.sh

# This syncs skills/, hooks/, client code, and installs deps.
# Then test locally with:
claude --plugin-dir mega-code-oss/plugin
```

The sync script copies the latest code from the main repo into this submodule
so you can iterate quickly without any git commits to GitHub.

## Project Structure

```
mega-code-oss/
├── .claude-plugin/
│   └── marketplace.json     # Marketplace listing (source: ./plugin)
├── plugin/                  # Plugin root (installed by Claude Code)
│   ├── .claude-plugin/
│   │   └── plugin.json      # Plugin metadata
│   ├── hooks/
│   │   └── hooks.json       # Lifecycle hooks (SessionStart, etc.)
│   ├── skills/
│   │   ├── login/SKILL.md   # /mega-code:login
│   │   ├── run/SKILL.md     # /mega-code:run
│   │   ├── status/SKILL.md  # /mega-code:status
│   │   ├── feedback/SKILL.md
│   │   └── help/SKILL.md    # /mega-code:help
│   ├── mega_code/
│   │   └── client/          # Python client modules
│   ├── scripts/
│   │   └── session-start.sh # Bootstrap script
│   └── pyproject.toml
└── README.md
```

## Configuration

Configuration is stored in `~/.local/mega-code/` and persists across sessions.
Use `/mega-code:login` to authenticate, or `mega-code configure` CLI for advanced settings.

## License

Apache-2.0
