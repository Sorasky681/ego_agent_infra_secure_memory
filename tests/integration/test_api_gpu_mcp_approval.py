"""Cross-runtime contract proof for API-issued GPU MCP approval tokens."""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

import pytest

from apps.api.service import DEMO_TASK_ID, ResearchOpsService
from apps.api.store import SQLiteStore
from egoagentos_mcp.approval import ApprovalClaims, HMACApprovalManager, InMemoryReplayStore
from egoagentos_mcp.common import StructuredToolError
from egoagentos_mcp.gpu_server import (
    APPROVAL_ACTION,
    GPUService,
    LaunchRequest,
    action_digest,
    action_payload,
    approval_scope,
)


SECRET = "integration-only-secret-that-is-at-least-32-bytes"


class FakeProcess:
    pid = 4242

    def poll(self) -> None:
        return None


class CapturingRunner:
    def __init__(self) -> None:
        self.calls: list[tuple[list[str], dict[str, object]]] = []

    def __call__(self, command: list[str], **kwargs: object) -> FakeProcess:
        self.calls.append((command, kwargs))
        return FakeProcess()


def test_api_approval_token_executes_exact_gpu_mcp_request_once(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[2]
    contract = json.loads((root / "contracts" / "approval-token-v1.json").read_text())
    store = SQLiteStore(str(tmp_path / "control-plane.sqlite3"))
    api = ResearchOpsService(store, approval_hmac_secret=SECRET)
    api.reset_demo("happy_path")
    paused = api.autorun(DEMO_TASK_ID)
    pending = paused["task"]["pending_approval"]

    request_payload: dict[str, Any] = pending["action_payload"]
    unsigned = LaunchRequest.model_validate({**request_payload, "dry_run": False})
    assert pending["action"] == APPROVAL_ACTION == contract["gpu_launch"]["action"]
    assert list(action_payload(unsigned)) == contract["gpu_launch"]["payload_fields"]
    assert list(ApprovalClaims.model_fields) == contract["token"]["claims"]
    assert pending["scope"] == approval_scope(unsigned)
    assert pending["action_digest"] == action_digest(unsigned)
    assert pending["config_sha256"] == unsigned.config_sha256
    assert pending["rollback_point"].startswith("Restore the baseline-ltx")

    decision = api.decide_approval(
        pending["id"],
        "approved",
        "integration-operator",
        pending["action_digest"],
    )
    token = decision["approval_token"]
    assert isinstance(token, str) and token.startswith("egoap1.")

    config_target = tmp_path / unsigned.config_path
    config_target.parent.mkdir(parents=True)
    shutil.copy2(root / unsigned.config_path, config_target)
    manager = HMACApprovalManager(SECRET, replay_store=InMemoryReplayStore())
    runner = CapturingRunner()
    gpu = GPUService(
        tmp_path,
        enable_synthetic_local_execution=True,
        approval_manager=manager,
        process_runner=runner,
    )

    preview = gpu.plan(unsigned.model_copy(update={"dry_run": True}))
    assert preview["execution_mode"] == "synthetic_dry_run"
    assert preview["action_digest"] == pending["action_digest"]
    assert preview["approval_scope"] == pending["scope"]

    accepted = gpu.launch(unsigned.model_copy(update={"approval_token": token}))

    assert accepted["execution_mode"] == "synthetic_local_process"
    assert accepted["action_digest"] == pending["action_digest"]
    assert len(runner.calls) == 1
    assert token not in repr(accepted)

    second_gpu = GPUService(
        tmp_path,
        enable_synthetic_local_execution=True,
        approval_manager=manager,
        process_runner=CapturingRunner(),
    )
    with pytest.raises(StructuredToolError) as replay:
        second_gpu.launch(unsigned.model_copy(update={"approval_token": token}))
    assert replay.value.code == "approval_token_replayed"


def test_hmac_token_keeps_control_plane_happy_path(tmp_path: Path) -> None:
    api = ResearchOpsService(
        SQLiteStore(str(tmp_path / "ui-happy-path.sqlite3")),
        approval_hmac_secret=SECRET,
    )
    api.reset_demo("happy_path")
    paused = api.autorun(DEMO_TASK_ID)
    pending = paused["task"]["pending_approval"]
    decision = api.decide_approval(
        pending["id"], "approved", "ui-operator", pending["action_digest"]
    )

    completed = api.autorun(DEMO_TASK_ID, decision["approval_token"])

    assert completed["status"] == "completed"
    assert completed["task"]["stage"] == "COMPLETED"
