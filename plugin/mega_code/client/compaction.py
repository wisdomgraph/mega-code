"""Code block compaction module for token reduction.

Pure regex — zero external dependencies beyond pydantic.
Extracted from pipeline/compaction.py for open-source client.
"""

import re

from pydantic import BaseModel


class CompactionResult(BaseModel):
    """Result of content compaction."""

    compacted: str
    code_blocks: dict[str, str]  # placeholder_id -> original_code


class CodeBlockCompactor:
    """Replace code blocks with placeholders for token reduction.

    Reusable across pipeline steps (Step 0, Step 1, etc.).
    """

    def __init__(self, prefix: str = "CODE_BLOCK"):
        self.prefix = prefix
        self._counter = 0

    def compact(self, content: str) -> CompactionResult:
        """Replace code blocks with placeholders."""
        code_blocks: dict[str, str] = {}

        def replace_block(match: re.Match) -> str:
            lang = match.group(1) or ""
            code = match.group(2)
            lines = code.count("\n") + 1
            placeholder_id = f"{self.prefix}_{self._counter}"
            code_blocks[placeholder_id] = match.group(0)
            self._counter += 1
            return f"```{lang}\n[{placeholder_id}_{lines}_LINES]\n```"

        # Match markdown code fences
        pattern = r"```(\w*)\n(.*?)\n```"
        compacted = re.sub(pattern, replace_block, content, flags=re.DOTALL)

        return CompactionResult(compacted=compacted, code_blocks=code_blocks)

    def restore(self, compacted: str, code_blocks: dict[str, str]) -> str:
        """Restore original code blocks from placeholders."""
        result = compacted
        for placeholder_id, original in code_blocks.items():
            pattern = rf"```\w*\n\[{placeholder_id}_\d+_LINES\]\n```"
            result = re.sub(pattern, original, result)
        return result

    def reset(self) -> None:
        """Reset counter for new session."""
        self._counter = 0
