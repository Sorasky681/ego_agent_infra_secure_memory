"""Risk classification and scoped, single-use approval token validation."""

import base64
import hashlib
import hmac
import json
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional, Tuple

from pydantic import Field

from .errors import ConflictError, PolicyError
from .models import ApprovalRecord, ApprovalStatus, RiskLevel, StrictModel
from .provenance import text_sha256


TOKEN_PREFIX = "egoap1"
MAX_TOKEN_TTL_SECONDS = 900


def _b64encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def issue_hmac_approval_token(
    approval: ApprovalRecord,
    secret: str,
    now: Optional[datetime] = None,
) -> str:
    """Mint the v1 tool-plane token for an approval already granted by a human.

    The canonical claims and signature format are specified in
    ``contracts/approval-token-v1.json`` and deliberately use only Python 3.9
    standard-library primitives so the API and MCP runtimes can interoperate.
    """

    secret_bytes = secret.encode("utf-8")
    if len(secret_bytes) < 32:
        raise PolicyError(
            "approval_secret_too_short",
            "The MCP approval HMAC secret must be at least 32 bytes",
        )
    if not approval.config_sha256:
        raise PolicyError(
            "approval_contract_legacy",
            "This approval predates the GPU config-bound token contract; request a fresh approval",
        )
    issued_at = int((now or datetime.now(timezone.utc)).timestamp())
    expires_at = int(approval.expires_at.timestamp())
    if expires_at <= issued_at or expires_at - issued_at > MAX_TOKEN_TTL_SECONDS:
        raise PolicyError(
            "approval_ttl_invalid",
            "The remaining MCP approval lifetime must be between 1 and 900 seconds",
        )
    claims = {
        "version": 1,
        "jti": approval.id,
        "action": approval.action,
        "scope": approval.scope,
        "action_digest": approval.action_digest,
        "config_sha256": approval.config_sha256,
        "issued_at": issued_at,
        "expires_at": expires_at,
    }
    payload = json.dumps(
        claims,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    signature = hmac.new(secret_bytes, payload, hashlib.sha256).digest()
    return "%s.%s.%s" % (TOKEN_PREFIX, _b64encode(payload), _b64encode(signature))


class ActionIntent(StrictModel):
    name: str
    read_only: bool = False
    mutates_sandbox: bool = False
    gpu_count: int = Field(default=0, ge=0)
    expected_gpu_hours: float = Field(default=0.0, ge=0.0)
    changes_dataset: bool = False
    publishes_external: bool = False
    changes_main: bool = False
    destructive: bool = False
    rollback_point: Optional[str] = None


class PolicyDecision(StrictModel):
    risk_level: RiskLevel
    requires_approval: bool
    requires_rollback: bool
    reasons: list[str]


def classify_action(intent: ActionIntent) -> PolicyDecision:
    if intent.destructive or intent.publishes_external or intent.changes_main:
        reasons = []
        if intent.destructive:
            reasons.append("destructive action")
        if intent.publishes_external:
            reasons.append("external publication")
        if intent.changes_main:
            reasons.append("protected branch mutation")
        return PolicyDecision(
            risk_level=RiskLevel.R3,
            requires_approval=True,
            requires_rollback=True,
            reasons=reasons,
        )
    if intent.changes_dataset or intent.gpu_count > 1 or intent.expected_gpu_hours > 2.0:
        reasons = []
        if intent.changes_dataset:
            reasons.append("dataset mutation")
        if intent.gpu_count > 1:
            reasons.append("multi-GPU execution")
        if intent.expected_gpu_hours > 2.0:
            reasons.append("compute budget exceeds 2 GPU-hours")
        return PolicyDecision(
            risk_level=RiskLevel.R2,
            requires_approval=True,
            requires_rollback=False,
            reasons=reasons,
        )
    if intent.read_only and not intent.mutates_sandbox:
        return PolicyDecision(
            risk_level=RiskLevel.R0,
            requires_approval=False,
            requires_rollback=False,
            reasons=["read-only action"],
        )
    return PolicyDecision(
        risk_level=RiskLevel.R1,
        requires_approval=False,
        requires_rollback=False,
        reasons=["bounded sandbox mutation"],
    )


def build_approval(
    approval_id: str,
    task_id: str,
    generation: str,
    risk_level: RiskLevel,
    scope: str,
    action: str,
    digest: str,
    config_sha256: str,
    action_payload: Dict[str, Any],
    rollback_point: Optional[str] = None,
    now: Optional[datetime] = None,
    ttl_seconds: int = 900,
) -> ApprovalRecord:
    requested_at = now or datetime.now(timezone.utc)
    if risk_level == RiskLevel.R3 and not rollback_point:
        raise PolicyError(
            "rollback_required", "R3 approval cannot be requested without a rollback point"
        )
    return ApprovalRecord(
        id=approval_id,
        task_id=task_id,
        generation=generation,
        status=ApprovalStatus.PENDING,
        risk_level=risk_level,
        scope=scope,
        action=action,
        action_digest=digest,
        config_sha256=config_sha256,
        action_payload=action_payload,
        rollback_point=rollback_point,
        requested_at=requested_at,
        expires_at=requested_at + timedelta(seconds=ttl_seconds),
    )


def decide_approval(
    approval: ApprovalRecord,
    decision: str,
    approver: str,
    expected_digest: str,
    now: Optional[datetime] = None,
    hmac_secret: Optional[str] = None,
) -> Tuple[ApprovalRecord, Optional[str]]:
    decided_at = now or datetime.now(timezone.utc)
    if approval.status != ApprovalStatus.PENDING:
        raise ConflictError(
            "approval_already_decided",
            "Approval is no longer pending",
            {"approval_id": approval.id, "status": approval.status.value},
        )
    if decided_at >= approval.expires_at:
        approval.status = ApprovalStatus.EXPIRED
        raise PolicyError(
            "approval_expired",
            "Approval request expired before a decision was recorded",
            {"approval_id": approval.id, "expires_at": approval.expires_at.isoformat()},
        )
    if not hmac.compare_digest(approval.action_digest, expected_digest):
        raise PolicyError(
            "approval_digest_mismatch",
            "The reviewed action digest does not match the pending action",
            {"approval_id": approval.id, "expected": approval.action_digest},
        )

    approval.approver = approver
    approval.decided_at = decided_at
    if decision == "denied":
        approval.status = ApprovalStatus.DENIED
        return approval, None
    if decision != "approved":
        raise ValueError("decision must be approved or denied")

    raw_token = (
        issue_hmac_approval_token(approval, hmac_secret, decided_at)
        if hmac_secret
        else "egoap_%s_%s" % (approval.id, secrets.token_urlsafe(32))
    )
    approval.status = ApprovalStatus.APPROVED
    approval.token_hash = text_sha256(raw_token)
    return approval, raw_token


def validate_approval_token(
    approval: ApprovalRecord,
    raw_token: Optional[str],
    task_id: str,
    generation: str,
    scope: str,
    action: str,
    expected_digest: str,
    now: Optional[datetime] = None,
) -> None:
    checked_at = now or datetime.now(timezone.utc)
    if not raw_token:
        raise PolicyError(
            "approval_required",
            "A scoped approval token is required for this transition",
            {"risk_level": approval.risk_level.value, "approval_id": approval.id},
        )
    if approval.status == ApprovalStatus.CONSUMED or approval.used_at is not None:
        raise PolicyError(
            "approval_token_replayed",
            "This approval token has already been consumed",
            {"approval_id": approval.id},
        )
    if approval.status != ApprovalStatus.APPROVED:
        raise PolicyError(
            "approval_not_granted",
            "The approval is not in an approved state",
            {"approval_id": approval.id, "status": approval.status.value},
        )
    if checked_at >= approval.expires_at:
        raise PolicyError(
            "approval_token_expired",
            "The approval token has expired",
            {"approval_id": approval.id, "expires_at": approval.expires_at.isoformat()},
        )
    mismatches = {}
    expected = {
        "task_id": task_id,
        "generation": generation,
        "scope": scope,
        "action": action,
        "action_digest": expected_digest,
    }
    actual = {
        "task_id": approval.task_id,
        "generation": approval.generation,
        "scope": approval.scope,
        "action": approval.action,
        "action_digest": approval.action_digest,
    }
    for key, expected_value in expected.items():
        if actual[key] != expected_value:
            mismatches[key] = {"expected": expected_value, "actual": actual[key]}
    if mismatches:
        raise PolicyError(
            "approval_scope_mismatch",
            "Approval token is not valid for this exact action scope",
            {"approval_id": approval.id, "mismatches": mismatches},
        )
    if not approval.token_hash or not hmac.compare_digest(
        approval.token_hash, text_sha256(raw_token)
    ):
        raise PolicyError(
            "approval_token_invalid", "Approval token is invalid", {"approval_id": approval.id}
        )


def consume_approval(approval: ApprovalRecord, now: Optional[datetime] = None) -> ApprovalRecord:
    approval.status = ApprovalStatus.CONSUMED
    approval.used_at = now or datetime.now(timezone.utc)
    return approval
