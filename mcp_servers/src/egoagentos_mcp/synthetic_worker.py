"""Harmless local worker used only to prove bounded argv-based execution."""

from __future__ import annotations

import argparse
import hmac
import json
from pathlib import Path

from .common import file_sha256


def main() -> None:
    parser = argparse.ArgumentParser(description="EgoAgentOS synthetic local experiment worker")
    parser.add_argument("--mode", choices=("train", "evaluate", "benchmark"), required=True)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--expected-config-sha256", required=True)
    parser.add_argument("--seed", type=int, required=True)
    parser.add_argument("--gpu-id", action="append", type=int, required=True)
    parser.add_argument("--tag", action="append", default=[])
    arguments = parser.parse_args()
    config_digest = file_sha256(arguments.config)
    if not hmac.compare_digest(config_digest, arguments.expected_config_sha256):
        raise SystemExit("config digest mismatch; refusing synthetic execution")
    # Output is intentionally synthetic. GPUService redirects it away from the MCP
    # stdio wire; invoking this module directly is useful for local verification.
    print(
        json.dumps(
            {
                "schema": "egoagentos.synthetic-worker-result.v1",
                "synthetic": True,
                "mode": arguments.mode,
                "seed": arguments.seed,
                "gpu_ids_requested_but_not_used": arguments.gpu_id,
                "tags": arguments.tag,
                "config_sha256": config_digest,
            },
            sort_keys=True,
            separators=(",", ":"),
        ),
        flush=True,
    )


if __name__ == "__main__":
    main()
