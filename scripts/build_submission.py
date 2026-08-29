#!/usr/bin/env python3
"""Create a deterministic, reviewable GOAI ZIP from an explicit allowlist."""

from __future__ import annotations

import argparse
import subprocess
import sys
import zipfile
from pathlib import Path
from typing import Iterable, List


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DIRS = (
    ".github",
    "agents",
    "apps",
    "contracts",
    "docs",
    "examples",
    "integrations",
    "mcp_servers",
    "protocols",
    "scripts",
    "skills",
    "submission",
    "tests",
)
DEFAULT_FILES = (
    ".dockerignore",
    ".env.example",
    ".gitignore",
    "CONTRIBUTING.md",
    "DATA_CARD.md",
    "LICENSE",
    "Makefile",
    "MODEL_CARD.md",
    "README.md",
    "requirements-api.lock",
    "SECURITY.md",
    "THIRD_PARTY.md",
    "docker-compose.yml",
    "pyproject.toml",
    "uv.lock",
)
EXCLUDED_PARTS = {
    ".mypy_cache",
    ".ruff_cache",
    ".tmp",
    ".git",
    ".pytest_cache",
    ".venv",
    "__pycache__",
    "coverage",
    "dist",
    "node_modules",
    "runtime",
    "test-results",
}
EXCLUDED_SUFFIXES = {
    ".db",
    ".key",
    ".log",
    ".p12",
    ".pem",
    ".pfx",
    ".pyc",
    ".sqlite",
    ".sqlite3",
    ".zip",
}
EXCLUDED_FILENAMES = {
    ".coverage",
    ".DS_Store",
    ".env",
}


def is_excluded(relative: Path) -> bool:
    """Keep generated caches and inspection sidecars out of the review artifact."""

    if relative.name in EXCLUDED_FILENAMES:
        return True
    if relative.name.endswith((".inspect.json", ".inspect.ndjson")):
        return True
    if any(part in EXCLUDED_PARTS or part.endswith(".egg-info") for part in relative.parts):
        return True
    return relative.suffix in EXCLUDED_SUFFIXES


def included_files() -> Iterable[Path]:
    for name in DEFAULT_FILES:
        path = ROOT / name
        if path.exists() and not path.is_symlink():
            yield path
    for directory in DEFAULT_DIRS:
        root = ROOT / directory
        if not root.exists():
            continue
        for path in sorted(root.rglob("*")):
            if not path.is_file() or path.is_symlink():
                continue
            relative = path.relative_to(ROOT)
            current = ROOT
            if any((current := current / part).is_symlink() for part in relative.parts):
                continue
            if is_excluded(relative):
                continue
            if relative.parts[:2] == ("submission", "dist"):
                continue
            yield path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "submission" / "dist" / "EgoAgentOS_GOAI_Initial.zip",
    )
    args = parser.parse_args()

    verify = subprocess.run([sys.executable, str(ROOT / "scripts" / "verify_submission.py")])
    if verify.returncode:
        return verify.returncode

    output = args.output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    files: List[Path] = sorted(set(included_files()))
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for path in files:
            info = zipfile.ZipInfo(path.relative_to(ROOT).as_posix())
            info.date_time = (2026, 8, 9, 0, 0, 0)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            archive.writestr(info, path.read_bytes())
    print("wrote %s (%d files)" % (output, len(files)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
