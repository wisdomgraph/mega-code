"""Install skills from a JSON array of SkillRefItem records read from stdin.

Usage:
    cat skills.json | python install_skills.py

Stdin must contain a JSON list of objects with `name`, `path`, and `url`
fields. Exits non-zero if any individual install reported "failed" so the
caller knows to inspect the input rather than treating partial success as
done.
"""

import json
import sys

from pydantic import ValidationError

from mega_code.client.api.protocol import SkillRefItem
from mega_code.client.skill_installer import install_skills


def main() -> None:
    try:
        raw = json.load(sys.stdin)
    except json.JSONDecodeError as exc:
        print(
            f"install_skills: stdin is not valid JSON ({exc.msg} at "
            f"line {exc.lineno} col {exc.colno}). Pipe the `skills` "
            f"field from the wisdom-curate result as a JSON array.",
            file=sys.stderr,
        )
        sys.exit(2)
    if not isinstance(raw, list):
        print(
            f"install_skills: expected a JSON array on stdin, got "
            f"{type(raw).__name__}. Pipe the `skills` field from the "
            f"wisdom-curate result, not the full result object.",
            file=sys.stderr,
        )
        sys.exit(2)
    try:
        skills = [SkillRefItem(**s) for s in raw]
    except (TypeError, ValidationError) as exc:
        print(
            f"install_skills: one or more entries do not match the "
            f"SkillRefItem schema (expected `name`, `path`, `url` per "
            f"item). Details: {exc}",
            file=sys.stderr,
        )
        sys.exit(2)
    results = install_skills(skills)
    for name, status in results.items():
        print(f"{name}: {status}")

    if any(status == "failed" for status in results.values()):
        sys.exit(1)


if __name__ == "__main__":
    main()
