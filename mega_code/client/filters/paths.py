"""Path anonymization filter.

Replaces absolute paths with project-relative or anonymized equivalents
to avoid leaking usernames and directory structures.
"""

import re

from mega_code.client.filters.base import TurnFilter

# Common home directory patterns: /Users/<user> or /home/<user>
# Trailing path segment is optional to also match bare /Users/alice
_HOME_PATTERN = re.compile(r"/(?:Users|home)/[^/\s]+(/[^\s]*)?")


class PathAnonymizer(TurnFilter):
    """Replace absolute paths with project-relative paths.

    Two-stage approach:
    1. Replace project_dir prefix with './' (most specific).
    2. Replace remaining /Users/<user>/... or /home/<user>/... with './...'

    Args:
        project_dir: Absolute path to the project root directory.
            If provided, all occurrences are replaced with '.'.
    """

    def __init__(self, project_dir: str | None = None):
        self._project_dir = project_dir.rstrip("/") if project_dir else None
        # Build regex that only matches project_dir followed by / , whitespace, or EOL
        self._project_pattern = (
            re.compile(re.escape(self._project_dir) + r"(?=/|\s|$)")
            if self._project_dir
            else None
        )

    def filter_text(self, text: str) -> str:
        """Anonymize absolute paths in text.

        Args:
            text: Input text possibly containing absolute paths.

        Returns:
            Text with paths anonymized.
        """
        result = text
        # Stage 1: project dir -> '.' (require path separator to avoid partial matches)
        if self._project_pattern:
            result = self._project_pattern.sub(".", result)
        # Stage 2: remaining home-dir paths -> './...'
        result = _HOME_PATTERN.sub(r".\1", result)
        return result
