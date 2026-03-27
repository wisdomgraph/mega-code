<div align="center">
  <img src="logo_mega_code.png" alt="MEGA Code Logo" width="50%">
</div>

<div align="center">
  <h3>The knowledge layer for AI coding agents.</h3>
</div>

<div align="center">
  <a href="https://github.com/wisdomgraph/mega-code/releases/tag/v1.0.3-beta"><img src="https://img.shields.io/badge/version-1.0.3--beta-blue" alt="Version"></a>
  <a href="https://opensource.org/licenses/Apache-2.0"><img src="https://img.shields.io/badge/License-Apache_2.0-green.svg" alt="License"></a>
  <a href="https://github.com/wisdomgraph/mega-code"><img src="https://img.shields.io/badge/plugin-Claude_Code-blueviolet" alt="Claude Code Plugin"></a>
  <a href="https://megacode.ai"><img src="https://img.shields.io/badge/docs-megacode.ai-orange" alt="Docs"></a>
</div>

<br>

MEGA Code hooks into Claude Code's session lifecycle, extracts reusable Skills and Strategies from your execution logs, and injects them into the next session — cutting token usage by 5x and improving task scores by 13 points over baseline, outperforming Anthropic's own skill creator.

It is the first layer of a broader knowledge infrastructure for AI coding agents: from extraction today, toward orchestration, offline optimization, and a notarized sub-agent marketplace.

---

## Why MEGA Code

Most approaches to AI agent skills fail in a specific way. Skills are stored as fixed blocks and injected wholesale into context at session start. As the library grows, the prompt grows — but the reasoning doesn't improve. More skills add noise without improving capability.

What matters is not how many skills you store, but whether they can be decomposed and recomposed into structures that fit the task at hand.

MEGA Code is built around one principle: **Evaluated knowledge compounds. Unevaluated assets just adds noise.**

---

## Real Work. Real Results.

Measured head-to-head against 5 leading systems on tasks developers actually ship.

<table>
<tr>
<td align="center"><h3>1/5</h3><b>Token Usage</b><br><sub>vs no-skill baseline</sub><br><sub>169K tokens vs 897K baseline</sub></td>
<td align="center"><h3>#1</h3><b>Highest Score</b><br><sub>against 5 competing systems</sub><br><sub>78% combined avg — 4 skills x 2 models</sub></td>
<td align="center"><h3>3x</h3><b>Structural Quality</b><br><sub>vs competitor average</sub><br><sub>16/16 score across 8 structural dimensions</sub></td>
</tr>
</table>

### Token Usage

```
MEGA Code        ████░░░░░░░░░░░░░░░░  169K   ← 81% reduction
HF Upskill       ████████████████░░░░  763K
anthropic-skill  █████████████████░░░  826K
Baseline         ██████████████████░░  897K
skill-factory    ██████████████████████████████  1,448K
skill-builder    ██████████████████████████████████████████  2,024K
```

### Combined Score

```
MEGA Code        ████████████████  78%   ← #1
HF Upskill       ██████████████░░  70%
anthropic-skill  █████████████░░░  65%
Baseline         █████████████░░░  65%
skill-builder    ██████████░░░░░░  50%
skill-factory    █████████░░░░░░░  43%
```

Two of the four competing systems perform **worse than using no skills at all**. MEGA Code is the only system that beats the no-skill baseline on both token efficiency and task quality simultaneously.

> [See the full benchmark →](https://www.megacode.ai/performance)

---

## How It Works

MEGA Code installs as a Claude Code plugin and runs automatically — no new workflow required.

**At session end:**
Claude Code's execution logs are read by the MEGA Code pipeline. The pipeline identifies patterns: what procedures succeeded and are worth repeating (Skills), and what decision rules emerged from corrections and repeated choices (Strategies). These are written to structured files in your project.

**At session start:**
The Skills and Strategies files are injected into the agent's context. The agent starts the next session already knowing what worked last time.

**What gets generated:**

```
~/.local/share/mega-code/data/
├── pending-skills/{skill-name}/SKILL.md        ← reusable procedures extracted from what worked
└── pending-strategies/{strategy-name}.md       ← decision rules extracted from corrections
```

**Example - SKILL.md entry:**
```markdown
---
name: ui-consistency-and-discovery
description: 'Guidelines for maintaining UI legibility and clean aesthetics while
  using ripgrep for efficient project exploration and global string replacement.'
metadata:
  tags: [ui-ux, ripgrep, accessibility, project-navigation]
  author: co-authored by http://www.megacode.ai
  version: "1.0.0"
  generated_at: "2026-03-26T05:22:58Z"
  roi:
    model: gemini-3-flash
    performance_increase: "75%"
    token_savings: "83%"
---

## Handle authentication token refresh

When an API call returns 401, check token expiry before retrying.
Refresh using POST /auth/refresh with the stored refresh_token.
Only retry the original request once — if it fails again, surface the error.

Applies to: src/api/client.py, any authenticated endpoint
Validated: 4 sessions
```

**Example — strategies.md entry:**
```markdown
## Database migration approach

In this project, always run migrations against a local test DB first.
Schema changes that touch the users table require a backup step before applying.
Learned from: 2 rollback incidents in sessions 3 and 7.
```

The agent reads these files at the start of every session. It does not repeat the mistake that generated the strategy. It does not re-derive the procedure that generated the skill.

---

## Quick Start

### Step 1 — Install the plugin

**Claude Code**

In a Claude Code session, run:

```
/plugin marketplace add https://github.com/wisdomgraph/mega-code
```

**Install the plugin:**

```
/plugin install mega-code@mind-ai-mega-code
```

Restart Claude Code to load the plugin.

### Step 2 — Sign in

```
/mega-code:login
```

Authenticates via GitHub or Google. Your API key is saved automatically.

### Step 3 — Add your own LLM API key

MEGA Code uses a **Bring Your Own Key (BYOK)** model — you supply your own Gemini or OpenAI key.

Visit [console.megacode.ai](https://console.megacode.ai) → **Account → API Keys** to register your key.

### Step 4 — Run in any project

```
/mega-code:wisdom-gen                    # Extract skills from your sessions
/mega-code:skill-enhance                 # Enhance skills and get ROI report
/mega-code:status                 # Check results
```

---

## Codex Support

MEGA Code also works with [OpenAI Codex CLI](https://github.com/openai/codex). Install from the `codex` branch:

```bash
npx skills add https://github.com/wisdomgraph/mega-code/tree/codex -a codex
```

For Codex-specific commands and usage, see the [Codex branch README](https://github.com/wisdomgraph/mega-code/tree/codex).

---

## Free to Start

MEGA Code is currently free to use — just bring your own LLM API key (Gemini or OpenAI).
Core learning, exports, and Skills/Strategies capture are available in the current release.

---

## Available Commands

| Command | Description |
|---|---|
| `/mega-code:login` | Sign in via GitHub or Google OAuth |
| `/mega-code:wisdom-gen` | Run skill extraction pipeline |
| `/mega-code:skill-enhance` | Evaluate and enhance a skill with A/B testing |
| `/mega-code:status` | Show pending items and status |
| `/mega-code:stop` | Stop a running pipeline |
| `/mega-code:profile` | View or update your developer profile (language, level, style) |
| `/mega-code:help` | Show help and reference |

### Example Session

```bash
/mega-code:login                  # Sign in (first time)
/mega-code:profile                # Set your language, level, and style
/mega-code:wisdom-gen --project   # Extract skills from all project sessions
/mega-code:skill-enhance <skill>  # Evaluate and enhance a skill
/mega-code:status                 # See what was generated
/mega-code:stop                   # Stop a pipeline if needed
```

---

## How to update

**Claude Code:**

```
/plugin marketplace update mind-ai-mega-code
```

---

## Development Setup (from main repo)

If you are developing from the main `mega-code` repository (which includes this
as a submodule), use the sync script to test changes without committing:

```bash
# From the main mega-code repo root:
bash scripts/setup-oss-test.sh

# This syncs skills/, hooks/, client code, and installs deps.
# Then test locally with:
claude --plugin-dir mega-code-oss/plugin
```

The sync script copies the latest code from the main repo into this submodule
so you can iterate quickly without any git commits to GitHub.

## Project Structure

```
plugin/
├── .claude-plugin/
│   └── plugin.json          # Plugin metadata
├── hooks/
│   └── hooks.json           # Lifecycle hooks (SessionStart, etc.)
├── skills/
│   ├── login/SKILL.md       # /mega-code:login
│   ├── wisdom-gen/SKILL.md  # /mega-code:wisdom-gen
│   ├── skill-enhance/SKILL.md # /mega-code:skill-enhance
│   ├── status/SKILL.md      # /mega-code:status
│   ├── stop/SKILL.md        # /mega-code:stop
│   ├── profile/SKILL.md     # /mega-code:profile
│   └── help/SKILL.md        # /mega-code:help
├── mega_code/
│   └── client/              # Python client modules
├── scripts/
│   └── session-start.sh     # Bootstrap script
└── pyproject.toml
```

## mega-code:wisdom-gen Behaviour

### Session resolution

The pipeline always operates on a **project** — a set of collected sessions
grouped by working directory.

| Invocation | What gets processed |
|---|---|
| `/mega-code:wisdom-gen` | All sessions for the **current working directory** |
| `/mega-code:wisdom-gen --project` | Same as above (explicit, equivalent to no args) |
| `/mega-code:wisdom-gen --project @name` | All sessions for the named project (prefix-matched against `mapping.json`; also accepts `name`, `name_hash`, or `/absolute/path`) |
| `/mega-code:wisdom-gen --session-id <uuid>` | A single session by ID |

When no explicit project or session is given, the current working directory is
hashed to locate its data folder under `~/.local/share/mega-code/projects/`.

### Trajectory sync

Before triggering the pipeline, sessions are uploaded to the server.
The sync process uses a **ledger file** per project to track which sessions
have already been uploaded. Ledgers are stored in
`~/.local/share/mega-code/projects/{project_id}/`:

| Ledger file | Tracks |
|---|---|
| `sync-ledger.json` | mega-code's own sessions (and Claude Code native sessions) |
| `codex-sync-ledger.json` | Codex CLI sessions |

- Sessions not in the ledger are uploaded.
- Sessions already in the ledger are skipped, **unless** the source file's
  `mtime` has changed since the last upload — in which case the session is
  re-uploaded. This handles sessions whose files are appended to after the
  initial upload (e.g. a long-running session that gains new turns).
- The ledger records `uploaded_at`, `turn_count`, and (where applicable)
  `file_mtime` for each synced session.

#### Sync invariants

1. **No data loss on first run.** When no ledger exists, every locally stored
   session for the project MUST be uploaded — not just the current terminal session.
2. **Idempotency.** Re-running `/mega-code:wisdom-gen` with an up-to-date ledger produces
   no duplicate uploads.
3. **Modified-session re-sync.** If a session file's `mtime` has changed
   since the last recorded upload, it MUST be re-uploaded.
4. **Filter-before-upload.** All turns pass through `SecretMasker` and
   `PathAnonymizer` before transmission. No raw absolute paths or secrets leave
   the client.

### Pipeline lifecycle

1. **Trigger** — the client sends the project ID (and optionally a session ID)
   to the server.
2. **Poll** — the client polls until the server reports completion, failure, or
   timeout. Default poll timeout is 20 minutes (`--poll-timeout` to override;
   `0` means wait indefinitely).
3. **Save** — on success, extracted Skills and Strategies are written to local
   pending folders for review.

| Exit code | Meaning |
|---|---|
| `0` | Success — outputs saved, post-pipeline review begins |
| `1` | Fatal error (auth, network, unexpected failure) |
| `2` | Conflict — a pipeline is already running for this project |
| `3` | Server timeout — the pipeline exceeded max server runtime |

---

## Configuration

Configuration is stored in `~/.local/share/mega-code/` and persists across sessions.
Use `/mega-code:login` to authenticate, or `mega-code configure` CLI for advanced settings.

## Terms of Service

By using this plugin, you agree to the [Terms of Service](https://megacode.ai/terms).

## License

Apache-2.0
