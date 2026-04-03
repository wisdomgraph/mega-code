#!/usr/bin/env bash
# Stop the enhancement viewer and read feedback.
# Usage: stop-viewer.sh <ITER_DIR>
set -euo pipefail

if [ $# -lt 1 ]; then
    echo "Usage: $(basename "$0") <ITER_DIR>" >&2
    exit 1
fi

export ITER_DIR="$1"

# Write stop-context so the viewer's SIGTERM handler can annotate its trace span.
# Written before kill so the file exists when the signal fires.
python3 - <<'PYEOF'
import json, os, time, pathlib
data = {
    "trigger": "stop_viewer_sh",
    "reason": os.environ.get("STOP_REASON", "explicit_stop"),
    "stopper_pid": os.getpid(),
    "stop_time_ns": time.time_ns(),
}
pathlib.Path(os.environ["ITER_DIR"]).joinpath("stop-context.json").write_text(
    json.dumps(data) + "\n", encoding="utf-8"
)
PYEOF

# Prefer the tracked PID, fall back to port-based kill
if [ -f "$ITER_DIR/viewer.pid" ]; then
    VIEWER_PID=$(cat "$ITER_DIR/viewer.pid")
    if kill -0 "$VIEWER_PID" 2>/dev/null; then
        kill "$VIEWER_PID" 2>/dev/null || true
    fi
    rm -f "$ITER_DIR/viewer.pid"
fi
# Also clean up anything still on the viewer port (belt and suspenders)
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
cat "$ITER_DIR/feedback.json" 2>/dev/null || echo '{"reviews": []}'
