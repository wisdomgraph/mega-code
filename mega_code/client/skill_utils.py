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

import yaml

DEFAULT_AUTHOR = "co-authored by www.megacode.ai"
DEFAULT_VERSION = "1.0.0"
MEGACODE_AUTHOR_MARKER = "megacode.ai"
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
        return render_frontmatter(normalized) + body

    return ensure_skill_frontmatter(
        skill_md,
        skill_name,
        author=resolved_author,
        version=resolved_version,
        generated_at=str(extra_frontmatter.get("generated_at", "")),
        tags=resolved_tags or None,
        extra_frontmatter={"roi": extra_frontmatter["roi"]} if "roi" in extra_frontmatter else None,
    )


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
