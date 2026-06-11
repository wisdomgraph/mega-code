"""Shared utilities for skill file generation and naming.

Functions:
    sanitize_name: Kebab-case sanitizer for directory/file names.
    ensure_skill_frontmatter: Prepend YAML frontmatter to SKILL.md when missing.
    ensure_strategy_frontmatter: Prepend YAML frontmatter to strategy markdown.
    ensure_lesson_frontmatter: Prepend YAML frontmatter to lesson markdown.
    get_author: Read author attribution string from env or default.
"""

from __future__ import annotations

import json
import os
import re
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import PurePosixPath
from typing import Literal

import yaml

from mega_code.client.api.protocol import (
    _BUNDLE_SUBDIRS as _BUNDLE_ROOTS,
)
from mega_code.client.api.protocol import (
    BundleFile,
    ResourcePlan,
    SkillBundle,
    ValidationFinding,
)

DEFAULT_AUTHOR = "co-authored by www.megacode.ai"

DEFAULT_VERSION = "1.0.0"

MEGACODE_AUTHOR_MARKER = "megacode.ai"

# Conservative default tool whitelist for SKILL.md `allowed-tools` when neither
# the source frontmatter nor metadata supplies a value. Runtime stage requires
# this key to be present and non-empty.
DEFAULT_ALLOWED_TOOLS = "Read, Grep, Glob, Bash, Edit, Write"

SKILL_METADATA_KEYS = (
    "version",
    "tags",
    "creator",
    "author",
    "generated_at",
    "roi",
)

DEPRECATED_SKILL_METADATA_KEYS = (
    "eval_version",
    "enhanced_from",
)


class _InlineList(list):
    """Marker list type rendered in YAML flow style."""


class _QuotedString(str):
    """Marker string type rendered with double quotes."""


class _SkillFrontmatterDumper(yaml.SafeDumper):
    """YAML dumper for skill frontmatter formatting."""


def _represent_inline_list(dumper: yaml.SafeDumper, data: _InlineList) -> yaml.SequenceNode:
    return dumper.represent_sequence("tag:yaml.org,2002:seq", data, flow_style=True)


def _represent_quoted_string(dumper: yaml.SafeDumper, data: _QuotedString) -> yaml.ScalarNode:
    return dumper.represent_scalar("tag:yaml.org,2002:str", str(data), style='"')


_SkillFrontmatterDumper.add_representer(_InlineList, _represent_inline_list)

_SkillFrontmatterDumper.add_representer(_QuotedString, _represent_quoted_string)


def _quote_skill_metadata_fields(frontmatter: dict) -> None:
    """Force canonical metadata fields to render with double quotes."""
    description = frontmatter.get("description")
    if isinstance(description, str):
        frontmatter["description"] = _normalize_frontmatter_description(description)

    metadata = frontmatter.get("metadata")
    if not isinstance(metadata, dict):
        return

    for key in ("version", "generated_at", "creator"):
        value = metadata.get(key)
        if isinstance(value, str):
            metadata[key] = _QuotedString(value)

    roi = metadata.get("roi")
    if isinstance(roi, list):
        for entry in roi:
            if not isinstance(entry, dict):
                continue
            for key in ("performance_increase", "token_savings"):
                value = entry.get(key)
                if isinstance(value, (int, float)):
                    value = format_roi_percent(value)
                if isinstance(value, str):
                    entry[key] = _QuotedString(value)


def parse_frontmatter(content: str) -> dict:
    """Parse YAML frontmatter from markdown content.

    Returns an empty dict if the content has no valid frontmatter block.
    """
    if not content.startswith("---"):
        return {}
    parts = content.split("---", 2)
    if len(parts) < 3:
        return {}
    try:
        data = yaml.safe_load(parts[1])
    except yaml.YAMLError:
        return {}
    return data if isinstance(data, dict) else {}


def split_frontmatter(content: str) -> tuple[dict, str]:
    """Split markdown into parsed frontmatter and body content."""
    if not content.startswith("---"):
        return {}, content
    parts = content.split("---", 2)
    if len(parts) < 3:
        return {}, content
    return parse_frontmatter(content), parts[2].lstrip("\n")


def order_metadata(metadata: dict) -> dict:
    """Reorder metadata keys into canonical ``SKILL_METADATA_KEYS`` order."""
    ordered: dict = {}
    for key in SKILL_METADATA_KEYS:
        if key in metadata:
            ordered[key] = metadata[key]
    for key, value in metadata.items():
        if key not in ordered:
            ordered[key] = value
    return ordered


def skill_metadata(frontmatter: dict) -> dict:
    """Return merged skill metadata from nested ``metadata`` or legacy top-level keys."""
    metadata = frontmatter.get("metadata")
    if isinstance(metadata, dict):
        merged = dict(metadata)
    else:
        merged = {}
    for key in SKILL_METADATA_KEYS:
        if key not in merged and key in frontmatter:
            merged[key] = frontmatter[key]
    return merged


def skill_frontmatter_value(frontmatter: dict, key: str, default: object = "") -> object:
    """Read a skill metadata field with nested-metadata fallback."""
    metadata = skill_metadata(frontmatter)
    return metadata.get(key, default)


def render_frontmatter(frontmatter: dict) -> str:
    """Render a frontmatter dict without extra section spacing."""
    rendered = deepcopy(frontmatter)
    metadata = rendered.get("metadata")
    if isinstance(metadata, dict) and isinstance(metadata.get("tags"), list):
        metadata["tags"] = _InlineList(metadata["tags"])
    _quote_skill_metadata_fields(rendered)
    yaml_text = yaml.dump(
        rendered,
        Dumper=_SkillFrontmatterDumper,
        sort_keys=False,
        allow_unicode=False,
        default_flow_style=False,
        width=4096,
    ).strip()
    return f"---\n{yaml_text}\n---\n\n"


def normalize_skill_frontmatter(frontmatter: dict) -> dict:
    """Rewrite skill frontmatter into the canonical nested ``metadata`` shape."""
    normalized: dict = {}
    for key, value in frontmatter.items():
        if key == "metadata" or key in SKILL_METADATA_KEYS or key in DEPRECATED_SKILL_METADATA_KEYS:
            continue
        # Canonicalise snake_case allowed_tools to the kebab key Claude Code
        # expects; F003 rejects the snake-case form at runtime, and emitting
        # both keys would trip _ensure_allowed_tools into a double-injection.
        if key == "allowed_tools" and "allowed-tools" not in frontmatter:
            normalized["allowed-tools"] = value
            continue
        if key == "allowed_tools":
            continue
        normalized[key] = value

    metadata = skill_metadata(frontmatter)
    if metadata:
        for key in DEPRECATED_SKILL_METADATA_KEYS:
            metadata.pop(key, None)
        normalized["metadata"] = order_metadata(metadata)
    return normalized


def _normalize_frontmatter_description(description: str) -> str:
    """Collapse description whitespace so YAML renders it as one logical line."""
    return " ".join(description.split())


def format_frontmatter_percent(value: object) -> str:
    """Format ROI values for SKILL.md frontmatter display."""
    return format_roi_percent(value)


def normalize_pending_skill_markdown(
    *,
    skill_md: str,
    skill_name: str,
    author: str = "",
    version: str = "",
    tags: list[str] | None = None,
    metadata_json: str = "",
    default_author: str = DEFAULT_AUTHOR,
    default_version: str = DEFAULT_VERSION,
) -> str:
    """Normalize pending-skill markdown into canonical nested frontmatter."""
    try:
        metadata_payload = json.loads(metadata_json) if metadata_json else {}
    except Exception:
        metadata_payload = {}
    if not isinstance(metadata_payload, dict):
        metadata_payload = {}

    frontmatter, body = split_frontmatter(skill_md)
    resolved_author = str(
        author or skill_frontmatter_value(frontmatter, "author", "") or default_author
    )
    resolved_version = str(
        version or skill_frontmatter_value(frontmatter, "version", "") or default_version
    )
    resolved_tags = tags or skill_frontmatter_value(frontmatter, "tags", [])
    if not isinstance(resolved_tags, list):
        resolved_tags = []

    extra_frontmatter: dict[str, object] = {}
    generated_at = skill_frontmatter_value(frontmatter, "generated_at", "")
    if not generated_at:
        generated_at = metadata_payload.get("generated_at", "")
    if isinstance(generated_at, str) and generated_at:
        extra_frontmatter["generated_at"] = generated_at

    roi = skill_frontmatter_value(frontmatter, "roi", None)
    if roi is None:
        raw_roi = metadata_payload.get("roi")
        if isinstance(raw_roi, dict):
            roi = [
                {
                    "model": str(raw_roi.get("model", "unknown")),
                    "performance_increase": format_frontmatter_percent(
                        raw_roi.get("performance_increase", 0)
                    ),
                    "token_savings": format_frontmatter_percent(raw_roi.get("token_savings", 0)),
                }
            ]
        elif isinstance(raw_roi, list):
            roi = [
                {
                    "model": str(item.get("model", "unknown")),
                    "performance_increase": format_frontmatter_percent(
                        item.get("performance_increase", 0)
                    ),
                    "token_savings": format_frontmatter_percent(item.get("token_savings", 0)),
                }
                for item in raw_roi
                if isinstance(item, dict)
            ]
    if roi:
        extra_frontmatter["roi"] = roi

    if frontmatter:
        normalized = normalize_skill_frontmatter(frontmatter)
        metadata_block = dict(normalized.get("metadata", {}))
        metadata_block.setdefault("author", resolved_author)
        metadata_block.setdefault("version", resolved_version)
        if resolved_tags and "tags" not in metadata_block:
            metadata_block["tags"] = resolved_tags
        for key, value in extra_frontmatter.items():
            metadata_block.setdefault(key, value)
        if metadata_block:
            normalized["metadata"] = metadata_block
        rendered = render_frontmatter(normalized) + body
    else:
        rendered = ensure_skill_frontmatter(
            skill_md,
            skill_name,
            author=resolved_author,
            version=resolved_version,
            generated_at=str(extra_frontmatter.get("generated_at", "")),
            tags=resolved_tags or None,
            extra_frontmatter=(
                {"roi": extra_frontmatter["roi"]} if "roi" in extra_frontmatter else None
            ),
        )

    allowed_tools_value = _allowed_tools_from_metadata(metadata_payload) or DEFAULT_ALLOWED_TOOLS
    return _ensure_allowed_tools(rendered, allowed_tools_value)


def _allowed_tools_from_metadata(metadata_payload: dict) -> str | None:
    """Extract `allowed-tools` from a metadata.json payload, normalised to a string.

    Accepts both kebab (`allowed-tools`) and snake (`allowed_tools`) keys, and
    both list and string shapes. Returns `None` if absent or empty so the
    caller can fall back to a default.
    """
    raw = metadata_payload.get("allowed-tools")
    if raw is None:
        raw = metadata_payload.get("allowed_tools")
    if raw is None:
        return None
    if isinstance(raw, list):
        items = [str(item).strip() for item in raw if str(item).strip()]
        return ", ".join(items) if items else None
    if isinstance(raw, str):
        stripped = raw.strip()
        return stripped or None
    return None


def _ensure_allowed_tools(skill_md: str, value: str) -> str:
    """Inject `allowed-tools: "<value>"` into the YAML frontmatter if missing.

    Idempotent — when `allowed-tools` already exists at the top level (even as
    an empty value), the input is returned unchanged. Stage validators surface
    empty-value violations separately.
    """
    frontmatter, _ = split_frontmatter(skill_md)
    if "allowed-tools" in frontmatter:
        return skill_md
    if not skill_md.startswith("---"):
        return skill_md
    parts = skill_md.split("---", 2)
    if len(parts) < 3:
        return skill_md
    header_block = parts[1].rstrip("\n")
    return parts[0] + "---" + header_block + "\n" + f'allowed-tools: "{value}"\n' + "---" + parts[2]


def get_author() -> str:
    """Return the author attribution string.

    Reads ``MEGA_CODE_AUTHOR`` env var; falls back to :data:`DEFAULT_AUTHOR`.
    """
    return os.environ.get("MEGA_CODE_AUTHOR", DEFAULT_AUTHOR)


def sanitize_name(name: str) -> str:
    """Sanitize a name for use as a directory or file name.

    - Lowercase
    - Replace spaces and special chars with hyphens
    - Remove consecutive hyphens
    - Limit length to 64 chars

    Args:
        name: Raw name string.

    Returns:
        Sanitized kebab-case name suitable for directory/file names.
    """
    sanitized = re.sub(r"[^a-z0-9]+", "-", name.lower())
    sanitized = sanitized.strip("-")
    sanitized = re.sub(r"-+", "-", sanitized)
    sanitized = sanitized[:64]
    if not sanitized:
        sanitized = "unnamed"
    return sanitized


def canonical_skill_name(skill_name: str, skill_md: str = "") -> str:
    """Resolve the canonical skill slug, preferring frontmatter ``name``."""
    frontmatter = parse_frontmatter(skill_md) if skill_md else {}
    frontmatter_name = frontmatter.get("name")
    if isinstance(frontmatter_name, str) and frontmatter_name.strip():
        return sanitize_name(frontmatter_name)
    return sanitize_name(skill_name)


def bump_minor_version(version: str) -> str:
    """Bump the minor version: 1.0.0 -> 1.1.0, 1.2.0 -> 1.3.0.

    Used by cross-run dedup when a previously-rejected item resurfaces
    with higher signal_strength.
    """
    parts = version.split(".")
    if len(parts) == 3:
        return f"{parts[0]}.{int(parts[1]) + 1}.0"
    return "1.1.0"


def parse_semantic_version(version: str) -> tuple[int, int, int]:
    """Parse ``major.minor.patch`` into a sortable tuple.

    Invalid or partial versions sort before valid semantic versions.
    """
    parts = version.split(".")
    if len(parts) != 3:
        return (0, 0, 0)
    try:
        return (int(parts[0]), int(parts[1]), int(parts[2]))
    except ValueError:
        return (0, 0, 0)


def current_timestamp_z() -> str:
    """Return the current UTC timestamp in canonical skill frontmatter format."""
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _find_frontmatter_end(lines: list[str]) -> int:
    """Find the index of the closing ``---`` in a frontmatter block.

    Assumes ``lines[0]`` is the opening ``---``.
    Returns -1 if no closing delimiter is found.
    """
    for i, line in enumerate(lines):
        if i == 0:
            continue
        if line.strip() == "---":
            return i
    return -1


def _collect_top_level_keys(lines: list[str], start: int, end: int) -> set[str]:
    """Collect top-level YAML keys from frontmatter lines[start:end]."""
    keys: set[str] = set()
    for line in lines[start:end]:
        if ":" in line and not line.startswith(" ") and not line.startswith("\t"):
            keys.add(line.split(":", 1)[0].strip())
    return keys


def _build_metadata_lines(
    *,
    author: str = "",
    version: str = "",
    generated_at: str = "",
    tags: list[str] | None = None,
) -> list[str]:
    """Build YAML frontmatter lines for tags/author/version with blank-line separators."""
    lines: list[str] = []
    if tags:
        lines.append("")
        lines.append("tags:")
        for tag in tags:
            lines.append(f"  - {tag}")
    if author:
        lines.append("")
        lines.append(f'author: "{author}"')
    if version:
        lines.append("")
        lines.append(f'version: "{version}"')
    if generated_at:
        lines.append("")
        lines.append(f'generated_at: "{generated_at}"')
    return lines


def _inject_metadata_into_existing(
    content: str,
    *,
    author: str = "",
    version: str = "",
    generated_at: str = "",
    tags: list[str] | None = None,
    extra_fields: dict[str, str] | None = None,
) -> str:
    """Inject missing metadata fields into existing YAML frontmatter.

    Only adds fields that are not already present in the frontmatter block.
    """
    lines = content.split("\n")
    end_idx = _find_frontmatter_end(lines)

    if end_idx == -1:
        return content  # malformed frontmatter, return unchanged

    existing_keys = _collect_top_level_keys(lines, 1, end_idx)

    # Build lines to inject (with blank-line separators between sections)
    inject: list[str] = []
    if tags and "tags" not in existing_keys:
        inject.append("")
        inject.append("tags:")
        for tag in tags:
            inject.append(f"  - {tag}")
    if author and "author" not in existing_keys:
        inject.append("")
        inject.append(f'author: "{author}"')
    if version and "version" not in existing_keys:
        inject.append("")
        inject.append(f'version: "{version}"')
    if generated_at and "generated_at" not in existing_keys:
        inject.append("")
        inject.append(f'generated_at: "{generated_at}"')
    if extra_fields:
        for key, val in extra_fields.items():
            if key not in existing_keys:
                inject.append("")
                inject.append(f"{key}: {val}")

    if not inject:
        return content

    # Insert before closing ---
    result = lines[:end_idx] + inject + lines[end_idx:]
    return "\n".join(result)


def _normalize_extra_frontmatter(extra_frontmatter: dict | None) -> dict[str, object]:
    """Normalize extra skill metadata fields before rendering."""
    if not extra_frontmatter:
        return {}

    normalized: dict[str, object] = {}
    for key, value in extra_frontmatter.items():
        if key == "roi" and isinstance(value, dict):
            normalized[key] = [value]
        else:
            normalized[key] = value
    return normalized


def _space_frontmatter_sections(content: str) -> str:
    """Ensure blank lines between top-level YAML keys in frontmatter."""
    lines = content.split("\n")
    end_idx = _find_frontmatter_end(lines)
    if end_idx <= 1:
        return content

    fm_lines = lines[1:end_idx]
    spaced: list[str] = []
    prev_key = ""
    for line in fm_lines:
        cur_key = ""
        if ":" in line and not line.startswith(" ") and not line.startswith("\t"):
            cur_key = line.split(":", 1)[0].strip()
        # Add blank line before top-level keys, except description right after name
        if cur_key and spaced and spaced[-1] != "":
            if not (cur_key == "description" and prev_key == "name"):
                spaced.append("")
        spaced.append(line)
        if cur_key:
            prev_key = cur_key

    return "\n".join(lines[:1] + spaced + lines[end_idx:])


def ensure_skill_frontmatter(
    skill_md: str,
    skill_name: str,
    *,
    author: str = "",
    email: str = "",
    version: str = "",
    generated_at: str = "",
    tags: list[str] | None = None,
    extra_frontmatter: dict | None = None,
) -> str:
    """Ensure SKILL.md content has required YAML frontmatter.

    Claude's skill loader requires a YAML frontmatter block (``name``,
    ``description``) at the top of every SKILL.md for indexing and loading.

    When content already starts with ``---``, missing metadata fields
    (author, version, tags) are injected into the existing block.

    When frontmatter is missing, a full block is prepended using *skill_name*
    as the ``name`` field and the first non-heading paragraph of the
    markdown body as the ``description``.

    Args:
        skill_md: The raw SKILL.md content (may or may not have frontmatter).
        skill_name: Already-sanitized kebab-case skill name.
        author: Attribution string (e.g. from :func:`get_author`).
        email: User email written as ``metadata.creator``.
        version: Semantic version string (e.g. "1.0.0").
        tags: List of lowercase kebab-case tags.
        extra_frontmatter: Optional dict of additional frontmatter fields (e.g. roi).

    Returns:
        SKILL.md content with a valid YAML frontmatter block.
    """
    extra_metadata = _normalize_extra_frontmatter(extra_frontmatter)

    if skill_md.strip().startswith("---"):
        frontmatter, body = split_frontmatter(skill_md)
        normalized = dict(frontmatter)
        has_nested_metadata = isinstance(frontmatter.get("metadata"), dict)
        has_legacy_metadata = any(key in frontmatter for key in SKILL_METADATA_KEYS)

        if has_nested_metadata:
            metadata = dict(frontmatter["metadata"])
            if version and "version" not in metadata:
                metadata["version"] = version
            if tags and "tags" not in metadata:
                metadata["tags"] = tags
            if email and "creator" not in metadata:
                metadata["creator"] = email
            if author and "author" not in metadata:
                metadata["author"] = author
            if generated_at and "generated_at" not in metadata:
                metadata["generated_at"] = generated_at
            for key, value in extra_metadata.items():
                metadata.setdefault(key, value)
            normalized["metadata"] = order_metadata(metadata)
            return render_frontmatter(normalized) + body

        if not has_legacy_metadata:
            metadata: dict[str, object] = {}
            if version:
                metadata["version"] = version
            if tags:
                metadata["tags"] = tags
            if email:
                metadata["creator"] = email
            if author:
                metadata["author"] = author
            if generated_at:
                metadata["generated_at"] = generated_at
            metadata.update(extra_metadata)
            if metadata:
                normalized["metadata"] = order_metadata(metadata)
            return render_frontmatter(normalized) + body

        normalized = normalize_skill_frontmatter(frontmatter)
        metadata = dict(normalized.get("metadata", {}))
        if email and "creator" not in metadata:
            metadata["creator"] = email
        if metadata:
            normalized["metadata"] = order_metadata(metadata)
        return render_frontmatter(normalized) + body

    # Extract description from first non-heading paragraph
    description = ""
    for line in skill_md.strip().split("\n"):
        stripped = line.strip()
        if stripped and not stripped.startswith("#"):
            description = stripped
            break

    frontmatter: dict[str, object] = {"name": skill_name}
    if description:
        frontmatter["description"] = description
    else:
        frontmatter["description"] = f"Skill {skill_name}"

    metadata: dict[str, object] = {}
    if version:
        metadata["version"] = version
    if tags:
        metadata["tags"] = tags
    if email:
        metadata["creator"] = email
    if author:
        metadata["author"] = author
    if generated_at:
        metadata["generated_at"] = generated_at
    metadata.update(extra_metadata)
    if metadata:
        frontmatter["metadata"] = order_metadata(metadata)

    return render_frontmatter(frontmatter) + skill_md


def ensure_strategy_frontmatter(
    content: str,
    category: str,
    *,
    author: str = "",
    version: str = "",
    tags: list[str] | None = None,
) -> str:
    """Ensure strategy markdown has YAML frontmatter with metadata.

    If the content already has frontmatter, injects missing fields.
    Otherwise prepends a new frontmatter block.

    Args:
        content: Strategy markdown content.
        category: Strategy category name (e.g. "Code Style").
        author: Attribution string.
        version: Semantic version string.
        tags: List of lowercase kebab-case tags.

    Returns:
        Strategy markdown with frontmatter.
    """
    if content.strip().startswith("---"):
        return _inject_metadata_into_existing(
            content,
            author=author,
            version=version,
            tags=tags,
        )

    fm_lines = ["---", f"category: {category}"]
    fm_lines.extend(_build_metadata_lines(author=author, version=version, tags=tags))
    fm_lines.append("---")
    fm_lines.append("")

    return "\n".join(fm_lines) + content


def ensure_lesson_frontmatter(
    content: str,
    title: str,
    *,
    author: str = "",
    version: str = "",
    tags: list[str] | None = None,
    level: str = "",
    style: str = "",
    language: str = "",
) -> str:
    """Ensure lesson markdown has YAML frontmatter with metadata.

    If the content already has frontmatter, injects missing fields.
    Otherwise prepends a new frontmatter block.

    Args:
        content: Lesson markdown content.
        title: Lesson title.
        author: Attribution string.
        version: Semantic version string.
        tags: List of lowercase kebab-case tags.
        level: User's proficiency level (e.g. "intermediate").
        style: Content style (e.g. "mentor").
        language: Content language (e.g. "English").

    Returns:
        Lesson markdown with frontmatter.
    """
    extra = {}
    if level:
        extra["level"] = level.lower()
    if style:
        extra["style"] = style.lower()
    if language:
        extra["language"] = language

    if content.strip().startswith("---"):
        return _inject_metadata_into_existing(
            content,
            author=author,
            version=version,
            tags=tags,
            extra_fields=extra,
        )

    fm_lines = ["---", f"title: {title}"]
    fm_lines.extend(_build_metadata_lines(author=author, version=version, tags=tags))
    for key, val in extra.items():
        fm_lines.append(f"{key}: {val}")
    fm_lines.append("---")
    fm_lines.append("")

    return "\n".join(fm_lines) + content


# =============================================================================
# ROI formatting — moved from mega_code.pipeline.store.base so the OSS
# distribution (which ships only mega_code.client) can format ROI entries
# without a pipeline dependency.
# =============================================================================


def format_roi_percent(value, *, clamp_min: float | None = None) -> str:
    """Normalize ROI values into display percentages like ``"98%"``."""
    if isinstance(value, str):
        return value if value.endswith("%") else value
    if isinstance(value, (int, float)):
        # Fractional ROI inputs (in -1..1 range) are treated as normalized
        # values and multiplied by 100.  Values outside that range are
        # treated as already-percentage values.
        # Negative fractions intentionally display as 0% for frontmatter/UI.
        if -1 <= value < 0:
            pct = 0
        elif 0 <= value <= 1:
            pct = value * 100
        else:
            pct = value
        if clamp_min is not None:
            pct = max(clamp_min, pct)
        return f"{pct:.0f}%"
    return "0%"


def format_eval_roi_entry(eval_roi_data: dict, *, include_analytics: bool = False) -> dict:
    """Build a normalized ROI entry for metadata/frontmatter storage."""
    roi_entry: dict = {}
    if eval_roi_data.get("model"):
        roi_entry["model"] = str(eval_roi_data["model"])

    roi_entry["performance_increase"] = format_roi_percent(
        eval_roi_data.get("performance_increase", 0)
    )
    roi_entry["token_savings"] = format_roi_percent(
        eval_roi_data.get("token_savings", 0),
        clamp_min=0,
    )

    if include_analytics:
        analytics_fields = (
            ("test_count", "test_count"),
            ("with_skill_avg", "with_success_rate"),
            ("baseline_avg", "baseline_success_rate"),
        )
        for source_key, output_key in analytics_fields:
            if eval_roi_data.get(source_key) is not None:
                roi_entry[output_key] = eval_roi_data[source_key]

    return roi_entry


# =============================================================================
# Validators: validate_frontmatter + validate_bundle
# =============================================================================
# Codes returned via the shared ValidationFinding type.
#
# F-prefix: frontmatter findings (validate_frontmatter).
# W-prefix: warnings (validate_bundle).
# E-prefix: bundle errors (validate_bundle).
# P-prefix: planner / ResourcePlan errors (validate_bundle).
# Codes are stable identifiers — see the per-code docstrings below.

_KEBAB_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")

# Path: forward-slash, two segments max in Phase 3, each segment kebab-ish.
# First segment is checked against the whitelist separately.
_BUNDLE_PATH_RE = re.compile(r"^[a-z0-9][a-z0-9._-]*(?:/[a-z0-9._-]+)?$")

_EVIDENCE_CITATION_RE = re.compile(r"\bev:\d+\b|\bL\d+-\d+\b")

_SHELL_VAR_CITATION_PREFIX = r'"[^"]*\$\{?[A-Za-z_][A-Za-z0-9_]*\}?[^"]*?/'

_REVERSE_CITATION_RE = re.compile(
    r"`((?:references|scripts|assets)/[a-z0-9][a-z0-9._-]*(?:/[a-z0-9._-]+)?)`"
    r"|\]\(((?:references|scripts|assets)/[a-z0-9][a-z0-9._-]*(?:/[a-z0-9._-]+)?)\)"
    r"|/load\s+((?:references|scripts|assets)/[a-z0-9][a-z0-9._-]*(?:/[a-z0-9._-]+)?)"
)

_REVERSE_SHELL_CITATION_RE = re.compile(
    _SHELL_VAR_CITATION_PREFIX
    + r"((?:references|scripts|assets)/[a-z0-9][a-z0-9._-]*(?:/[a-z0-9._-]+)?)"
    + r'"'
)

_EXTRACT_PHRASES = ("extract", "move to references", "pull out")

_EXTENSIONS_BY_KIND: dict[str, set[str]] = {
    "reference": {".md"},
    "script": {".py", ".sh"},
    "asset": {".md", ".json", ".yaml", ".txt", ".csv", ".tmpl"},
}

# Allowed top-level frontmatter keys at runtime stage (F003 source).
_ALLOWED_RUNTIME_KEYS: frozenset[str] = frozenset(
    {
        "description",
        "allowed-tools",
        "name",
        "version",
        "author",
        "tags",
        "argument-hint",
        "disable-model-invocation",
        "metadata",
    }
)

# Required keys per stage (F001 source).
_REQUIRED_KEYS_BY_STAGE: dict[str, tuple[str, ...]] = {
    "runtime": ("description", "allowed-tools"),
    "extracted": ("description", "name"),
}

_W001_BODY_LINES = 500

_E002_BODY_LINES = 800

_PER_FILE_BYTES_LIMIT = 32 * 1024

_TOTAL_BUNDLE_BYTES_LIMIT = 256 * 1024


def _is_empty_value(value: object) -> bool:
    """Treat None, '', and [] as empty; everything else as populated."""
    if value is None:
        return True
    if isinstance(value, str) and value.strip() == "":
        return True
    if isinstance(value, list) and len(value) == 0:
        return True
    return False


def _description_starts_first_person(description: str) -> bool:
    stripped = description.lstrip().lower()
    for marker in ("i ", "my ", "we "):
        if stripped.startswith(marker):
            return True
    return False


def validate_frontmatter(
    skill_md: str,
    *,
    stage: Literal["runtime", "extracted"],
    expected_slug: str | None = None,
) -> list[ValidationFinding]:
    """Validate SKILL.md YAML frontmatter against the per-stage contract.

    Returns every finding in one pass. Callers decide policy:
    `severity="error"` blocks, `severity="warning"` is report-only.

    `expected_slug` is the directory/slug the skill is expected to own. When
    `None`, the F005 name-vs-directory check is skipped — all other checks
    still run.
    """
    findings: list[ValidationFinding] = []
    frontmatter = parse_frontmatter(skill_md)

    required = _REQUIRED_KEYS_BY_STAGE[stage]
    for key in required:
        if key not in frontmatter:
            findings.append(
                ValidationFinding(
                    code="F001_MISSING_REQUIRED_KEY",
                    severity="error",
                    message=f"Required key {key!r} missing at stage={stage!r}",
                )
            )
        elif _is_empty_value(frontmatter[key]):
            # Empty allowed-tools list is a more specific F007.
            if key == "allowed-tools" and isinstance(frontmatter[key], list):
                findings.append(
                    ValidationFinding(
                        code="F007_ALLOWED_TOOLS_EMPTY_LIST",
                        severity="error",
                        message="'allowed-tools' is an empty list",
                    )
                )
            else:
                findings.append(
                    ValidationFinding(
                        code="F002_EMPTY_VALUE",
                        severity="error",
                        message=f"Key {key!r} is present but empty at stage={stage!r}",
                    )
                )

    if stage == "runtime":
        for key in frontmatter:
            if key not in _ALLOWED_RUNTIME_KEYS:
                findings.append(
                    ValidationFinding(
                        code="F003_FORBIDDEN_KEY",
                        severity="error",
                        message=f"Key {key!r} is not permitted at stage='runtime'",
                    )
                )

    name = frontmatter.get("name")
    if isinstance(name, str) and name:
        if not _KEBAB_RE.match(name):
            findings.append(
                ValidationFinding(
                    code="F004_BAD_KEBAB_CASE",
                    severity="error",
                    message=f"'name' value {name!r} is not kebab-case",
                )
            )
        if expected_slug is not None and name != expected_slug:
            findings.append(
                ValidationFinding(
                    code="F005_NAME_DIR_MISMATCH",
                    severity="error",
                    message=f"'name' is {name!r} but expected slug is {expected_slug!r}",
                )
            )

    description = frontmatter.get("description")
    if isinstance(description, str) and _description_starts_first_person(description):
        findings.append(
            ValidationFinding(
                code="F006_DESCRIPTION_WRONG_PERSON",
                severity="warning",
                message=(
                    "'description' should be written in the third person "
                    "(starts with 'I ', 'my ', or 'we ')"
                ),
            )
        )

    return findings


def _validate_bundle_path(bf: BundleFile, nbytes: int) -> list[ValidationFinding]:
    """Path-safety checks for a single BundleFile. `nbytes` is its UTF-8 size."""
    findings: list[ValidationFinding] = []
    path = bf.path
    if not path or path.startswith("/") or "\\" in path or ".." in path.split("/"):
        findings.append(
            ValidationFinding(
                code="E003_BUNDLE_PATH_UNSAFE",
                severity="error",
                message=f"Path {path!r} is unsafe (absolute, backslash, or traversal)",
                path=path,
            )
        )
        # No further per-path checks once we've established unsafety.
        return findings

    head, _, tail = path.partition("/")
    if head not in _BUNDLE_ROOTS:
        findings.append(
            ValidationFinding(
                code="E004_BUNDLE_PATH_WHITELIST",
                severity="error",
                message=f"Top-level directory {head!r} must be one of {sorted(_BUNDLE_ROOTS)}",
                path=path,
            )
        )
        return findings

    if not tail:
        findings.append(
            ValidationFinding(
                code="E003_BUNDLE_PATH_UNSAFE",
                severity="error",
                message=f"Path {path!r} is just a directory; expected a file",
                path=path,
            )
        )
        return findings

    if not _BUNDLE_PATH_RE.match(path):
        findings.append(
            ValidationFinding(
                code="E003_BUNDLE_PATH_UNSAFE",
                severity="error",
                message=f"Path {path!r} fails the bundle path regex",
                path=path,
            )
        )
        return findings

    suffix = PurePosixPath(path).suffix
    allowed = _EXTENSIONS_BY_KIND[bf.kind]
    if suffix not in allowed:
        findings.append(
            ValidationFinding(
                code="E005_BUNDLE_EXTENSION",
                severity="error",
                message=(
                    f"Extension {suffix!r} not permitted for kind={bf.kind!r}; "
                    f"allowed: {sorted(allowed)}"
                ),
                path=path,
            )
        )

    # Per-file size cap.
    if nbytes > _PER_FILE_BYTES_LIMIT:
        findings.append(
            ValidationFinding(
                code="E006_BUNDLE_FILE_TOO_LARGE",
                severity="error",
                message=f"File {path!r} exceeds {_PER_FILE_BYTES_LIMIT} bytes",
                path=path,
            )
        )
    return findings


def _validate_bundle_consistency(bundle: SkillBundle) -> list[ValidationFinding]:
    """ResourcePlan.create_X iff any BundleFile.kind == X."""
    findings: list[ValidationFinding] = []
    rp = bundle.resource_plan
    flag_by_kind = {
        "reference": rp.create_references,
        "script": rp.create_scripts,
        "asset": rp.create_assets,
    }
    has_kind = {k: any(bf.kind == k for bf in bundle.bundle_files) for k in flag_by_kind}
    for kind, flag in flag_by_kind.items():
        if flag != has_kind[kind]:
            findings.append(
                ValidationFinding(
                    code="E008_BUNDLE_FLAG_FILES_MISMATCH",
                    severity="error",
                    message=(
                        f"create_{kind}s={flag} but bundle_files {'has' if has_kind[kind] else 'lacks'} "
                        f"kind={kind!r}"
                    ),
                )
            )
    return findings


def _cites_path_shell(skill_md: str, path: str) -> bool:
    """Match a shell-string citation: `"$VAR/.../<path>"` with variable expansion."""
    pattern = _SHELL_VAR_CITATION_PREFIX + re.escape(path) + '"'
    return re.search(pattern, skill_md) is not None


def iter_cited_bundle_paths(skill_md: str) -> set[str]:
    """Bundle-file paths cited in ``skill_md`` (contract §Consistency).

    Recognises the four citation forms that the bundle contract considers
    resolvable: ``` `<root>/<file>` ``` (backtick), ``[label](<root>/<file>)``
    (markdown link), ``/load <root>/<file>`` (slash directive), and the
    shell-string form ``"${VAR}/<root>/<file>"`` (matches ``_cites_path_shell``
    on the forward direction). ``<root>`` is one of ``references`` /
    ``scripts`` / ``assets``. Returns the set of distinct paths cited; order
    is not meaningful.

    Used by ``validate_bundle`` for E012 dangling-citation detection and by
    downstream consumers (pipeline diagnostics, judge harness) that need to
    know which bundle files a SKILL.md references without re-implementing
    the grammar.
    """
    cited: set[str] = set()
    for match in _REVERSE_CITATION_RE.finditer(skill_md):
        for group in match.groups():
            if group:
                cited.add(group)
    for match in _REVERSE_SHELL_CITATION_RE.finditer(skill_md):
        cited.add(match.group(1))
    return cited


def _validate_bundle_citations(bundle: SkillBundle) -> list[ValidationFinding]:
    """Bidirectional citation check — only meaningful when bundle_files is non-empty."""
    findings: list[ValidationFinding] = []
    if not bundle.bundle_files:
        return findings

    bundle_paths = {bf.path for bf in bundle.bundle_files}
    cited = iter_cited_bundle_paths(bundle.skill_md)

    # Forward: every BundleFile.path must be cited somewhere in SKILL.md.
    for bf in bundle.bundle_files:
        if bf.path in cited or _cites_path_shell(bundle.skill_md, bf.path):
            continue
        findings.append(
            ValidationFinding(
                code="E009_BUNDLE_CITATION_MISSING",
                severity="error",
                message=f"BundleFile {bf.path!r} is not cited in SKILL.md",
                path=bf.path,
            )
        )

    # Reverse: every recognised citation to references|scripts|assets must
    # resolve to an actual BundleFile.
    for path in cited - bundle_paths:
        findings.append(
            ValidationFinding(
                code="E012_BUNDLE_DANGLING_CITATION",
                severity="error",
                message=f"SKILL.md cites {path!r} but no matching BundleFile exists",
                path=path,
            )
        )
    return findings


def _validate_resource_plan(rp: ResourcePlan) -> list[ValidationFinding]:
    """Planner codes P001-P004."""
    findings: list[ValidationFinding] = []

    if rp.create_assets:
        findings.append(
            ValidationFinding(
                code="P001_CREATE_ASSETS_FORBIDDEN_IN_PHASE_3",
                severity="error",
                message="create_assets=True is forbidden until a dedicated assets design doc lifts the restriction",
            )
        )

    any_create = rp.create_references or rp.create_scripts or rp.create_assets
    if any_create and not rp.rationale.strip():
        findings.append(
            ValidationFinding(
                code="P002_EMPTY_RATIONALE",
                severity="error",
                message="'rationale' must be non-empty when any create_* flag is True",
            )
        )
    if any_create and rp.rationale.strip() and not _EVIDENCE_CITATION_RE.search(rp.rationale):
        findings.append(
            ValidationFinding(
                code="P003_RATIONALE_LACKS_CITATION",
                severity="error",
                message="'rationale' must cite at least one evidence id (ev:NNN) or line range (L<n>-<m>)",
            )
        )

    rationale_lower = rp.rationale.lower()
    if any(phrase in rationale_lower for phrase in _EXTRACT_PHRASES) and not any_create:
        findings.append(
            ValidationFinding(
                code="P004_RATIONALE_EXTRACT_FLAG_MISMATCH",
                severity="error",
                message="'rationale' claims extraction but every create_* flag is False",
            )
        )
    return findings


def validate_bundle(
    bundle: SkillBundle,
    *,
    stage: Literal["runtime", "extracted"],
) -> list[ValidationFinding]:
    """Validate a SkillBundle and its embedded SKILL.md frontmatter.

    Concatenates `validate_frontmatter(skill_md, stage=stage,
    expected_slug=bundle.skill_slug)` findings directly (shared
    ValidationFinding type — no conversion).

    Note: the citation check (E009/E012) is only meaningful on the final
    assembled bundle, post-Stage C. Callers must not invoke `validate_bundle`
    on intermediate pipeline artefacts where `bundle_files` has not yet been
    populated (per contract §Consistency — Timing).
    """
    findings: list[ValidationFinding] = []

    # SKILL.md body size (excluding frontmatter).
    _, body = split_frontmatter(bundle.skill_md)
    body_lines = len(body.splitlines())
    if body_lines > _E002_BODY_LINES:
        findings.append(
            ValidationFinding(
                code="E002_SKILL_MD_TOO_LONG",
                severity="error",
                message=f"SKILL.md body has {body_lines} lines (limit {_E002_BODY_LINES})",
            )
        )
    elif body_lines > _W001_BODY_LINES:
        findings.append(
            ValidationFinding(
                code="W001_SKILL_MD_LONG",
                severity="warning",
                message=(
                    f"SKILL.md body has {body_lines} lines (Anthropic soft cap {_W001_BODY_LINES})"
                ),
            )
        )

    # Per-file path safety + extension + size.
    total_bytes = 0
    for bf in bundle.bundle_files:
        nbytes = len(bf.content.encode("utf-8"))
        findings.extend(_validate_bundle_path(bf, nbytes))
        total_bytes += nbytes
    if total_bytes > _TOTAL_BUNDLE_BYTES_LIMIT:
        findings.append(
            ValidationFinding(
                code="E007_BUNDLE_TOTAL_TOO_LARGE",
                severity="error",
                message=(
                    f"Total bundle content is {total_bytes} bytes "
                    f"(limit {_TOTAL_BUNDLE_BYTES_LIMIT})"
                ),
            )
        )

    findings.extend(_validate_bundle_consistency(bundle))
    findings.extend(_validate_bundle_citations(bundle))
    findings.extend(_validate_resource_plan(bundle.resource_plan))
    findings.extend(
        validate_frontmatter(bundle.skill_md, stage=stage, expected_slug=bundle.skill_slug)
    )
    return findings
