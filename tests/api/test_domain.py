from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from apps.api.errors import ConflictError, PolicyError
from apps.api.evaluator import evaluate_paired_metric
from apps.api.evidence import REQUIRED_FOR_DECISION, evidence_gate
from apps.api.memory import require_validated_memory, score_memory
from apps.api.models import (
    ApprovalStatus,
    EvidenceKind,
    EvidenceRecord,
    GateResult,
    GateStatus,
    MemorySignals,
    RiskLevel,
    RunManifest,
    Stage,
)
from apps.api.policy import (
    ActionIntent,
    build_approval,
    classify_action,
    consume_approval,
    decide_approval,
    validate_approval_token,
)
from apps.api.provenance import canonical_json, canonical_sha256, run_manifest_digest, text_sha256
from apps.api.state_machine import validate_transition


def test_forbidden_transition_is_structured() -> None:
    with pytest.raises(ConflictError) as caught:
        validate_transition(Stage.PLAN, Stage.EXECUTE)
    assert caught.value.code == "illegal_transition"
    assert caught.value.details == {
        "current": "PLAN",
        "target": "EXECUTE",
        "allowed": ["PLAN_REVIEW"],
    }


def test_risk_policy_r0_through_r3() -> None:
    assert classify_action(ActionIntent(name="read", read_only=True)).risk_level == RiskLevel.R0
    assert (
        classify_action(ActionIntent(name="sandbox", mutates_sandbox=True)).risk_level
        == RiskLevel.R1
    )
    assert (
        classify_action(ActionIntent(name="gpu", gpu_count=8, expected_gpu_hours=24)).risk_level
        == RiskLevel.R2
    )
    critical = classify_action(ActionIntent(name="publish", publishes_external=True))
    assert critical.risk_level == RiskLevel.R3
    assert critical.requires_approval is True
    assert critical.requires_rollback is True


def _approved_token(now: datetime):
    approval = build_approval(
        approval_id="apr_test",
        task_id="task-a",
        generation="gen-a",
        risk_level=RiskLevel.R2,
        scope="task:task-a:generation:gen-a:experiment",
        action="launch",
        digest="a" * 64,
        config_sha256="c" * 64,
        action_payload={"config_sha256": "c" * 64},
        now=now,
        ttl_seconds=60,
    )
    approval, token = decide_approval(approval, "approved", "human@example.test", "a" * 64, now=now)
    assert token is not None
    return approval, token


def test_approval_bypass_scope_expiry_and_replay_are_rejected() -> None:
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    approval, token = _approved_token(now)
    common = dict(
        approval=approval,
        task_id="task-a",
        generation="gen-a",
        scope="task:task-a:generation:gen-a:experiment",
        action="launch",
        expected_digest="a" * 64,
        now=now + timedelta(seconds=1),
    )

    with pytest.raises(PolicyError, match="required") as bypass:
        validate_approval_token(raw_token=None, **common)
    assert bypass.value.code == "approval_required"

    with pytest.raises(PolicyError) as wrong_scope:
        validate_approval_token(raw_token=token, **{**common, "task_id": "task-b"})
    assert wrong_scope.value.code == "approval_scope_mismatch"

    with pytest.raises(PolicyError) as expired:
        validate_approval_token(raw_token=token, **{**common, "now": now + timedelta(seconds=61)})
    assert expired.value.code == "approval_token_expired"

    validate_approval_token(raw_token=token, **common)
    consume_approval(approval, now + timedelta(seconds=2))
    assert approval.status == ApprovalStatus.CONSUMED
    with pytest.raises(PolicyError) as replayed:
        validate_approval_token(raw_token=token, **common)
    assert replayed.value.code == "approval_token_replayed"


def test_r3_approval_requires_rollback_point() -> None:
    with pytest.raises(PolicyError) as caught:
        build_approval(
            approval_id="apr-r3",
            task_id="task",
            generation="gen",
            risk_level=RiskLevel.R3,
            scope="critical",
            action="publish",
            digest="b" * 64,
            config_sha256="c" * 64,
            action_payload={"config_sha256": "c" * 64},
        )
    assert caught.value.code == "rollback_required"


def test_canonical_hash_is_order_independent_and_manifest_sensitive() -> None:
    left = {"z": [3, 2, 1], "a": {"unicode": "研究", "flag": True}}
    right = {"a": {"flag": True, "unicode": "研究"}, "z": [3, 2, 1]}
    assert canonical_json(left) == canonical_json(right)
    assert canonical_sha256(left) == canonical_sha256(right)

    digest = text_sha256("artifact")
    manifest = RunManifest(
        git_commit="deadbeef",
        config_sha256=digest,
        dataset_manifest_sha256=digest,
        environment_lock_sha256=digest,
        base_model_sha256=digest,
        seed=42,
    )
    same = RunManifest(**manifest.model_dump())
    changed = RunManifest(**{**manifest.model_dump(), "seed": 43})
    assert run_manifest_digest(manifest) == run_manifest_digest(same)
    assert run_manifest_digest(manifest) != run_manifest_digest(changed)
    with pytest.raises(ValueError):
        canonical_sha256({"bad": float("nan")})


def _evidence(kind: EvidenceKind, producer: str, payload=None) -> EvidenceRecord:
    body = payload or {"kind": kind.value, "raw": True}
    return EvidenceRecord(
        id="evd-%s" % kind.value,
        task_id="task",
        generation="gen",
        kind=kind,
        producer_id=producer,
        artifact_digest=canonical_sha256(body),
        payload=body,
        synthetic=True,
    )


def _complete_evidence(review_payload=None, metric_payload=None):
    producer = {
        EvidenceKind.CODE: "runtime",
        EvidenceKind.CONFIG: "architect",
        EvidenceKind.DATASET_MANIFEST: "scout",
        EvidenceKind.LOG: "runtime",
        EvidenceKind.METRIC: "evaluator",
        EvidenceKind.TRACE: "runtime",
    }
    records = [
        _evidence(
            kind,
            producer[kind],
            metric_payload if kind == EvidenceKind.METRIC else {"kind": kind.value, "raw": True},
        )
        for kind in REQUIRED_FOR_DECISION - {EvidenceKind.REVIEW}
    ]
    records.append(
        _evidence(
            EvidenceKind.REVIEW,
            "reviewer",
            review_payload
            or {
                "reviewer_id": "reviewer",
                "reviewed_producers": ["runtime", "architect", "scout", "evaluator"],
                "independent": True,
                "verdict": "PASS",
            },
        )
    )
    return records


def test_evidence_gate_checks_completeness_raw_metrics_and_independence() -> None:
    records = _complete_evidence(
        metric_payload={
            "deterministic": True,
            "summary_only": False,
            "raw_samples": {"baseline": [1.0], "candidate": [2.0]},
        }
    )
    passed = evidence_gate(records)
    assert passed.status == GateStatus.PASS
    assert passed.independent_reviewer == "reviewer"

    missing = evidence_gate(
        [record for record in records if record.kind != EvidenceKind.DATASET_MANIFEST]
    )
    assert missing.status == GateStatus.FAIL
    assert EvidenceKind.DATASET_MANIFEST in missing.missing

    summary_only = _complete_evidence(
        metric_payload={"deterministic": True, "summary_only": True, "value": 12.3}
    )
    assert "raw values" in " ".join(evidence_gate(summary_only).reasons)

    tampered = list(records)
    tampered[0] = tampered[0].model_copy(update={"artifact_digest": "0" * 64})
    assert "artifact digest mismatch" in " ".join(evidence_gate(tampered).reasons)

    self_review = _complete_evidence(
        review_payload={
            "reviewer_id": "runtime",
            "reviewed_producers": ["runtime"],
            "independent": True,
            "verdict": "PASS",
        },
        metric_payload={
            "deterministic": True,
            "raw_samples": {"baseline": [1.0], "candidate": [2.0]},
        },
    )
    assert "independent PASS review" in " ".join(evidence_gate(self_review).reasons)


def test_evidence_gate_rejects_forged_reviewer_identity_and_partial_coverage() -> None:
    metric = {
        "deterministic": True,
        "summary_only": False,
        "raw_samples": {"baseline": [1.0], "candidate": [2.0]},
    }
    forged = _complete_evidence(
        review_payload={
            "reviewer_id": "trusted-reviewer",
            "reviewed_producers": ["runtime", "architect", "scout", "evaluator"],
            "independent": True,
            "verdict": "PASS",
        },
        metric_payload=metric,
    )
    forged[-1] = forged[-1].model_copy(update={"producer_id": "runtime"})
    assert evidence_gate(forged).status == GateStatus.FAIL

    partial = _complete_evidence(metric_payload=metric)
    partial = [
        record.model_copy(update={"producer_id": "trace-agent"})
        if record.kind == EvidenceKind.TRACE
        else record
        for record in partial
    ]
    # The review payload omits trace-agent, so it has not covered every non-review producer.
    assert evidence_gate(partial).status == GateStatus.FAIL


def test_evaluator_is_deterministic_and_thresholded() -> None:
    kwargs = dict(
        metric="MPJPE",
        baseline=[40.0, 42.0, 41.0, 43.0],
        candidate=[41.0, 43.0, 42.0, 44.0],
        direction="lower_better",
        threshold=0.05,
        seed=77,
        iterations=500,
    )
    first = evaluate_paired_metric(**kwargs)
    second = evaluate_paired_metric(**kwargs)
    assert first == second
    assert first.verdict == "PASS"
    assert first.ci95 == [1.0, 1.0]
    assert first.relative_delta == pytest.approx(1.0 / 41.5, abs=1e-6)


def test_memory_scoring_is_bounded_and_requires_passed_review() -> None:
    signals = MemorySignals(semantic=1.0, component=0.5, evidence=0.8, recency=0.4, failure=0.9)
    assert score_memory(signals) == 0.8
    with pytest.raises(ValidationError):
        MemorySignals(semantic=1.1, component=0, evidence=0, recency=0, failure=0)

    failed = GateResult(status=GateStatus.FAIL, present=[], missing=[], reasons=["missing"])
    with pytest.raises(PolicyError) as caught:
        require_validated_memory(failed, "reviewer")
    assert caught.value.code == "memory_not_validated"
