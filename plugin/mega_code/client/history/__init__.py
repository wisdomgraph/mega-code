"""Claude Code historical data loading and analysis.

This module provides a unified interface for loading Claude Code
conversation data from multiple sources:

- Claude Code native storage (~/.claude/projects/)
- MEGA-Code collector storage (~/.local/mega-code/)
- Parquet datasets (ZAI CC-Bench, NLILE, etc.)

Example:
    from mega_code.client.history import create_loader, Session
    from pathlib import Path

    # Create loader with default sources
    loader = create_loader()

    # Or with custom datasets
    loader = create_loader(
        dataset_paths={
            "zai_bench": Path("datasets/zai-cc-bench/train.parquet"),
        }
    )

    # Iterate over all sessions
    for session in loader.iter_all():
        print(f"[{session.metadata.source}] {session.metadata.session_id}")
        print(f"  Messages: {len(session.messages)}")
        print(f"  Tool calls: {session.stats.tool_call_count}")

    # Load specific session
    session = loader.load_from("claude_native", "abc123-...")
"""

from mega_code.client.history.loader import (
    DataLoader,
    create_loader,
    load_session_by_id,
    load_sessions_from_project,
)
from mega_code.client.history.models import (
    HistorySessionMetadata,
    HistorySessionStats,
    Message,
    Session,
    TokenUsage,
    ToolCall,
)
from mega_code.client.history.protocol import DataSource
from mega_code.client.history.sources import (
    ClaudeNativeSource,
    CodexSource,
    CursorSource,
    GeminiSource,
    MegaCodeSource,
    OpenCodeSource,
    ParquetDatasetSource,
)

__all__ = [
    # Main API
    "create_loader",
    "DataLoader",
    "load_session_by_id",
    "load_sessions_from_project",
    # Protocol
    "DataSource",
    # Models
    "Message",
    "Session",
    "HistorySessionMetadata",
    "HistorySessionStats",
    "TokenUsage",
    "ToolCall",
    # Sources
    "ClaudeNativeSource",
    "CodexSource",
    "CursorSource",
    "GeminiSource",
    "MegaCodeSource",
    "OpenCodeSource",
    "ParquetDatasetSource",
]
