#!/usr/bin/env python3
"""Export the exact FastAPI contract as deterministic JSON for offline review."""

from __future__ import annotations

import json
from pathlib import Path

from apps.api.main import create_app


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "docs" / "openapi.json"


def main() -> int:
    schema = create_app(":memory:").openapi()
    OUTPUT.write_text(
        json.dumps(schema, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print("wrote %s" % OUTPUT)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
