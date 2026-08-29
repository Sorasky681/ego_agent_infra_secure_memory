"""Typed domain and transport models for the ResearchOps control plane."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", use_enum_values=False)


class Stage(str, Enum):
    INTAKE = "INTAKE"
    CONTEXT = "CONTEXT"
    PLAN = "PLAN"
    PLAN_REVIEW = "PLAN_REVIEW"
    APPROVAL = "APPROVAL"
    EXECUTE = "EXECUTE"
    OBSERVE = "OBSERVE"
    EVALUATE = "EVALUATE"
    VERIFY = "VERIFY"
    DECIDE = "DECIDE"
    ARCHIVE = "ARCHIVE"
    MEMORY_SKILL = "MEMORY_SKILL"
    COMPLETED = "COMPLETED"


class RiskLevel(str, Enum):
    R0 = "R0"
    R1 = "R1"
    R2 = "R2"
    R3 = "R3"


class ApprovalStatus(str, Enum):
    PENDING = "pending"
    APPROVED = "approved"
    DENIED = "denied"
    EXPIRED = "expired"
    CONSUMED = "consumed"


class ApprovalDecision(str, Enum):
    APPROVED = "approved"
    DENIED = "denied"


class EvidenceKind(str, Enum):
    CODE = "code"
    CONFIG = "config"
    DATASET_MANIFEST = "dataset_manifest"
    LOG = "log"
    METRIC = "metric"
    TRACE = "trace"
    REVIEW = "review"


class GateStatus(str, Enum):
    NOT_RUN = "not_run"
    PASS = "pass"
    FAIL = "fail"


class IntegrationTruth(str, Enum):
    READY = "ready"
    NOT_CONFIGURED = "not_configured"
    CONFIGURED_UNVERIFIED = "configured_unverified"
    UNAVAILABLE = "unavailable"


class CandidateArm(StrictModel):
    id: str
    name: str
    description: str


class AcceptanceMetric(StrictModel):
    name: str
    direction: Literal["higher_better", "lower_better"]
    threshold: float
    unit: str
    rule: str


class ResearchGoal(StrictModel):
    objective: str
    frozen: bool = True
    hardware: str
    constraints: Dict[str, Any]
    acceptance_metrics: List[AcceptanceMetric]
    candidate_arms: List[CandidateArm]


class RunManifest(StrictModel):
    git_commit: str
    config_sha256: str
    dataset_manifest_sha256: str
    environment_lock_sha256: str
    base_model_sha256: str
    seed: int

    @field_validator(
        "config_sha256",
        "dataset_manifest_sha256",
        "environment_lock_sha256",
        "base_model_sha256",
    )
    @classmethod
    def validate_digest(cls, value: str) -> str:
        value = value.lower()
        if len(value) != 64 or any(char not in "0123456789abcdef" for char in value):
            raise ValueError("must be a lowercase SHA-256 digest")
        return value


class EvaluationResult(StrictModel):
    metric: str
    direction: Literal["higher_better", "lower_better"]
    baseline_mean: float
    candidate_mean: float
    mean_delta: float
    relative_delta: float
    ci95: List[float]
    threshold: float
    verdict: Literal["PASS", "FAIL", "INCONCLUSIVE"]
    bootstrap_seed: int
    bootstrap_samples: int
    sample_count: int
    data_classification: str


class GateResult(StrictModel):
    status: GateStatus
    present: List[EvidenceKind]
    missing: List[EvidenceKind]
    reasons: List[str]
    independent_reviewer: Optional[str] = None
    checked_at: datetime = Field(default_factory=utc_now)


class EvidenceRecord(StrictModel):
    id: str
    task_id: str
    generation: str
    kind: EvidenceKind
    producer_id: str
    artifact_digest: str
    payload: Dict[str, Any]
    synthetic: bool
    created_at: datetime = Field(default_factory=utc_now)

    @field_validator("artifact_digest")
    @classmethod
    def artifact_digest_is_sha256(cls, value: str) -> str:
        value = value.lower()
        if len(value) != 64 or any(char not in "0123456789abcdef" for char in value):
            raise ValueError("artifact_digest must be a SHA-256 digest")
        return value


class ApprovalPublic(StrictModel):
    id: str
    task_id: str
    generation: str
    status: ApprovalStatus
    risk_level: RiskLevel
    scope: str
    action: str
    action_digest: str
    # Optional/defaulted only so databases created before approval-contract v1 remain
    # readable. Every newly built approval supplies both fields; legacy approvals cannot
    # mint a tool-plane token and fail the exact current action-contract comparison.
    config_sha256: Optional[str] = None
    action_payload: Dict[str, Any] = Field(default_factory=dict)
    rollback_point: Optional[str] = None
    requested_at: datetime
    expires_at: datetime
    decided_at: Optional[datetime] = None
    approver: Optional[str] = None
    used_at: Optional[datetime] = None

    @field_validator("action_digest", "config_sha256")
    @classmethod
    def approval_digest_is_sha256(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        value = value.lower()
        if len(value) != 64 or any(char not in "0123456789abcdef" for char in value):
            raise ValueError("approval digests must be lowercase SHA-256 values")
        return value


class ApprovalRecord(ApprovalPublic):
    token_hash: Optional[str] = Field(default=None, exclude=True)


class TaskRecord(StrictModel):
    id: str
    generation: str
    title: str
    objective: str
    stage: Stage
    risk_level: RiskLevel
    goal: ResearchGoal
    scenario: Literal["happy_path", "insufficient_evidence"]
    synthetic_demo: bool
    data_notice: str
    owner_agent: str
    current_agent: str
    version: int = 1
    run_manifest_digest: Optional[str] = None
    latest_evaluation: List[EvaluationResult] = Field(default_factory=list)
    gate_result: GateResult = Field(
        default_factory=lambda: GateResult(
            status=GateStatus.NOT_RUN, present=[], missing=[], reasons=[]
        )
    )
    decision: Optional[str] = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class AuditEvent(StrictModel):
    sequence: int
    id: str
    task_id: str
    generation: str
    event_type: str
    actor: str
    stage: Optional[Stage]
    payload: Dict[str, Any]
    previous_hash: str
    event_hash: str
    created_at: datetime


class MemorySignals(StrictModel):
    semantic: float
    component: float
    evidence: float
    recency: float
    failure: float

    @field_validator("semantic", "component", "evidence", "recency", "failure")
    @classmethod
    def bounded(cls, value: float) -> float:
        if not 0.0 <= value <= 1.0:
            raise ValueError("memory signal must be in [0, 1]")
        return value


class MemoryRecord(StrictModel):
    id: str
    task_id: str
    generation: str
    memory_type: Literal["semantic", "episodic", "procedural"]
    statement: str
    component: str
    evidence_digest: str
    review_id: str
    validated: bool
    created_at: datetime = Field(default_factory=utc_now)

    @field_validator("evidence_digest")
    @classmethod
    def evidence_digest_is_sha256(cls, value: str) -> str:
        value = value.lower()
        if len(value) != 64 or any(char not in "0123456789abcdef" for char in value):
            raise ValueError("evidence_digest must be a SHA-256 digest")
        return value


class AdvanceRequest(StrictModel):
    target: Optional[Stage] = None
    approval_token: Optional[str] = None
    idempotency_key: Optional[str] = Field(default=None, min_length=8, max_length=128)


class AutorunRequest(StrictModel):
    approval_token: Optional[str] = None
    idempotency_key: Optional[str] = Field(default=None, min_length=8, max_length=128)


class ApprovalDecisionRequest(StrictModel):
    decision: ApprovalDecision
    approver: str = Field(min_length=2, max_length=120)
    expected_digest: str

    @field_validator("expected_digest")
    @classmethod
    def expected_digest_is_sha256(cls, value: str) -> str:
        value = value.lower()
        if len(value) != 64 or any(char not in "0123456789abcdef" for char in value):
            raise ValueError("expected_digest must be a SHA-256 digest")
        return value


class DemoResetRequest(StrictModel):
    scenario: Literal["happy_path", "insufficient_evidence"] = "happy_path"
    idempotency_key: Optional[str] = Field(default=None, min_length=8, max_length=128)


class RXPVerifyRequest(StrictModel):
    ledger: Dict[str, Any]


class IntegrationState(StrictModel):
    id: str
    name: str
    role: str
    status: IntegrationTruth
    endpoint_configured: bool
    checked_at: datetime
    detail: str
