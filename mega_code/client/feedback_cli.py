"""CLI for saving feedback data from /mega-code:feedback workflow.

This script is called from SKILL.md bash commands after AskUserQuestion
collects user responses. Uses the client protocol so feedback is saved
via the appropriate backend (local → feedback.json, remote → server API).

Usage:
    uv run python -m mega_code.client.feedback_cli \
        --run-id f47ac10b-58cc-4372-a567-0e02b2c3d479 \
        --project my-project_a1b2c3d4 \
        --overall-quality good \
        --comments "Great skills but could use more context"

    # With per-item ratings (JSON string)
    uv run python -m mega_code.client.feedback_cli \
        --run-id f47ac10b-58cc-4372-a567-0e02b2c3d479 \
        --project my-project_a1b2c3d4 \
        --overall-quality excellent \
        --item-ratings '{"my-skill": {"ratings": {"focus": 5}, "useful": "yes"}}'
"""

import argparse
import json
import sys

from mega_code.client.feedback import load_manifest
from mega_code.client.models import FeedbackItem


def _build_feedback_item(
    item: dict, item_type: str, actions: dict[str, str], item_ratings: dict
) -> FeedbackItem:
    """Build a FeedbackItem from manifest data and optional per-item ratings."""
    name = item["name"]
    data = item_ratings.get(name, {})
    return FeedbackItem(
        item_id=name,
        item_type=item_type,
        ratings=data.get("ratings", {}),
        useful=data.get("useful"),
        reason=data.get("reason"),
        improvement_suggestion=data.get("improvement_suggestion"),
        correction=data.get("correction"),
        action_taken=actions.get(name, "pending"),
        item_path=item.get("path"),
        item_name=item.get("name"),
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Save feedback for a pipeline run")
    parser.add_argument("--run-id", required=True, help="Archive run ID (UUID)")
    parser.add_argument(
        "--project",
        required=True,
        help="Project identifier (e.g. my-project_a1b2c3d4)",
    )
    parser.add_argument(
        "--overall-quality",
        choices=["excellent", "good", "mixed", "poor"],
        help="Overall quality rating",
    )
    parser.add_argument(
        "--comments",
        type=str,
        default=None,
        help="Additional free-text comments",
    )
    parser.add_argument(
        "--item-ratings",
        type=str,
        default=None,
        help=(
            "Per-item ratings as JSON: "
            '{"name": {"ratings": {"focus": 5}, "useful": "yes", "reason": "..."}}'
        ),
    )
    args = parser.parse_args()

    # Load manifest to get item list (local-only: manifest lives on filesystem)
    manifest = load_manifest(run_id=args.run_id, project_id=args.project)
    if not manifest:
        print(f"Error: No archived run found with ID: {args.run_id} for project: {args.project}")
        return 1

    # Parse per-item ratings if provided
    item_ratings = {}
    if args.item_ratings:
        try:
            item_ratings = json.loads(args.item_ratings)
        except json.JSONDecodeError as e:
            print(f"Error: Invalid JSON in --item-ratings: {e}")
            return 1

    # Build typed FeedbackItem objects for client protocol
    items = [
        _build_feedback_item(s, "skill", manifest.actions, item_ratings) for s in manifest.skills
    ] + [
        _build_feedback_item(s, "strategy", manifest.actions, item_ratings)
        for s in manifest.strategies
    ]

    # Use client protocol — auto-selects local or remote
    from mega_code.client.api import create_client

    client = create_client()
    result = client.submit_feedback(
        run_id=args.run_id,
        project_id=args.project,
        overall_quality=args.overall_quality,
        additional_comments=args.comments,
        items=items,
    )

    if result.success:
        print(f"Feedback saved for run {args.run_id} ({len(items)} items)")
        return 0
    else:
        print(f"Error: {result.message}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
