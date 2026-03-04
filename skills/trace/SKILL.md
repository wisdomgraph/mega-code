---
description: Fetch and summarise a pipeline trace from Phoenix (local) or Honeycomb (remote). Pass a trace ID, span ID, or Phoenix UI URL.
argument-hint: "<trace_id|span_id|phoenix_url>"
allowed-tools: Bash
---

# MEGA-Code Trace Inspector

Fetch a pipeline trace from Phoenix or Honeycomb and display a structured summary.

## Usage

```
/mega-code:trace <trace_id>          # 32-char hex trace ID
/mega-code:trace <span_id>           # 16-char hex span ID → looks up its trace
/mega-code:trace <phoenix_url>       # paste any Phoenix UI URL
```

## Setup

```bash
MEGA_DIR="${CLAUDE_PLUGIN_ROOT:-$(cat ~/.local/mega-code/plugin-root 2>/dev/null)}"
ARGS="$1"
```

## Detect Backend

```bash
# Phoenix (local or internal-dev) takes priority if reachable
PHOENIX_HOST="${MEGA_CODE_PHOENIX_HOST:-192.168.2.119:6006}"
if curl -s --connect-timeout 2 "http://${PHOENIX_HOST}/graphql" -X POST \
   -H "Content-Type: application/json" \
   -d '{"query":"{ __typename }"}' | grep -q '__typename'; then
  BACKEND="phoenix"
else
  BACKEND="honeycomb"
fi
echo "Backend: $BACKEND"
```

## Fetch Trace

```bash
uv run --directory "$MEGA_DIR" python -c "
import sys, os
sys.path.insert(0, '$MEGA_DIR')
from mega_code.utils.trace_fetch import fetch_and_print_trace
fetch_and_print_trace('$ARGS')
"
```

## Output Format

The command prints:

```
=== Trace <id[:16]>... (Phoenix: mega-code-pipeline) ===
Duration : 220,345ms   Spans: 50   LLM calls: 21   Errors: 0
Tokens   : prompt=28,304  completion=2,900  total=31,204
Cost     : $0.0842

Span tree (slowest first):
  [chain ]  pipeline.run_pipeline               220,345ms
  [chain ]    pipeline.process_turns            215,690ms
  [chain ]      pipeline.phase5_generate         15,032ms
  [llm   ]        step5.llm_call                 14,901ms  tok=2,900
  ...

Phoenix URL: http://192.168.2.119:6006/projects/UHJvamVjdDoyMA==/traces/<trace_id>
```

## Notes

- **Phoenix**: Uses GraphQL at `MEGA_CODE_PHOENIX_HOST` (default: `192.168.2.119:6006`)
- **Honeycomb**: Requires `HONEYCOMB_API_KEY` env var with `queriesRead` permission
- Trace IDs are 32-char hex; span IDs are 16-char hex
- Phoenix URLs: the skill auto-extracts `traceId` or `selectedSpanNodeId` from the URL
