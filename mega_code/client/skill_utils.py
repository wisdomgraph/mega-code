"""Shared utilities for skill file generation and naming.

Functions:
    sanitize_name: Kebab-case sanitizer for directory/file names.
    ensure_skill_frontmatter: Prepend YAML frontmatter to SKILL.md when missing.
    ensure_strategy_frontmatter: Prepend YAML frontmatter to strategy markdown.
    ensure_lesson_frontmatter: Prepend YAML frontmatter to lesson markdown.
    get_author: Read author attribution string from env or default.
"""

from __future__ import annotations

import os
import re

DEFAULT_AUTHOR = "co-authored by www.megacode.ai"
DEFAULT_VERSION = "1.0.0"


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


def bump_minor_version(version: str) -> str:
    """Bump the minor version: 1.0.0 -> 1.1.0, 1.2.0 -> 1.3.0.

    Used by cross-run dedup when a previously-rejected item resurfaces
    with higher signal_strength.
    """
    parts = version.split(".")
    if len(parts) == 3:
        return f"{parts[0]}.{int(parts[1]) + 1}.0"
    return "1.1.0"


def _build_metadata_lines(
    *,
    author: str = "",
    version: str = "",
    tags: list[str] | None = None,
) -> list[str]:
    """Build YAML frontmatter lines for author/version/tags."""
    lines: list[str] = []
    if author:
        lines.append(f"author: {author}")
    if version:
        lines.append(f"version: {version}")
    if tags:
        lines.append("tags:")
        for tag in tags:
            lines.append(f"  - {tag}")
    return lines


def _inject_metadata_into_existing(
    content: str,
    *,
    author: str = "",
    version: str = "",
    tags: list[str] | None = None,
    extra_fields: dict[str, str] | None = None,
) -> str:
    """Inject missing metadata fields into existing YAML frontmatter.

    Only adds fields that are not already present in the frontmatter block.
    """
    lines = content.split("\n")
    # Find the closing --- of frontmatter
    end_idx = -1
    for i, line in enumerate(lines):
        if i == 0:
            continue  # skip opening ---
        if line.strip() == "---":
            end_idx = i
            break

    if end_idx == -1:
        return content  # malformed frontmatter, return unchanged

    # Collect existing keys
    existing_keys: set[str] = set()
    for line in lines[1:end_idx]:
        if ":" in line and not line.startswith(" ") and not line.startswith("\t"):
            key = line.split(":", 1)[0].strip()
            existing_keys.add(key)

    # Build lines to inject
    inject: list[str] = []
    if author and "author" not in existing_keys:
        inject.append(f"author: {author}")
    if version and "version" not in existing_keys:
        inject.append(f"version: {version}")
    if tags and "tags" not in existing_keys:
        inject.append("tags:")
        for tag in tags:
            inject.append(f"  - {tag}")
    if extra_fields:
        for key, val in extra_fields.items():
            if key not in existing_keys:
                inject.append(f"{key}: {val}")

    if not inject:
        return content

    # Insert before closing ---
    result = lines[:end_idx] + inject + lines[end_idx:]
    return "\n".join(result)


def ensure_skill_frontmatter(
    skill_md: str,
    skill_name: str,
    *,
    author: str = "",
    version: str = "",
    tags: list[str] | None = None,
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
        version: Semantic version string (e.g. "1.0.0").
        tags: List of lowercase kebab-case tags.

    Returns:
        SKILL.md content with a valid YAML frontmatter block.
    """
    if skill_md.strip().startswith("---"):
        return _inject_metadata_into_existing(
            skill_md,
            author=author,
            version=version,
            tags=tags,
        )

    # Extract description from first non-heading paragraph
    description = ""
    for line in skill_md.strip().split("\n"):
        stripped = line.strip()
        if stripped and not stripped.startswith("#"):
            description = stripped
            break

    # Build YAML frontmatter
    fm_lines = ["---", f"name: {skill_name}"]
    if description:
        fm_lines.append("description: |")
        fm_lines.append(f"  {description}")
    else:
        fm_lines.append(f"description: Skill {skill_name}")
    fm_lines.extend(_build_metadata_lines(author=author, version=version, tags=tags))
    fm_lines.append("---")
    fm_lines.append("")

    return "\n".join(fm_lines) + skill_md


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
