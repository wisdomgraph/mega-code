<div align="center">
  <img src="logo_mega_code.png" alt="MEGA Code Logo" width="50%">
</div>

<div align="center">
  <h3>Self Optimizing Infrastructure for AI Coding Agents</h3>
</div>

<div align="center">
  <a href="https://github.com/wisdomgraph/mega-code/releases/tag/v1.1.1-beta"><img src="https://img.shields.io/badge/version-1.1.1--beta-blue" alt="Version"></a>
  <a href="https://opensource.org/licenses/Apache-2.0"><img src="https://img.shields.io/badge/License-Apache_2.0-green.svg" alt="License"></a>
  <a href="https://github.com/wisdomgraph/mega-code/tree/codex"><img src="https://img.shields.io/badge/plugin-Codex_CLI-blueviolet" alt="Codex CLI Plugin"></a>
  <a href="https://megacode.ai"><img src="https://img.shields.io/badge/docs-megacode.ai-orange" alt="Docs"></a>
</div>

<br>

MEGA Code is a self-evolving infrastructure layer for AI coding agents. It hooks into Codex CLI's session lifecycle, turning your coding sessions into reusable wisdom by generating skills and strategies from real execution traces, decomposing validated knowledge into Primary-Context-Resultant (PCR) units, and reinjecting the right knowledge back into future tasks. Instead of treating skills as flat blocks, MEGA Code structures them at the atomic level so they can be retrieved, recomposed, and improved over time. The result is not just persistence, but compounding problem-solving quality.

This wisdom is stored in the Wisdom Graph DB: a structured graph that maps relationships between procedures, contexts, constraints, and outcomes across sessions. Rather than loading entire skill blocks into context, MEGA Code retrieves only the knowledge relevant to the user’s current intent, along with workflow-level guidance and step-by-step cheatmaps. It also evaluates generated skills, surfaces ROI, and provides enhanced versions, so the system improves not only by accumulation, but by refinement. This is what allows quality and efficiency to improve together rather than trade off against each other.

---

## Why MEGA Code

Most approaches to AI agent skills fail in a predictable way. Skills are stored as fixed blocks and injected wholesale into context at session start. As the library grows, the prompt grows — but the reasoning does not. More skills often mean more noise, not more capability.

What matters is not how many skills you store, but whether knowledge can be decomposed, retrieved, recomposed, and improved in a form that fits the task at hand.

MEGA Code is built around one principle: **Evaluated wisdom compounds. Unevaluated assets just add noise.**

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

MEGA Code installs as a Codex CLI plugin and runs inside your existing workflow — no new coding workflow required.

MEGA Code works through three core flows:

**1. wisdom-gen**  
MEGA Code reads your coding session traces and extracts reusable wisdom from what actually happened. It identifies:
- **Skills**: reusable procedures that worked
- **Strategies**: decision rules and correction patterns that emerged across repeated choices
- **PCR units**: atomic Primary-Context-Resultant structures distilled from validated knowledge

These are written into structured local assets and prepared for reuse.

**2. wisdom-curate**  
MEGA Code does not simply inject an entire skill library into context. Instead, it decomposes curated skills into atomic PCR-level wisdom, stores them in the Wisdom Graph DB, and retrieves only the knowledge relevant to your current intent.

For a given command or task, MEGA Code can provide:
- the most relevant Skills and Strategies
- a recommended workflow for solving the problem
- a **Cheatmap** explaining which skills should be used at each step and why

This allows the agent to use the right knowledge in the right structure, instead of loading everything and adding noise.

> **Note:** For wisdom-curate to work correctly, all Skills recommended by MEGA Code must be installed. Missing skills will cause the curation to reference procedures that the agent cannot access.

**3. skill-enhance**  
MEGA Code evaluates generated skills, measures their ROI, and produces enhanced versions. Instead of merely accumulating more assets, the system improves the quality, efficiency, and transferability of the skills you already have.

**What gets generated locally:**

```bash
~/.local/share/mega-code/data/
├── pending-skills/{skill-name}/SKILL.md         ← reusable procedures extracted from session traces
├── pending-strategies/{strategy-name}.md        ← decision rules extracted from corrections and repeated choices
└── enhanced-skills/{skill-name}/SKILL.md        ← evaluated and enhanced versions with ROI insights

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

**Example — Cheatmap output:**
```markdown
Wisdom Curation

Problem
Situation: The user is currently in the late stages of a web development project and is looking to refine the visual aesthetics and user interface components of their existing website to achieve a more professional look.
Symptoms: The current design likely suffers from a generic layout, poor visual hierarchy, or suboptimal color schemes that result in low user engagement metrics and a lack of distinctive brand identity.
Goals: The user aims to acquire advanced front-end design techniques and UI/UX principles that will significantly elevate the visual quality of the site, ultimately improving user retention rates and overall aesthetic appeal.

Expected ROI
Metric          Value
Portfolio P     0.98
Items           5
Steps routed    4/4

IMPORTANT: How to use this curation
This curation contains a step-by-step workflow. Each step may have a Reference: entry pointing to domain-specific knowledge that you likely do NOT already know. Before executing each step, you MUST read the referenced section.

step-1: Visual Hierarchy and Aesthetic Audit
Stage: diagnosis | P=1.00 | PASS

Portfolio: 1 core + 0 supporting skills selected for complementary coverage.

1. [H] Visual and Accessibility Audit (score=0.508)
P: Assess visual polish against an 8px spacing scale, typography hierarchy, and semantic color usage. Verify WCAG 2.1 AA compliance, specifically color contrast ratios and keyboard tab order.
R: UI components are fully keyboard-accessible and screen-reader friendly. The design system remains consistent by using a single source of truth for primitives.
Reference: design-review/SKILL.md#Phase 3: Visual Polish L136-150
Reference: design-review/SKILL.md#Phase 4: Accessibility (WCAG 2.1 AA) L153-174

step-2: Advanced UI Component Design Systems
Stage: planning | P=1.00 | PASS

Portfolio: 1 core + 1 supporting skills selected for complementary coverage.

1. [H] micro-interaction-and-animation-implementation (score=0.501)
P: Apply subtle CSS transitions and spring physics to buttons, toggles, and form elements to create satisfying tactile feedback.
R: Interface elements provide immediate, satisfying visual and haptic feedback within 1 second.
Reference: delight/SKILL.md#Micro-interactions & Animation L84-122
Reference: delight/SKILL.md#Satisfying Interactions L175-200

2. [M] Animation and Motion Constraints (score=0.372)
P: Apply performant animation constraints using motion/react and Tailwind CSS to prevent interface slop.
R: Animations are smooth and do not trigger expensive browser layout or paint cycles.
Reference: baseline-ui/SKILL.md#Animation L52-64

step-3: Micro-interaction and Motion Implementation
Stage: implementation | P=1.00 | PASS

step-4: User Engagement and Retention Testing
Stage: validation | P=1.00 | PASS
```

---

## Quick Start

### Step 1 — Install

```bash
npx skills add https://github.com/wisdomgraph/mega-code/tree/codex -a codex
```

### Step 2 — Sign in

Open a Codex CLI session, then run:

```
$mega-code-login
```

Authenticates via GitHub or Google. A service key is automatically issued and saved for your account.

### Step 3 — Run in any project

```
$mega-code-wisdom-gen             # Generate skills and strategies from session traces
$mega-code-wisdom-curate          # Retrieve the right skills, workflows, and cheatmaps for your intent
$mega-code-skill-enhance          # Evaluate skills, measure ROI, and generate enhanced versions
$mega-code-status                 # Check results and pipeline status
```

---

## Improving User Interaction UX in Codex

Compared to Claude Code, Codex currently has limitations in user interaction UX. However, the good news is that Codex has started implementing features to address this gap.

While this is not yet officially released, you can already enable a Claude Code–like user interaction experience by updating your local configuration.

Add the following to your `~/.codex/config.toml`:

```toml
[features]
multi_agent = true
default_mode_request_user_input = true
```

With this configuration, Codex can provide a more interactive and responsive UX, similar to what we see in Claude Code.

> **Note:** This is an early-stage capability, but it's a strong signal that Codex is moving toward a more advanced interaction model.

---

## Claude Code Support

MEGA Code also works with [Claude Code](https://docs.anthropic.com/en/docs/claude-code). See the [main branch README](https://github.com/wisdomgraph/mega-code) for Claude Code installation and commands.

---

## Free to Start

MEGA Code is currently free to use.

The current release includes:
- **wisdom-gen** for generating Skills and Strategies from coding sessions
- **wisdom-curate** for retrieving relevant workflows and Cheatmaps from the Wisdom Graph DB
- **skill-enhance** for evaluating skills and generating enhanced versions with ROI insights

---

## Available Commands

| Command | Description |
|---|---|
| `$mega-code-login` | Sign in via GitHub or Google OAuth |
| `$mega-code-wisdom-gen` | Generate Skills and Strategies from session traces |
| `$mega-code-wisdom-curate` | Retrieve relevant Skills, workflows, and Cheatmaps for your current intent |
| `$mega-code-skill-enhance` | Evaluate and enhance a Skill with ROI analysis |
| `$mega-code-status` | Show generated assets and pipeline status |
| `$mega-code-stop` | Stop a running pipeline |
| `$mega-code-profile` | View or update your developer profile (language, level, style) |
| `$mega-code-update` | Update the mega-code plugin |
| `$mega-code-help` | Show help and reference |

### Example Session

```bash
$mega-code-login                  # Sign in (first time)
$mega-code-profile                # Set your language, level, and style
$mega-code-wisdom-gen --project   # Generate skills and strategies from project session traces
$mega-code-wisdom-curate          # Retrieve the best workflow and cheatmap for the current task
$mega-code-skill-enhance <skill>  # Evaluate and enhance a skill
$mega-code-status                 # See what was generated
$mega-code-stop                   # Stop a pipeline if needed
```

---

## How to update

**Codex CLI:**
```
$mega-code-update
```

In case you do not have this skill yet:
```bash
npx skills add https://github.com/wisdomgraph/mega-code/tree/codex -a codex
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
codex --plugin-dir mega-code-oss/plugin
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
│   ├── login/SKILL.md       # $mega-code-login
│   ├── wisdom-gen/SKILL.md  # $mega-code-wisdom-gen
│   ├── status/SKILL.md      # $mega-code-status
│   ├── stop/SKILL.md        # $mega-code-stop
│   ├── profile/SKILL.md     # $mega-code-profile
│   ├── update/SKILL.md      # $mega-code-update
│   └── help/SKILL.md        # $mega-code-help
├── mega_code/
│   └── client/              # Python client modules
├── scripts/
│   └── session-start.sh     # Bootstrap script
└── pyproject.toml
```

## `$mega-code-wisdom-gen` Behaviour

### Session resolution

The pipeline always operates on a **project** — a set of collected sessions
grouped by working directory.

| Invocation | What gets processed |
|---|---|
| `$mega-code-wisdom-gen` | All sessions for the **current working directory** |
| `$mega-code-wisdom-gen --project` | Same as above (explicit, equivalent to no args) |
| `$mega-code-wisdom-gen --project @name` | All sessions for the named project (prefix-matched against `mapping.json`; also accepts `name`, `name_hash`, or `/absolute/path`) |
| `$mega-code-wisdom-gen --session-id <uuid>` | A single session by ID |

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
2. **Idempotency.** Re-running `$mega-code-wisdom-gen` with an up-to-date ledger produces
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
Use `$mega-code-login` to authenticate, or `mega-code configure` CLI for advanced settings.

## Terms of Service

By using this plugin, you agree to the [Terms of Service](https://megacode.ai/terms).

## License

Apache-2.0
