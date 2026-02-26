"""Base class for turn content filters.

All filters implement TurnFilter ABC with a single required method: filter_text().
The base class provides filter_turn() which applies filter_text() to all text fields
of a Turn, returning a new immutable Turn instance.
"""

from abc import ABC, abstractmethod

from mega_code.client.models import Turn


class TurnFilter(ABC):
    """Base class for turn content filters.

    Subclasses must implement filter_text(). The default filter_turn()
    applies filter_text() to all text fields listed in _TEXT_FIELDS.
    """

    _TEXT_FIELDS = ("content", "command", "tool_target")

    @abstractmethod
    def filter_text(self, text: str) -> str:
        """Apply filter to a text string.

        Args:
            text: Input text to filter.

        Returns:
            Filtered text.
        """
        ...

    def filter_turn(self, turn: Turn) -> Turn:
        """Apply filter to all sensitive text fields of a Turn.

        Returns a new Turn instance (Turn model is immutable).

        Args:
            turn: Input Turn object.

        Returns:
            New Turn with filtered text fields.
        """
        changes: dict[str, str] = {
            field: self.filter_text(value)
            for field in self._TEXT_FIELDS
            if (value := getattr(turn, field)) is not None
        }
        return turn.model_copy(update=changes)
