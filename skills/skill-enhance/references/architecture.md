# `/mega-code:skill-enhance` (remote) — Architecture Reference

Quick lookup for the host LLM while running the slash command. This file
extracts only the tables the host LLM needs at decision time. The full
design contract is available to internal contributors only.

## Data-dir layout

The literal `<DATA_DIR>` value is printed by `scripts/setup.sh` during
Phase 1 (always XDG-resolved — `~/.local/share/mega-code` by default,
or `$MEGA_CODE_DATA_DIR` / `$XDG_DATA_HOME/mega-code` when overridden;
**never** macOS-native `~/Library/Application Support/...`). Use the
captured `<DATA_DIR>` literal in user-facing prose — do not invent a
platform-specific path.

```
<DATA_DIR>/
├── enhancements/
│   ├── {job_id}/                  staging dir — downloaded artifact bytes
│   │   ├── SKILL.md               (only present when status=succeeded
│   │   ├── scripts/                AND artifact_kind=enhanced; for
│   │   └── references/             rejected/failed/etc terminals the
│   │                               client never downloads, so this
│   │                               directory does not exist)
│   ├── {job_id}.result.json       cached upstream JobResult envelope
│   │                              (sibling of staging — never inside it,
│   │                              so it is not re-bundled on re-upload)
│   └── {ts}-backup/               backup of any pre-existing skill at
│       └── <skill-name>/          the install destination, taken just
│                                  before os.replace swap
└── ...
```

The tee'd log lives at `/tmp/mega-code-enhance-<YYYYMMDD-HHMMSS>.log`.
Bearer tokens are filtered before write via `SecretMasker`.

## Exit-code map (final stdout JSON)

| Code | Meaning | Stdout JSON shape |
|---|---|---|
| 0 | Terminal status reached + install state resolved | varies — three sub-shapes (below) |
| 2 | 409 `duplicate_content_hash` | `{"conflict": {"existing_job_id", "content_hash"}}` |
| 3 | Poll timeout | `{"timeout": {"job_id", "elapsed_s"}}` |
| 4 | Bad input — 4xx upload, malformed bundle, missing SKILL.md, frontmatter validation failure, `missing_install_location`, `missing_skill_name`, `missing_staging`, `invalid_path`, `sha256_mismatch` | `{"error": {"code", "message"}}` |
| 5 | Auth or network failure (401, 502, 503, 504, `queue_full` after one backoff) | `{"error": {"code", "message"}}` |

### Exit-0 sub-shapes (host LLM dispatch)

Branch on these keys in order:

| Sub-state | Detect via | Action |
|---|---|---|
| Ready-to-install | `needs_install_location == true` | AskUserQuestion (project / global; **label the option matching `ORIGINAL_SCOPE` as "recommended"**) → re-invoke with `--install-existing <job_id> --install-location <choice>` |
| Installed | `installed == true` | Render `Installed at <installed_path>.` on its own line, then one `ROI:` line per entry in `result.roi`. **Render both `performance_increase` and `token_savings` for every entry** — never drop a field, even when "0%" (the zero is data, not absence). Format per ROI line: `ROI: +<performance_increase> performance, <token_savings> token savings, model=<model>`. **Run the cross-scope-duplicate check** (below). |
| Terminal-no-install | neither key true | Render `result.reason` payload, do NOT install. Inspection handles: the `job_id` (server record) and `/tmp/mega-code-enhance-*.log` (tee'd session log). No local artifact dir is created — the client downloads bytes only on `succeeded` + `artifact_kind=enhanced`. |

### Cross-scope-duplicate cleanup (after `installed=true`)

| `installed_path` vs `dirname(ORIGINAL_SKILL_PATH)` | Behavior |
|---|---|
| Same directory | No prompt. Installer's sibling-tmp + `os.replace` already swapped in place and backed up the original to `<DATA_DIR>/enhancements/<ts>-backup/`. |
| Different directories | AskUserQuestion: *"The original is still at `<ORIGINAL_SKILL_DIR>`. Delete it to avoid divergence?"*. On **Yes**, `rm -rf` the original — guarded to refuse any path outside `~/.claude/skills/` or `<...>/.claude/skills/`. On **No**, leave it; warn the user that the project-scope copy wins at runtime and they have two divergent copies. |

The terminal-no-install branch fires when `status` is `failed`, `quarantined`,
`rejected`, `cancelled`, `enhancement_blocked`, or `revoked`, **or** when
`artifact_kind != "enhanced"` (e.g. source carry-forward).

## `phase_public` enum (for progress lines)

The poller renders progress from `(phase_public, current_iteration, total_iterations, phase_started_at)`.
Authoritative enum is **four members** (not five — there is no `queued`
member; queued state is derived client-side from `status='queued' AND
phase_public IS NULL`):

| `status` | `phase_public` | Progress line |
|---|---|---|
| `queued` | `None` | `"queued — waiting for worker"` |
| `running` | `intake` | `"intake [age]"` |
| `running` | `evaluation_setup` | `"evaluation_setup [age]"` |
| `running` | `iterating` | `"iterating [N/M] [age]"` |
| `running` | `publishing` | `"publishing [age]"` |
| terminal | (any) | poller exits and makes one `/result` call |

Never branch on the internal `phase: str` field — it is operator/Honeycomb-only.

## `status × artifact_kind` decision table

| `status` | `artifact_kind` | Installs? | Renders |
|---|---|---|---|
| `succeeded` | `enhanced` | yes | ready-to-install → installed |
| `succeeded` | other | no | source carry-forward: render reason, surface staging |
| `failed`, `rejected`, `quarantined`, `cancelled`, `enhancement_blocked`, `revoked` | (any) | no | render `result.reason` payload |

## 4xx upstream error codes (passed through verbatim from gateway)

The Python module's stdout `error.code` mirrors the upstream `error.code`
verbatim — no client-side rewriting. Upstream codes the host LLM may see:

| `error.code` | Exit | Notes |
|---|---|---|
| `duplicate_content_hash` | 2 | resumable — offer `--poll-existing <existing_job_id>` |
| `queue_full` | 5 | upstream is busy — wait + retry |
| `path_traversal`, `invalid_archive`, `empty_archive`, `size_exceeded`, `missing_skill_md`, `nested_package_not_supported`, `skill_md_not_at_root`, `invalid_skill_md` | 4 | bundle-validation failure — show error.message and stop |
| `body_too_large` | 4 | exceeded 25 MB cap |
| `invalid_skill_id`, `invalid_source`, `invalid_user_id` | 4 | gateway/client bug, not user error — surface and stop |
| `not_terminal` | 5 | poller invariant violation; should never fire from the slash command |
| `prefix_exists` | 4 | handled client-side (see below) — never surfaced raw to the user |

### `prefix_exists` — client-side cached-install fallback

When the upload returns `prefix_exists`, the slash command does **not**
surface the raw upstream message. Instead it runs

```
python -m mega_code.client.remote_enhance --list-cached --skill-name <name>
```

to walk `~/.local/share/mega-code/enhancements/*.result.json` for
prior succeeded + enhanced jobs of the same skill. The output is

```json
{"cached": [{"job_id": "...", "completed_at": "...", "roi": {...}}, ...]}
```

sorted newest-first. The slash command then offers either install-from-cache
(`--install-existing <job_id>` reuses the staging dir + cached envelope,
no network round-trip) or pick-a-different-skill via `AskUserQuestion`. The
`skill_name` field that powers this lookup is written into the cache by
`_save_result_cache` at the end of the default and poll-existing modes.

## Frontmatter contract (validated by the installer when `artifact_kind=enhanced`)

```yaml
metadata:
  author: co-authored by www.megacode.ai
  version: "1.0.0"
  generated_at: "2026-04-22T06:11:55Z"
  tags: [<non-empty list>]
  roi:
    - model: <model-id>
      performance_increase: "<pct>"
      token_savings: "<pct>"
```

Validation failures land at exit 4 (`error.code=invalid_frontmatter`). The
installer never stamps frontmatter — server output is preserved verbatim.

## CLI flags (`python -m mega_code.client.remote_enhance`)

| Flag | When required | Purpose |
|---|---|---|
| `--skill-name <name>` | default mode and `--install-existing` | resolved via `skill_enhance_helper` |
| `--poll-timeout <s>` | optional (default 1200; 0 = ∞) | polling deadline |
| `--poll-existing <job_id>` | mutually exclusive with `--install-existing` | resume polling an existing job |
| `--install-existing <job_id>` | mutually exclusive with `--poll-existing` | install from a previously staged job — **must be paired with `--install-location`** |
| `--install-location project\|global` | required when `--install-existing` is set | `project` → `<project>/.claude/skills/<name>/`; `global` → `~/.claude/skills/<name>/` |
