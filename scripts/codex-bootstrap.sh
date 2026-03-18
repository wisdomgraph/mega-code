#!/bin/bash
# MEGA-Code Codex Bootstrap
#
# Lazy bootstrap for Codex CLI — replaces session-start.sh.
# Called from each Codex skill:
#   bash "$MEGA_DIR/scripts/codex-bootstrap.sh" "$MEGA_DIR"
#
# Idempotent: uses a breadcrumb file to skip on subsequent runs.

set -euo pipefail

MEGA_DIR="${1:?Usage: codex-bootstrap.sh <MEGA_DIR>}"
DATA_DIR="${MEGA_CODE_DATA_DIR:-$HOME/.local/share/mega-code}"
BREADCRUMB="$DATA_DIR/pkg-breadcrumb"

# ── Fast path: already bootstrapped ──────────────────────────────────
if [ -f "$BREADCRUMB" ]; then
    saved="$(cat "$BREADCRUMB")"
    [ -d "$saved" ] && exit 0
fi

# ── 1. Bootstrap uv if not available ─────────────────────────────────
if ! command -v uv &>/dev/null; then
    for candidate in "$HOME/.local/bin/uv" "$HOME/.cargo/bin/uv"; do
        if [ -x "$candidate" ]; then
            export PATH="$(dirname "$candidate"):$PATH"
            break
        fi
    done
fi

if ! command -v uv &>/dev/null; then
    curl -LsSf https://astral.sh/uv/install.sh | sh || echo "WARNING: uv install failed — some features may be unavailable"
    export PATH="$HOME/.local/bin:$PATH"
fi

# ── 2. Create data dir ────────────────────────────────────────────────
mkdir -p "$DATA_DIR"

# ── 3. Initialize profile.json if absent ─────────────────────────────
if [ ! -f "$DATA_DIR/profile.json" ]; then
    printf '{}' > "$DATA_DIR/profile.json"
fi

# ── 4. Ensure stable credential store exists ─────────────────────────
if [ ! -f "$DATA_DIR/.env" ]; then
    if [ -f "$MEGA_DIR/.env" ] && grep -q "MEGA_CODE_API_KEY" "$MEGA_DIR/.env" 2>/dev/null; then
        cp "$MEGA_DIR/.env" "$DATA_DIR/.env"
        chmod 0600 "$DATA_DIR/.env"
    else
        touch "$DATA_DIR/.env"
        chmod 0600 "$DATA_DIR/.env"
    fi
fi

# ── 5. Ensure Python environment is ready ────────────────────────────
if [ ! -x "$MEGA_DIR/.venv/bin/python" ]; then
    uv sync --directory "$MEGA_DIR" --quiet 2>/dev/null || true
fi

# ── 6. Write breadcrumb (stores MEGA_DIR path for fast-path check) ───
echo "$MEGA_DIR" > "$BREADCRUMB"

# ── 7. Write plugin-root breadcrumb (used by skills to locate MEGA_DIR) ──
echo "$MEGA_DIR" > "$DATA_DIR/plugin-root"
