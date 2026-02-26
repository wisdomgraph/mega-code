---
description: Sign in to MEGA-Code via GitHub or Google OAuth to get an API key.
argument-hint: [--provider github|google]
allowed-tools: Bash, Read, AskUserQuestion
---

# Login to MEGA-Code

Authenticate with MEGA-Code to obtain an API key. Uses a two-step flow:
first creates a session and shows a URL, then polls in background for completion.

## Finding the MEGA-Code Directory

```bash
# Discover mega-code root (marketplace or symlink install)
MEGA_DIR="$(cat ~/.local/mega-code/plugin-root 2>/dev/null || echo $HOME/.claude/mega-code)"
```

## Usage (Two-Step Flow)

### Step 1: Create session and get the login URL (fast, non-blocking)

```bash
MEGA_DIR="$(cat ~/.local/mega-code/plugin-root 2>/dev/null || echo $HOME/.claude/mega-code)"
set -a && . "$MEGA_DIR/.env" 2>/dev/null && set +a && \
  uv run --directory "$MEGA_DIR" python -m mega_code.client.login --step create
```

This prints a **JSON object** to stdout:

```json
{"login_url": "https://...", "client_id": "abc-123", "base_url": "https://..."}
```

If there's an error, the JSON will have an `error` field instead.

**After getting the JSON:**
1. Parse the output as JSON
2. Show the `login_url` to the user — tell them to open it in their browser
3. Save `client_id` and `base_url` for Step 2

### Step 2: Poll for completion (run in background)

```bash
MEGA_DIR="$(cat ~/.local/mega-code/plugin-root 2>/dev/null || echo $HOME/.claude/mega-code)"
set -a && . "$MEGA_DIR/.env" 2>/dev/null && set +a && \
  uv run --directory "$MEGA_DIR" python -m mega_code.client.login \
    --step poll --client-id CLIENT_ID --url BASE_URL
```

Replace `CLIENT_ID` and `BASE_URL` with values from Step 1's JSON output.
Run this command **in the background** so the user is not blocked.

When the user completes login in their browser, this command will:
- Save `MEGA_CODE_API_KEY` to `.env`
- Set `MEGA_CODE_CLIENT_MODE=remote`
- Set `MEGA_CODE_SERVER_URL` (derived from the base URL)
- Print "Login successful!" and exit

### With GitHub Provider

Add `--provider github` to the create step:

```bash
MEGA_DIR="$(cat ~/.local/mega-code/plugin-root 2>/dev/null || echo $HOME/.claude/mega-code)"
set -a && . "$MEGA_DIR/.env" 2>/dev/null && set +a && \
  uv run --directory "$MEGA_DIR" python -m mega_code.client.login --step create --provider github
```

## What Happens

1. **Step 1 (create)**: Creates an auth session with mega-service, returns JSON with login URL
2. **User action**: User opens the URL in their browser and signs in
3. **Step 2 (poll)**: Polls every 3 seconds until sign-in completes (up to 10 min)
4. **On success**, saves to the plugin `.env` file:
   - `MEGA_CODE_API_KEY` — the new API key
   - `MEGA_CODE_CLIENT_MODE=remote` — switches to remote mode
   - `MEGA_CODE_SERVER_URL` — derived from the mega-service URL

## After Login

Verify the config is saved:

```bash
MEGA_DIR="$(cat ~/.local/mega-code/plugin-root 2>/dev/null || echo $HOME/.claude/mega-code)"
grep -E "MEGA_CODE_(API_KEY|CLIENT_MODE|SERVER_URL)" "$MEGA_DIR/.env"
```

## Troubleshooting

- **Timeout**: The session expires after 10 minutes. Just re-run the command.
- **Connection error**: Check that `MEGA_SERVICE_URL` in `.env` is correct.
- **Already logged in**: Running login again replaces the existing API key.
