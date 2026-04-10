"""Save a WisdomCurateResult read from stdin to the pending curations dir.

Usage:
    cat curate_result.json | WC_SESSION_ID=<id> python save_curation.py

Stdin must contain a serialized WisdomCurateResult JSON object. When the
WC_SESSION_ID env var is present (passed inline by the wisdom-curate
skill), this script asserts that the result's session_id matches it.
Downstream status updates and the `mega-code wisdom-feedback` invocation
use that same id, so a mismatch would silently orphan the saved file.
Fail loudly instead.
"""

import json
import os
import sys

from pydantic import ValidationError

from mega_code.client.api.protocol import WisdomCurateResult
from mega_code.client.curation_store import save_curation


def main() -> None:
    try:
        raw = json.load(sys.stdin)
    except json.JSONDecodeError as exc:
        print(
            f"save_curation: stdin is not valid JSON ({exc.msg} at "
            f"line {exc.lineno} col {exc.colno}). Pipe the full "
            f"`mega-code wisdom-curate` JSON response, not a fragment.",
            file=sys.stderr,
        )
        sys.exit(2)

    try:
        result = WisdomCurateResult(**raw)
    except (TypeError, ValidationError) as exc:
        print(
            f"save_curation: stdin JSON does not match WisdomCurateResult "
            f"schema. The server response shape may have changed, or a "
            f"different object was piped in. Details: {exc}",
            file=sys.stderr,
        )
        sys.exit(2)

    expected = os.environ.get("WC_SESSION_ID")
    if expected and result.session_id != expected:
        print(
            f"save_curation: session_id mismatch — expected {expected!r} "
            f"(from WC_SESSION_ID env), got {result.session_id!r} (from "
            f"wisdom-curate CLI response). The server did not echo the "
            f"--session-id we sent, so subsequent status updates and "
            f"feedback submission would target the wrong file. Aborting.",
            file=sys.stderr,
        )
        sys.exit(1)

    save_curation(result)


if __name__ == "__main__":
    main()
