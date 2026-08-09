from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
from pydantic import ValidationError

from egoagentos_mcp.approval import HMACApprovalManager, InMemoryReplayStore
from egoagentos_mcp.common import StructuredToolError
from egoagentos_mcp.gpu_server import (
    APPROVAL_ACTION,
    GPUService,
    LaunchRequest,
    action_digest,
    approval_scope,
    classify_gpu_action,
)

SECRET = b"test-only-secret-that-is-at-least-32-bytes-long"
CONFIG_BYTES = b"synthetic: true\n"
CONFIG_SHA256 = hashlib.sha256(CONFIG_BYTES).hexdigest()


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


def make_request(**overrides: object) -> LaunchRequest:
    payload: dict[str, object] = {
        "experiment_id": "exp-001",
        "idempotency_key": "idem-0001",
        "entrypoint": "train_pose",
        "config_path": "config.yaml",
        "config_sha256": CONFIG_SHA256,
        "gpu_ids": [0],
        "seed": 42,
        "expected_gpu_hours": 1.0,
        "tags": [],
        "dry_run": True,
    }
    payload.update(overrides)
    return LaunchRequest.model_validate(payload)


def test_unknown_entrypoint_and_command_field_are_rejected() -> None:
    with pytest.raises(ValidationError):
        make_request(entrypoint="bash")
    with pytest.raises(ValidationError):
        LaunchRequest.model_validate(
            {
                **make_request().model_dump(),
                "command": "rm -rf /",
            }
        )


def test_shell_syntax_is_an_inert_argv_item_and_launch_is_idempotent(tmp_path: Path) -> None:
    config = tmp_path / "config.yaml"
    config.write_text("synthetic: true\n", encoding="utf-8")
    marker = tmp_path / "must-not-exist"
    injection = "; touch must-not-exist"
    runner = CapturingRunner()
    service = GPUService(
        tmp_path,
        enable_synthetic_local_execution=True,
        process_runner=runner,
        python_executable="/trusted/python3.12",
    )
    request = make_request(tags=[injection], dry_run=False)

    first = service.launch(request)
    second = service.launch(request)

    assert len(runner.calls) == 1
    command, kwargs = runner.calls[0]
    assert injection in command
    assert command[command.index("--tag") + 1] == injection
    assert command[command.index("--expected-config-sha256") + 1] == CONFIG_SHA256
    assert kwargs["shell"] is False
    assert marker.exists() is False
    assert first["idempotent_replay"] is False
    assert second["idempotent_replay"] is True


def test_risk_classification_and_missing_approval(tmp_path: Path) -> None:
    (tmp_path / "config.yaml").write_text("synthetic: true\n", encoding="utf-8")
    service = GPUService(tmp_path, enable_synthetic_local_execution=True)
    request = make_request(
        gpu_ids=[0, 1], expected_gpu_hours=8.0, dry_run=False, idempotency_key="idem-0002"
    )

    assert classify_gpu_action(request)["risk_level"] == "R2"
    with pytest.raises(StructuredToolError) as missing:
        service.launch(request)
    assert missing.value.code == "approval_required"
    assert "token" not in missing.value.details


def test_approval_scope_expiry_and_replay() -> None:
    now = 1_800_000_000
    manager = HMACApprovalManager(SECRET, replay_store=InMemoryReplayStore(), clock=lambda: now)
    request = make_request(gpu_ids=[0, 1], expected_gpu_hours=8.0)
    digest = action_digest(request)
    scope = approval_scope(request)
    token = manager.issue(
        jti="nonce_for_scope_001",
        action=APPROVAL_ACTION,
        scope=scope,
        action_digest=digest,
        config_sha256=request.config_sha256,
        ttl_seconds=60,
    )

    with pytest.raises(StructuredToolError) as wrong_scope:
        manager.validate_and_consume(
            token,
            expected_action=APPROVAL_ACTION,
            expected_scope="gpu.launch:other:idem-0001",
            expected_digest=digest,
            expected_config_sha256=request.config_sha256,
        )
    assert wrong_scope.value.code == "approval_scope_mismatch"

    with pytest.raises(StructuredToolError) as wrong_config:
        manager.validate_and_consume(
            token,
            expected_action=APPROVAL_ACTION,
            expected_scope=scope,
            expected_digest=digest,
            expected_config_sha256="0" * 64,
        )
    assert wrong_config.value.code == "approval_scope_mismatch"
    assert wrong_config.value.details["mismatched_fields"] == ["config_sha256"]

    manager.validate_and_consume(
        token,
        expected_action=APPROVAL_ACTION,
        expected_scope=scope,
        expected_digest=digest,
        expected_config_sha256=request.config_sha256,
    )
    with pytest.raises(StructuredToolError) as replay:
        manager.validate_and_consume(
            token,
            expected_action=APPROVAL_ACTION,
            expected_scope=scope,
            expected_digest=digest,
            expected_config_sha256=request.config_sha256,
        )
    assert replay.value.code == "approval_token_replayed"

    expired = manager.issue(
        jti="nonce_for_expiry_002",
        action=APPROVAL_ACTION,
        scope=scope,
        action_digest=digest,
        config_sha256=request.config_sha256,
        ttl_seconds=10,
        now=now - 20,
    )
    with pytest.raises(StructuredToolError) as expiry:
        manager.validate_and_consume(
            expired,
            expected_action=APPROVAL_ACTION,
            expected_scope=scope,
            expected_digest=digest,
            expected_config_sha256=request.config_sha256,
        )
    assert expiry.value.code == "approval_token_expired"


def test_r2_launch_accepts_exact_token_without_returning_it(tmp_path: Path) -> None:
    (tmp_path / "config.yaml").write_text("synthetic: true\n", encoding="utf-8")
    manager = HMACApprovalManager(SECRET, replay_store=InMemoryReplayStore())
    runner = CapturingRunner()
    unsigned = make_request(
        gpu_ids=[0, 1], expected_gpu_hours=8.0, dry_run=False, idempotency_key="idem-0003"
    )
    token = manager.issue(
        jti="nonce_for_launch_003",
        action=APPROVAL_ACTION,
        scope=approval_scope(unsigned),
        action_digest=action_digest(unsigned),
        config_sha256=unsigned.config_sha256,
    )
    request = unsigned.model_copy(update={"approval_token": token})
    service = GPUService(
        tmp_path,
        enable_synthetic_local_execution=True,
        approval_manager=manager,
        process_runner=runner,
    )

    response = service.launch(request)

    assert response["execution_mode"] == "synthetic_local_process"
    assert token not in repr(response)
    assert len(runner.calls) == 1


def test_config_bytes_are_bound_to_action_digest_and_rechecked_before_launch(
    tmp_path: Path,
) -> None:
    config = tmp_path / "config.yaml"
    config.write_bytes(CONFIG_BYTES)
    service = GPUService(tmp_path)
    approved_request = make_request()
    approved_digest = action_digest(approved_request)

    assert service.plan(approved_request)["config_sha256"] == CONFIG_SHA256

    changed_bytes = b"synthetic: false\n"
    config.write_bytes(changed_bytes)
    with pytest.raises(StructuredToolError) as mismatch:
        service.plan(approved_request)
    assert mismatch.value.code == "config_digest_mismatch"

    changed_request = approved_request.model_copy(
        update={"config_sha256": hashlib.sha256(changed_bytes).hexdigest()}
    )
    assert action_digest(changed_request) != approved_digest
