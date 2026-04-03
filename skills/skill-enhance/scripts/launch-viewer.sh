#!/usr/bin/env bash
# Launch the enhancement viewer with optional previous-iteration context.
# Usage: launch-viewer.sh <MEGA_DIR> <ITER_DIR> <SKILL_NAME> <ITERATION_NUM> [--foreground]
#
# The supported mode is foreground execution for Codex. The viewer waits until the
# server is bound, opens the browser automatically, exits after feedback is submitted,
# and then prints the feedback JSON to stdout.
set -euo pipefail

if [ $# -lt 4 ]; then
    echo "Usage: $(basename "$0") <MEGA_DIR> <ITER_DIR> <SKILL_NAME> <ITERATION_NUM> [--foreground]" >&2
    exit 1
fi

MEGA_DIR="$1"
ITER_DIR="$2"
SKILL_NAME="$3"
ITERATION_NUM="$4"
if [ "${5:-}" != "" ] && [ "${5:-}" != "--foreground" ]; then
    echo "Unsupported argument: ${5}" >&2
    exit 1
fi

PREV_WORKSPACE_ARGS=()
if [ "$ITERATION_NUM" -gt 1 ]; then
    PREV_DIR="$(dirname "$ITER_DIR")/iteration-$((ITERATION_NUM - 1))"
    PREV_WORKSPACE_ARGS=(--previous-workspace "$PREV_DIR")
fi

# Kill any leftover viewer: prefer tracked PID, fall back to port scan
if [ -f "$ITER_DIR/viewer.pid" ]; then
    OLD_PID=$(cat "$ITER_DIR/viewer.pid")
    if kill -0 "$OLD_PID" 2>/dev/null; then
        kill "$OLD_PID" 2>/dev/null || true
    fi
    rm -f "$ITER_DIR/viewer.pid"
fi
# Belt-and-suspenders: kill anything still on the viewer port
CLEANUP_PORT=3117
if [ -f "$ITER_DIR/viewer.port" ]; then
    CLEANUP_PORT=$(cat "$ITER_DIR/viewer.port")
fi
if command -v lsof >/dev/null 2>&1; then
    lsof -ti :"$CLEANUP_PORT" 2>/dev/null | xargs kill 2>/dev/null || true
elif command -v fuser >/dev/null 2>&1; then
    fuser -k "$CLEANUP_PORT"/tcp 2>/dev/null || true
fi
rm -f "$ITER_DIR/viewer.port"
sleep 0.5

export UV_CACHE_DIR="${UV_CACHE_DIR:-$MEGA_DIR/.uv-cache}"
uv run --directory "$MEGA_DIR" python -m mega_code.client.enhancement_viewer \
    "$ITER_DIR" \
    --skill-name "$SKILL_NAME" \
    --iteration "$ITERATION_NUM" \
    --exit-on-feedback \
    ${PREV_WORKSPACE_ARGS[@]+"${PREV_WORKSPACE_ARGS[@]}"}
cat "$ITER_DIR/feedback.json" 2>/dev/null || echo '{"reviews": []}'
