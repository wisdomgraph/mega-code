#!/usr/bin/env python3
"""Export the raw OpenAPI spec from the FastAPI app to stdout as YAML.

Run with mega-code's uv env so `mega_code.server.app` is importable:

    uv run --directory <mega-code-dir> python <abs-path>/scripts/export_openapi.py
"""

from __future__ import annotations

import sys

import yaml

from mega_code.server.app import create_app


def main() -> None:
    app = create_app()
    yaml.safe_dump(
        app.openapi(),
        sys.stdout,
        sort_keys=False,
        default_flow_style=False,
    )


if __name__ == "__main__":
    main()
