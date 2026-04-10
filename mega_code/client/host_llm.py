# mega_code/client/host_llm.py
"""Host-agent LLM abstraction for skill evaluation.

Detects the available coding agent CLI (Claude Code, Codex, etc.) and runs
isolated completions via subprocess.  Used by skill-enhance A/B testing so that
evaluations run through the user's existing agent session — no external API
keys required.

All LLM calls use the agent's own model; the caller never specifies a model.

Canonical location — previously lived in ``mega_code.pipeline.host_llm`` but
moved here because the module has zero pipeline dependencies and must be
available in the OSS distribution (which ships only ``mega_code.client``).
"""

from __future__ import annotations

import asyncio
import functools
import json
import logging
import os
import shutil
import weakref
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Agent CLI registry
# ---------------------------------------------------------------------------
# Each entry: (cli_name, print_flags, system_prompt_flag, extra_flags)
# extra_flags are appended unconditionally (e.g. to disable tools).

_AGENT_CLIS: list[tuple[str, list[str], str, list[str]]] = [
    (
        "claude",
        ["-p", "--output-format", "json"],
        "--system-prompt",
        [
            "--tools",
            "",  # disable built-in tools for pure completion
            "--setting-sources",
            "",  # skip user/project settings (prevents hooks from firing)
            "--strict-mcp-config",  # ignore all MCP servers (no --mcp-config = zero servers)
            "--disable-slash-commands",  # no skills loaded
        ],
    ),
    (
        "codex",
        ["exec", "--json", "--ephemeral"],  # non-interactive JSONL output, no session persistence
        "--config",  # system prompt via: --config system_prompt="..."
        [
            "--skip-git-repo-check",  # allow running outside a git repo
        ],
    ),
]

# Concurrency limiter — how many parallel CLI calls we allow.
_MAX_CONCURRENCY = int(os.getenv("MEGA_CODE_EVAL_CONCURRENCY", "4"))
_EVAL_TIMEOUT_SECONDS = float(os.getenv("MEGA_CODE_EVAL_TIMEOUT", "300"))
_semaphores: weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, asyncio.Semaphore] = (
    weakref.WeakKeyDictionary()
)


def _get_semaphore() -> asyncio.Semaphore:
    """Return a semaphore scoped to the current running event loop."""
    loop = asyncio.get_running_loop()
    sem = _semaphores.get(loop)
    if sem is None:
        sem = asyncio.Semaphore(_MAX_CONCURRENCY)
        _semaphores[loop] = sem
    return sem


def _codex_system_prompt_override(system_prompt: str) -> str:
    """Encode Codex's TOML-style ``system_prompt=...`` override safely."""
    return f"system_prompt={json.dumps(system_prompt)}"


# ---------------------------------------------------------------------------
# Detection
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AgentCLI:
    """Resolved agent CLI configuration."""

    name: str
    print_flags: tuple[str, ...]
    system_prompt_flag: str
    extra_flags: tuple[str, ...]


@functools.lru_cache(maxsize=4)
def detect_agent_cli(preferred: str | None = None) -> AgentCLI:
    """Detect the available coding-agent CLI on ``$PATH``.

    Results are cached for the process lifetime (the CLI won't move
    mid-process).

    Args:
        preferred: If set, force a specific agent (e.g. ``"codex"``).
            The agent must still be on ``$PATH``.  When ``None``,
            the first available agent from the registry is used.

    Returns an :class:`AgentCLI` with the flags needed to run print-mode
    completions.

    Raises:
        RuntimeError: If no known agent CLI is found.
    """
    for cli_name, print_flags, sp_flag, extra in _AGENT_CLIS:
        if preferred and cli_name != preferred:
            continue
        if shutil.which(cli_name):
            logger.debug("Detected agent CLI: %s", cli_name)
            return AgentCLI(
                name=cli_name,
                print_flags=tuple(print_flags),
                system_prompt_flag=sp_flag,
                extra_flags=tuple(extra),
            )
    agent_label = preferred or "claude/codex"
    raise RuntimeError(
        f"Agent CLI '{agent_label}' not found on $PATH. "
        "Install Claude Code (`claude`) or Codex (`codex`) to run skill evaluations."
    )


# ---------------------------------------------------------------------------
# Completion
# ---------------------------------------------------------------------------


@dataclass
class CompletionResult:
    """Result of a single host-agent completion."""

    text: str
    model: str
    cost_usd: float
    duration_ms: int
    is_error: bool
    output_tokens: int = 0


def _parse_claude_json(raw: str) -> CompletionResult:
    """Parse ``claude -p --output-format json`` response."""
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        # Fallback: treat raw output as plain text
        return CompletionResult(
            text=raw.strip(),
            model="unknown",
            cost_usd=0.0,
            duration_ms=0,
            is_error=False,
        )

    # claude JSON output has a 'result' field with the text
    text = data.get("result", "")
    if not text and isinstance(data.get("content"), list):
        # Alternative shape: content blocks
        text = "".join(
            block.get("text", "") for block in data["content"] if block.get("type") == "text"
        )

    # Extract token count from usage if available
    usage = data.get("usage", {})
    output_tokens = usage.get("output_tokens", 0)

    # Extract model name from modelUsage keys (e.g. "claude-opus-4-6[1m]")
    model = "claude"
    model_usage = data.get("modelUsage", {})
    if model_usage:
        raw_model = next(iter(model_usage))
        # Strip context-window suffix like "[1m]"
        model = raw_model.split("[")[0] if "[" in raw_model else raw_model

    return CompletionResult(
        text=text.strip() if isinstance(text, str) else str(text),
        model=model,
        cost_usd=float(data.get("total_cost_usd", data.get("cost_usd", 0.0))),
        duration_ms=int(data.get("duration_ms", 0)),
        is_error=data.get("is_error", False),
        output_tokens=output_tokens,
    )


@functools.lru_cache(maxsize=1)
def _read_codex_model() -> str:
    """Read the model name from ``~/.codex/config.toml``.

    Falls back to ``"codex"`` if the config is missing or unreadable.
    Cached for the process lifetime.
    """
    config_path = Path.home() / ".codex" / "config.toml"
    if not config_path.exists():
        return "codex"
    try:
        content = config_path.read_text(encoding="utf-8")
        for line in content.splitlines():
            line = line.strip()
            # Match top-level: model = "gpt-5.4"
            if line.startswith("model") and "=" in line:
                # Skip lines inside [sections] (they have dots or brackets before 'model')
                value = line.split("=", 1)[1].strip().strip('"').strip("'")
                if value:
                    return value
    except OSError:
        pass
    return "codex"


def _parse_codex_jsonl(raw: str) -> CompletionResult:
    """Parse ``codex exec --json`` JSONL output.

    Codex streams events as newline-delimited JSON.  We extract the
    last ``item.completed`` event for the response text and the
    ``turn.completed`` event for token usage.  The model name is read
    from ``~/.codex/config.toml``.
    """
    text = ""
    output_tokens = 0
    model = _read_codex_model()

    for line in raw.strip().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue

        event_type = event.get("type", "")

        if event_type == "item.completed":
            item = event.get("item", {})
            if item.get("type") == "agent_message":
                text = item.get("text", "")

        elif event_type == "turn.completed":
            usage = event.get("usage", {})
            output_tokens = usage.get("output_tokens", 0)

    return CompletionResult(
        text=text.strip(),
        model=model,
        cost_usd=0.0,
        duration_ms=0,
        is_error=not bool(text),
        output_tokens=output_tokens,
    )


def _parse_response(cli_name: str, raw: str) -> CompletionResult:
    """Dispatch to the right parser based on the CLI."""
    if cli_name == "claude":
        return _parse_claude_json(raw)
    if cli_name == "codex":
        return _parse_codex_jsonl(raw)
    # Default: treat as plain text
    return CompletionResult(
        text=raw.strip(),
        model="unknown",
        cost_usd=0.0,
        duration_ms=0,
        is_error=False,
    )


def _clean_env() -> dict[str, str]:
    """Build a clean subprocess environment without plugin/hook context.

    Strips ``CODEX_PLUGIN_ROOT`` and related vars so that ``codex``
    runs as a vanilla session — no hooks, no pending-skill prompts.
    """
    env = dict(os.environ)
    for key in (
        "CODEX_PLUGIN_ROOT",
        "CODEX_PROJECT_DIR",
        "MEGA_CODE_CLIENT_MODE",
    ):
        env.pop(key, None)
    return env


async def complete(
    prompt: str,
    *,
    system_prompt: str | None = None,
    agent: str | None = None,
) -> CompletionResult:
    """Run an isolated completion via the detected agent CLI.

    The prompt is passed via **stdin** to avoid shell-escaping issues with
    long skill content.  Tools are disabled and the plugin environment is
    stripped so the agent produces a pure text completion without hooks.

    Args:
        prompt: The user-message / task prompt.
        system_prompt: Optional system prompt (e.g. skill content for the
            "with-skill" arm of A/B testing).

    Returns:
        A :class:`CompletionResult` with the response text and metadata.

    Raises:
        RuntimeError: If the agent CLI exits with a non-zero status.
    """
    cli = detect_agent_cli(preferred=agent)

    # For baseline (no system_prompt): provide a minimal directive so the
    # model doesn't try to use tools or read files — just answers directly.
    effective_sp = system_prompt or (
        "You are a coding assistant. Answer the question directly with text only. "
        "Do not attempt to use any tools, read files, or access external resources."
    )

    cmd: list[str] = [cli.name, *cli.print_flags]

    # System prompt injection differs by agent CLI:
    # - Claude: --system-prompt "text"
    # - Codex:  --config system_prompt="text"  (TOML-style config override)
    if cli.name == "codex":
        cmd.extend([cli.system_prompt_flag, _codex_system_prompt_override(effective_sp)])
    else:
        cmd.extend([cli.system_prompt_flag, effective_sp])

    cmd.extend(cli.extra_flags)
    # --no-session-persistence: don't save these ephemeral eval sessions
    if cli.name == "claude":
        cmd.append("--no-session-persistence")
    # Prompt comes via stdin, so no positional argument needed.

    env = _clean_env()

    sem = _get_semaphore()
    async with sem:
        logger.debug("Running: %s (prompt length=%d)", cli.name, len(prompt))
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )
        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(input=prompt.encode()),
                timeout=_EVAL_TIMEOUT_SECONDS,
            )
        except TimeoutError as exc:
            proc.kill()
            await proc.communicate()
            raise TimeoutError(f"{cli.name} timed out after {_EVAL_TIMEOUT_SECONDS:.0f}s") from exc

    if proc.returncode != 0:
        err_msg = stderr.decode().strip() if stderr else "unknown error"
        raise RuntimeError(f"{cli.name} exited with code {proc.returncode}: {err_msg}")

    return _parse_response(cli.name, stdout.decode())
