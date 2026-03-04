"""Shared utilities for skill file generation and naming.

Functions:
    sanitize_name: Kebab-case sanitizer for directory/file names.
    ensure_skill_frontmatter: Prepend YAML frontmatter to SKILL.md when missing.
"""

import re


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


def ensure_skill_frontmatter(skill_md: str, skill_name: str) -> str:
    """Ensure SKILL.md content has required YAML frontmatter.

    Claude's skill loader requires a YAML frontmatter block (``name``,
    ``description``) at the top of every SKILL.md for indexing and loading.
    If the content already starts with ``---``, it is returned unchanged.

    When frontmatter is missing, a block is prepended using *skill_name*
    as the ``name`` field and the first non-heading paragraph of the
    markdown body as the ``description``.

    Args:
        skill_md: The raw SKILL.md content (may or may not have frontmatter).
        skill_name: Already-sanitized kebab-case skill name.  Callers are
            responsible for passing a sanitized value (see :func:`sanitize_name`).

    Returns:
        SKILL.md content with a valid YAML frontmatter block.
    """
    if skill_md.strip().startswith("---"):
        return skill_md

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
    fm_lines.append("---")
    fm_lines.append("")

    return "\n".join(fm_lines) + skill_md
