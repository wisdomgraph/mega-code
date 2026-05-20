#!/usr/bin/env bash
# Stop the enhancement viewer and read feedback.
# Usage: stop-viewer.sh <ITER_DIR>
set -euo pipefail

if [ $# -lt 1 ]; then
    echo "Usage: $(basename "$0") <ITER_DIR>" >&2
    exit 1
fi

ITER_DIR="$1"

lsof -ti :3117 2>/dev/null | xargs kill 2>/dev/null || true
sleep 0.5
cat "$ITER_DIR/feedback.json" 2>/dev/null || echo '{"reviews": []}'
