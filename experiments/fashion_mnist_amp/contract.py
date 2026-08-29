"""Fail-closed contract and deterministic evaluator for the live GPU workload.

This module intentionally has no Torch dependency. Judges can recompute a Decision
from frozen raw JSON on a CPU-only machine. The executor lives in ``run.py`` and is
the only component allowed to claim that CUDA was actually used.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import statistics
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional


CONFIG_SCHEMA = "egoagentos.real-gpu-config/v1"
RAW_SCHEMA = "egoagentos.real-gpu-raw/v1"
DECISION_SCHEMA = "egoagentos.real-gpu-decision/v1"
SHA256_HEX_LENGTH = 64


class ContractError(ValueError):
    """Raised when live evidence is incomplete, contradictory, or tampered."""


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def canonical_sha256(value: Any) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ContractError(message)


def _is_sha256(value: Any) -> bool:
    return (
        isinstance(value, str)
        and len(value) == SHA256_HEX_LENGTH
        and all(character in "0123456789abcdef" for character in value)
    )


def _finite_numbers(values: Any, *, name: str, positive: bool = False) -> List[float]:
    _require(isinstance(values, list) and bool(values), f"{name} must be a non-empty list")
    result: List[float] = []
    for value in values:
        _require(
            isinstance(value, (int, float)) and not isinstance(value, bool),
            f"{name} contains a non-number",
        )
        number = float(value)
        _require(math.isfinite(number), f"{name} contains NaN or infinity")
        if positive:
            _require(number > 0.0, f"{name} must contain only positive values")
        result.append(number)
    return result


def validate_config(config: Mapping[str, Any]) -> Dict[str, Any]:
    """Validate the deliberately narrow public-dataset, one-GPU experiment."""

    _require(config.get("schema") == CONFIG_SCHEMA, "unsupported config schema")
    _require(config.get("workload_id") == "fashion-mnist-amp-v1", "unknown workload")
    dataset = config.get("dataset")
    model = config.get("model")
    comparison = config.get("comparison")
    budget = config.get("budget")
    determinism = config.get("determinism")
    _require(isinstance(dataset, dict), "dataset config is required")
    _require(isinstance(model, dict), "model config is required")
    _require(isinstance(comparison, dict), "comparison config is required")
    _require(isinstance(budget, dict), "budget config is required")
    _require(isinstance(determinism, dict), "determinism config is required")

    _require(dataset.get("name") == "FashionMNIST", "only FashionMNIST is allowlisted")
    _require(dataset.get("synthetic") is False, "live workload must declare synthetic=false")
    _require(dataset.get("license") == "MIT", "dataset license must be frozen as MIT")
    _require(
        isinstance(dataset.get("source"), str)
        and dataset["source"].startswith("https://github.com/zalandoresearch/fashion-mnist"),
        "dataset source is not the allowlisted upstream",
    )
    _require(isinstance(dataset.get("download"), bool), "dataset.download must be boolean")
    for field, upper in (("train_samples", 60_000), ("eval_samples", 10_000)):
        value = dataset.get(field)
        _require(
            isinstance(value, int) and not isinstance(value, bool) and 32 <= value <= upper,
            f"dataset.{field} is outside the bounded range",
        )

    _require(model.get("architecture") == "tiny-cnn-v1", "unknown model architecture")
    _require(model.get("classes") == 10, "FashionMNIST must use ten classes")
    _require(
        isinstance(model.get("epochs"), int) and 1 <= model["epochs"] <= 5,
        "model.epochs must be in [1, 5]",
    )
    for field in ("train_batch_size", "eval_batch_size"):
        value = model.get(field)
        _require(
            isinstance(value, int) and not isinstance(value, bool) and 32 <= value <= 2048,
            f"model.{field} is outside the bounded range",
        )
    learning_rate = model.get("learning_rate")
    _require(
        isinstance(learning_rate, (int, float))
        and not isinstance(learning_rate, bool)
        and 0.0 < float(learning_rate) <= 0.1,
        "model.learning_rate is outside the bounded range",
    )

    _require(comparison.get("baseline") == "cuda-fp32", "baseline must be cuda-fp32")
    _require(
        comparison.get("candidate") == "cuda-amp-fp16",
        "candidate must be cuda-amp-fp16",
    )
    for field, low, high in (
        ("warmup_repetitions", 1, 20),
        ("latency_repetitions", 5, 100),
    ):
        value = comparison.get(field)
        _require(
            isinstance(value, int) and not isinstance(value, bool) and low <= value <= high,
            f"comparison.{field} is outside the bounded range",
        )
    for field, low, high in (
        ("max_accuracy_degradation", 0.0, 0.05),
        ("min_latency_speedup", -0.25, 0.75),
    ):
        value = comparison.get(field)
        _require(
            isinstance(value, (int, float))
            and not isinstance(value, bool)
            and low <= float(value) <= high,
            f"comparison.{field} is outside the bounded range",
        )

    _require(budget.get("gpu_count") == 1, "exactly one GPU is permitted")
    duration = budget.get("max_duration_seconds")
    gpu_hours = budget.get("max_gpu_hours")
    download_bytes = budget.get("max_download_bytes")
    _require(
        isinstance(duration, int) and not isinstance(duration, bool) and 1 <= duration <= 900,
        "budget.max_duration_seconds must be in [1, 900]",
    )
    _require(
        isinstance(gpu_hours, (int, float))
        and not isinstance(gpu_hours, bool)
        and 0.0 < float(gpu_hours) <= 0.25,
        "budget.max_gpu_hours must be in (0, 0.25]",
    )
    _require(
        isinstance(download_bytes, int)
        and not isinstance(download_bytes, bool)
        and 1 <= download_bytes <= 100 * 1024 * 1024,
        "budget.max_download_bytes must be in (0, 100 MiB]",
    )
    _require(
        determinism
        == {
            "cublas_workspace_config": ":4096:8",
            "deterministic_algorithms": True,
            "cudnn_benchmark": False,
            "tf32": False,
        },
        "determinism settings must match the v1 contract",
    )
    seed = config.get("seed")
    _require(
        isinstance(seed, int) and not isinstance(seed, bool) and 0 <= seed <= 2**31 - 1,
        "seed is outside the bounded range",
    )
    return dict(config)


def load_and_validate_config(path: str | Path) -> Dict[str, Any]:
    config_path = Path(path)
    try:
        payload = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ContractError(f"cannot read config: {type(error).__name__}") from error
    _require(isinstance(payload, dict), "config root must be an object")
    return validate_config(payload)


def _classification_rows(raw: Mapping[str, Any], expected_count: int) -> List[Mapping[str, Any]]:
    rows = raw.get("samples")
    _require(isinstance(rows, list), "samples must be a list")
    _require(len(rows) == expected_count, "sample count does not match frozen config")
    seen: set[int] = set()
    normalized: List[Mapping[str, Any]] = []
    for row in rows:
        _require(isinstance(row, dict), "each sample must be an object")
        _require(set(row) == {"sample_id", "target", "baseline_pred", "candidate_pred"},
                 "sample row fields must match the v1 raw contract")
        sample_id = row.get("sample_id")
        target = row.get("target")
        baseline = row.get("baseline_pred")
        candidate = row.get("candidate_pred")
        _require(
            isinstance(sample_id, int) and not isinstance(sample_id, bool) and sample_id >= 0,
            "sample_id must be a non-negative integer",
        )
        _require(sample_id not in seen, "duplicate sample_id")
        seen.add(sample_id)
        for value in (target, baseline, candidate):
            _require(
                isinstance(value, int)
                and not isinstance(value, bool)
                and 0 <= value < 10,
                "class ids must be integers in [0, 9]",
            )
        normalized.append(row)
    _require([int(row["sample_id"]) for row in normalized] == sorted(seen),
             "samples must be ordered by sample_id")
    return normalized


def _digest_projection(raw: Mapping[str, Any]) -> Dict[str, Any]:
    return {key: value for key, value in raw.items() if key != "raw_sha256"}


def evaluate_raw_result(
    raw: Mapping[str, Any], *, expected_config_sha256: Optional[str] = None
) -> Dict[str, Any]:
    """Recompute the terminal Decision from raw evidence and reject unsafe claims."""

    _require(raw.get("schema") == RAW_SCHEMA, "unsupported raw-result schema")
    _require(raw.get("execution_mode") == "real_cuda", "execution_mode is not real_cuda")
    _require(raw.get("synthetic") is False, "synthetic evidence cannot pass the live gate")
    _require(raw.get("physical_launch_count") == 1, "exactly one physical launch is required")
    _require(raw.get("cpu_fallback_used") is False, "CPU fallback invalidates live evidence")

    config = raw.get("config")
    _require(isinstance(config, dict), "frozen config is missing")
    config = validate_config(config)
    config_sha256 = raw.get("config_sha256")
    _require(_is_sha256(config_sha256), "config_sha256 is invalid")
    _require(config_sha256 == canonical_sha256(config), "config digest mismatch")
    if expected_config_sha256 is not None:
        _require(config_sha256 == expected_config_sha256, "unexpected config digest")

    for digest_name in (
        "dataset_manifest_sha256",
        "config_file_sha256",
        "git_commit_sha256",
        "approval_receipt_sha256",
        "agentteams_receipt_sha256",
        "matrix_plan_sha256",
        "environment_lock_sha256",
        "trained_model_sha256",
    ):
        _require(_is_sha256(raw.get(digest_name)), f"{digest_name} is invalid")
    git_commit = raw.get("git_commit")
    _require(
        isinstance(git_commit, str)
        and len(git_commit) in (40, 64)
        and all(character in "0123456789abcdef" for character in git_commit),
        "git_commit is invalid",
    )
    _require(
        raw["git_commit_sha256"] == hashlib.sha256(git_commit.encode("ascii")).hexdigest(),
        "git commit digest mismatch",
    )
    for identifier_name in ("run_id", "physical_launch_id"):
        identifier = raw.get(identifier_name)
        _require(
            isinstance(identifier, str)
            and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{7,127}", identifier) is not None,
            f"{identifier_name} is invalid",
        )

    device = raw.get("device")
    _require(isinstance(device, dict), "device evidence is missing")
    _require(device.get("type") == "cuda", "device type is not cuda")
    _require(device.get("cuda_available") is True, "CUDA was not available")
    _require(device.get("visible_device_count") == 1, "exactly one CUDA device must be visible")
    _require(isinstance(device.get("name"), str) and bool(device["name"]), "GPU name is missing")
    determinism = raw.get("determinism")
    _require(isinstance(determinism, dict), "runtime determinism evidence is missing")
    _require(
        determinism
        == {
            "cublas_workspace_config": ":4096:8",
            "deterministic_algorithms": True,
            "cudnn_benchmark": False,
            "tf32": False,
        },
        "runtime determinism settings do not match the frozen contract",
    )

    duration = raw.get("duration_seconds")
    _require(
        isinstance(duration, (int, float))
        and not isinstance(duration, bool)
        and math.isfinite(float(duration))
        and float(duration) > 0.0,
        "duration_seconds must be a positive finite number",
    )
    max_duration = float(config["budget"]["max_duration_seconds"])
    max_gpu_hours = float(config["budget"]["max_gpu_hours"])
    _require(float(duration) <= max_duration, "wall-time budget exceeded")
    observed_gpu_hours = float(duration) / 3600.0
    _require(observed_gpu_hours <= max_gpu_hours, "GPU-hour budget exceeded")

    rows = _classification_rows(raw, int(config["dataset"]["eval_samples"]))
    latency = raw.get("latency_ms")
    _require(isinstance(latency, dict), "latency_ms is missing")
    baseline_latency = _finite_numbers(latency.get("baseline"), name="baseline latency", positive=True)
    candidate_latency = _finite_numbers(
        latency.get("candidate"), name="candidate latency", positive=True
    )
    expected_repetitions = int(config["comparison"]["latency_repetitions"])
    _require(len(baseline_latency) == expected_repetitions, "baseline latency count mismatch")
    _require(len(candidate_latency) == expected_repetitions, "candidate latency count mismatch")

    max_memory = raw.get("max_memory_bytes")
    _require(isinstance(max_memory, dict), "max_memory_bytes is missing")
    for key in ("baseline", "candidate"):
        value = max_memory.get(key)
        _require(
            isinstance(value, int) and not isinstance(value, bool) and value > 0,
            f"max_memory_bytes.{key} must be positive",
        )

    baseline_correct = sum(row["baseline_pred"] == row["target"] for row in rows)
    candidate_correct = sum(row["candidate_pred"] == row["target"] for row in rows)
    agreements = sum(row["candidate_pred"] == row["baseline_pred"] for row in rows)
    count = len(rows)
    baseline_accuracy = baseline_correct / count
    candidate_accuracy = candidate_correct / count
    agreement = agreements / count
    baseline_median = statistics.median(baseline_latency)
    candidate_median = statistics.median(candidate_latency)
    latency_speedup = (baseline_median - candidate_median) / baseline_median
    accuracy_degradation = baseline_accuracy - candidate_accuracy

    max_accuracy_degradation = float(config["comparison"]["max_accuracy_degradation"])
    min_latency_speedup = float(config["comparison"]["min_latency_speedup"])
    accuracy_pass = accuracy_degradation <= max_accuracy_degradation
    latency_pass = latency_speedup >= min_latency_speedup
    decision = "KEEP" if accuracy_pass and latency_pass else "REJECT"

    supplied_digest = raw.get("raw_sha256")
    _require(_is_sha256(supplied_digest), "raw_sha256 is invalid")
    recomputed_digest = canonical_sha256(_digest_projection(raw))
    _require(supplied_digest == recomputed_digest, "raw evidence digest mismatch")

    return {
        "schema": DECISION_SCHEMA,
        "decision": decision,
        "gate_status": "PASS",
        "execution_mode": "real_cuda",
        "synthetic": False,
        "sample_count": count,
        "baseline_accuracy": baseline_accuracy,
        "candidate_accuracy": candidate_accuracy,
        "accuracy_degradation": accuracy_degradation,
        "prediction_agreement": agreement,
        "baseline_latency_median_ms": baseline_median,
        "candidate_latency_median_ms": candidate_median,
        "latency_speedup": latency_speedup,
        "accuracy_rule_pass": accuracy_pass,
        "latency_rule_pass": latency_pass,
        "observed_gpu_hours": observed_gpu_hours,
        "raw_sha256": supplied_digest,
        "config_sha256": config_sha256,
    }


def add_raw_digest(raw_without_digest: Mapping[str, Any]) -> Dict[str, Any]:
    """Return a copy with the canonical raw evidence digest attached."""

    _require("raw_sha256" not in raw_without_digest, "raw_sha256 is already present")
    payload = dict(raw_without_digest)
    payload["raw_sha256"] = canonical_sha256(payload)
    return payload


def file_manifest(root: Path, files: Iterable[Path]) -> Dict[str, Any]:
    """Create a deterministic relative-path manifest for produced artifact files."""

    entries = []
    resolved_root = root.resolve()
    for path in sorted(files, key=lambda item: item.as_posix()):
        resolved = path.resolve()
        _require(resolved.is_relative_to(resolved_root), "artifact escaped output root")
        data = resolved.read_bytes()
        entries.append(
            {
                "path": resolved.relative_to(resolved_root).as_posix(),
                "size": len(data),
                "sha256": hashlib.sha256(data).hexdigest(),
            }
        )
    return {"schema": "egoagentos.real-gpu-artifacts/v1", "files": entries}
