# Skill Enhance A/B Test Timeout Issue

## Problem

Running Phase 4 (A/B tests) in `skills/skill-enhance/SKILL.md` hits a 300-second
timeout on the Claude subprocess, causing test failures.

```
The runner hit a 300s timeout on the Claude subprocess.
```

## Root Cause

The eval timeout is defined in a single location:

**`mega_code/client/host_llm.py:62`**
```python
_EVAL_TIMEOUT_SECONDS = float(os.getenv("MEGA_CODE_EVAL_TIMEOUT", "300"))
```

Used at `host_llm.py:355-362`:
```python
stdout, stderr = await asyncio.wait_for(
    proc.communicate(input=prompt.encode()),
    timeout=_EVAL_TIMEOUT_SECONDS,
)
```

## Call Path

```
skills/skill-enhance/SKILL.md (Phase 4)
  └→ Bash tool invocation (no explicit timeout — uses Claude Code default, ~5 min)
       └→ uv run python -m mega_code.client.skill_enhance_runner
            └→ asyncio.gather(*[_one(tc) for tc in test_cases])   ← runner:69, ALL cases start concurrently
                 └→ _one(tc):                                      ← runner:47-67
                      └→ asyncio.gather(                           ← runner:49-53
                           complete(with-skill),                   ← semaphore-gated, timeout applies
                           complete(baseline),                     ← semaphore-gated, timeout applies
                         )
                           └→ host_llm._get_semaphore()            ← _MAX_CONCURRENCY=4
                           └→ asyncio.wait_for(..., timeout=_EVAL_TIMEOUT_SECONDS)
```

All test-case coroutines start simultaneously via `asyncio.gather` at
`skill_enhance_runner.py:69`. Concurrency is controlled by an `asyncio.Semaphore`
(capacity = `_MAX_CONCURRENCY`, default 4) inside `host_llm.complete()`, acting as a
**sliding window** — not discrete batch-then-wait waves. As soon as one slot frees,
the next queued completion starts immediately.

Each test case spawns 2 subprocess completions (`with-skill` + `baseline`) via
`asyncio.gather` (`skill_enhance_runner.py:49-53`).

## Impact Analysis (400s, 4 test cases)

### Timeout Scope

| Setting | Current | Proposed (400s) |
|---------|---------|-----------------|
| Per-subprocess max wait | 300s | 400s |
| Total completions | 4 x 2 = 8 | 8 |
| Semaphore capacity | 4 (`MEGA_CODE_EVAL_CONCURRENCY`) | 4 |
| Worst-case total wait | ceil(8/4) x 300 = 600s (10 min) | **ceil(8/4) x 400 = 800s (~13 min)** |

> **Note**: Actual wall time is typically much shorter than worst-case because the
> semaphore sliding window releases slots as completions finish, not in lock-step batches.

### Bash Tool Timeout (Critical)

The SKILL.md Phase 4 runs the runner via the Claude Code Bash tool. **The current
Phase 4 block has no explicit `timeout` parameter** — it relies on the Claude Code
Bash tool default (120,000ms / 2 min, shown as `(timeout 5m)` in some configurations).

Since SKILL.md is interpreted by the LLM agent, the timeout cannot be set as a code
block attribute. Instead, a natural-language instruction must be added above the Phase 4
bash block telling the agent to use `timeout: 900000` when invoking the Bash tool.

**With 400s eval timeout**: worst-case runner time is ~13 min. The Bash tool timeout
must be at least 15 min (900,000ms) to avoid the runner being killed mid-execution.

### Environment Variable Loading Gap

`skill_enhance_runner.py` does **not** call `_load_env()` or `dotenv.load_dotenv()`
internally. The `MEGA_CODE_EVAL_TIMEOUT` env var reaches `host_llm.py` only if:

1. The SKILL.md Setup step (`set -a && . ~/.local/share/mega-code/.env && set +a`)
   was executed in the **same Bash session** before the `uv run` invocation, OR
2. The variable is set in the shell profile / system environment.

Other modules (`collector.py`, `check_pending.py`, `run_pipeline.py`) explicitly call
`_load_env()` — the runner is inconsistent in this regard.

### Resource Usage

- Memory: ~100-200 MB per Claude CLI subprocess x 4 concurrent = ~400-800 MB
- Duration: up to ~7 min at peak concurrency
- CPU: mostly idle (waiting on LLM responses)

### Unaffected Modules

These timeouts are completely independent of `_EVAL_TIMEOUT_SECONDS`:

| Module | Timeout | Purpose |
|--------|---------|---------|
| `skill_installer.py` | `_DOWNLOAD_TIMEOUT` | Skill archive download |
| `login.py` | `_POLL_TIMEOUT_SECONDS` | OAuth polling |
| `run_pipeline.py` | `--poll-timeout` | Pipeline status polling |
| `pending.py` | 1200s default | Pipeline completion polling |
| `api/remote.py` | 30s | HTTP request timeout |

## Solution

### 1. Set eval timeout via environment variable

```bash
# In .env
MEGA_CODE_EVAL_TIMEOUT=400
```

> **Important**: Ensure the SKILL.md Setup step sources `.env` before Phase 4 runs,
> so the variable propagates to the `uv run` subprocess. If running the runner
> independently (outside SKILL.md), export the variable in your shell first.

### 2. Add Bash tool timeout instruction to SKILL.md Phase 4

The Phase 4 section in `skills/skill-enhance/SKILL.md` currently has **no timeout
instruction**. Add a natural-language directive above the bash block:

```markdown
When running this command via the Bash tool, set `timeout: 900000` (15 minutes)
to accommodate worst-case A/B execution times.
```

This is an LLM-interpreted hint, not a guaranteed enforcement. The agent may still
use the default timeout if it does not follow the instruction.

### 3. Optional: reduce concurrency to lower memory pressure

```bash
# In .env (reduces parallel subprocesses but increases total wall time)
MEGA_CODE_EVAL_CONCURRENCY=2
```

With concurrency=2: ceil(8/2) x 400s = 1,600s (~27 min) worst-case.

## Known Limitations

### No partial result recovery

If one completion times out, `_one()` raises immediately
(`skill_enhance_runner.py:54-56`). The outer `asyncio.gather` uses
`return_exceptions=True`, so other test cases continue, but the first exception
encountered in the result loop (`runner:70-72`) aborts the entire run — discarding
all successful results.

**Future improvement**: Return error records for failed test cases instead of
raising, so partial A/B results can still be graded.

### No runner-level overall timeout

There is no timeout wrapper around the outer `asyncio.gather` at
`skill_enhance_runner.py:69`. The only external time boundary is the Bash tool
timeout. If the Bash tool kills the Python process, child Claude CLI subprocesses
may become orphaned.

## Verification

After applying the changes:

1. **Env var**: Run `echo $MEGA_CODE_EVAL_TIMEOUT` inside the Bash session that
   executes Phase 4 — confirm it shows `400`.
2. **Bash timeout**: Observe the Bash tool invocation output — confirm it shows
   `(timeout 15m)` instead of `(timeout 5m)`.
3. **End-to-end**: Run a full skill-enhance cycle with 4 test cases and confirm
   Phase 4 completes without timeout errors.
4. **Rollback**: To revert, remove `MEGA_CODE_EVAL_TIMEOUT` from `.env` (defaults
   back to 300s) and remove the timeout instruction from SKILL.md.

## Decision Log

| Decision | Value | Rationale |
|----------|-------|-----------|
| Eval timeout | 400s (not 500+) | Minimal bump above observed P95 completion times (~280-320s). Keeps worst-case total under 15 min. |
| Bash tool timeout | 900s (15 min) | 2x worst-case runner time (800s) with margin for Python startup and JSON I/O. |
| Concurrency | 4 (unchanged) | Current memory usage (~800 MB peak) is acceptable. No evidence of resource pressure. |

## Summary

Two timeouts must be adjusted together:

| Layer | Variable / Setting | Current | Recommended |
|-------|-------------------|---------|-------------|
| Subprocess | `MEGA_CODE_EVAL_TIMEOUT` | 300 | 400 |
| Bash tool | timeout instruction in SKILL.md Phase 4 | **not set** (uses default) | 900000 (15m) |

Additionally, ensure `.env` is sourced in the Bash session before the runner is invoked.
