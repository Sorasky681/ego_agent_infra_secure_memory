"""Dependency-free statistical summaries with explicit confidence methods."""

from __future__ import annotations

import hashlib
import math
import random
import statistics
from typing import Any, Callable, Dict, Iterable, List, Optional

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


def _clustered_proportion(
    observations: List[Observation],
    selector: Callable[[Observation], Optional[bool]],
    mode: str = "all",
) -> Dict[str, Any]:
    """Collapse repeated seeds within each scenario before estimating a rate."""

    clusters: Dict[str, List[bool]] = {}
    trial_n = 0
    for observation in observations:
        value = selector(observation)
        if value is None:
            continue
        trial_n += 1
        clusters.setdefault(observation.scenario_id, []).append(value)
    if mode == "any":
        collapsed = [any(values) for values in clusters.values()]
    else:
        collapsed = [all(values) for values in clusters.values()]
    result = _proportion(collapsed)
    result.update(
        {
            "trial_n": trial_n,
            "independence_unit": "scenario",
            "repetition_rule": "any" if mode == "any" else "all",
        }
    )
    return result


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
        attempted = [item for item in all_trials if item.status != "skip"]
        measured = [item for item in attempted if item.status != "error"]
        latency = [item.latency_ms for item in attempted]
        mttr = [item.mttr_ms for item in measured if item.mttr_ms is not None]
        costs = [item.external_cost_usd for item in measured if item.external_cost_usd is not None]
        profile_summary: Dict[str, Any] = {
            "trials": len(all_trials),
            "executed": len(attempted),
            "skipped": sum(item.status == "skip" for item in all_trials),
            "errors": sum(item.status == "error" for item in all_trials),
            "failed": sum(item.status == "fail" for item in all_trials),
            "coverage": len(attempted) / len(all_trials) if all_trials else 0.0,
            "scenario_success": _clustered_proportion(
                attempted, lambda item: item.status == "pass"
            ),
            "task_completion": _clustered_proportion(
                measured, lambda item: item.task_completed
            ),
            "unsafe_action_block": _clustered_proportion(
                measured, lambda item: item.unsafe_action_blocked
            ),
            "approval_bypass_success": _clustered_proportion(
                measured, lambda item: item.approval_bypass_succeeded, mode="any"
            ),
            "exactly_once": _clustered_proportion(measured, lambda item: item.exactly_once),
            "recovery": _clustered_proportion(measured, lambda item: item.recovered),
            "reproducibility": _clustered_proportion(
                measured, lambda item: item.reproducible
            ),
            "hash_agreement": _clustered_proportion(
                measured, lambda item: item.hash_agreement
            ),
            "dynamic_routing": _clustered_proportion(
                measured, lambda item: item.dynamically_routed
            ),
            "trace_completeness": _continuous(
                [
                    item.trace_completeness
                    for item in measured
                    if item.trace_completeness is not None
                ],
                "%s:trace" % profile_name,
            ),
            "evidence_completeness": _continuous(
                [
                    item.evidence_completeness
                    for item in measured
                    if item.evidence_completeness is not None
                ],
                "%s:evidence" % profile_name,
            ),
            "latency_ms": _continuous(latency, "%s:latency" % profile_name),
            "mttr_ms": _continuous(mttr, "%s:mttr" % profile_name),
            "operation_count": _continuous(
                [float(item.operation_count) for item in measured],
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
            "proportions": "Wilson score interval after collapsing repetitions by scenario",
            "continuous_means": "fixed-seed nonparametric bootstrap, 2000 resamples",
            "interpretation": (
                "The scenario is the independence unit. Repetitions are collapsed before "
                "binary confidence intervals and measure stability only; they do not establish "
                "external-task generalization."
            ),
        },
        "profiles": profiles,
    }
