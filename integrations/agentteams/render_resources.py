#!/usr/bin/env python3
"""Render the AgentTeams CR template without accepting secret values."""

from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path


PLACEHOLDER = re.compile(r"\$\{([A-Z][A-Z0-9_]*)\}")
ALLOWED = {"AGENTTEAMS_MODEL", "HIGRESS_GATEWAY_URL"}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    template = Path(__file__).with_name("agentteams-resources.yaml.tmpl").read_text(encoding="utf-8")

    def replace(match: re.Match[str]) -> str:
        name = match.group(1)
        if name not in ALLOWED:
            raise ValueError("unsupported placeholder: %s" % name)
        value = os.environ.get(name, "").strip()
        if not value:
            raise ValueError("missing required environment variable: %s" % name)
        if any(character in value for character in "\n\r\0\""):
            raise ValueError("unsafe character in %s" % name)
        return value.rstrip("/")

    try:
        rendered = PLACEHOLDER.sub(replace, template)
    except ValueError as error:
        print(error, file=sys.stderr)
        return 2
    if PLACEHOLDER.search(rendered):
        print("unresolved placeholder", file=sys.stderr)
        return 2
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(rendered, encoding="utf-8")
    print(args.output)
    return 0


if __name__ == "__main__":
    sys.exit(main())

