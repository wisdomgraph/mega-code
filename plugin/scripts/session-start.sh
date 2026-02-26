#!/bin/bash
# MEGA-Code SessionStart hook
#
# Runs on every Claude Code session start. Handles:
#   1. Bootstrap uv package manager if not installed
#   2. Write plugin-root breadcrumb file
#   3. Initialize profile.json with empty defaults
#   4. Ensure Python environment is ready (uv sync on first run)
#   5. Run the session collector
#
# Called from hooks/hooks.json with ${CLAUDE_PLUGIN_ROOT} set by Claude Code.

set -euo pipefail

MEGA_DIR="${CLAUDE_PLUGIN_ROOT}"
DATA_DIR="$HOME/.local/mega-code"

# ── 1. Bootstrap uv if not available ──────────────────────────────────
if ! command -v uv &>/dev/null; then
    # Check common install locations first
    for candidate in "$HOME/.local/bin/uv" "$HOME/.cargo/bin/uv"; do
        if [ -x "$candidate" ]; then
            export PATH="$(dirname "$candidate"):$PATH"
            break
        fi
    done
fi

if ! command -v uv &>/dev/null; then
    curl -LsSf https://astral.sh/uv/install.sh | sh 2>/dev/null
    export PATH="$HOME/.local/bin:$PATH"
fi

# ── 2. Plugin root breadcrumb ─────────────────────────────────────────
mkdir -p "$DATA_DIR"
echo "$MEGA_DIR" > "$DATA_DIR/plugin-root"

# ── 3. Initialize profile.json if absent ──────────────────────────────
if [ ! -f "$DATA_DIR/profile.json" ]; then
    cat > "$DATA_DIR/profile.json" << 'EOF'
{
  "api_key": "",
  "server_url": "",
  "client_mode": "local",
  "user_id": ""
}
EOF
fi

# ── 4. Ensure .env exists (zsh returns 127 when sourcing a missing file) ──
if [ ! -f "$MEGA_DIR/.env" ]; then
    touch "$MEGA_DIR/.env"
fi

# ── 5. Ensure Python environment is ready (first-run only) ────────────
# Check for the actual python binary, not just .venv directory — the dir
# may exist but be empty (e.g. only .gitignore and .lock from git).
if [ ! -x "$MEGA_DIR/.venv/bin/python" ]; then
    uv sync --directory "$MEGA_DIR" --quiet 2>/dev/null || true
fi

# ── 6. Run collector ──────────────────────────────────────────────────
uv run --directory "$MEGA_DIR" python mega_code/client/collector.py --event SessionStart
