from __future__ import annotations

import asyncio

from mcp import Client

from egoagentos_mcp.dataset_server import mcp as dataset_mcp
from egoagentos_mcp.gpu_server import mcp as gpu_mcp
from egoagentos_mcp.metrics_server import (
    BOOTSTRAP_SEED,
    PairedMetricRequest,
    evaluate_paired_metric,
    metrics_compare_paired,
)
from egoagentos_mcp.metrics_server import (
    mcp as metrics_mcp,
)
from egoagentos_mcp.repo_server import mcp as repo_mcp


def test_metrics_are_deterministic_and_direction_aware() -> None:
    request = PairedMetricRequest(
        metric_name="fps",
        baseline=[8.0, 9.0, 10.0, 11.0],
        candidate=[10.0, 11.0, 12.0, 13.0],
        direction="higher_better",
        min_absolute_improvement=1.0,
    )

    first = evaluate_paired_metric(request)
    second = metrics_compare_paired(request)

    assert first == second
    assert first["result_sha256"] == second["result_sha256"]
    assert first["bootstrap"]["seed"] == BOOTSTRAP_SEED
    assert first["improvement"] == 2.0
    assert first["threshold_verdict"] == "PASS"
    assert first["evidence_verdict"] == "PASS"


def test_lower_is_better_normalises_improvement_positive() -> None:
    result = evaluate_paired_metric(
        PairedMetricRequest(
            metric_name="mpjpe",
            baseline=[12.0, 10.0, 11.0],
            candidate=[10.0, 8.0, 9.0],
            direction="lower_better",
            min_absolute_improvement=1.0,
        )
    )
    assert result["raw_candidate_minus_baseline"] == -2.0
    assert result["improvement"] == 2.0
    assert result["evidence_verdict"] == "PASS"


def test_all_four_mcp_v2_servers_register_tools() -> None:
    async def names(server: object) -> set[str]:
        tools = await server.list_tools()  # type: ignore[attr-defined]
        return {tool.name for tool in tools}

    assert asyncio.run(names(repo_mcp)) == {"repo_snapshot", "repo_read_files"}
    assert asyncio.run(names(dataset_mcp)) == {
        "dataset_create_manifest",
        "dataset_verify_manifest",
    }
    assert asyncio.run(names(gpu_mcp)) == {"gpu_launch_experiment", "gpu_job_status"}
    assert asyncio.run(names(metrics_mcp)) == {"metrics_compare_paired"}


def test_official_v2_client_calls_structured_metric_tool() -> None:
    async def call() -> object:
        async with Client(metrics_mcp) as client:
            result = await client.call_tool(
                "metrics_compare_paired",
                {
                    "request": {
                        "metric_name": "fps",
                        "baseline": [8.0, 9.0],
                        "candidate": [10.0, 11.0],
                        "direction": "higher_better",
                        "min_absolute_improvement": 1.0,
                    }
                },
            )
            assert result.is_error is False
            return result.structured_content

    structured = asyncio.run(call())
    assert isinstance(structured, dict)
    assert structured["schema"] == "egoagentos.paired-metric-comparison.v1"
    assert structured["result_sha256"]
