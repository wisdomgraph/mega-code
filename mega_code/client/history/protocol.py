"""DataSource protocol definition for Codex historical data.

This module defines the protocol interface that all data sources must implement
to provide a consistent API for loading conversation data.
"""

from collections.abc import Iterator
from typing import Protocol, runtime_checkable

from mega_code.client.history.models import HistorySessionMetadata, Session


@runtime_checkable
class DataSource(Protocol):
    """Protocol for Codex data sources.

    All data source implementations must conform to this protocol
    to be usable with the DataLoader.

    Example:
        class MyCustomSource:
            @property
            def name(self) -> str:
                return "my_source"

            def list_sessions(self) -> Iterator[HistorySessionMetadata]:
                yield HistorySessionMetadata(session_id="123", source=self.name)

            def load_session(self, session_id: str) -> Session:
                return Session(metadata=HistorySessionMetadata(...), messages=[...])

            def iter_sessions(self) -> Iterator[Session]:
                for meta in self.list_sessions():
                    yield self.load_session(meta.session_id)

            def count_sessions(self) -> int:
                return sum(1 for _ in self.list_sessions())
    """

    @property
    def name(self) -> str:
        """Return the source identifier.

        Returns:
            A unique identifier for this data source
            (e.g., 'claude_native', 'mega_code', 'zai_bench').
        """
        ...

    def list_sessions(self) -> Iterator[HistorySessionMetadata]:
        """List available sessions without loading full message content.

        This method provides efficient enumeration of sessions for
        filtering and selection before loading.

        Yields:
            HistorySessionMetadata for each available session.
        """
        ...

    def load_session(self, session_id: str) -> Session:
        """Load a complete session by its ID.

        Args:
            session_id: The unique identifier of the session to load.

        Returns:
            A Session object containing all messages and metadata.

        Raises:
            KeyError: If the session_id is not found.
            ValueError: If the session data is malformed.
        """
        ...

    def iter_sessions(self) -> Iterator[Session]:
        """Iterate over all sessions with lazy loading.

        This method loads sessions one at a time to support
        memory-efficient processing of large datasets.

        Yields:
            Session objects for each available session.
        """
        ...

    def count_sessions(self) -> int:
        """Return the total number of sessions available.

        Returns:
            The count of sessions in this source.
        """
        ...
