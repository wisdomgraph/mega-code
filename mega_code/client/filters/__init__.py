"""Sensitive data filter pipeline for turns.

Composes pluggable TurnFilter stages that are applied to turns before upload.
Raw events.jsonl is preserved unfiltered for local debugging; filtering
happens only before network transmission.

Usage:
    from mega_code.client.filters import filter_turns

    filtered = filter_turns(turns, project_dir="/path/to/project")
"""

from mega_code.client.filters.base import TurnFilter
from mega_code.client.filters.cleaning import CleaningResult, clean_mega_code_turns
from mega_code.client.filters.paths import PathAnonymizer
from mega_code.client.filters.secrets import SecretMasker
from mega_code.client.models import SessionMetadata, Turn


def create_default_pipeline(
    project_dir: str | None = None,
) -> list[TurnFilter]:
    """Create the default filter pipeline.

    Args:
        project_dir: Project root path for path anonymization.

    Returns:
        Ordered list of TurnFilter instances.
    """
    return [
        SecretMasker(),
        PathAnonymizer(project_dir=project_dir),
    ]


def filter_turns(
    turns: list[Turn],
    filters: list[TurnFilter] | None = None,
    project_dir: str | None = None,
) -> list[Turn]:
    """Apply all filters to a list of turns.

    Args:
        turns: Input turns to filter.
        filters: Explicit filter list. If None, uses create_default_pipeline().
        project_dir: Project root path (forwarded to default pipeline).

    Returns:
        New list of filtered Turn instances.
    """
    if filters is None:
        filters = create_default_pipeline(project_dir=project_dir)
    result = []
    for turn in turns:
        filtered = turn
        for f in filters:
            filtered = f.filter_turn(filtered)
        result.append(filtered)
    return result


def filter_metadata(
    metadata: SessionMetadata,
    filters: list[TurnFilter] | None = None,
    project_dir: str | None = None,
) -> SessionMetadata:
    """Apply filters to sensitive fields in SessionMetadata.

    Currently filters project_path (which contains the full absolute path).

    Args:
        metadata: Session metadata to filter.
        filters: Explicit filter list. If None, uses create_default_pipeline().
        project_dir: Project root path (forwarded to default pipeline).

    Returns:
        New SessionMetadata with filtered fields.
    """
    if filters is None:
        filters = create_default_pipeline(project_dir=project_dir)
    if not metadata.project_path:
        return metadata
    filtered_path = metadata.project_path
    for f in filters:
        filtered_path = f.filter_text(filtered_path)
    return metadata.model_copy(update={"project_path": filtered_path})


__all__ = [
    "CleaningResult",
    "PathAnonymizer",
    "SecretMasker",
    "TurnFilter",
    "clean_mega_code_turns",
    "create_default_pipeline",
    "filter_metadata",
    "filter_turns",
]
