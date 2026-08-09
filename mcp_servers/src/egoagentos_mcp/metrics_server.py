"""Deterministic paired metric comparison MCP server."""

from __future__ import annotations

import math
import random
from statistics import fmean
from typing import Any, Literal

from mcp.server import MCPServer
from mcp.types import ToolAnnotations
from pydantic import Field, field_validator

from .common import StrictModel, StructuredToolError, canonical_sha256, run_mcp_server

BOOTSTRAP_SEED = 20_260_809
BOOTSTRAP_ITERATIONS = 2_000
CONFIDENCE_LEVEL = 0.95
MAX_SAMPLE_COUNT = 10_000


class PairedMetricRequest(StrictModel):
    metric_name: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,127}$")
    baseline: list[float] = Field(min_length=2, max_length=MAX_SAMPLE_COUNT)
    candidate: list[float] = Field(min_length=2, max_length=MAX_SAMPLE_COUNT)
    direction: Literal["higher_better", "lower_better"]
    min_absolute_improvement: float = 0.0
    data_classification: Literal["synthetic_demo", "public", "internal"] = "synthetic_demo"

    @field_validator("baseline", "candidate")
    @classmethod
    def validate_finite(cls, values: list[float]) -> list[float]:
        if any(not math.isfinite(value) for value in values):
            raise ValueError("metric samples must contain only finite values")
        return values

    @field_validator("min_absolute_improvement")
    @classmethod
    def validate_threshold(cls, value: float) -> float:
        if not math.isfinite(value) or value < 0:
            raise ValueError("min_absolute_improvement must be finite and non-negative")
        return value


def _percentile(sorted_values: list[float], probability: float) -> float:
    position = (len(sorted_values) - 1) * probability
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return sorted_values[lower]
    fraction = position - lower
    return sorted_values[lower] * (1.0 - fraction) + sorted_values[upper] * fraction


def paired_bootstrap_improvements(
    baseline: list[float],
    candidate: list[float],
    *,
    direction: Literal["higher_better", "lower_better"],
) -> list[float]:
    """Fixed-seed paired bootstrap; positive values always mean improvement."""

    if len(baseline) != len(candidate) or len(baseline) < 2:
        raise StructuredToolError(
            "invalid_paired_samples", "Paired samples must have the same length of at least two"
        )
    raw_deltas = [candidate[index] - baseline[index] for index in range(len(baseline))]
    improvements = raw_deltas if direction == "higher_better" else [-value for value in raw_deltas]
    randomizer = random.Random(BOOTSTRAP_SEED)
    sample_count = len(improvements)
    results: list[float] = []
    for _ in range(BOOTSTRAP_ITERATIONS):
        results.append(
            fmean(improvements[randomizer.randrange(sample_count)] for _ in range(sample_count))
        )
    return results


def evaluate_paired_metric(request: PairedMetricRequest) -> dict[str, Any]:
    """Compute one deterministic comparison and a fixed-seed paired bootstrap CI."""

    if len(request.baseline) != len(request.candidate):
        raise StructuredToolError(
            "invalid_paired_samples", "Baseline and candidate must have equal sample counts"
        )
    baseline_mean = fmean(request.baseline)
    candidate_mean = fmean(request.candidate)
    raw_delta = candidate_mean - baseline_mean
    improvement = raw_delta if request.direction == "higher_better" else -raw_delta
    relative_improvement = improvement / abs(baseline_mean) if baseline_mean != 0 else None

    samples = sorted(
        paired_bootstrap_improvements(
            request.baseline, request.candidate, direction=request.direction
        )
    )
    alpha = 1.0 - CONFIDENCE_LEVEL
    ci_low = _percentile(samples, alpha / 2.0)
    ci_high = _percentile(samples, 1.0 - alpha / 2.0)
    threshold_pass = improvement >= request.min_absolute_improvement
    confidence_pass = ci_low >= request.min_absolute_improvement

    def rounded(value: float | None) -> float | None:
        return None if value is None else round(value, 10)

    core = {
        "schema": "egoagentos.paired-metric-comparison.v1",
        "metric_name": request.metric_name,
        "direction": request.direction,
        "sample_count": len(request.baseline),
        "baseline_mean": rounded(baseline_mean),
        "candidate_mean": rounded(candidate_mean),
        "raw_candidate_minus_baseline": rounded(raw_delta),
        "improvement": rounded(improvement),
        "relative_improvement": rounded(relative_improvement),
        "min_absolute_improvement": rounded(request.min_absolute_improvement),
        "ci95_improvement": [rounded(ci_low), rounded(ci_high)],
        "threshold_verdict": "PASS" if threshold_pass else "FAIL",
        "evidence_verdict": "PASS" if confidence_pass else "INCONCLUSIVE_OR_FAIL",
        "bootstrap": {
            "method": "paired_resampling_with_replacement",
            "seed": BOOTSTRAP_SEED,
            "iterations": BOOTSTRAP_ITERATIONS,
            "confidence_level": CONFIDENCE_LEVEL,
        },
        "data_classification": request.data_classification,
        "deterministic": True,
    }
    request_hash = canonical_sha256(request.model_dump(mode="json"))
    result_hash = canonical_sha256({"request_sha256": request_hash, "result": core})
    return {**core, "request_sha256": request_hash, "result_sha256": result_hash}


mcp = MCPServer(
    "egoagentos-metrics",
    version="0.1.0",
    instructions=(
        "Pure deterministic metric computation with paired samples and a fixed bootstrap seed. "
        "No LLM judges numerical acceptance."
    ),
)


@mcp.tool(
    title="Compare paired baseline and candidate metrics",
    annotations=ToolAnnotations(read_only_hint=True, open_world_hint=False),
)
def metrics_compare_paired(request: PairedMetricRequest) -> dict[str, Any]:
    """Return deterministic means, improvement, fixed-seed 95% CI, verdicts, and hashes."""

    return evaluate_paired_metric(request)


def main() -> None:
    run_mcp_server(mcp)


if __name__ == "__main__":
    main()
