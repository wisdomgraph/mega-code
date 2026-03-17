#!/bin/bash
# MEGA-Code SessionStart hook
#
# Runs on every session start (Claude Code, Gemini CLI, etc.). Handles:
#   1. Bootstrap uv package manager if not installed
#   2. Write plugin-root breadcrumb file
#   3. Initialize profile.json with empty defaults
#   4. Ensure Python environment is ready (uv sync on first run)
#   5. Run the session collector
#
# Called from hooks/hooks.json with the extension path as $1.
# Claude Code passes ${CLAUDE_PLUGIN_ROOT}, Gemini passes ${extensionPath}.

set -euo pipefail

# Resolve MEGA_DIR: argument > CLAUDE_PLUGIN_ROOT > self-locate from $0
if [ -n "${1:-}" ]; then
  MEGA_DIR="$1"
elif [ -n "${CLAUDE_PLUGIN_ROOT:-}" ]; then
  MEGA_DIR="$CLAUDE_PLUGIN_ROOT"
else
  # Self-locate: this script lives in scripts/, so parent dir is MEGA_DIR
  MEGA_DIR="$(cd "$(dirname "$0")/.." && pwd)"
fi
export UV_CACHE_DIR="${UV_CACHE_DIR:-$MEGA_DIR/.uv-cache}"
# Allow tests and CI to override the data directory via MEGA_CODE_DATA_DIR.
DATA_DIR="${MEGA_CODE_DATA_DIR:-$HOME/.local/share/mega-code}"

# ── 0. Migrate legacy data dir (one-time) ──────────────────────────────
# Old versions stored data in ~/.local/mega-code. Move to XDG-compliant
# ~/.local/share/mega-code and leave a symlink for backward compatibility.
LEGACY_DIR="$HOME/.local/mega-code"
if [ -z "${MEGA_CODE_DATA_DIR:-}" ] && [ -d "$LEGACY_DIR" ] && [ ! -L "$LEGACY_DIR" ] && [ ! -d "$DATA_DIR" ]; then
    mkdir -p "$(dirname "$DATA_DIR")"
    mv "$LEGACY_DIR" "$DATA_DIR"
    ln -s "$DATA_DIR" "$LEGACY_DIR"
fi

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
    curl -LsSf https://astral.sh/uv/install.sh | sh || echo "WARNING: uv install failed — some features may be unavailable"
    export PATH="$HOME/.local/bin:$PATH"
fi

# ── 2. Plugin root breadcrumb ─────────────────────────────────────────
mkdir -p "$DATA_DIR"
echo "$MEGA_DIR" > "$DATA_DIR/plugin-root"

# ── 3. Initialize profile.json if absent ──────────────────────────────
# Write an empty JSON object; UserProfile fields are populated by `mega-code profile`.
if [ ! -f "$DATA_DIR/profile.json" ]; then
    printf '{}' > "$DATA_DIR/profile.json"
fi

# ── 4. Ensure stable credential store exists ──────────────────────────
# Credentials live in ~/.local/share/mega-code/.env (version-independent, survives
# plugin updates — same pattern as AWS ~/.aws/credentials, gh ~/.config/gh/).
# The versioned plugin .env is kept as a non-secret config overlay only.
if [ ! -f "$DATA_DIR/.env" ]; then
    # One-time migration: copy credentials from old versioned location if present.
    if [ -f "$MEGA_DIR/.env" ] && grep -q "MEGA_CODE_API_KEY" "$MEGA_DIR/.env" 2>/dev/null; then
        cp "$MEGA_DIR/.env" "$DATA_DIR/.env"
        chmod 0600 "$DATA_DIR/.env"
    else
        touch "$DATA_DIR/.env"
        chmod 0600 "$DATA_DIR/.env"
    fi
fi
# Ensure the plugin dir still has a (possibly empty) .env so sourcing it is safe.
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
uv run --directory "$MEGA_DIR" python mega_code/client/collector.py --event SessionStart < /dev/null
