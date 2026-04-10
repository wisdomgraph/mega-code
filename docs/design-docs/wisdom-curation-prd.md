# Wisdom Curation — Product Requirements Document

**Product**: MEGA-Code Wisdom Curation System  
**Version**: 1.0  
**Author**: Senior Software Architect  
**Date**: 2026-04-06  
**Status**: Review-Ready (3 iterations completed)

---

## 1. Executive Summary

The Wisdom Curation System is a Claude Code skill that bridges the PCR (Primary-Context-Resultant) Wisdom Graph with actionable developer workflows. Given a natural-language task description, it retrieves relevant wisdoms, curates them into a step-by-step workflow with skill references, installs the recommended skills, and optionally executes the workflow — all within a single interactive session. A mandatory post-execution feedback loop (after Run Now) closes the learning cycle, continuously improving the wisdom graph's routing accuracy.

---

## 2. Problem Statement

Developers using Claude Code lack a structured mechanism to:
1. **Discover** relevant best practices and patterns from the accumulated wisdom graph for their specific task.
2. **Operationalize** those wisdoms into an executable, context-aware workflow.
3. **Install** the right skill packages automatically without manual lookup.
4. **Provide feedback** to improve future curation quality.

The current state requires developers to manually search for skills, read documentation, and assemble workflows — a time-consuming, error-prone process that does not leverage the collective knowledge in the wisdom graph.

---

## 3. Goals and Non-Goals

### Goals
- **G1**: Provide a single-command entry point (`/mega-code:wisdom-curate`) that transforms a task description into a curated, executable workflow.
- **G2**: Automatically detect project context (language, framework, version) to improve curation relevance.
- **G3**: Install recommended skills from presigned URLs with security guarantees (SSRF, path traversal, zip slip protection).
- **G4**: Persist curations with lifecycle management (`pending → running → completed`) to enable session resumption.
- **G5**: Collect structured feedback (ratings, per-step analysis, missing/unexpected items) to feed the wisdom graph's learning loop.
- **G6**: Support both immediate execution and deferred ("Later") workflows.

### Non-Goals
- **NG1**: Building the server-side wisdom graph or curation LLM — this PRD covers the client-side orchestration only.
- **NG2**: Offline/local-only curation — the system requires remote mode (MegaCodeRemote) for wisdom retrieval.
- **NG3**: Partial or selective skill installation — the design enforces a binary all-or-nothing install decision.
- **NG4**: Automatic re-curation on feedback — feedback is collected and submitted; re-routing is a server-side concern.

---

## 4. Architecture Overview

### 4.1 System Context

```
┌──────────────────────────────────────────────────────────────────┐
│                        Claude Code Host                          │
│  ┌─────────────────────────────────────────────────────────────┐ │
│  │  /mega-code:wisdom-curate  (SKILL.md orchestrator)         │ │
│  │                                                             │ │
│  │  Setup:   Auth Gate (check_auth)                             │ │
│  │  Step 1:  Validate Input                                     │ │
│  │  Step 2:  Generate Session ID                                │ │
│  │  Step 2b: Detect Project Context                             │ │
│  │  Step 3:  Curate Skills (CLI → Remote API)                   │ │
│  │  Step 4:  Present Summary + Install Decision                 │ │
│  │  Step 5:  Install Skills                                     │ │
│  │  Step 6:  Save Curation + Run Decision                       │ │
│  │  Step 7:  Run Now (optional) / Step 8: Later                 │ │
│  │  Feedback: Submit (after Run Now only)                       │ │
│  └─────────────┬──────────────────────┬────────────────────────┘ │
│                │                      │                          │
│  ┌─────────────▼──────────────────────▼─────────────┐            │
│  │  CLI Layer (mega-code wisdom-curate/feedback)     │            │
│  │  (cli.py → create_client() → MegaCodeRemote)     │            │
│  └─────────────┬──────────────────────┬─────────────┘            │
│                │                      │                          │
│  ┌─────────────▼──────┐  ┌───────────▼─────────────┐            │
│  │  Skill Installer   │  │  Curation Store          │            │
│  │  (skill_installer) │  │  (curation_store)        │            │
│  │  Download + Extract │  │  Persist + Lifecycle     │            │
│  └─────────────┬──────┘  └───────────┬─────────────┘            │
│                │                      │                          │
│  ┌─────────────▼──────────────────────▼─────────────┐            │
│  │  Local Data Store ({data_dir()})                 │            │
│  │  skills/{name}/  |  curations/{status}/          │            │
│  └──────────────────────────────────────────────────┘            │
└─────────────────────────────┬────────────────────────────────────┘
                              │ HTTPS (Bearer token)
                              ▼
┌──────────────────────────────────────────────────────────────────┐
│                    MEGA-Code API Server                           │
│  POST /api/megacode/v1/wisdom/curate                             │
│  POST /api/megacode/v1/wisdom/feedback                           │
│  (Wisdom Graph + Curation LLM + Skill Registry)                 │
└──────────────────────────────────────────────────────────────────┘
```

### 4.2 Component Inventory

| Component | Module | Responsibility |
|-----------|--------|----------------|
| **CLI Commands** | `mega_code/client/cli.py` | Parse arguments, invoke remote client, print JSON output |
| **Remote Client** | `mega_code/client/api/remote.py` | HTTP POST with retry/backoff/tracing to server endpoints. **Note**: `wisdom_curate` and `wisdom_feedback` are implemented exclusively on `MegaCodeRemote` — they are not part of the `MegaCodeBaseClient` Protocol, enforced via `isinstance` check in the CLI layer. |
| **Protocol Models** | `mega_code/client/api/protocol.py` | Pydantic schemas: `WisdomCurateResult`, `SkillRefItem`, `WisdomResultItem`, `WisdomFeedbackResult`. **Note**: `WisdomCurateResult`, `WisdomResultItem`, and `WisdomFeedbackResult` are not currently exported in `__all__`. |
| **Skill Installer** | `mega_code/client/skill_installer.py` | Download ZIPs from presigned S3 URLs, validate, extract |
| **Curation Store** | `mega_code/client/curation_store.py` | Persist curations as JSON, manage `pending → running → completed` lifecycle |
| **Directory Resolver** | `mega_code/client/dirs.py` | XDG-compliant path resolution for data storage |
| **Client Factory** | `mega_code/client/api/__init__.py` | Mode detection (local/remote), client instantiation |
| **Skill Orchestrator** | `skills/wisdom-curate/SKILL.md` | User-facing 8-step workflow (Claude Code skill) |

---

## 5. Functional Requirements

### FR-0: Authentication Gate (Prerequisite)
- **FR-0.1**: Before any workflow step, run `mega_code.client.check_auth` to verify the user's API key is valid and not expired.
- **FR-0.2**: If the auth check fails (non-zero exit), display the error output to the user and halt the entire workflow.
- **FR-0.3**: Auth failure messages vary by cause:
  - Expired key → direct to `/mega-code:login` to re-authenticate.
  - Missing key → direct to `mega-code configure --api-key`.
  - Connection failure (`ConnectError`/`TimeoutException`) → display "Cannot reach authentication server. Check your connection." — login/configure will not help in this case.

### FR-1: Input Validation
- **FR-1.1**: If no task description is provided via `$ARGUMENTS`, prompt the user interactively via `AskUserQuestion`.
- **FR-1.2**: Do not proceed until a non-empty task description is obtained.
- **FR-1.3**: Store the validated input as `TASK_QUERY`.

### FR-2: Session Management
- **FR-2.1**: Generate a unique session ID per curation (prefer `CLAUDE_SESSION_ID`, fallback to `uuidgen` or Python `uuid4`).
- **FR-2.2**: The session ID must link the curation, its execution status, and its feedback.
- **FR-2.3**: Session IDs must be alphanumeric with dashes/underscores only (regex: `[a-zA-Z0-9_\-]+`).

### FR-3: Project Context Detection
- **FR-3.1**: Scan the working directory for manifest files (package.json, pyproject.toml, go.mod, Cargo.toml, pom.xml, build.gradle, Gemfile, composer.json, *.csproj, etc.).
- **FR-3.2**: Read the first manifest found (limit 50 lines) to determine language, version, and key frameworks.
- **FR-3.3**: Compose a descriptive `TASK_CONTEXT` string (e.g., "Python 3.12 project using FastAPI, SQLAlchemy").
- **FR-3.4**: If no recognizable manifest is found, leave `TASK_CONTEXT` empty.
- **FR-3.5**: Context detection must use the LLM's own knowledge to interpret manifest contents — do NOT use external scripts or tooling for tech stack analysis.

### FR-4: Wisdom Curation
- **FR-4.1**: Call `mega-code wisdom-curate` CLI with the formatted query (appending `TASK_CONTEXT` if available).
- **FR-4.2**: The CLI must operate in remote mode only — fail with a clear error if local mode is detected.
- **FR-4.3**: Parse the JSON response into `curation` (Markdown workflow), `skills` (list of `SkillRefItem`), and `wisdoms` (list of `WisdomResultItem`).
- **FR-4.4**: Support configurable `top_k` parameter (default: 20) for wisdom retrieval depth.
- **FR-4.5**: Retry transient server errors (429, 502, 503, 504) up to 5 times with exponential backoff (0.5s → 8s) and jitter.

### FR-5: Workflow Presentation
- **FR-5.1**: Present a structured summary: workflow title, overview, numbered steps with skill references.
- **FR-5.2**: Show each recommended skill's installation status (already installed / not installed).
- **FR-5.3**: Offer a binary install decision: "Yes" (install all missing skills) or "Skip" (install nothing).
- **FR-5.4**: Do not offer partial or selective installation.

### FR-6: Skill Installation
- **FR-6.1**: Download skill ZIPs from presigned S3 URLs via HTTPS only (no HTTP, no redirects).
- **FR-6.2**: Validate skill names to prevent path traversal.
- **FR-6.3**: Detect zip slip attacks by verifying all extracted paths are within the destination directory.
- **FR-6.4**: Enforce download size limits (default: 100MB, configurable via `SKILL_MAX_DOWNLOAD_MB`, capped at 500MB).
- **FR-6.5**: Enforce download timeout (default: 120s, configurable via `SKILL_DOWNLOAD_TIMEOUT`, capped at 300s).
- **FR-6.6**: Extract skills to `{data_dir()}/skills/{skill-name}/` with a `SKILL.md` file as the recognition marker.
- **FR-6.7**: Report per-skill installation status (installed / skipped / failed).

### FR-7: Curation Persistence
- **FR-7.1**: Save curations as JSON in `{data_dir()}/curations/{status}/{session_id}.json`.
- **FR-7.2**: Support three lifecycle states: `pending`, `running`, `completed`.
- **FR-7.3**: Provide CRUD operations: save, get (by session_id), list (by status), update status.
- **FR-7.4**: Status transitions: `pending → running` (on execution start), `running → completed` (on execution finish).

### FR-8: Workflow Execution (Optional)
- **FR-8.1**: If the user chooses "Run now", update curation status to `running` and execute each step.
- **FR-8.2**: For each step referencing a skill, read the installed skill's `SKILL.md` for domain knowledge.
- **FR-8.3**: Adapt each step to the user's specific project context.
- **FR-8.4**: Support section-level references (e.g., `python-pro/SKILL.md#Type Hints L42-78`).
- **FR-8.5**: On completion, update curation status to `completed`.
- **FR-8.6**: If the user chooses "Later", inform them of saved curation location and installed skills, and explicitly tell them they can resume execution **in the same conversation** by asking Claude to run it.
- **FR-8.7**: **In-session resume**: if the user later asks in the same conversation to run a Later-saved curation, Claude must re-enter the Run Now path (FR-8.1–FR-8.5) using the SESSION_ID and curation already held in conversation context — no new skill invocation, no new CLI command. After resumption, FR-9.1 (mandatory feedback) applies just as if Run Now had been chosen initially. Cross-session resumption is out of scope for this PRD.

### FR-9: Feedback Collection (After Execution)
- **FR-9.1**: Feedback submission is mandatory **whenever Step 7 actually executes** — whether the user picked "Run now" at Step 6 or resumed a Later-saved curation in the same conversation per FR-8.7. The "Later" path (Step 8) before any resumption does NOT submit feedback, because no execution result exists to evaluate — feedback is purpose-built to capture install + run outcomes, not curation alone.
- **FR-9.2**: After "Run now" execution, submit full feedback with 6 fields:
  - Overall rating (1-5) with accuracy/efficiency impact estimate
  - Per-step rating with wisdom application status (applied / partial / not used)
  - Missing skills or strategies
  - Unexpected inclusions (surprisingly useful or harmful)
  - Per-item improvement recommendations
  - Update flags for outdated information (wrong model names, deprecated APIs)
- **FR-9.3**: Submit feedback via `mega-code wisdom-feedback --session-id <ID> --feedback-text <text>`.

---

## 6. Non-Functional Requirements

### NFR-1: Security
- **NFR-1.1**: All API communication uses Bearer token authentication.
- **NFR-1.2**: Skill downloads enforce HTTPS-only, no-redirect policy (SSRF mitigation).
- **NFR-1.3**: Path traversal protection on session IDs, skill names, and ZIP contents.
- **NFR-1.4**: Zip slip detection on all archive extraction operations.
- **NFR-1.5**: Download size and timeout caps prevent resource exhaustion. **Note**: Size check is post-download (full buffer in memory) rather than streaming — acceptable for the default 100MB cap but could be a concern at the 500MB maximum.

### NFR-2: Reliability
- **NFR-2.1**: Automatic retry with exponential backoff and jitter on transient failures (5 attempts, 0.5s–8s delay).
- **NFR-2.2**: Graceful degradation: missing skill URLs result in "skipped" status, not failure.
- **NFR-2.3**: Auth errors produce actionable error messages. Two distinct paths exist: `check_auth` directs users to `/mega-code:login` for re-authentication; `MegaCodeRemote._check_response` directs to `mega-code configure --api-key` for 401/403 HTTP errors.

### NFR-3: Observability
- **NFR-3.1**: All remote API calls instrumented with OpenTelemetry spans (client.remote.wisdom_curate, client.remote.wisdom_feedback).
- **NFR-3.2**: Span attributes include: query, session_id, top_k (for curate); session_id (for feedback).
- **NFR-3.3**: Token count and cost (USD) tracked per curation for usage monitoring.

### NFR-4: Data Integrity
- **NFR-4.1**: Curations stored as individual JSON files — no shared database, no corruption risk from concurrent access.
- **NFR-4.2**: Session ID validation via regex prevents file system abuse.
- **NFR-4.3**: Status transitions use write-then-unlink (write to new status directory, then delete from old). This is not truly atomic — a crash between write and unlink could leave a curation in two status directories. Acceptable for single-user CLI usage; a future improvement could use `os.replace()` for same-partition atomicity.

### NFR-5: Portability
- **NFR-5.1**: XDG Base Directory Specification compliance for data storage.
- **NFR-5.2**: `MEGA_CODE_DATA_DIR` environment variable for explicit override.
- **NFR-5.3**: Python 3.11–3.13 compatibility.

### NFR-6: Performance
- **NFR-6.1**: Manifest detection via `Glob("*")` — single directory scan, no recursive traversal.
- **NFR-6.2**: CLI outputs clean JSON (`indent=2`) to stdout on success; errors are written to stderr only. No extraneous echo statements that would corrupt JSON parsing.
- **NFR-6.3**: Skill installer cleans old folder before extraction — no stale file accumulation.

---

## 7. Data Models

### 7.1 WisdomCurateResult

| Field | Type | Description |
|-------|------|-------------|
| `session_id` | `str` | UUID linking curation, execution, and feedback |
| `query` | `str` | Original user task description |
| `curation` | `str` | Markdown document with step-by-step workflow |
| `skills` | `list[SkillRefItem]` | Curated skill references with presigned download URLs |
| `wisdoms` | `list[WisdomResultItem]` | Retrieved wisdom records with relevance scores |
| `token_count` | `int` | LLM tokens consumed during curation |
| `cost_usd` | `float` | Estimated cost in USD |

### 7.2 SkillRefItem

| Field | Type | Description |
|-------|------|-------------|
| `name` | `str` | Skill identifier (used as directory name) |
| `path` | `str` | Relative path to skill file within the ZIP |
| `url` | `str` | Presigned S3 URL for skill ZIP download (empty = no download) |

### 7.3 WisdomResultItem

| Field | Type | Description |
|-------|------|-------------|
| `wisdom_id` | `str` | Unique identifier in the wisdom graph |
| `score` | `float` | Relevance score from graph traversal |
| `is_seed` | `bool` | Whether this wisdom is a seed/root node |

### 7.4 WisdomFeedbackResult

| Field | Type | Description |
|-------|------|-------------|
| `session_id` | `str` | Links back to the original curation |
| `feedback_id` | `str` | Unique identifier for this feedback submission |
| `status` | `str` | Submission status (default: "saved") |

### 7.5 SavedCuration

| Field | Type | Description |
|-------|------|-------------|
| `session_id` | `str` | UUID linking to the original curation |
| `query` | `str` | Original user task description |
| `curation` | `str` | Markdown workflow document |
| `token_count` | `int` | Tokens consumed |
| `cost_usd` | `float` | Cost in USD |
| `created_at` | `str` | ISO 8601 timestamp |
| `status` | `CurationStatus` | `"pending"` / `"running"` / `"completed"` |

> **Design Decision**: `SavedCuration` intentionally excludes `skills` and `wisdoms` from `WisdomCurateResult`. Presigned S3 URLs in `skills` expire after a short TTL, making them useless at resume time. Wisdom IDs are not actionable for resumption. Only the `curation` Markdown (which embeds skill references by name) is preserved.
>
> **Spec Divergence (Action Required)**: SKILL.md's Data Directory section incorrectly lists `skills` and `wisdoms` as persisted fields in the curation JSON. This is incorrect documentation in the skill spec — the Data Directory section must be updated to reflect the actual persisted fields. The implementation in `curation_store.py` is the authoritative source. Any resume-path work must not assume `skills` or `wisdoms` are available in the saved JSON.

---

## 8. API Contracts

### 8.1 Wisdom Curate

```
POST /api/megacode/v1/wisdom/curate
Authorization: Bearer <api_key>
Content-Type: application/json

Request:
{
  "query": "Build a REST API with authentication",
  "session_id": "abc-123",     // optional — omitted from body entirely when empty
  "top_k": 20                  // optional, default 20
}

Note: The client omits `session_id` from the request body when it is an
empty string, rather than sending an empty value.

Response (200):
{
  "session_id": "abc-123",
  "query": "Build a REST API with authentication",
  "curation": "# REST API Workflow\n\n## Step 1: ...",
  "skills": [
    {"name": "fastapi-pro", "path": "SKILL.md", "url": "https://..."}
  ],
  "wisdoms": [
    {"wisdom_id": "w-001", "score": 0.95, "is_seed": false}
  ],
  "token_count": 1500,
  "cost_usd": 0.02
}
```

### 8.2 Wisdom Feedback

```
POST /api/megacode/v1/wisdom/feedback
Authorization: Bearer <api_key>
Content-Type: application/json

Request:
{
  "session_id": "abc-123",
  "feedback_text": "Overall: 4/5. Step 1 was excellent..."
}

Response (200):
{
  "session_id": "abc-123",
  "feedback_id": "fb-456",
  "status": "saved"
}
```

### 8.3 Error Responses

| Status | Condition | Client Behavior |
|--------|-----------|-----------------|
| 400 | Malformed request | Raise `ValueError` with response body |
| 401/403 | Invalid/expired API key | Raise `ValueError` with auth setup instructions |
| 429 | Rate limited | Retry with backoff (up to 5 attempts) |
| 502/503/504 | Transient server error | Retry with backoff (up to 5 attempts) |

---

## 9. User Interaction Flow

### 9.1 Happy Path

```
User: /mega-code:wisdom-curate Build a FastAPI app with JWT auth

[Auth check passes]

> Analyzing task... Curating skills...

[Server returns curation with 3 skills]

Workflow: FastAPI JWT Authentication
Overview: Build a production-ready FastAPI application with JWT-based auth.
Steps:
1. Project scaffolding — Skill: fastapi-pro
2. JWT middleware setup — Skill: auth-patterns
3. Database models — Skill: sqlalchemy-pro
4. Testing — (no skill reference)

3 skills recommended for this workflow.

The following skills are recommended:
1. fastapi-pro — [Not installed]
2. auth-patterns — [Not installed]
3. sqlalchemy-pro — [Already installed]

Would you like to install the 2 new skills? (Yes / Skip)

User: Yes

Installed: fastapi-pro ✓
Installed: auth-patterns ✓
Skipped: sqlalchemy-pro (already installed)

Your task is ready to run. Would you like to:
- Run now — execute the workflow with the installed skills
- Later — end here, you can use the skills manually later

User: Run now

[Executes each step, reading installed skills for context]
[Updates status: pending → running → completed]

[Submits feedback automatically]
```

### 9.2 Deferred Execution Path

```
User: /mega-code:wisdom-curate best practices for React testing

[Curation completes, skills installed]

User: Later

Skills installed: react-testing, jest-patterns
Curation saved to: ~/.local/share/mega-code/curations/pending/{session_id}.json

You can ask me to run this workflow later in this same conversation —
just say "run it now" and I will resume with mandatory feedback after
execution.

[End — no feedback submitted yet because the workflow was not executed]

... (later in the same conversation) ...

User: ok let's run that curation now

[Re-enters Step 7 with the same session_id from context]
[Updates status: pending → running → completed]
[Submits 6-field feedback — mandatory per FR-9.1]
```

---

## 10. File System Layout

```
{data_dir()}/
├── skills/
│   ├── fastapi-pro/
│   │   ├── SKILL.md              ← recognition marker
│   │   ├── scripts/              ← optional
│   │   └── references/           ← optional
│   ├── auth-patterns/
│   │   └── SKILL.md
│   └── sqlalchemy-pro/
│       └── SKILL.md
└── curations/
    ├── pending/
    │   └── {session_id}.json     ← saved, not yet executed
    ├── running/
    │   └── {session_id}.json     ← currently executing
    └── completed/
        └── {session_id}.json     ← finished
```

---

## 11. Security Considerations

| Threat | Mitigation | Implementation |
|--------|-----------|----------------|
| **SSRF** | HTTPS-only, no-redirect policy for skill downloads | `skill_installer.py`: URL scheme validation, `follow_redirects=False` |
| **Path Traversal** | Regex validation on session IDs and skill names | `curation_store.py`: `[a-zA-Z0-9_\-]+`; `skill_installer.py`: `is_relative_to()` check |
| **Zip Slip** | Post-extraction path validation | `skill_installer.py`: verify all extracted paths within destination |
| **Resource Exhaustion** | Size and timeout caps on downloads | 100MB default (500MB max), 120s default (300s max) |
| **Token Leakage** | Bearer token via Authorization header only | `remote.py`: httpx client with header-based auth |
| **Prompt Injection** | CLI outputs JSON only — no user-controlled echo | SKILL.md: explicit warning against `echo` in curate step |

---

## 12. Configuration

| Environment Variable | Default | Description |
|---------------------|---------|-------------|
| `MEGA_CODE_API_KEY` | (required) | Bearer token for API authentication |
| `MEGA_CODE_SERVER_URL` | `http://localhost:8000` | API server base URL |
| `MEGA_CODE_DATA_DIR` | XDG data home / `mega-code` | Override for data directory |
| `MEGA_CODE_CLIENT_MODE` | See note | Force `local` or `remote` mode. **Note**: Two code paths exist with different defaults when this env var is unset. `resolve_mode()` defaults to `"local"`. `create_client()` (used by wisdom-curate CLI) auto-detects: `"local"` if `mega_code.pipeline` is installed, otherwise `"remote"`. In standalone client environments (no pipeline package), the effective default for wisdom commands is `"remote"`. |
| `SKILL_MAX_DOWNLOAD_MB` | `100` | Max skill ZIP size in MB (capped at 500) |
| `SKILL_DOWNLOAD_TIMEOUT` | `120` | Skill download timeout in seconds (capped at 300) |

---

## 13. Dependencies

| Package | Version | Purpose |
|---------|---------|---------|
| `httpx` | ≥ 0.27.0 | HTTP client for API calls and skill downloads |
| `tenacity` | ≥ 8.2.0 | Retry logic with exponential backoff |
| `pydantic` | ≥ 2.0.0 | Data validation for all protocol models |
| `python-dotenv` | ≥ 1.0.0 | `.env` file loading for credentials |
| `rich` | ≥ 14.3.3 | Terminal UI (logging, progress output) |
| `opentelemetry-api` | ≥ 1.20.0 | Distributed tracing — **optional extra** (`telemetry` group in pyproject.toml), not a core dependency |
| `opentelemetry-sdk` | ≥ 1.20.0 | Tracing SDK — **optional extra** (`telemetry` group in pyproject.toml), not a core dependency |

---

## 14. Testing Strategy

### 14.1 Unit Tests
- Protocol model serialization/deserialization round-trips.
- Session ID validation (valid/invalid patterns).
- Curation store CRUD operations with mock filesystem.
- Skill installer: path traversal detection, zip slip detection, size limit enforcement.

### 14.2 Integration Tests
- End-to-end CLI invocation with mocked HTTP server.
- Skill installation from test ZIP archives.
- Curation lifecycle: save → get → update status → list by status.
- Retry behavior verification against simulated transient failures.

### 14.3 Orchestrator Tests
- SKILL.md end-to-end flow with a mock server returning known curation JSON.
- Variable propagation: verify `SESSION_ID`, `TASK_QUERY`, `TASK_CONTEXT` pass correctly across bash blocks.
- Heredoc JSON formatting: verify skill arrays and curation results serialize without corruption.
- Edge cases: empty skill list, all skills already installed, vague vs. specific queries.

### 14.4 Security Tests
- SSRF: attempt HTTP URLs, redirect URLs, internal IPs.
- Path traversal: malicious skill names (`../`, `..%2F`).
- Zip slip: archives with `../../` path entries.
- Oversized downloads: payloads exceeding size limits.

---

## 15. Known Limitations and Future Considerations

### Current Limitations
1. **No offline mode**: Requires network connectivity and remote API access.
2. **Binary install decision**: Cannot selectively install a subset of skills.
3. **No curation versioning**: Overwriting a curation with the same session_id replaces it.
4. **No concurrent execution protection**: Multiple "Run now" sessions could conflict.
5. **Wisdom endpoints not in OpenAPI spec**: `wisdom-curate` and `wisdom-feedback` are implemented in code but not yet documented in `spec/openapi.yaml`.
6. **Presigned URL expiry race condition**: If the user delays the install decision (Step 4 → Step 5) beyond the presigned URL TTL, skill downloads will fail with a 403/404 error. The current `install_skill` does not distinguish URL expiry from other HTTP errors — no retry or re-curation is offered.
7. **Non-atomic status transitions**: A crash between `write_text()` and `unlink()` in `update_curation_status` can leave a curation in two status directories simultaneously.
8. **Post-download size check**: Skill ZIPs are fully buffered in memory before size validation, which could cause memory pressure at the 500MB cap.

### Future Considerations
- **Curation resumption**: Allow resuming a `pending` or `running` curation from a previous session.
- **Selective skill installation**: Per-skill opt-in/out while maintaining simplicity.
- **Curation diffing**: Show what changed when re-curating the same query.
- **Offline caching**: Cache wisdom results for frequently used queries.
- **Cost budgeting**: Allow users to set token/cost limits for curation calls.
- **OpenAPI spec update**: Add wisdom endpoints to `spec/openapi.yaml` for contract-first development.

---

## 16. Success Metrics

| Metric | Target | Measurement |
|--------|--------|-------------|
| **Curation acceptance rate** | > 70% of curations lead to "Yes" install | `save_curation` count vs. install count |
| **Run-now rate** | > 40% of curations executed immediately | `running` status transitions / total curations |
| **Feedback submission rate** | 100% of executed curations (Run Now or in-session resume) | `wisdom-feedback` calls / `running → completed` transitions |
| **Average feedback rating** | ≥ 3.5/5 | Parsed from feedback text |
| **Skill installation success rate** | > 95% | `installed` / (`installed` + `failed`) |
| **Retry success rate** | > 80% of retried requests eventually succeed | Tenacity metrics / OpenTelemetry spans |

---

## Appendix A: Glossary

| Term | Definition |
|------|------------|
| **PCR** | Primary-Context-Resultant — the knowledge structure used in the wisdom graph |
| **Wisdom** | A discrete unit of knowledge in the graph, identified by `wisdom_id` |
| **Curation** | A Markdown document describing a step-by-step workflow assembled from wisdoms |
| **Skill** | A downloadable package containing `SKILL.md` and optional scripts/references |
| **Seed Node** | A root wisdom in the graph that anchors a knowledge cluster |
| **Presigned URL** | Time-limited S3 URL for secure, direct skill ZIP download |

## Appendix B: Related Documents

- `skills/wisdom-curate/SKILL.md` — Skill orchestration specification
- `mega_code/client/api/protocol.py` — Pydantic protocol models
- `mega_code/client/api/remote.py` — HTTP client implementation
- `mega_code/client/skill_installer.py` — Skill download and extraction
- `mega_code/client/curation_store.py` — Curation persistence layer
- `mega_code/client/dirs.py` — XDG-compliant directory resolution
- `spec/openapi.yaml` — API specification (wisdom endpoints pending)
