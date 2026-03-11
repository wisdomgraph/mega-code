"""DataLoader facade for unified access to Claude Code historical data.

Provides a single interface to load and iterate over sessions from
multiple data sources.

Also provides convenience functions for common loading patterns:
- ``load_session_by_id``: Load a single session by UUID.
- ``load_sessions_from_project``: Load sessions from a project directory,
  optionally enriching with Claude native and Codex CLI sessions.
"""

import logging
from pathlib import Path
from typing import Iterator, Literal

from mega_code.client.history.models import Session
from mega_code.client.history.protocol import DataSource
from mega_code.client.history.sources.claude_native import ClaudeNativeSource
from mega_code.client.history.sources.mega_code import MegaCodeSource
from mega_code.client.history.sources.parquet import ParquetDatasetSource

logger = logging.getLogger(__name__)


class DataLoader:
    """Unified loader for multiple Claude Code data sources.

    Provides a single interface to register and iterate over sessions
    from different data sources (Claude native, MEGA-Code, Parquet datasets).

    Example:
        loader = DataLoader()
        loader.register_source(ClaudeNativeSource())
        loader.register_source(ParquetDatasetSource(
            path=Path("datasets/zai-bench.parquet"),
            source_name="zai_bench",
        ))

        # Iterate over all sources
        for session in loader.iter_all():
            print(f"[{session.metadata.source}] {session.metadata.session_id}")

        # Iterate over specific source
        for session in loader.iter_source("claude_native"):
            print(session.metadata.first_prompt)
    """

    def __init__(self) -> None:
        """Initialize an empty DataLoader."""
        self._sources: dict[str, DataSource] = {}

    def register_source(self, source: DataSource) -> None:
        """Register a data source.

        Args:
            source: A DataSource implementation to register.

        Raises:
            ValueError: If a source with the same name is already registered.
        """
        if source.name in self._sources:
            raise ValueError(f"Source already registered: {source.name}")
        self._sources[source.name] = source
        logger.info(f"Registered data source: {source.name}")

    @property
    def sources(self) -> list[str]:
        """Return list of registered source names."""
        return list(self._sources.keys())

    def get_source(self, source_name: str) -> DataSource:
        """Get a registered source by name.

        Args:
            source_name: Name of the source to retrieve.

        Returns:
            The DataSource implementation.

        Raises:
            KeyError: If the source is not registered.
        """
        if source_name not in self._sources:
            raise KeyError(f"Source not registered: {source_name}")
        return self._sources[source_name]

    def load_from(self, source_name: str, session_id: str) -> Session:
        """Load a specific session from a named source.

        Args:
            source_name: Name of the source to load from.
            session_id: Unique identifier of the session.

        Returns:
            The loaded Session object.

        Raises:
            KeyError: If source or session is not found.
        """
        source = self.get_source(source_name)
        return source.load_session(session_id)

    def iter_all(
        self,
        errors: Literal["raise", "warn", "ignore"] = "warn",
    ) -> Iterator[Session]:
        """Iterate over all sessions from all registered sources.

        Sessions are yielded lazily to support memory-efficient
        processing of large datasets.

        Args:
            errors: Error handling strategy.
                - "raise": re-raise exceptions from sources.
                - "warn": log a warning and skip the source (default).
                - "ignore": silently skip sources that error.

        Yields:
            Session objects from all sources.
        """
        for source in self._sources.values():
            try:
                yield from source.iter_sessions()
            except Exception as e:
                if errors == "raise":
                    raise
                if errors == "warn":
                    logger.warning(f"Error iterating source {source.name}: {e}")

    def iter_source(self, source_name: str) -> Iterator[Session]:
        """Iterate over sessions from a specific source.

        Args:
            source_name: Name of the source to iterate.

        Yields:
            Session objects from the specified source.

        Raises:
            KeyError: If the source is not registered.
        """
        source = self.get_source(source_name)
        yield from source.iter_sessions()

    def count_all(self) -> dict[str, int]:
        """Count sessions in all sources.

        Returns:
            Dictionary mapping source names to session counts.
        """
        counts: dict[str, int] = {}
        for name, source in self._sources.items():
            try:
                counts[name] = source.count_sessions()
            except Exception as e:
                logger.warning(f"Error counting source {name}: {e}")
                counts[name] = 0
        return counts


def create_loader(
    include_claude_native: bool = True,
    include_mega_code: bool = True,
    claude_native_path: Path | None = None,
    mega_code_path: Path | None = None,
    dataset_paths: dict[str, Path | tuple[Path, str]] | None = None,
) -> DataLoader:
    """Create a DataLoader with specified sources.

    Factory function to create a DataLoader pre-configured with
    common data sources.

    Args:
        include_claude_native: Include ~/.claude/projects source.
        include_mega_code: Include ~/.local/share/mega-code source.
        claude_native_path: Custom path for Claude native data.
        mega_code_path: Custom path for MEGA-Code data.
        dataset_paths: Dict mapping source names to Parquet paths.
            Values can be:
            - Path: Uses default trajectory column name
            - tuple[Path, str]: (path, trajectory_column_name)

    Returns:
        Configured DataLoader instance.

    Example:
        # Basic usage with all defaults
        loader = create_loader()

        # With custom dataset
        loader = create_loader(
            dataset_paths={
                "zai_bench": Path("datasets/zai-cc-bench/train.parquet"),
                "nlile": (Path("datasets/nlile/data/"), "messages_json"),
            }
        )

        # Only load from datasets
        loader = create_loader(
            include_claude_native=False,
            include_mega_code=False,
            dataset_paths={
                "zai_bench": Path("datasets/train.parquet"),
            }
        )
    """
    loader = DataLoader()

    def _try_register(source_cls: type, label: str, **kwargs) -> None:
        """Register a source, skipping gracefully on missing path or error."""
        try:
            source = source_cls(**kwargs)
            if source.base_path.exists():
                loader.register_source(source)
            else:
                logger.info(
                    f"Skipping {label} source (path not found): {source.base_path}"
                )
        except Exception as e:
            logger.warning(f"Failed to register {label} source: {e}")

    if include_claude_native:
        _try_register(ClaudeNativeSource, "Claude native", base_path=claude_native_path)

    if include_mega_code:
        _try_register(MegaCodeSource, "MEGA-Code", base_path=mega_code_path)

    # Register Parquet dataset sources
    if dataset_paths:
        for name, path_spec in dataset_paths.items():
            try:
                if isinstance(path_spec, tuple):
                    path, trajectory_col = path_spec
                else:
                    path = path_spec
                    trajectory_col = "trajectory"

                if path.exists():
                    source = ParquetDatasetSource(
                        path=path,
                        source_name=name,
                        trajectory_column=trajectory_col,
                    )
                    loader.register_source(source)
                else:
                    logger.warning(f"Dataset path not found: {path}")
            except Exception as e:
                logger.warning(f"Failed to register dataset source {name}: {e}")

    return loader


# =============================================================================
# Convenience Functions
# =============================================================================


def load_session_by_id(session_id: str) -> Session:
    """Load a single session by ID.

    Tries MegaCodeSource first, then ClaudeNativeSource.

    Args:
        session_id: Session UUID.

    Returns:
        Session object.
    """
    loader = DataLoader()
    loader.register_source(MegaCodeSource())
    loader.register_source(ClaudeNativeSource())

    # Try MegaCodeSource first, fall back to ClaudeNativeSource
    try:
        return loader.load_from("mega_code", session_id)
    except KeyError:
        return loader.load_from("claude_native", session_id)


def load_sessions_from_project(
    project_path: Path,
    limit: int | None = None,
    include_claude: bool = False,
    include_codex: bool = False,
) -> list[Session]:
    """Load sessions from a project directory.

    Loads MEGA-Code sessions. Optionally enriches with related Claude native sessions
    and/or Codex CLI sessions when flags are enabled. Sessions are deduplicated by
    session_id (MEGA-Code takes precedence over Claude). Codex sessions are appended
    (no deduplication - different IDs).

    Args:
        project_path: Path to project folder.
        limit: Maximum number of sessions to load (applied to combined total).
        include_claude: Include related Claude Code sessions (default: False).
        include_codex: Include related Codex CLI sessions (default: False).

    Returns:
        List of Session objects (MEGA-Code only, or enriched with Claude/Codex).
    """
    # Load MEGA-Code sessions
    source = MegaCodeSource()
    mega_sessions = list(source.iter_sessions_from_path(project_path))
    logger.info(f"Loaded {len(mega_sessions)} MEGA-Code sessions from {project_path}")

    # Early return if not including Claude or Codex sessions
    if not include_claude and not include_codex:
        result = mega_sessions[:limit] if limit else mega_sessions
        if limit:
            logger.info(f"Applied limit: returning {len(result)} sessions")
        return result

    # Extract unique project paths
    project_paths = set()
    for session in mega_sessions:
        if session.metadata.project_path:
            project_paths.add(session.metadata.project_path)

    if not project_paths:
        logger.debug("No project paths found in MEGA-Code sessions")
        return mega_sessions[:limit] if limit else mega_sessions

    logger.debug(
        f"Found {len(project_paths)} unique project paths in MEGA-Code sessions"
    )

    # Load related Claude native sessions
    claude_sessions: list[Session] = []
    if include_claude:
        try:
            claude_source = ClaudeNativeSource()
            entries = list(
                claude_source.iter_sessions_by_project_paths(list(project_paths))
            )
            logger.info(f"Found {len(entries)} related Claude native session entries")

            for entry in entries:
                session_id = entry.get("sessionId")
                if not session_id:
                    continue
                try:
                    full_path = entry.get("fullPath")
                    if full_path:
                        project_dir = Path(full_path).parent.parent
                        session = claude_source._load_session_from_entry(
                            entry, project_dir
                        )
                        claude_sessions.append(session)
                        logger.debug(f"Loaded session {session_id} from claude_native")
                except Exception as e:
                    logger.warning(f"Failed to load Claude session {session_id}: {e}")

            logger.info(
                f"Successfully loaded {len(claude_sessions)} Claude native sessions"
            )
        except Exception as e:
            logger.warning(f"Failed to load Claude native sessions: {e}")

    # Load related Codex sessions
    codex_sessions: list[Session] = []
    if include_codex:
        try:
            from mega_code.client.history.sources.codex import CodexSource

            codex_source = CodexSource()
            codex_entries = list(
                codex_source.iter_sessions_by_project_paths(list(project_paths))
            )
            logger.info(f"Found {len(codex_entries)} related Codex session entries")

            for entry in codex_entries:
                session_id = entry.get("payload", {}).get("id")
                if not session_id:
                    continue
                try:
                    jsonl_path = Path(entry["session_file_path"])
                    entries = codex_source._load_jsonl_entries(jsonl_path)
                    if entries:
                        session = codex_source._load_session_from_entries(
                            entries, jsonl_path
                        )
                        codex_sessions.append(session)
                        logger.debug(f"Loaded session {session_id} from codex_cli")
                except Exception as e:
                    logger.warning(f"Failed to load Codex session {session_id}: {e}")

            logger.info(f"Successfully loaded {len(codex_sessions)} Codex sessions")
        except Exception as e:
            logger.warning(f"Failed to load Codex sessions: {e}")

    session_map: dict[str, Session] = {}

    for session in claude_sessions:
        session_map[session.metadata.session_id] = session

    for session in mega_sessions:
        session_map[session.metadata.session_id] = session
        logger.debug(f"Loaded session {session.metadata.session_id} from mega_code")

    # Build session map for deduplication (MEGA-Code > Claude)
    # Merge: deduplicated sessions + appended Codex
    merged = list(session_map.values())
    merged.extend(codex_sessions)

    logger.info(
        f"Merged: {len(mega_sessions)} MEGA-Code + "
        f"{len(claude_sessions)} Claude + "
        f"{len(codex_sessions)} Codex = {len(merged)} total after dedup"
    )

    if limit:
        merged = merged[:limit]
        logger.info(f"Applied limit: returning {len(merged)} sessions")

    return merged
