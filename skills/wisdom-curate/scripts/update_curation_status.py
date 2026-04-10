"""Transition a curation between status directories.

Usage:
    python update_curation_status.py <session_id> <new_status>

`new_status` must be one of `pending`, `running`, `completed`.
"""

import sys

from mega_code.client.curation_store import update_curation_status

VALID_STATUSES = ("pending", "running", "completed")


def main() -> None:
    if len(sys.argv) != 3:
        print(
            "update_curation_status: expected exactly 2 arguments: "
            "<session_id> <new_status>",
            file=sys.stderr,
        )
        sys.exit(2)

    session_id, new_status = sys.argv[1], sys.argv[2]

    if new_status not in VALID_STATUSES:
        print(
            f"update_curation_status: invalid status {new_status!r}; "
            f"expected one of {VALID_STATUSES}",
            file=sys.stderr,
        )
        sys.exit(2)

    update_curation_status(session_id, new_status)


if __name__ == "__main__":
    main()
