"""Deterministic numerical evaluation; an LLM never computes acceptance metrics."""

import math
import random
from statistics import mean
from typing import List, Literal, Sequence

from .models import EvaluationResult


def _validate_samples(baseline: Sequence[float], candidate: Sequence[float]) -> None:
    if not baseline or len(baseline) != len(candidate):
        raise ValueError("paired samples must be non-empty and have equal length")
    if any(not math.isfinite(float(value)) for value in list(baseline) + list(candidate)):
        raise ValueError("samples must contain only finite values")


def _percentile(sorted_values: Sequence[float], probability: float) -> float:
    if not sorted_values:
        raise ValueError("cannot compute a percentile of an empty sequence")
    position = (len(sorted_values) - 1) * probability
    lower = int(math.floor(position))
    upper = int(math.ceil(position))
    if lower == upper:
        return float(sorted_values[lower])
    fraction = position - lower
    return float(sorted_values[lower] * (1.0 - fraction) + sorted_values[upper] * fraction)


def paired_bootstrap_delta(
    baseline: Sequence[float],
    candidate: Sequence[float],
    seed: int = 2025,
    iterations: int = 2000,
) -> List[float]:
    """Bootstrap the paired candidate-minus-baseline mean with a local seeded RNG."""
    _validate_samples(baseline, candidate)
    if iterations < 100:
        raise ValueError("iterations must be at least 100")
    deltas = [float(candidate[index]) - float(baseline[index]) for index in range(len(baseline))]
    randomizer = random.Random(seed)
    bootstrapped: List[float] = []
    for _ in range(iterations):
        bootstrapped.append(mean(deltas[randomizer.randrange(len(deltas))] for _ in deltas))
    return bootstrapped


def evaluate_paired_metric(
    metric: str,
    baseline: Sequence[float],
    candidate: Sequence[float],
    direction: Literal["higher_better", "lower_better"],
    threshold: float,
    seed: int = 2025,
    iterations: int = 2000,
    data_classification: str = "synthetic_demo",
) -> EvaluationResult:
    _validate_samples(baseline, candidate)
    baseline_mean = mean(float(value) for value in baseline)
    candidate_mean = mean(float(value) for value in candidate)
    mean_delta = candidate_mean - baseline_mean
    relative_delta = mean_delta / abs(baseline_mean) if baseline_mean != 0 else 0.0
    samples = sorted(paired_bootstrap_delta(baseline, candidate, seed, iterations))
    ci95 = [_percentile(samples, 0.025), _percentile(samples, 0.975)]

    verdict: Literal["PASS", "FAIL", "INCONCLUSIVE"]
    if direction == "higher_better":
        verdict = "PASS" if candidate_mean >= threshold else "FAIL"
    elif direction == "lower_better":
        verdict = "PASS" if relative_delta <= threshold else "FAIL"
    else:  # defensive even though transport validation also enforces this
        raise ValueError("unsupported metric direction")

    return EvaluationResult(
        metric=metric,
        direction=direction,
        baseline_mean=round(baseline_mean, 6),
        candidate_mean=round(candidate_mean, 6),
        mean_delta=round(mean_delta, 6),
        relative_delta=round(relative_delta, 6),
        ci95=[round(value, 6) for value in ci95],
        threshold=threshold,
        verdict=verdict,
        bootstrap_seed=seed,
        bootstrap_samples=iterations,
        sample_count=len(baseline),
        data_classification=data_classification,
    )
