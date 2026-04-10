#!/usr/bin/env bash
# Wisdom-curate setup: source env, verify auth, generate session id, and
# resolve data/skills dirs. Prints MEGA_DIR, SESSION_ID, DATA_DIR, SKILLS_DIR
# on stdout for the caller to remember and substitute literally into later
# bash blocks (each Bash tool call is a fresh shell, so shell variables do
# not persist across blocks — literal substitution is the cheapest transport).
#
# Usage: bash setup.sh
# Requires: CLAUDE_SKILL_DIR (set by Claude Code), and optionally
#           CLAUDE_SESSION_ID (used as session id when present).
#
# Exits non-zero if auth check fails.

set -eu

if [ -z "${CLAUDE_SKILL_DIR:-}" ]; then
  echo "setup.sh: CLAUDE_SKILL_DIR is not set — must be invoked from Claude Code skill context" >&2
  exit 1
fi

MEGA_DIR="$(cd "${CLAUDE_SKILL_DIR}/../.." && pwd)"
set -a
[ -f "$MEGA_DIR/.env" ] && . "$MEGA_DIR/.env"
set +a

uv run --directory "$MEGA_DIR" python -m mega_code.client.check_auth

SESSION_ID="${CLAUDE_SESSION_ID:-$(uuidgen 2>/dev/null || python3 -c 'import uuid;print(uuid.uuid4())')}"

DATA_DIR=$(uv run --directory "$MEGA_DIR" python -c "from mega_code.client.dirs import data_dir; print(data_dir())")
SKILLS_DIR=$(uv run --directory "$MEGA_DIR" python -c "from mega_code.client.skill_installer import skills_dir; print(skills_dir())")

echo "MEGA_DIR=$MEGA_DIR"
echo "SESSION_ID=$SESSION_ID"
echo "DATA_DIR=$DATA_DIR"
echo "SKILLS_DIR=$SKILLS_DIR"
