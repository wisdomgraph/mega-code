---
description: Enhance a mega-code skill — defaults to the remote server flow; pass --hitl for the local human-in-the-loop A/B flow.
argument-hint: "[<skill-name>] [--hitl] [--poll-timeout <s>] [--poll-existing <job-id>]"
allowed-tools: Bash, Read, AskUserQuestion
disable-model-invocation: true
---

# Skill Enhance

By default this command offloads the entire skill A/B + iteration loop
to the mega-code server. The host agent (you) is a thin orchestrator:
package → upload → poll → download → install. The pipeline runs
server-side; this command blocks until terminal status is reached.

For the local human-in-the-loop flow (host-driven grading + HTML viewer),
pass `--hitl` (case-insensitive). Phase 0 below detects the flag and
delegates to `skills/skill-enhance-hitl/SKILL.md`.

`references/architecture.md` is your runtime lookup — exit-code map,
`phase_public` enum, `status × artifact_kind` decision table, frontmatter
contract. Read it whenever you hit an unfamiliar code or shape.

## Phase 0 — Argument Dispatch (`--hitl`)

Before doing anything else, scan `$ARGUMENTS` for a `--hitl` token
(case-insensitive: `--hitl`, `--HITL`, `--Hitl` all match). Run:

```bash
HITL=0
RESIDUAL_ARGS="$ARGUMENTS"
if echo " $ARGUMENTS " | grep -iqE '(^|[[:space:]])--hitl([[:space:]]|$)'; then
  HITL=1
  RESIDUAL_ARGS="$(echo " $ARGUMENTS " | sed -E 's/[[:space:]]--[Hh][Ii][Tt][Ll]([[:space:]]|$)/ /g' | sed -E 's/^[[:space:]]+|[[:space:]]+$//g')"
fi
echo "HITL=$HITL"
echo "RESIDUAL_ARGS=$RESIDUAL_ARGS"
```

**If `HITL=1`:**

- `--hitl` and `--poll-existing` are mutually exclusive (the latter is a
  remote-only concept). If `$ARGUMENTS` also contains `--poll-existing`,
  stop immediately and tell the user to pick one.
- Otherwise, tell the user "Switching to local human-in-the-loop flow"
  and **load `skills/skill-enhance-hitl/SKILL.md`**, following its
  phases verbatim with `$ARGUMENTS` set to `RESIDUAL_ARGS` (so any
  `<skill-name>` token is preserved). Do **not** run any of the remote
  phases below.

**If `HITL=0`:** proceed to Phase 1 as written.

## Phase 1 — Setup & Auth Preflight

```bash
bash "${CLAUDE_SKILL_DIR}/scripts/setup.sh"
```

The script verifies auth (`MEGA_CODE_API_KEY`) and gates on
`MEGA_CODE_CLIENT_MODE=remote`. It prints four lines on stdout:

```
MEGA_DIR=<path>
DATA_DIR=<path>
PROJECT_DIR=<path>
MEGA_CODE_SESSION_ID=<uuid>
```

**Remember all four literal values.**

- `<MEGA_DIR>` — repo root; every later bash block substitutes this in
  `uv run --directory <MEGA_DIR> ...`.
- `<DATA_DIR>` — resolved `data_dir()` (always XDG, e.g.
  `~/.local/share/mega-code`; **never** macOS-native
  `~/Library/Application Support/...`). Use this when telling the user
  where staging dirs, the result cache, or backups live. Do **not**
  invent a platform-specific path in your prose.
- `<PROJECT_DIR>` — the user's real project root, captured *before* `uv
  run --directory` shifts cwd into the plugin cache. Substitute as
  `--project-dir "<PROJECT_DIR>"` on every `python -m` call below so
  skill resolution always sees the user's project, never the plugin
  cache. setup.sh already rejected plugin-cache paths, so by the time
  you read this value it is safe to forward verbatim — no extra guards
  needed in later bash blocks.
- `<MEGA_CODE_SESSION_ID>` — fresh UUID for this slash-command run.
  Substitute as the env-var prefix on every later
  `uv run python -m mega_code.client.remote_enhance ...` and
  `uv run python -m mega_code.client.skill_enhance_helper ...` block,
  e.g. `MEGA_CODE_SESSION_ID=<MEGA_CODE_SESSION_ID> uv run ...`.
  Internal testers with `OTEL_EXPORTER_OTLP_ENDPOINT` set will see all
  spans this run emits joined under one `mega_code.session_id` resource
  attribute. Public users (no OTEL endpoint) generate the id but emit
  nothing — zero overhead.

Bash tool calls start fresh shells, so shell variables do not persist
across blocks — literal substitution is the only reliable transport.

If setup exits non-zero:
- **auth failure** → surface stderr, stop the workflow.
- **mode failure** (`MEGA_CODE_CLIENT_MODE != remote`) → surface stderr
  and tell the user to re-run with `/mega-code:skill-enhance --hitl` for
  the local human-in-the-loop flow. Stop.

**Failure rule for every bash block below:** if any `uv run` command
exits non-zero, surface its stderr to the user and stop the workflow.

## Phase 2 — Skill Selection

Parse `$ARGUMENTS` into three signals:
- `--poll-existing <job-id>` → set `JOB_ID`; **skip this phase** and go
  straight to Phase 3 in resume mode.
- `--poll-timeout <s>` → set `POLL_TIMEOUT` (default 1200; 0 = wait
  indefinitely).
- a bare token (not starting with `--`) → treat as `SKILL_NAME`.

If no `SKILL_NAME` and no `--poll-existing`, list the user's mega-code-authored
skills and ask them to pick one. The helper filters by
`metadata.author == "co-authored by www.megacode.ai"` (the canonical
stamp written by the server) so only re-enhanceable skills appear.

Substitute `<MEGA_DIR>` and `<MEGA_CODE_SESSION_ID>` from Phase 1 into
every block below. The session id rides as an env-var prefix on each
`python -m` invocation so all spans this slash command emits land under
one `mega_code.session_id` resource attribute (only fires when an
internal tester has `OTEL_EXPORTER_OTLP_ENDPOINT` set; otherwise no-op).

```bash
MEGA_CODE_SESSION_ID=<MEGA_CODE_SESSION_ID> \
  uv run --directory "<MEGA_DIR>" python -m mega_code.client.skill_enhance_helper \
    list-skills --project-dir "<PROJECT_DIR>" 2>&1
```

Parse the JSON output and decide based on how many candidates the
helper returned:

- **0 candidates** → tell the user no eligible skills were found and
  exit.
- **exactly 1 candidate** → skip the prompt and use it directly.
- **2–4 candidates** → present them via `AskUserQuestion` and use the
  picked name.
- **5+ candidates** → do **not** call `AskUserQuestion` (the tool caps
  `options` at 4). Print the JSON list as a numbered table and tell
  the user to re-invoke the slash command with an explicit skill name,
  e.g. `/mega-code:skill-enhance <skill-name>`. Then exit. Don't try
  to truncate the list to 4 — silently dropping eligible skills hides
  options the user may have meant to pick.

Whether the skill came from the picker or an explicit arg, validate it
via `resolve-skill` so the canonical name and original path are known
downstream (Phase 4 uses the original path to detect cross-scope
installs and offer cleanup):

```bash
MEGA_CODE_SESSION_ID=<MEGA_CODE_SESSION_ID> \
  uv run --directory "<MEGA_DIR>" python -m mega_code.client.skill_enhance_helper \
    resolve-skill --name "<SKILL_NAME>" --project-dir "<PROJECT_DIR>" 2>&1
```

Parse the JSON output (last line of stdout) and store **two** values:
- `SKILL_NAME` from `d['name']` — the canonical skill name.
- `ORIGINAL_SKILL_PATH` from `d['path']` — full path to the existing
  `SKILL.md` file (e.g. `/Users/you/.claude/skills/<name>/SKILL.md` or
  `<project>/.claude/skills/<name>/SKILL.md`).

Derive `ORIGINAL_SCOPE` from `ORIGINAL_SKILL_PATH`:
- path under `$HOME/.claude/skills/` → `ORIGINAL_SCOPE=global`
- path under any `<...>/.claude/skills/` (project-local) → `ORIGINAL_SCOPE=project`

Both values are needed in Phase 4 — the **install-location prompt
recommends the matching scope**, and the **post-install cleanup prompt
fires when the user picks a different scope** (otherwise the original
becomes stale dead weight).

## Phase 3 — Run the Enhance Module

Single bash call, tee'd to a timestamped log under `/tmp` so the user can
inspect it later. Default `--poll-timeout` is 1200s (20 min); the user
may have overridden it via `$ARGUMENTS`.

Both modes also write the final envelope to `--result-json` so Phase 4
can read it from a deterministic file path instead of tailing the log.
Logs mix stdout and stderr, and a late `logger.warning` after the final
envelope would push the JSON off the last line — file-based delivery
removes that flake.

**Default mode** (when `JOB_ID` is unset):

```bash
TS="$(date +%Y%m%d-%H%M%S)"
LOG="/tmp/mega-code-enhance-$TS.log"
RESULT_JSON="/tmp/mega-code-enhance-$TS.result.json"
MEGA_CODE_SESSION_ID=<MEGA_CODE_SESSION_ID> \
  uv run --directory "<MEGA_DIR>" python -m mega_code.client.remote_enhance \
    --skill-name "<SKILL_NAME>" \
    --project-dir "<PROJECT_DIR>" \
    --poll-timeout <POLL_TIMEOUT> \
    --result-json "$RESULT_JSON" \
    2>&1 | tee "$LOG"
```

**Resume mode** (when `JOB_ID` was parsed from `--poll-existing`):

```bash
TS="$(date +%Y%m%d-%H%M%S)"
LOG="/tmp/mega-code-enhance-$TS.log"
RESULT_JSON="/tmp/mega-code-enhance-$TS.result.json"
MEGA_CODE_SESSION_ID=<MEGA_CODE_SESSION_ID> \
  uv run --directory "<MEGA_DIR>" python -m mega_code.client.remote_enhance \
    --poll-existing "<JOB_ID>" \
    --project-dir "<PROJECT_DIR>" \
    --poll-timeout <POLL_TIMEOUT> \
    --result-json "$RESULT_JSON" \
    2>&1 | tee "$LOG"
```

Tell the user up front:

> The server is running the A/B + iteration loop. **Do not Ctrl+C.**
> Silence for several minutes during LLM inference is normal — the
> poller emits a progress line every ~10s. The full transcript is at
> `<LOG>`.

The module's exit code is captured by the shell (`$?` after the `tee`
pipeline finishes — but `tee` is the last process, so use
`PIPESTATUS[0]` if you need the module's exit code in the same block;
otherwise the final stdout JSON line of the log carries the same
information).

## Phase 4 — Exit-Code Branching

Read the JSON envelope from `$RESULT_JSON` (the file written by
`--result-json`). It is the final stdout envelope from the module. The
tee'd log at `$LOG` is still useful for surfacing progress lines to the
user, but Phase 4 dispatches purely on the file — there is no log-tailing
heuristic that can break when stderr fires after the envelope.

Dispatch on exit code, then on JSON shape. The full table lives in
`references/architecture.md` (Exit-code map + Exit-0 sub-shapes); the
rules below are the user-facing branches.

### Exit 0 — terminal status reached

Three sub-shapes; branch on JSON keys in this order:

1. `needs_install_location == true` → ask the user where to install.
   Use `AskUserQuestion` with two options, **labelling the option that
   matches `ORIGINAL_SCOPE` (from Phase 2) as recommended** so the
   default keeps both copies at the same path:
   - **project** — `<project>/.claude/skills/<name>/` (tracked by VCS)
     → label as *"project (recommended — replaces current install)"*
       when `ORIGINAL_SCOPE=project`.
   - **global** — `~/.claude/skills/<name>/` (follows the user)
     → label as *"global (recommended — replaces current install)"*
       when `ORIGINAL_SCOPE=global`.

   Then re-invoke the module to do the install:

   ```bash
   INSTALL_RESULT_JSON="/tmp/mega-code-enhance-$(date +%Y%m%d-%H%M%S).install.json"
   MEGA_CODE_SESSION_ID=<MEGA_CODE_SESSION_ID> \
     uv run --directory "<MEGA_DIR>" python -m mega_code.client.remote_enhance \
       --install-existing "<JOB_ID_FROM_RESULT>" \
       --skill-name "<SKILL_NAME>" \
       --install-location <project|global> \
       --project-dir "<PROJECT_DIR>" \
       --result-json "$INSTALL_RESULT_JSON" 2>&1
   ```

   Then read `$INSTALL_RESULT_JSON` for the install envelope.

   The `<JOB_ID_FROM_RESULT>` value comes from the staging path in the
   prior envelope (`staging_dir` ends in `/<job_id>/`).

2. `installed == true` → render "Installed at `<installed_path>`" plus
   the full ROI summary from `result.roi`.

   **ROI rendering contract.** `result.roi` is a list; each entry has
   `model`, `performance_increase`, and `token_savings`. For **every**
   entry, render **both** percentages — never drop a field, even when
   it is `"0%"`. The "0%" data point is meaningful (proves the model
   was measured and the delta was zero, not that the field is missing).

   Render the install path on its own line, then `ROI:` on the next
   line. Format per entry: `+<performance_increase> performance,
   <token_savings> token savings, model=<model>`. Example:

   ```
   Installed at /path/to/skill/.
   ROI: +67% performance, 0% token savings, model=gemini-3-flash-preview
   ```

   If multiple models are present, render one `ROI:` line per entry. If
   `result.roi` is missing or empty (shouldn't happen for
   `artifact_kind=enhanced`, since the installer validates it per §5.6
   of the design doc), surface "no ROI data" rather than silently
   omitting the line.

   **Cross-scope-duplicate cleanup.** Compare `installed_path` (from
   the install envelope) against `ORIGINAL_SKILL_PATH`'s parent
   directory (from Phase 2). If they differ, the original is now a
   stale duplicate at the other scope and at runtime the new copy
   wins — leaving the old one is dead weight. Use `AskUserQuestion`:

   *"The original is still at `<ORIGINAL_SKILL_DIR>`. Delete it to
   avoid divergence?"*

   - **Yes** → run the guarded delete below.
   - **No** → leave it; tell the user they have two copies and at
     runtime the project-scope copy takes precedence.

   If they were the same directory (matching scope), skip the prompt
   entirely — the installer's sibling-tmp + os.replace already swapped
   in place and backed up the original to
   `<DATA_DIR>/enhancements/<ts>-backup/`.

   **Guarded delete** — delegated to the Python entrypoint, which uses
   `Path.resolve(strict=True)` (walks the full symlink chain) and
   `Path.is_relative_to()` against the resolved `~/.claude/skills/` and
   `<project>/.claude/skills/` roots. Refuses leaf-symlink-escape that the
   prior bash `case`-glob did not catch. Returns exit 4 with `error.code`
   ∈ {`cleanup_unsafe_path`, `cleanup_path_missing`, `cleanup_failed`}
   on refusal and prints `{"removed": "<path>"}` on success.

   ```bash
   ORIGINAL_DIR="$(dirname "<ORIGINAL_SKILL_PATH>")"
   MEGA_CODE_SESSION_ID=<MEGA_CODE_SESSION_ID> \
     uv run --directory "<MEGA_DIR>" python -m mega_code.client.remote_enhance \
       --cleanup-original "$ORIGINAL_DIR" \
       --project-dir "<PROJECT_DIR>" 2>&1
   ```

   On non-zero exit, surface the JSON envelope's `error.code` /
   `error.message` to the user — do not retry, the refusal is intentional.

3. neither key true → terminal-no-install (e.g. `failed`, `rejected`,
   `quarantined`, `cancelled`, `enhancement_blocked`, `revoked`,
   `invariant_violation`).

   **Render the rejection detail to the user up front — do NOT emit a
   vague "terminal state, no install" line and wait to be asked why.**
   The full upstream body is forwarded under `result`. Walk it and
   surface, in this order, whatever is populated:

   - `status` (e.g. `invariant_violation`, `rejected`) — the headline.
   - `result.invariants` — list of invariants that fired; render each
     one as a bullet so the user sees *what was missing*.
   - `result.evidence` / `result.evaluation` — validator notes or
     per-test evidence; render as a short summary, not a dump.
   - `result.reason` — free-form reason string from the server; render
     verbatim if present.
   - any other top-level keys under `result` you don't recognize —
     render their key + value (one line each) rather than dropping them.

   Then surface two inspection handles:
   - the **`job_id`** (server-side record — share with the team if the
     reason is unclear);
   - the **session log** at `/tmp/mega-code-enhance-*.log` (tee'd by the
     bash blocks earlier — shows the upstream progress lines).

   Do **not** claim a local bundle was preserved — for non-succeeded
   terminals the client never downloads an artifact, so nothing exists
   under `<DATA_DIR>/enhancements/<job_id>/`. Do NOT install.

### Exit 2 — duplicate content hash

The skill content has already been submitted (same `content_hash`).
Read `conflict.existing_job_id` from the JSON. Use `AskUserQuestion`:

- **Resume** — re-invoke this skill with `--poll-existing <existing_job_id>`
  (loop back to Phase 3 in resume mode with that JOB_ID).
- **Cancel** — stop the workflow.

### Exit 3 — poll timeout

Read `timeout.job_id` and `timeout.elapsed_s`. Use `AskUserQuestion`:

- **Wait longer** — re-invoke with `--poll-existing <job_id>` and a
  larger `--poll-timeout` (or `0` for unlimited).
- **Exit** — stop. The user can resume later via
  `/mega-code:skill-enhance --poll-existing <job_id>`.

### Exit 4 — bad input

Read `error.code` and `error.message`.

**Special case: `error.code == "prefix_exists"`** — handle entirely
client-side; do NOT surface the raw upstream message and do NOT mention
server-side cleanup or waiting. Look up locally-cached prior enhancements
of the current skill:

```bash
MEGA_CODE_SESSION_ID=<MEGA_CODE_SESSION_ID> \
  uv run --directory "<MEGA_DIR>" python -m mega_code.client.remote_enhance \
    --list-cached --skill-name "<SKILL_NAME>" \
    --project-dir "<PROJECT_DIR>" 2>&1
```

Parse the stdout envelope `{"cached": [...]}`. Each entry has
`{job_id, completed_at, roi}`. Branch on the list:

- **Non-empty** — render a short summary (most recent first; use `roi`
  to show the score delta if present) and use `AskUserQuestion`:
  - **Install one** — pick a `job_id` and re-invoke this skill via
    `--install-existing <job_id> --skill-name <SKILL_NAME>
    --install-location <project|global>` (the install path is offline
    by default — the cached envelope is read back without a network
    round-trip).
  - **Try a different skill** — loop back to **Phase 2** (skill
    selection). Reset `SKILL_NAME`, `ORIGINAL_SKILL_PATH`,
    `ORIGINAL_SCOPE`, and `JOB_ID` first.
  - **Exit** — stop the workflow cleanly.
- **Empty** — tell the user "no prior enhancement of `<SKILL_NAME>` is
  cached locally" and use `AskUserQuestion` with two options:
  - **Try a different skill** — loop back to **Phase 2**.
  - **Exit** — stop.

**All other 4xx codes:** surface `error.code` + `error.message` to the
user and stop. Do not retry — these are caller-actionable (oversized
bundle, missing SKILL.md, malformed frontmatter, etc.). Common codes
are listed in `references/architecture.md` (4xx upstream error codes).

### Exit 5 — auth or network failure

Read `error.code` and `error.message`. If `auth_failure`, the user's
`MEGA_CODE_API_KEY` may be invalid or expired — point them at
`MEGA_CODE_API_KEY` in `.env`. For other codes (`network_failure`,
`queue_full`), surface the message and suggest retrying after a moment.

## Phase 5 — Loop or Exit

Use `AskUserQuestion`:

- **Yes** — back to **Phase 2** (skill selection). Setup is preserved
  from this invocation; you do NOT need to re-run `scripts/setup.sh`.
  Reset `JOB_ID`, `SKILL_NAME`, `ORIGINAL_SKILL_PATH`, `ORIGINAL_SCOPE`,
  and `POLL_TIMEOUT` for the next round.
- **No** — show a one-line summary (skills enhanced this session, install
  paths, log paths) and end the workflow.

If the user cancels the prompt or returns a blank response, treat it as
**No** and exit cleanly — re-prompting would just be friction.
