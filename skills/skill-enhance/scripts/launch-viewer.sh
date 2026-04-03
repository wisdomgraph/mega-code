#!/usr/bin/env bash
# Launch the enhancement viewer with optional previous-iteration context.
# Usage: launch-viewer.sh <MEGA_DIR> <ITER_DIR> <SKILL_NAME> <ITERATION_NUM> [--foreground]
#
# --foreground: Run the viewer in the foreground (blocks until feedback is submitted).
#               Use in environments where background processes are not reliable (e.g. Codex).
#               Outputs the feedback JSON to stdout on exit. Does not require stop-viewer.sh.
set -euo pipefail

if [ $# -lt 4 ]; then
    echo "Usage: $(basename "$0") <MEGA_DIR> <ITER_DIR> <SKILL_NAME> <ITERATION_NUM> [--foreground]" >&2
    exit 1
fi

MEGA_DIR="$1"
ITER_DIR="$2"
SKILL_NAME="$3"
ITERATION_NUM="$4"
FOREGROUND=false
if [ "${5:-}" = "--foreground" ]; then
    FOREGROUND=true
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

if [ "$FOREGROUND" = "true" ]; then
    # Foreground mode: run the viewer directly (blocking).
    # The viewer exits automatically after feedback is submitted (--exit-on-feedback).
    # This mode is for environments where background processes are unreliable (e.g. Codex).
    uv run --directory "$MEGA_DIR" python -m mega_code.client.enhancement_viewer \
        "$ITER_DIR" \
        --skill-name "$SKILL_NAME" \
        --iteration "$ITERATION_NUM" \
        --no-browser \
        --exit-on-feedback \
        ${PREV_WORKSPACE_ARGS[@]+"${PREV_WORKSPACE_ARGS[@]}"}
    # Output feedback (same as stop-viewer.sh does in background mode)
    cat "$ITER_DIR/feedback.json" 2>/dev/null || echo '{"reviews": []}'
    exit 0
fi

# Background mode: launch with nohup (+ setsid when available) so the viewer survives shell exit
SETSID=""
if command -v setsid >/dev/null 2>&1; then
    SETSID="setsid"
fi
nohup $SETSID uv run --directory "$MEGA_DIR" python -m mega_code.client.enhancement_viewer \
    "$ITER_DIR" \
    --skill-name "$SKILL_NAME" \
    --iteration "$ITERATION_NUM" \
    --no-browser \
    ${PREV_WORKSPACE_ARGS[@]+"${PREV_WORKSPACE_ARGS[@]}"} \
    >"$ITER_DIR/viewer.log" 2>&1 &
VIEWER_PID=$!

# Wait until the server writes its port file and responds (up to 15 seconds)
PORT_FILE="$ITER_DIR/viewer.port"
ACTUAL_PORT=""
for i in $(seq 1 30); do
    # Fail fast if the process already exited
    if ! kill -0 "$VIEWER_PID" 2>/dev/null; then
        echo "ERROR: Viewer process exited unexpectedly." >&2
        echo "Viewer log:" >&2
        cat "$ITER_DIR/viewer.log" 2>/dev/null >&2
        rm -f "$ITER_DIR/viewer.pid"
        exit 1
    fi
    # Read the port file once it appears
    if [ -z "$ACTUAL_PORT" ] && [ -f "$PORT_FILE" ]; then
        ACTUAL_PORT=$(cat "$PORT_FILE")
    fi
    # Health-check the actual port
    if [ -n "$ACTUAL_PORT" ] && curl -sf "http://localhost:$ACTUAL_PORT" > /dev/null 2>&1; then
        if command -v xdg-open >/dev/null 2>&1; then
            xdg-open "http://localhost:$ACTUAL_PORT"
        elif command -v open >/dev/null 2>&1; then
            open "http://localhost:$ACTUAL_PORT"
        fi
        echo "Viewer is running on http://localhost:$ACTUAL_PORT (PID $VIEWER_PID)"
        exit 0
    fi
    sleep 0.5
done

# If we get here, the server didn't start
echo "ERROR: Viewer failed to start within 15 seconds." >&2
echo "Viewer log:" >&2
cat "$ITER_DIR/viewer.log" 2>/dev/null >&2
kill "$VIEWER_PID" 2>/dev/null || true
rm -f "$ITER_DIR/viewer.pid" "$ITER_DIR/viewer.port"
exit 1
