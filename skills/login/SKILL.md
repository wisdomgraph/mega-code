---
description: Sign in to MEGA-Code via GitHub or Google OAuth to get an API key.
argument-hint: [--provider github|google]
allowed-tools: Bash, Read, AskUserQuestion
---

# Login to MEGA-Code

Authenticate with MEGA-Code to obtain an API key using a two-step OAuth flow.

## Setup

```bash
MEGA_DIR="${CLAUDE_PLUGIN_ROOT:-$(cat ~/.local/mega-code/plugin-root 2>/dev/null)}"
```

## Step 1: Create session (fast, non-blocking)

```bash
uv run --directory "$MEGA_DIR" python -m mega_code.client.login --step create
```

Add `--provider github` for GitHub OAuth instead of Google.

Returns a **JSON object** to stdout:

```json
{"login_url": "https://...", "client_id": "abc-123", "base_url": "https://..."}
```

On error, the JSON has an `error` field instead.

**After getting the JSON:**
1. Parse the output as JSON
2. Show `login_url` to the user — tell them to open it in their browser
3. Save `client_id` and `base_url` for Step 2

## Step 2: Poll for completion (run in background)

```bash
uv run --directory "$MEGA_DIR" python -m mega_code.client.login \
  --step poll --client-id CLIENT_ID --url BASE_URL
```

Replace `CLIENT_ID` and `BASE_URL` with values from Step 1.
Run this **in the background** so the user is not blocked.

On success, saves to `~/.local/mega-code/.env` (stable, version-independent):
- `MEGA_CODE_API_KEY`, `MEGA_CODE_CLIENT_MODE=remote`, `MEGA_CODE_SERVER_URL`
- Prints "Login successful!" and exits

Polls every 3s, times out after 10 minutes.

## Verify

Credentials are stored in the stable data directory, not the versioned plugin dir.
Do **not** print the raw API key — mask it.

```bash
grep -E "MEGA_CODE_(API_KEY|CLIENT_MODE|SERVER_URL)" "$HOME/.local/mega-code/.env" \
  | sed -E 's/(MEGA_CODE_API_KEY=.{6}).*/\1***/'
```

## Troubleshooting

- **Timeout**: Session expires after 10 min. Re-run the command.
- **Connection error**: Check `MEGA_CODE_SERVER_URL` in `~/.local/mega-code/.env`.
- **Already logged in**: Running login again replaces the existing key.
