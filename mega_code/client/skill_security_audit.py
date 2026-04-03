"""Static security audit for skill-enhance.

Scans a SKILL.md for common red flags before A/B execution and persists
the result as a workspace artifact for the host agent to review.
"""

from __future__ import annotations

import argparse
import logging
import re
import sys
from pathlib import Path

from rich.console import Console

from mega_code.client.eval_workspace import save_artifact
from mega_code.client.skill_utils import (
    MEGACODE_AUTHOR_MARKER,
    parse_frontmatter,
    skill_frontmatter_value,
)

logger = logging.getLogger(__name__)
console = Console()
error_console = Console(stderr=True)

_PATTERN_RULES = [
    {
        "name": "curl_pipe_bash",
        "category": "Supply Chain & Dependencies",
        "severity": "high",
        "regex": re.compile(r"curl\b[^\n|]*\|\s*(?:bash|sh)\b", re.IGNORECASE),
        "description": "Remote code execution pattern via curl pipe shell.",
    },
    {
        "name": "wget_pipe_shell",
        "category": "Supply Chain & Dependencies",
        "severity": "high",
        "regex": re.compile(r"wget\b[^\n|]*\|\s*(?:bash|sh)\b", re.IGNORECASE),
        "description": "Remote code execution pattern via wget pipe shell.",
    },
    {
        "name": "eval_curl",
        "category": "Command Injection",
        "severity": "high",
        "regex": re.compile(r"eval\s+\$\(\s*curl\b", re.IGNORECASE),
        "description": "Dynamic shell evaluation of remote content.",
    },
    {
        "name": "credential_paths",
        "category": "Credential & Secret Management",
        "severity": "high",
        "regex": re.compile(
            r"~/(?:\.ssh(?:/[\w./-]+)?|\.aws(?:/credentials|/config)?)|\bid_rsa\b|"
            r"\.aws/credentials\b|~/.git-credentials\b",
            re.IGNORECASE,
        ),
        "description": "Access to sensitive credential paths or files.",
    },
    {
        "name": "env_reference",
        "category": "Credential & Secret Management",
        "severity": "low",
        "regex": re.compile(r"\.env\b", re.IGNORECASE),
        "description": "Environment file reference that may warrant context review.",
    },
    {
        "name": "prompt_override",
        "category": "Prompt Injection & Instruction Override",
        "severity": "high",
        "regex": re.compile(
            r"ignore (?:previous|all) instructions|you are now|bypass (?:safety|guardrails)",
            re.IGNORECASE,
        ),
        "description": "Instruction override or prompt injection language.",
    },
    {
        "name": "config_persistence",
        "category": "Hook & Config Exploitation",
        "severity": "high",
        "regex": re.compile(
            r"(?:write|modify|edit|update|install|add|create)\b[^\n]{0,80}"
            r"(?:\.claude(?:/settings\.json|\.json)|settings\.json|\bhooks?\b|\bMCP\b)",
            re.IGNORECASE,
        ),
        "description": "Persistent config or hook manipulation pattern.",
    },
    {
        "name": "obfuscated_payload",
        "category": "Dual-Layer Attack Detection",
        "severity": "medium",
        "regex": re.compile(r"\b(?:base64|atob|decode)\b|0x[a-f0-9]{8,}|[A-Fa-f0-9]{40,}"),
        "description": "Potential payload obfuscation or encoded content.",
    },
    {
        "name": "external_post",
        "category": "Data Exfiltration",
        "severity": "medium",
        "regex": re.compile(
            r"\b(?:curl|wget)\b[^\n]*(?:https?://|--data|-d\s|--upload-file)", re.IGNORECASE
        ),
        "description": "Outbound request or file upload pattern.",
    },
    {
        "name": "shell_interpolation",
        "category": "Command Injection",
        "severity": "medium",
        "regex": re.compile(
            r"\b(?:bash|sh)\s+-c\b|\bsubprocess\.(?:run|Popen)\b|\bexec\b", re.IGNORECASE
        ),
        "description": "Shell execution surface that may need input sanitization review.",
    },
]

_SEVERITY_ORDER = {"low": 1, "medium": 2, "high": 3}
_ALLOWED_IGNORE_PATTERNS = {"env_reference"}


def _ignored_pattern_names(frontmatter: dict) -> set[str]:
    """Return ignore list from frontmatter security_review.ignore_patterns."""
    security_review = frontmatter.get("security_review")
    if not isinstance(security_review, dict):
        return set()
    ignore_patterns = security_review.get("ignore_patterns", [])
    if not isinstance(ignore_patterns, list):
        return set()
    return {str(name) for name in ignore_patterns}


def _applied_ignored_pattern_names(requested_patterns: set[str]) -> set[str]:
    """Return the subset of requested ignore names that are safe to honor."""
    return requested_patterns & _ALLOWED_IGNORE_PATTERNS


def _classify_trust(frontmatter: dict, source: str = "auto") -> tuple[str, str, str]:
    """Return (trust_level, trust_reason, trust_explanation).

    Two trust levels:
    - ``trusted`` — the skill has a mega-code author marker.
    - ``semitrusted`` — the skill does not have a mega-code author marker.
    """
    if source == "same-repo":
        return (
            "trusted",
            "source_same_repo",
            "The skill was explicitly marked as coming from the same repo, so it is treated as trusted.",
        )
    if source == "known-org":
        return (
            "trusted",
            "source_known_org",
            "The skill was explicitly marked as coming from a known organization, so it is treated as trusted.",
        )

    author = str(skill_frontmatter_value(frontmatter, "author", "")).lower()

    if source == "unknown" or MEGACODE_AUTHOR_MARKER not in author:
        return (
            "semitrusted",
            "unknown_author_or_source",
            "The skill does not have a mega-code author marker, so it is treated as semitrusted.",
        )

    return (
        "trusted",
        "megacode_authored",
        "The skill has a mega-code author marker, so it is treated as trusted.",
    )


def classify_trust_level(skill_md: str, source: str = "auto") -> str:
    """Classify skill trust level for evaluation policy."""
    frontmatter = parse_frontmatter(skill_md)
    trust_level, _, _ = _classify_trust(frontmatter, source=source)
    return trust_level


def _build_line_index(content: str) -> list[int]:
    """Build a sorted list of newline offsets for O(log n) line lookups."""
    index: list[int] = []
    pos = -1
    while True:
        pos = content.find("\n", pos + 1)
        if pos == -1:
            break
        index.append(pos)
    return index


def _line_number_from_index(line_index: list[int], offset: int) -> int:
    from bisect import bisect_left

    return bisect_left(line_index, offset) + 1


def scan_red_flags(skill_md: str, ignored_patterns: set[str] | None = None) -> list[dict]:
    """Return deterministic red-flag findings for a skill."""
    ignored_patterns = ignored_patterns or set()
    findings: list[dict] = []
    line_index = _build_line_index(skill_md)
    for rule in _PATTERN_RULES:
        if rule["name"] in ignored_patterns:
            continue
        for match in rule["regex"].finditer(skill_md):
            findings.append(
                {
                    "name": rule["name"],
                    "category": rule["category"],
                    "severity": rule["severity"],
                    "description": rule["description"],
                    "line": _line_number_from_index(line_index, match.start()),
                    "evidence": match.group(0)[:200],
                }
            )
    findings.sort(
        key=lambda item: (-_SEVERITY_ORDER[item["severity"]], item["line"], item["category"])
    )
    return findings


def security_score(findings: list[dict]) -> int:
    """Map findings to a 1-5 score where 5 is safest."""
    if not findings:
        return 5

    severities = {finding["severity"] for finding in findings}
    if "high" in severities:
        return 1
    if len(findings) >= 3 or "medium" in severities:
        return 3
    return 4


def derive_ab_policy(trust_level: str, findings: list[dict]) -> str:
    """Determine whether Phase 4 should proceed normally."""
    has_high = any(finding["severity"] == "high" for finding in findings)
    has_any = bool(findings)

    if has_high and trust_level == "semitrusted":
        return "skip_ab"
    # Intentional: trusted skills with findings still get a warning rather than
    # silently passing with full_access. Only finding-free skills skip the gate.
    if has_any:
        return "warn_and_continue"

    return "full_access"


def summarize_audit(trust_level: str, findings: list[dict], policy: str) -> str:
    if not findings:
        return (
            f"No deterministic red flags found. Trust level is {trust_level}; "
            f"A/B policy is {policy}."
        )

    categories = ", ".join(dict.fromkeys(finding["category"] for finding in findings))
    return (
        f"Found {len(findings)} red flag(s) across: {categories}. "
        f"Trust level is {trust_level}; A/B policy is {policy}."
    )


def audit_skill(skill_path: Path, source: str = "auto") -> dict:
    """Run a static security audit over a skill file."""
    skill_md = skill_path.read_text(encoding="utf-8")
    frontmatter = parse_frontmatter(skill_md)
    trust_level, trust_reason, trust_explanation = _classify_trust(frontmatter, source=source)
    requested_ignored_patterns = _ignored_pattern_names(frontmatter)
    ignored_patterns = _applied_ignored_pattern_names(requested_ignored_patterns)
    findings = scan_red_flags(skill_md, ignored_patterns=ignored_patterns)
    policy = derive_ab_policy(trust_level, findings)

    return {
        "skill_name": str(frontmatter.get("name") or skill_path.parent.name),
        "skill_path": str(skill_path),
        "trust_level": trust_level,
        "trust_reason": trust_reason,
        "trust_explanation": trust_explanation,
        "security_score": security_score(findings),
        "ab_policy": policy,
        "requested_ignored_patterns": sorted(requested_ignored_patterns),
        "ignored_patterns": sorted(ignored_patterns),
        "red_flags": findings,
        "summary": summarize_audit(trust_level, findings, policy),
    }


def _format_summary(report: dict) -> str:
    lines = [
        f"Security review for: {report['skill_name']}",
        f"Trust level: {report['trust_level']}",
        f"Trust reason: {report['trust_reason']}",
        f"Security score: {report['security_score']}/5",
        f"A/B policy: {report['ab_policy']}",
        "",
        report["trust_explanation"],
        "",
        report["summary"],
    ]
    if report["red_flags"]:
        lines.append("")
        lines.append("Red flags:")
        for finding in report["red_flags"]:
            lines.append(
                f"- [{finding['severity']}] line {finding['line']} "
                f"{finding['category']}: {finding['evidence']}"
            )
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="mega_code.client.skill_security_audit",
        description="Run a static security audit for skill-enhance before A/B testing.",
    )
    parser.add_argument("--skill-path", required=True, help="Path to the SKILL.md file.")
    parser.add_argument(
        "--iteration-dir",
        default=None,
        help="Optional iteration directory for saving security-review.json.",
    )
    parser.add_argument(
        "--source",
        default="auto",
        choices=["auto", "same-repo", "known-org", "unknown"],
        help="Trust source hint for policy derivation.",
    )
    parser.add_argument("--debug", action="store_true", help="Enable debug logging.")
    args = parser.parse_args()

    if args.debug:
        logging.basicConfig(
            level=logging.DEBUG, format="%(asctime)s %(name)s %(levelname)s %(message)s"
        )
    else:
        logging.basicConfig(level=logging.WARNING)

    skill_path = Path(args.skill_path)
    if skill_path.is_dir():
        skill_path = skill_path / "SKILL.md"
    if not skill_path.exists():
        error_console.print(f"SKILL.md not found: {skill_path}")
        sys.exit(1)

    report = audit_skill(skill_path, source=args.source)
    console.print(_format_summary(report))

    if args.iteration_dir:
        save_artifact(Path(args.iteration_dir), "security-review.json", report)

    if report["ab_policy"] == "skip_ab":
        sys.exit(2)


if __name__ == "__main__":
    main()
