#!/usr/bin/env bash
# Launch the enhancement viewer with optional previous-iteration context.
# Usage: launch-viewer.sh <MEGA_DIR> <ITER_DIR> <SKILL_NAME> <ITERATION_NUM>
set -euo pipefail

if [ $# -lt 4 ]; then
    echo "Usage: $(basename "$0") <MEGA_DIR> <ITER_DIR> <SKILL_NAME> <ITERATION_NUM>" >&2
    exit 1
fi

MEGA_DIR="$1"
ITER_DIR="$2"
SKILL_NAME="$3"
ITERATION_NUM="$4"

PREV_WORKSPACE_ARGS=()
if [ "$ITERATION_NUM" -gt 1 ]; then
    PREV_DIR="$(dirname "$ITER_DIR")/iteration-$((ITERATION_NUM - 1))"
    PREV_WORKSPACE_ARGS=(--previous-workspace "$PREV_DIR")
fi

# Kill any leftover viewer on port 3117
lsof -ti :3117 2>/dev/null | xargs kill 2>/dev/null || true
sleep 0.5

uv run --directory "$MEGA_DIR" python -m mega_code.client.enhancement_viewer \
    "$ITER_DIR" \
    --skill-name "$SKILL_NAME" \
    --iteration "$ITERATION_NUM" \
    --no-browser \
    ${PREV_WORKSPACE_ARGS[@]+"${PREV_WORKSPACE_ARGS[@]}"} \
    > /dev/null 2>"$ITER_DIR/viewer.log" &

# Wait for the server to be ready before returning
for i in $(seq 1 20); do
    if curl -s -o /dev/null http://localhost:3117 2>/dev/null; then
        if command -v xdg-open >/dev/null 2>&1; then
            xdg-open "http://localhost:3117"
        elif command -v open >/dev/null 2>&1; then
            open "http://localhost:3117"
        fi
        echo "Viewer is ready at http://localhost:3117"
        exit 0
    fi
    sleep 0.5
done

echo "Warning: viewer may not have started — check $ITER_DIR/viewer.log" >&2
