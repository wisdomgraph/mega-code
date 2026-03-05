# MEGA-Code 1.0.0-beta — Open Source Edition

An open-source Claude Code plugin that collects interaction data, extracts
reusable skills, and optimizes AI workflows.

## Quick Start

### Install via Claude Code Plugin Marketplace

Inside a Claude Code session, run:

**Step 1 — Add the repository to your marketplace:**

```
/plugin marketplace add https://github.com/wisdomgraph/mega-code.git
```

**Step 2 — Install the plugin:**

```
/plugin install mega-code@mind-ai-mega-code
```

Then restart Claude Code to load the plugin. The slash commands will be available in your next session.

### Update to Latest Version

```
/plugin marketplace update mind-ai-mega-code
```

### First Steps After Install

**Step 1 — Sign in:**

```
/mega-code:login
```

This opens a browser-based OAuth flow (GitHub or Google). Once signed in,
your API key is saved automatically.

**Step 2 — Add your own LLM API key (required):**

MEGA-Code uses a **Bring Your Own Key (BYOK)** model — you supply your own
OpenAI or Gemini key. The server never holds platform-level credentials.

Visit the web UI and add your key under **Account → API Keys**:

👉 **[https://megacode.ai](https://megacode.ai)**

Supported providers: **OpenAI** (`OPENAI_API_KEY`) and **Google Gemini** (`GEMINI_API_KEY`).

> Without a key registered, `/mega-code:run` will prompt you to add one at
> [https://megacode.ai](https://megacode.ai).

### Available Slash Commands

| Command | Description |
|---------|-------------|
| `/mega-code:login` | Sign in via GitHub or Google OAuth |
| `/mega-code:run` | Run skill extraction pipeline |
| `/mega-code:status` | Show pending items and status |
| `/mega-code:profile` | View or update your developer profile (language, level, style) |
| `/mega-code:help` | Show help and reference |

### Example Usage

```
# In a Claude Code session:
/mega-code:login                  # Sign in and get API key (first time)
/mega-code:profile                # Set your language, level, and style
/mega-code:run --project          # Extract skills from all project sessions
/mega-code:status                 # See what was generated
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
│   │   ├── login/SKILL.md    # /mega-code:login
│   │   ├── run/SKILL.md      # /mega-code:run
│   │   ├── status/SKILL.md   # /mega-code:status
│   │   ├── profile/SKILL.md  # /mega-code:profile
│   │   └── help/SKILL.md     # /mega-code:help
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

## Terms of Service

This plugin requires use of the Mega Code API.

By using this plugin, you agree to:

https://megacode.ai/terms

## License

Apache-2.0
