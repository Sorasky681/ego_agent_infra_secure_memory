#!/usr/bin/env python3
"""Build canonical dataset manifest JSON on stdout without mutating the dataset."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Dict, List


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def build(root: Path, dataset_id: str, version: str) -> Dict[str, object]:
    trusted = root.resolve(strict=True)
    files: List[Dict[str, object]] = []
    for candidate in sorted(path for path in trusted.rglob("*") if path.is_file()):
        resolved = candidate.resolve(strict=True)
        if trusted not in resolved.parents:
            raise ValueError("E_PATH_ESCAPE")
        files.append(
            {
                "path": resolved.relative_to(trusted).as_posix(),
                "bytes": resolved.stat().st_size,
                "sha256": sha256_file(resolved),
            }
        )
    core: Dict[str, object] = {
        "schema": "egoagentos.dataset-manifest.v1",
        "dataset_id": dataset_id,
        "version": version,
        "files": files,
        "file_count": len(files),
        "total_bytes": sum(int(item["bytes"]) for item in files),
    }
    encoded = json.dumps(core, sort_keys=True, separators=(",", ":")).encode("utf-8")
    core["manifest_sha256"] = hashlib.sha256(encoded).hexdigest()
    return core


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("root", type=Path)
    parser.add_argument("--dataset-id", required=True)
    parser.add_argument("--version", required=True)
    args = parser.parse_args()
    print(json.dumps(build(args.root, args.dataset_id, args.version), sort_keys=True, indent=2))


if __name__ == "__main__":
    main()

