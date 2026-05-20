#!/usr/bin/env bash
# Wisdom-curate setup: source env, verify auth, generate session id, and
# resolve data/skills dirs. Prints MEGA_DIR, SESSION_ID, DATA_DIR, SKILLS_DIR
# on stdout for the caller to remember and substitute literally into later
# bash blocks (each Bash tool call is a fresh shell, so shell variables do
# not persist across blocks — literal substitution is the cheapest transport).
#
# Usage: bash setup.sh
# Optional env: CLAUDE_SESSION_ID — used as session id when present.
#
# Self-locating: derives MEGA_DIR from its own path (BASH_SOURCE) so it
# works regardless of caller env. Layout: MEGA_DIR/skills/wisdom-curate/scripts/setup.sh
#
# Exits non-zero if auth check fails.

set -eu

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MEGA_DIR="$(cd "$SCRIPT_DIR/../../.." && pwd)"

uv run --directory "$MEGA_DIR" python -m mega_code.client.check_auth

SESSION_ID="${CLAUDE_SESSION_ID:-$(uuidgen 2>/dev/null || python3 -c 'import uuid;print(uuid.uuid4())')}"

DATA_DIR=$(uv run --directory "$MEGA_DIR" python -c "from mega_code.client.dirs import data_dir; print(data_dir())")
SKILLS_DIR=$(uv run --directory "$MEGA_DIR" python -c "from mega_code.client.skill_installer import skills_dir; print(skills_dir())")

echo "MEGA_DIR=$MEGA_DIR"
echo "SESSION_ID=$SESSION_ID"
echo "DATA_DIR=$DATA_DIR"
echo "SKILLS_DIR=$SKILLS_DIR"
