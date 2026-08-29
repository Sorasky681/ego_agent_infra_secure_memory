"""Dependency-free statistical summaries with explicit confidence methods."""

from __future__ import annotations

import hashlib
import math
import random
import statistics
from typing import Any, Dict, Iterable, List, Optional

from benchmarks.model import Observation


def _quantile(values: List[float], probability: float) -> Optional[float]:
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * probability
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    weight = position - lower
    return ordered[lower] * (1.0 - weight) + ordered[upper] * weight


def _wilson(successes: int, count: int, z: float = 1.959963984540054) -> Optional[List[float]]:
    if count == 0:
        return None
    proportion = successes / count
    denominator = 1.0 + z * z / count
    center = (proportion + z * z / (2.0 * count)) / denominator
    margin = (
        z
        * math.sqrt(proportion * (1.0 - proportion) / count + z * z / (4.0 * count * count))
        / denominator
    )
    return [max(0.0, center - margin), min(1.0, center + margin)]


def _proportion(values: Iterable[Optional[bool]]) -> Dict[str, Any]:
    present = [value for value in values if value is not None]
    successes = sum(value is True for value in present)
    return {
        "value": successes / len(present) if present else None,
        "successes": successes,
        "n": len(present),
        "ci95": _wilson(successes, len(present)),
        "confidence_method": "Wilson score interval, z=1.959963984540054",
    }


def _bootstrap_mean_ci(values: List[float], seed_material: str, samples: int = 2000) -> Any:
    if not values:
        return None
    if len(values) == 1:
        return [values[0], values[0]]
    seed = int(hashlib.sha256(seed_material.encode("utf-8")).hexdigest()[:16], 16)
    rng = random.Random(seed)
    means = [
        statistics.fmean(rng.choice(values) for _ in values)
        for _ in range(samples)
    ]
    return [_quantile(means, 0.025), _quantile(means, 0.975)]


def _continuous(values: List[float], seed_material: str) -> Dict[str, Any]:
    return {
        "mean": statistics.fmean(values) if values else None,
        "median": statistics.median(values) if values else None,
        "p95": _quantile(values, 0.95),
        "min": min(values) if values else None,
        "max": max(values) if values else None,
        "n": len(values),
        "mean_ci95": _bootstrap_mean_ci(values, seed_material),
        "confidence_method": "fixed-seed nonparametric bootstrap of the mean, 2000 resamples",
    }


def summarize(observations: List[Observation]) -> Dict[str, Any]:
    profiles: Dict[str, Any] = {}
    for profile_name in sorted({item.profile for item in observations}):
        all_trials = [item for item in observations if item.profile == profile_name]
        executed = [item for item in all_trials if item.status not in {"skip", "error"}]
        latency = [item.latency_ms for item in executed]
        mttr = [item.mttr_ms for item in executed if item.mttr_ms is not None]
        costs = [item.external_cost_usd for item in executed if item.external_cost_usd is not None]
        profile_summary: Dict[str, Any] = {
            "trials": len(all_trials),
            "executed": len(executed),
            "skipped": sum(item.status == "skip" for item in all_trials),
            "errors": sum(item.status == "error" for item in all_trials),
            "coverage": len(executed) / len(all_trials) if all_trials else 0.0,
            "scenario_success": _proportion(
                item.status == "pass" for item in executed
            ),
            "task_completion": _proportion(item.task_completed for item in executed),
            "unsafe_action_block": _proportion(
                item.unsafe_action_blocked for item in executed
            ),
            "approval_bypass_success": _proportion(
                item.approval_bypass_succeeded for item in executed
            ),
            "exactly_once": _proportion(item.exactly_once for item in executed),
            "recovery": _proportion(item.recovered for item in executed),
            "reproducibility": _proportion(item.reproducible for item in executed),
            "hash_agreement": _proportion(item.hash_agreement for item in executed),
            "dynamic_routing": _proportion(item.dynamically_routed for item in executed),
            "trace_completeness": _continuous(
                [
                    item.trace_completeness
                    for item in executed
                    if item.trace_completeness is not None
                ],
                "%s:trace" % profile_name,
            ),
            "evidence_completeness": _continuous(
                [
                    item.evidence_completeness
                    for item in executed
                    if item.evidence_completeness is not None
                ],
                "%s:evidence" % profile_name,
            ),
            "latency_ms": _continuous(latency, "%s:latency" % profile_name),
            "mttr_ms": _continuous(mttr, "%s:mttr" % profile_name),
            "operation_count": _continuous(
                [float(item.operation_count) for item in executed],
                "%s:operations" % profile_name,
            ),
            "external_cost_usd": (
                {
                    "status": "measured",
                    "total": sum(costs),
                    "per_trial": _continuous(costs, "%s:cost" % profile_name),
                }
                if costs
                else {
                    "status": "not_measured",
                    "total": None,
                    "reason": "No external billing meter was attached; local CPU cost is not estimated.",
                }
            ),
        }
        scenario_status: Dict[str, Dict[str, int]] = {}
        for item in all_trials:
            counts = scenario_status.setdefault(
                item.scenario_id, {"pass": 0, "fail": 0, "skip": 0, "error": 0}
            )
            counts[item.status] += 1
        profile_summary["scenario_status"] = scenario_status
        profiles[profile_name] = profile_summary
    return {
        "confidence": {
            "level": 0.95,
            "proportions": "Wilson score interval",
            "continuous_means": "fixed-seed nonparametric bootstrap, 2000 resamples",
            "interpretation": (
                "Repeated trials measure implementation and local runtime stability for this "
                "versioned synthetic corpus; they do not establish external-task generalization."
            ),
        },
        "profiles": profiles,
    }
