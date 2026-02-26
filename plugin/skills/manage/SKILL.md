---
description: Manage MEGA-Code installation — upload session data, update to latest version, configure credentials, set up developer profile, or uninstall.
argument-hint: <upload|update|config|profile|uninstall> [options]
allowed-tools: Bash, Read, Write, AskUserQuestion
disable-model-invocation: true
---

# Manage MEGA-Code

Administrative commands for MEGA-Code: upload data, update, configure, profile, and uninstall.

## Finding the MEGA-Code Directory

```bash
# Discover mega-code root (marketplace or symlink install)
MEGA_DIR="$(cat ~/.local/mega-code/plugin-root 2>/dev/null || echo $HOME/.claude/mega-code)"
```

Then use `uv run --directory "$MEGA_DIR"` for all subsequent commands.

## Upload Session Data

Upload session data to Bitbucket Downloads for server-side processing.

- **Without --project**: Upload all project data
- **With --project /path**: Upload data for a specific project only
- Creates a timestamped tar.gz archive with user ID and hostname
- Requires `MEGA_CODE_USER_ID` and `BITBUCKET_ACCESS_TOKEN` in .env

```bash
MEGA_DIR="$(cat ~/.local/mega-code/plugin-root 2>/dev/null || echo $HOME/.claude/mega-code)"

# Upload all project data
uv run --directory "$MEGA_DIR" mega-code upload

# Upload data for a specific project
uv run --directory "$MEGA_DIR" mega-code upload --project /path/to/project
```

If credentials are missing, run `/mega-code:manage config` first to set them up.

## Update

Update MEGA-Code to the latest version from the remote repository.

- Pulls latest code from git origin
- Re-runs the full installation process
- Only works for remote installs (where `~/.mega-code/repo` exists)
- For marketplace installs, use `/plugin marketplace update wisdomgraph-mega-code`
- For local development installs, use `git pull` in the source repo instead

```bash
MEGA_DIR="$(cat ~/.local/mega-code/plugin-root 2>/dev/null || echo $HOME/.claude/mega-code)"

# Update to latest version
uv run --directory "$MEGA_DIR" mega-code update
```

**Note**: This modifies the installation. After update, restart your Claude Code
session to pick up any new hooks or statusline changes.

## Configure

View and modify MEGA-Code configuration.

```bash
MEGA_DIR="$(cat ~/.local/mega-code/plugin-root 2>/dev/null || echo $HOME/.claude/mega-code)"

# Show current configuration
uv run --directory "$MEGA_DIR" mega-code configure

# Set user ID
uv run --directory "$MEGA_DIR" mega-code configure --user-id <your-name>

# Set Bitbucket access token (for upload/update)
uv run --directory "$MEGA_DIR" mega-code configure --bitbucket-token <token>

# Set OpenAI API key (for gpt-5-mini, etc.)
uv run --directory "$MEGA_DIR" mega-code configure --openai-api-key <key>

# Set Gemini API key (for gemini-3-flash, etc.)
uv run --directory "$MEGA_DIR" mega-code configure --gemini-api-key <key>

# Set multiple values at once
uv run --directory "$MEGA_DIR" mega-code configure --openai-api-key <key> --gemini-api-key <key>
```

Configuration is stored in the `.env` file at the mega-code source directory.
Sensitive values (tokens, keys) are displayed as `***` in status output.

## Developer Profile

Set up your developer profile to personalize skill extraction. Profile determines
which skills are too basic for your experience level.

**Interactive setup (recommended):**
Ask the user for their profile using AskUserQuestion with these fields:
- **language**: Preferred communication language — options: `English`, `Korean`, `Thai` (user can also type a custom language via "Other")
- **level**: `Beginner`, `Intermediate`, or `Expert`
- **style**: `Mentor`, `Formal`, or `Concise` (reserved for future use)

Then save using the CLI:

```bash
MEGA_DIR="$(cat ~/.local/mega-code/plugin-root 2>/dev/null || echo $HOME/.claude/mega-code)"
uv run --directory "$MEGA_DIR" mega-code profile --language "<language>" --level <level> --style <style>
```

**Show current profile:**
```bash
MEGA_DIR="$(cat ~/.local/mega-code/plugin-root 2>/dev/null || echo $HOME/.claude/mega-code)"
uv run --directory "$MEGA_DIR" mega-code profile
```

**Reset profile:**
```bash
MEGA_DIR="$(cat ~/.local/mega-code/plugin-root 2>/dev/null || echo $HOME/.claude/mega-code)"
uv run --directory "$MEGA_DIR" mega-code profile --reset
```

Profile is stored at `~/.local/mega-code/profile.json` and used by the pipeline
to filter out skills too basic for the user's experience level.

## Uninstall

Remove MEGA-Code from the system.

- Removes the symlink (`~/.claude/mega-code`)
- Removes hooks from `~/.claude/settings.json`
- Removes the MEGA-Code skills
- **Preserves** all session data in `~/.local/mega-code/`

For marketplace installs, use `/plugin uninstall mega-code@wisdomgraph-mega-code` instead.

```bash
MEGA_DIR="$(cat ~/.local/mega-code/plugin-root 2>/dev/null || echo $HOME/.claude/mega-code)"
uv run --directory "$MEGA_DIR" mega-code uninstall
```

**Warning**: This will remove the MEGA-Code slash commands. To reinstall,
you'll need to run the install script again or use `mega-code install` from the
source repository.
