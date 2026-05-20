#!/usr/bin/env bash
# enhance-skill setup: derive MEGA_DIR, verify auth, gate on remote mode.
#
# Prints four lines on stdout for the caller to remember and substitute
# literally into later bash blocks (each Bash tool call is a fresh shell, so
# shell variables do not persist across blocks — literal substitution is the
# cheapest transport, matching the wisdom-curate template):
#
#   MEGA_DIR=<path>             — repo root, used for `uv run --directory`
#   DATA_DIR=<path>             — resolved `mega_code.client.dirs.data_dir()`,
#                                  used for user-facing paths (staging, result
#                                  cache, log inspection). Always XDG-compliant
#                                  (~/.local/share/mega-code by default), never
#                                  macOS-native ~/Library/Application Support —
#                                  published explicitly so the host LLM does not
#                                  hallucinate a platform-specific path in user
#                                  messages.
#   PROJECT_DIR=<path>          — user's real project root, derived from
#                                  CLAUDE_PROJECT_DIR (or pwd as fallback)
#                                  *before* `uv run --directory` shifts cwd
#                                  into the plugin cache. Substituted as
#                                  `--project-dir "<PROJECT_DIR>"` on every
#                                  later `python -m` invocation so skill
#                                  resolution always sees the user's project,
#                                  never the plugin cache.
#   MEGA_CODE_SESSION_ID=<uuid> — fresh per-run correlation id. Internal
#                                  testers with OTEL_EXPORTER_OTLP_ENDPOINT
#                                  set get every span this slash command emits
#                                  tagged with `mega_code.session_id=<uuid>`,
#                                  so all spans (helper scan, packager,
#                                  poller, installer, cleanup) join one
#                                  logical run in Phoenix/Honeycomb.
#                                  Public users (no OTEL endpoint) generate
#                                  the id but emit nothing — zero overhead.
#
# Usage: bash setup.sh
#
# Exits non-zero if:
#   - check_auth fails (missing/invalid MEGA_CODE_API_KEY)
#   - MEGA_CODE_CLIENT_MODE != "remote"
#
# The mode gate is loud (rather than silently defaulting to remote) because
# this command has no local fallback — the entire flow goes through the
# remote gateway. The host LLM in SKILL.md catches the non-zero exit and
# suggests /mega-code:skill-enhance for the local A/B + iteration flow.

set -eu

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MEGA_DIR="$(cd "$SCRIPT_DIR/../../.." && pwd)"

# check_auth.py loads .env internally via load_env_file(get_env_path()) and
# validates MEGA_CODE_API_KEY. Exits non-zero if the key is missing.
uv run --directory "$MEGA_DIR" python -m mega_code.client.check_auth

# Source the same .env check_auth.py reads so the mode gate sees values
# written to disk, not just the parent shell. check_auth.py's
# os.environ.setdefault only affects its own subprocess; this shell still
# needs an explicit load.
ENV_FILE="$(uv run --directory "$MEGA_DIR" python -c \
  'from mega_code.client.cli import get_env_path; print(get_env_path())')"
if [ -f "$ENV_FILE" ]; then
  set -a
  # shellcheck disable=SC1090
  . "$ENV_FILE"
  set +a
fi

if [ "${MEGA_CODE_CLIENT_MODE:-}" != "remote" ]; then
  echo "ERROR: /mega-code:enhance-skill requires MEGA_CODE_CLIENT_MODE=remote." >&2
  echo "       Current: MEGA_CODE_CLIENT_MODE='${MEGA_CODE_CLIENT_MODE:-<unset>}'" >&2
  echo "       For local A/B + iteration, run /mega-code:skill-enhance instead." >&2
  exit 1
fi

# When an OTLP endpoint is configured (internal testers / CI), make sure the
# telemetry extras are present in the project venv. Without this, tracing.py
# falls back to its silent no-op stub because `import opentelemetry` fails,
# and spans never reach Phoenix/Honeycomb. Public users have no endpoint set
# and skip this step entirely — zero install overhead.
if [ -n "${OTEL_EXPORTER_OTLP_ENDPOINT:-}" ]; then
  uv sync --directory "$MEGA_DIR" --extra telemetry >&2
fi

DATA_DIR="$(uv run --directory "$MEGA_DIR" python -c \
  'from mega_code.client.dirs import data_dir; print(data_dir())')"

# Capture the user's real project dir *before* any later `uv run --directory`
# shifts cwd into the plugin cache. CLAUDE_PROJECT_DIR is preferred; pwd is
# the fallback. Either way, plugin-cache / marketplace paths are rejected —
# resolving skills under there is always wrong (the cache is overwritten on
# plugin update and the user's VCS never sees it).
PROJECT_DIR_CANDIDATE="${CLAUDE_PROJECT_DIR:-$(pwd -P)}"
case "$PROJECT_DIR_CANDIDATE" in
  *"/.claude/plugins/cache/"*|*"/.claude/plugins/marketplaces/"*)
    echo "ERROR: cwd and CLAUDE_PROJECT_DIR both point under the Claude plugin" >&2
    echo "       cache (${PROJECT_DIR_CANDIDATE}). Re-launch claude from the" >&2
    echo "       project root, or export CLAUDE_PROJECT_DIR=<your-project>." >&2
    exit 1
    ;;
esac
PROJECT_DIR="$PROJECT_DIR_CANDIDATE"

# Per-run correlation id. Forwarded as MEGA_CODE_SESSION_ID on every later
# `python -m` call by SKILL.md so all spans share one resource attribute.
# uuidgen is universal on macOS/Linux; the python3 fallback covers minimal
# images that lack it.
MEGA_CODE_SESSION_ID="$(uuidgen 2>/dev/null \
  || python3 -c 'import uuid; print(uuid.uuid4())')"

echo "MEGA_DIR=$MEGA_DIR"
echo "DATA_DIR=$DATA_DIR"
echo "PROJECT_DIR=$PROJECT_DIR"
echo "MEGA_CODE_SESSION_ID=$MEGA_CODE_SESSION_ID"
