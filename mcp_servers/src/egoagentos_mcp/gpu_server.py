"""Bounded synthetic experiment launcher MCP server.

There is intentionally no generic shell, command, executable, environment, or
working-directory field in the request schema. Local execution is disabled by
default and the only executable target is this package's synthetic worker.
"""

from __future__ import annotations

import os
import subprocess
import sys
import threading
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

from mcp.server import MCPServer
from mcp.types import ToolAnnotations
from pydantic import Field, field_validator

from .approval import FileReplayStore, HMACApprovalManager
from .common import (
    StrictModel,
    StructuredToolError,
    TrustedRoot,
    canonical_sha256,
    file_sha256,
    redact_text,
    run_mcp_server,
)

Entrypoint = Literal["train_pose", "eval_pose", "benchmark_stream"]
ENTRYPOINT_MODES: dict[str, str] = {
    "train_pose": "train",
    "eval_pose": "evaluate",
    "benchmark_stream": "benchmark",
}
ALLOWED_CONFIG_SUFFIXES = {".json", ".toml", ".yaml", ".yml"}
APPROVAL_ACTION = "gpu.launch_experiment"


class LaunchRequest(StrictModel):
    experiment_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
    idempotency_key: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{7,127}$")
    entrypoint: Entrypoint
    config_path: str = Field(min_length=1, max_length=1_024)
    config_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    gpu_ids: list[int] = Field(min_length=1, max_length=8)
    seed: int = Field(ge=0, le=2**31 - 1)
    expected_gpu_hours: float = Field(gt=0.0, le=10_000.0)
    tags: list[str] = Field(default_factory=list, max_length=16)
    dry_run: bool = True
    approval_token: str | None = Field(default=None, repr=False, max_length=4_096)

    @field_validator("gpu_ids")
    @classmethod
    def validate_gpu_ids(cls, values: list[int]) -> list[int]:
        if len(set(values)) != len(values):
            raise ValueError("gpu_ids must be unique")
        if any(value < 0 or value > 63 for value in values):
            raise ValueError("gpu_ids must be between 0 and 63")
        return values

    @field_validator("tags")
    @classmethod
    def validate_tags(cls, values: list[str]) -> list[str]:
        if any(not value or len(value) > 128 or "\x00" in value or "\n" in value for value in values):
            raise ValueError("tags must be non-empty, single-line, NUL-free, and at most 128 chars")
        if any(redact_text(value) != value for value in values):
            raise ValueError("credential-like values are forbidden in tags")
        return values


def classify_gpu_action(request: LaunchRequest) -> dict[str, Any]:
    reasons: list[str] = []
    if len(request.gpu_ids) > 1:
        reasons.append("multi_gpu")
    if request.expected_gpu_hours > 2.0:
        reasons.append("gpu_hour_budget_over_2")
    risk_level = "R2" if reasons else "R1"
    return {
        "risk_level": risk_level,
        "expected_gpu_hours": request.expected_gpu_hours,
        "gpu_count": len(request.gpu_ids),
        "requires_approval_for_execution": risk_level == "R2",
        "reasons": reasons or ["bounded_single_gpu_budget"],
    }


def action_payload(request: LaunchRequest) -> dict[str, Any]:
    """Canonical approval payload, excluding transport-only token and dry-run preview flag."""

    return request.model_dump(mode="json", exclude={"approval_token", "dry_run"})


def action_digest(request: LaunchRequest) -> str:
    return canonical_sha256({"action": APPROVAL_ACTION, "payload": action_payload(request)})


def approval_scope(request: LaunchRequest) -> str:
    return f"gpu.launch:{request.experiment_id}:{request.idempotency_key}"


class _Job:
    def __init__(self, response: dict[str, Any], process: Any) -> None:
        self.response = response
        self.process = process

    def current(self) -> dict[str, Any]:
        result = dict(self.response)
        return_code = self.process.poll() if self.process is not None else None
        result["process_status"] = "running" if return_code is None else "finished"
        result["return_code"] = return_code
        return result


class GPUService:
    """Enforces execution policy independently of MCP annotations or clients."""

    def __init__(
        self,
        workspace_root: str | Path,
        *,
        enable_synthetic_local_execution: bool = False,
        approval_manager: HMACApprovalManager | None = None,
        process_runner: Any = subprocess.Popen,
        python_executable: str = sys.executable,
    ) -> None:
        self.workspace = TrustedRoot(workspace_root, label="GPU workspace root")
        self.enable_synthetic_local_execution = enable_synthetic_local_execution
        self.approval_manager = approval_manager
        self.process_runner = process_runner
        self.python_executable = python_executable
        self._jobs: dict[str, _Job] = {}
        self._lock = threading.Lock()

    def _config_path(self, request: LaunchRequest) -> Path:
        path = self.workspace.resolve_existing(request.config_path, require_file=True)
        if path.suffix.lower() not in ALLOWED_CONFIG_SUFFIXES:
            raise StructuredToolError(
                "config_type_rejected", "Only JSON, TOML, and YAML config files are allowed"
            )
        if path.stat().st_size > 1024 * 1024:
            raise StructuredToolError("config_too_large", "Config files are limited to 1 MiB")
        actual_digest = file_sha256(path)
        if actual_digest != request.config_sha256:
            raise StructuredToolError(
                "config_digest_mismatch",
                "The config bytes do not match the digest bound to this launch request",
                {"expected_sha256": request.config_sha256, "actual_sha256": actual_digest},
            )
        return path

    def _command(self, request: LaunchRequest, config: Path) -> list[str]:
        # Every item is a separate argv value. No request field can replace the
        # executable/module and subprocess is always called with shell=False.
        command = [
            self.python_executable,
            "-I",
            "-m",
            "egoagentos_mcp.synthetic_worker",
            "--mode",
            ENTRYPOINT_MODES[request.entrypoint],
            "--config",
            str(config),
            "--expected-config-sha256",
            request.config_sha256,
            "--seed",
            str(request.seed),
        ]
        for gpu_id in request.gpu_ids:
            command.extend(("--gpu-id", str(gpu_id)))
        for tag in request.tags:
            command.extend(("--tag", tag))
        return command

    def plan(self, request: LaunchRequest) -> dict[str, Any]:
        config = self._config_path(request)
        digest = action_digest(request)
        risk = classify_gpu_action(request)
        return {
            "schema": "egoagentos.gpu-launch-plan.v1",
            "execution_mode": "synthetic_dry_run",
            "synthetic": True,
            "dry_run": True,
            "live_gpu_integration": False,
            "entrypoint": request.entrypoint,
            "config_path": self.workspace.relative(config),
            "config_sha256": request.config_sha256,
            "command_argv": self._command(request, config),
            "shell": False,
            "run_id": f"run_{digest[:20]}",
            "action_digest": digest,
            "approval_scope": approval_scope(request),
            "risk": risk,
            "approval_required_to_execute": risk["requires_approval_for_execution"],
        }

    def launch(self, request: LaunchRequest) -> dict[str, Any]:
        plan = self.plan(request)
        if request.dry_run:
            return plan
        if not self.enable_synthetic_local_execution:
            raise StructuredToolError(
                "local_execution_disabled",
                "Synthetic local execution is disabled; use dry_run or set the operator opt-in",
            )

        digest = plan["action_digest"]
        with self._lock:
            existing = self._jobs.get(request.idempotency_key)
            if existing is not None:
                if existing.response["action_digest"] != digest:
                    raise StructuredToolError(
                        "idempotency_conflict",
                        "The idempotency key is already bound to a different action",
                    )
                return {**existing.current(), "idempotent_replay": True}

            if plan["risk"]["requires_approval_for_execution"]:
                if not request.approval_token:
                    raise StructuredToolError(
                        "approval_required",
                        "R2 synthetic local execution requires a scoped approval token",
                        {
                            "risk_level": plan["risk"]["risk_level"],
                            "approval_scope": plan["approval_scope"],
                            "action_digest": digest,
                        },
                    )
                if self.approval_manager is None:
                    raise StructuredToolError(
                        "approval_validator_unconfigured",
                        "The operator has not configured an HMAC approval validator",
                    )
                self.approval_manager.validate_and_consume(
                    request.approval_token,
                    expected_action=APPROVAL_ACTION,
                    expected_scope=plan["approval_scope"],
                    expected_digest=digest,
                    expected_config_sha256=request.config_sha256,
                )

            environment = {
                "PYTHONUNBUFFERED": "1",
                "CUDA_VISIBLE_DEVICES": ",".join(str(value) for value in request.gpu_ids),
            }
            try:
                process = self.process_runner(
                    plan["command_argv"],
                    shell=False,
                    cwd=str(self.workspace.path),
                    env=environment,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    close_fds=True,
                    start_new_session=False,
                )
            except Exception as exc:
                raise StructuredToolError(
                    "synthetic_launch_failed",
                    "The allowlisted synthetic worker could not be launched",
                    {"reason": type(exc).__name__},
                ) from exc
            response = {
                **plan,
                "execution_mode": "synthetic_local_process",
                "dry_run": False,
                "pid": int(process.pid),
                "idempotent_replay": False,
            }
            job = _Job(response, process)
            self._jobs[request.idempotency_key] = job
            return job.current()

    def job_status(self, idempotency_key: str) -> dict[str, Any]:
        with self._lock:
            job = self._jobs.get(idempotency_key)
            if job is None:
                raise StructuredToolError("job_not_found", "No local synthetic job has this key")
            return job.current()


@lru_cache(maxsize=1)
def default_gpu_service() -> GPUService:
    workspace = TrustedRoot.from_env("EGO_MCP_WORKSPACE_ROOT", label="GPU workspace root")
    enabled = os.environ.get("EGO_MCP_ENABLE_SYNTHETIC_LOCAL_EXECUTION") == "1"
    secret = os.environ.get("EGO_MCP_APPROVAL_HMAC_SECRET")
    manager: HMACApprovalManager | None = None
    if secret:
        replay_directory = os.environ.get("EGO_MCP_APPROVAL_REPLAY_DIR")
        if not replay_directory:
            raise StructuredToolError(
                "approval_replay_store_required",
                "Set EGO_MCP_APPROVAL_REPLAY_DIR when configuring approval validation",
            )
        manager = HMACApprovalManager(secret, replay_store=FileReplayStore(replay_directory))
    return GPUService(
        workspace.path,
        enable_synthetic_local_execution=enabled,
        approval_manager=manager,
    )


mcp = MCPServer(
    "egoagentos-gpu",
    version="0.1.0",
    instructions=(
        "Allowlisted synthetic launcher. Dry-run is the default. This server has no generic shell "
        "and no live GPU or AgentTeams integration. Server-side policy is authoritative."
    ),
)


@mcp.tool(
    title="Plan or launch an allowlisted synthetic experiment",
    annotations=ToolAnnotations(
        read_only_hint=False,
        destructive_hint=False,
        idempotent_hint=True,
        open_world_hint=False,
    ),
)
def gpu_launch_experiment(request: LaunchRequest) -> dict[str, Any]:
    """Dry-run by default; optional local execution only runs the packaged synthetic worker."""

    return default_gpu_service().launch(request)


@mcp.tool(
    title="Read synthetic local job status",
    annotations=ToolAnnotations(read_only_hint=True, open_world_hint=False),
)
def gpu_job_status(idempotency_key: str) -> dict[str, Any]:
    """Read process status for a job launched by this server process."""

    return default_gpu_service().job_status(idempotency_key)


def main() -> None:
    run_mcp_server(mcp)


if __name__ == "__main__":
    main()
