"""Execute the one-GPU Fashion-MNIST FP32 versus AMP acceptance workload.

Torch and torchvision are imported lazily so the contract verifier remains usable in
the repository's CPU-only development environment. This executable refuses CPU
fallback and refuses to run when more than one CUDA device is visible.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Sequence, Tuple

from .contract import (
    ContractError,
    RAW_SCHEMA,
    add_raw_digest,
    canonical_bytes,
    canonical_sha256,
    evaluate_raw_result,
    file_manifest,
    load_and_validate_config,
)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _validate_sha256(value: str, name: str) -> str:
    if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
        raise ContractError(f"{name} must be a lowercase SHA-256 digest")
    return value


def _prepare_output(path: Path) -> Path:
    resolved = path.resolve()
    if resolved.exists() and any(resolved.iterdir()):
        raise ContractError("output directory must be new or empty")
    resolved.mkdir(parents=True, exist_ok=True)
    return resolved


def _tree_manifest(root: Path) -> Dict[str, Any]:
    resolved_root = root.resolve()
    if not resolved_root.exists() or not resolved_root.is_dir():
        raise ContractError("dataset root does not exist after dataset initialization")
    entries: List[Dict[str, Any]] = []
    total_bytes = 0
    for path in sorted(resolved_root.rglob("*"), key=lambda item: item.as_posix()):
        if path.is_symlink():
            raise ContractError("dataset tree contains a symlink")
        if not path.is_file():
            continue
        size = path.stat().st_size
        total_bytes += size
        entries.append(
            {
                "path": path.relative_to(resolved_root).as_posix(),
                "size": size,
                "sha256": _sha256_file(path),
            }
        )
    if not entries:
        raise ContractError("dataset tree contains no files")
    return {
        "schema": "egoagentos.dataset-tree/v1",
        "dataset": "FashionMNIST",
        "synthetic": False,
        "files": entries,
        "total_bytes": total_bytes,
    }


def _deadline_guard(started: float, max_seconds: int) -> None:
    if time.monotonic() - started > max_seconds:
        raise ContractError("wall-time budget exceeded during execution")


def _trace_event(
    events: List[Dict[str, Any]], event_type: str, payload: Dict[str, Any]
) -> None:
    previous_hash = events[-1]["event_hash"] if events else "0" * 64
    body = {
        "sequence": len(events) + 1,
        "event_type": event_type,
        "payload": payload,
        "previous_hash": previous_hash,
    }
    body["event_hash"] = canonical_sha256(body)
    events.append(body)


def _run_torch_workload(
    config: Dict[str, Any], data_root: Path, started: float
) -> Tuple[Dict[str, Any], Dict[str, Any], List[Dict[str, Any]]]:
    visible = os.environ.get("CUDA_VISIBLE_DEVICES", "")
    visible_ids = [item.strip() for item in visible.split(",") if item.strip()]
    if len(visible_ids) != 1 or visible_ids[0] == "-1":
        raise ContractError("CUDA_VISIBLE_DEVICES must expose exactly one GPU")
    os.environ["CUBLAS_WORKSPACE_CONFIG"] = str(
        config["determinism"]["cublas_workspace_config"]
    )

    try:
        import torch
        from torch import nn
        from torch.utils.data import DataLoader, Subset
        from torchvision import datasets, transforms
    except ImportError as error:
        raise ContractError("torch and torchvision are required on the live worker") from error

    if not torch.cuda.is_available():
        raise ContractError("CUDA is unavailable; CPU fallback is forbidden")
    if torch.cuda.device_count() != 1:
        raise ContractError("Torch must observe exactly one CUDA device")

    seed = int(config["seed"])
    random.seed(seed)
    torch.manual_seed(seed)
    torch.cuda.manual_seed_all(seed)
    torch.use_deterministic_algorithms(True)
    torch.backends.cudnn.benchmark = False
    torch.backends.cudnn.deterministic = True
    torch.backends.cuda.matmul.allow_tf32 = False
    torch.backends.cudnn.allow_tf32 = False

    class TinyCNN(nn.Module):
        def __init__(self) -> None:
            super().__init__()
            self.features = nn.Sequential(
                nn.Conv2d(1, 32, kernel_size=3, padding=1),
                nn.ReLU(),
                nn.MaxPool2d(2),
                nn.Conv2d(32, 64, kernel_size=3, padding=1),
                nn.ReLU(),
                nn.MaxPool2d(2),
            )
            self.classifier = nn.Sequential(
                nn.Flatten(),
                nn.Linear(64 * 7 * 7, 128),
                nn.ReLU(),
                nn.Linear(128, 10),
            )

        def forward(self, inputs: Any) -> Any:
            return self.classifier(self.features(inputs))

    transform = transforms.Compose(
        [transforms.ToTensor(), transforms.Normalize((0.2860,), (0.3530,))]
    )
    download = bool(config["dataset"]["download"])
    train_data = datasets.FashionMNIST(
        root=str(data_root), train=True, transform=transform, download=download
    )
    eval_data = datasets.FashionMNIST(
        root=str(data_root), train=False, transform=transform, download=download
    )
    train_count = int(config["dataset"]["train_samples"])
    eval_count = int(config["dataset"]["eval_samples"])
    train_subset = Subset(train_data, list(range(train_count)))
    eval_subset = Subset(eval_data, list(range(eval_count)))
    generator = torch.Generator().manual_seed(seed)
    train_loader = DataLoader(
        train_subset,
        batch_size=int(config["model"]["train_batch_size"]),
        shuffle=True,
        generator=generator,
        num_workers=0,
        drop_last=False,
    )
    eval_loader = DataLoader(
        eval_subset,
        batch_size=int(config["model"]["eval_batch_size"]),
        shuffle=False,
        num_workers=0,
        drop_last=False,
    )

    device = torch.device("cuda:0")
    model = TinyCNN().to(device)
    optimizer = torch.optim.Adam(model.parameters(), lr=float(config["model"]["learning_rate"]))
    loss_function = nn.CrossEntropyLoss()
    events: List[Dict[str, Any]] = []
    _trace_event(events, "gpu.environment.verified", {"visible_device_count": 1})

    model.train()
    for epoch in range(int(config["model"]["epochs"])):
        for images, targets in train_loader:
            _deadline_guard(started, int(config["budget"]["max_duration_seconds"]))
            images = images.to(device, non_blocking=False)
            targets = targets.to(device, non_blocking=False)
            optimizer.zero_grad(set_to_none=True)
            logits = model(images)
            loss = loss_function(logits, targets)
            loss.backward()
            optimizer.step()
        _trace_event(events, "training.epoch.completed", {"epoch": epoch + 1})

    model.eval()
    samples: List[Dict[str, int]] = []
    sample_offset = 0
    with torch.inference_mode():
        for images, targets in eval_loader:
            _deadline_guard(started, int(config["budget"]["max_duration_seconds"]))
            images = images.to(device, non_blocking=False)
            baseline_predictions = model(images).argmax(dim=1).cpu().tolist()
            with torch.autocast(device_type="cuda", dtype=torch.float16):
                candidate_predictions = model(images).argmax(dim=1).cpu().tolist()
            target_values = targets.tolist()
            for index, target in enumerate(target_values):
                samples.append(
                    {
                        "sample_id": sample_offset + index,
                        "target": int(target),
                        "baseline_pred": int(baseline_predictions[index]),
                        "candidate_pred": int(candidate_predictions[index]),
                    }
                )
            sample_offset += len(target_values)
    _trace_event(events, "predictions.frozen", {"sample_count": len(samples)})

    first_images, _ = next(iter(eval_loader))
    benchmark_images = first_images.to(device, non_blocking=False)
    warmups = int(config["comparison"]["warmup_repetitions"])
    repetitions = int(config["comparison"]["latency_repetitions"])

    def measure(*, amp: bool) -> Tuple[List[float], int]:
        with torch.inference_mode():
            for _ in range(warmups):
                with torch.autocast(device_type="cuda", dtype=torch.float16, enabled=amp):
                    model(benchmark_images)
            torch.cuda.synchronize()
            torch.cuda.reset_peak_memory_stats(device)
            values: List[float] = []
            for _ in range(repetitions):
                _deadline_guard(started, int(config["budget"]["max_duration_seconds"]))
                begin = torch.cuda.Event(enable_timing=True)
                end = torch.cuda.Event(enable_timing=True)
                begin.record()
                with torch.autocast(device_type="cuda", dtype=torch.float16, enabled=amp):
                    model(benchmark_images)
                end.record()
                torch.cuda.synchronize()
                values.append(float(begin.elapsed_time(end)))
            return values, int(torch.cuda.max_memory_allocated(device))

    baseline_latency, baseline_memory = measure(amp=False)
    candidate_latency, candidate_memory = measure(amp=True)
    _trace_event(events, "latency.raw.frozen", {"repetitions": repetitions})

    device_evidence = {
        "type": "cuda",
        "cuda_available": True,
        "visible_device_count": 1,
        "visible_device_binding": visible_ids[0],
        "name": str(torch.cuda.get_device_name(0)),
        "capability": list(torch.cuda.get_device_capability(0)),
        "cuda_runtime": str(torch.version.cuda),
        "torch_version": str(torch.__version__),
        "cudnn_version": int(torch.backends.cudnn.version() or 0),
    }
    metrics = {
        "samples": samples,
        "latency_ms": {"baseline": baseline_latency, "candidate": candidate_latency},
        "max_memory_bytes": {"baseline": baseline_memory, "candidate": candidate_memory},
    }
    return device_evidence, metrics, events


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True)
    parser.add_argument("--data-root", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--git-commit", required=True)
    parser.add_argument("--approval-receipt-sha256", required=True)
    parser.add_argument("--agentteams-receipt-sha256", required=True)
    parser.add_argument("--matrix-plan-sha256", required=True)
    parser.add_argument(
        "--allow-download",
        action="store_true",
        help="Required in addition to dataset.download=true before any dataset download is allowed.",
    )
    return parser


def execute(arguments: argparse.Namespace) -> Dict[str, Any]:
    config_path = Path(arguments.config).resolve()
    config = load_and_validate_config(config_path)
    if bool(config["dataset"]["download"]) != bool(arguments.allow_download):
        raise ContractError(
            "config dataset.download and explicit --allow-download must agree"
        )
    for field in (
        "approval_receipt_sha256",
        "agentteams_receipt_sha256",
        "matrix_plan_sha256",
    ):
        _validate_sha256(str(getattr(arguments, field)), field)
    git_commit = str(arguments.git_commit).lower()
    if len(git_commit) not in (40, 64) or any(
        character not in "0123456789abcdef" for character in git_commit
    ):
        raise ContractError("git_commit must be a 40- or 64-character hexadecimal object id")

    output_root = _prepare_output(Path(arguments.output_dir))
    data_root = Path(arguments.data_root).resolve()
    started = time.monotonic()
    device, metrics, events = _run_torch_workload(config, data_root, started)
    duration_seconds = time.monotonic() - started
    dataset_manifest = _tree_manifest(data_root)
    if int(dataset_manifest["total_bytes"]) > int(config["budget"]["max_download_bytes"]):
        raise ContractError("dataset tree exceeds frozen byte budget")
    dataset_manifest_path = output_root / "dataset-manifest.json"
    dataset_manifest_path.write_bytes(canonical_bytes(dataset_manifest) + b"\n")
    dataset_manifest_sha256 = canonical_sha256(dataset_manifest)

    raw_without_digest: Dict[str, Any] = {
        "schema": RAW_SCHEMA,
        "execution_mode": "real_cuda",
        "synthetic": False,
        "physical_launch_count": 1,
        "cpu_fallback_used": False,
        "workload_id": config["workload_id"],
        "config": config,
        "config_sha256": canonical_sha256(config),
        "config_file_sha256": _sha256_file(config_path),
        "dataset_manifest_sha256": dataset_manifest_sha256,
        "git_commit": git_commit,
        "git_commit_sha256": hashlib.sha256(git_commit.encode("ascii")).hexdigest(),
        "approval_receipt_sha256": arguments.approval_receipt_sha256,
        "agentteams_receipt_sha256": arguments.agentteams_receipt_sha256,
        "matrix_plan_sha256": arguments.matrix_plan_sha256,
        "device": device,
        "determinism": {
            "cublas_workspace_config": os.environ["CUBLAS_WORKSPACE_CONFIG"],
            "deterministic_algorithms": True,
            "cudnn_benchmark": False,
            "tf32": False,
        },
        "duration_seconds": duration_seconds,
        **metrics,
    }
    raw = add_raw_digest(raw_without_digest)
    decision = evaluate_raw_result(raw)
    _trace_event(
        events,
        "decision.recomputed",
        {"decision": decision["decision"], "raw_sha256": raw["raw_sha256"]},
    )

    raw_path = output_root / "raw-metrics.json"
    decision_path = output_root / "decision.json"
    trace_path = output_root / "trace.jsonl"
    raw_path.write_bytes(canonical_bytes(raw) + b"\n")
    decision_path.write_bytes(canonical_bytes(decision) + b"\n")
    trace_path.write_text(
        "".join(json.dumps(event, sort_keys=True, separators=(",", ":")) + "\n" for event in events),
        encoding="utf-8",
    )
    manifest = file_manifest(
        output_root, [dataset_manifest_path, raw_path, decision_path, trace_path]
    )
    manifest["artifact_root"] = canonical_sha256(manifest["files"])
    manifest_path = output_root / "artifact-manifest.json"
    manifest_path.write_bytes(canonical_bytes(manifest) + b"\n")
    return {
        "ok": True,
        "decision": decision["decision"],
        "output_dir": str(output_root),
        "raw_sha256": raw["raw_sha256"],
        "artifact_root": manifest["artifact_root"],
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    arguments = parser.parse_args(argv)
    try:
        result = execute(arguments)
    except ContractError as error:
        print(
            json.dumps(
                {"ok": False, "error": {"code": "contract_rejected", "message": str(error)}},
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 2
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
