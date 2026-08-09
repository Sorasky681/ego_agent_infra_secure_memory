"""Application service implementing the deterministic ResearchOps workflow."""

import hashlib
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional

from .errors import ConflictError, PolicyError
from .evaluator import evaluate_paired_metric
from .evidence import REQUIRED_FOR_DECISION, evidence_gate
from .memory import MEMORY_WEIGHTS, require_validated_memory, score_memory
from .models import (
    AcceptanceMetric,
    ApprovalPublic,
    ApprovalRecord,
    ApprovalStatus,
    CandidateArm,
    EvidenceKind,
    EvidenceRecord,
    GateStatus,
    IntegrationState,
    IntegrationTruth,
    MemoryRecord,
    MemorySignals,
    ResearchGoal,
    RiskLevel,
    RunManifest,
    Stage,
    TaskRecord,
    utc_now,
)
from .policy import (
    ActionIntent,
    build_approval,
    classify_action,
    consume_approval,
    decide_approval,
    validate_approval_token,
)
from .provenance import canonical_sha256, run_manifest_digest, text_sha256
from .state_machine import next_forward_stage, progress_for, validate_transition
from .store import SQLiteStore


DEMO_TASK_ID = "ego-lite-001"
SYNTHETIC_NOTICE = (
    "SYNTHETIC DEMO DATA — metrics and artifacts exercise the control plane; "
    "they are not claims from a physical GPU run."
)
GPU_APPROVAL_ACTION = "gpu.launch_experiment"
GPU_CONFIG_RELATIVE_PATH = "apps/api/fixtures/egolite-mcp-launch.yaml"
GPU_ROLLBACK_POINT = (
    "Restore the baseline-ltx configuration pointer and preserve all failed-run evidence."
)

AGENT_ROLES = [
    ("research-pi", "Research PI", "Decomposes goals and owns state transitions"),
    ("scout-agent", "Scout", "Freezes repository, dataset, and prior-failure context"),
    ("architect-agent", "Experiment Architect", "Creates bounded experiment plans"),
    ("runtime-agent", "Runtime", "Executes allowlisted experiment entrypoints"),
    ("evaluation-agent", "Evaluator", "Computes deterministic metrics and bootstrap CIs"),
    ("reviewer-agent", "Independent Reviewer", "Challenges design and verifies evidence"),
    ("memory-agent", "Memory Curator", "Writes only independently validated memories"),
]


STAGE_AGENT = {
    Stage.INTAKE: "research-pi",
    Stage.CONTEXT: "scout-agent",
    Stage.PLAN: "architect-agent",
    Stage.PLAN_REVIEW: "reviewer-agent",
    Stage.APPROVAL: "human",
    Stage.EXECUTE: "runtime-agent",
    Stage.OBSERVE: "runtime-agent",
    Stage.EVALUATE: "evaluation-agent",
    Stage.VERIFY: "reviewer-agent",
    Stage.DECIDE: "research-pi",
    Stage.ARCHIVE: "research-pi",
    Stage.MEMORY_SKILL: "memory-agent",
    Stage.COMPLETED: "research-pi",
}


class ResearchOpsService:
    def __init__(
        self, store: SQLiteStore, approval_hmac_secret: Optional[str] = None
    ) -> None:
        self.store = store
        self.approval_hmac_secret = (
            approval_hmac_secret
            if approval_hmac_secret is not None
            else os.getenv("EGO_MCP_APPROVAL_HMAC_SECRET")
        )
        if self.approval_hmac_secret and len(self.approval_hmac_secret.encode("utf-8")) < 32:
            raise PolicyError(
                "approval_secret_too_short",
                "EGO_MCP_APPROVAL_HMAC_SECRET must contain at least 32 bytes",
            )
        self.ensure_demo()

    def ensure_demo(self) -> None:
        with self.store.transaction():
            if not self.store.list_tasks():
                self.reset_demo("happy_path")

    @staticmethod
    def _goal() -> ResearchGoal:
        return ResearchGoal(
            objective=(
                "Replace EgoLite-PWM's heavy video representation with streaming perception "
                "while reaching at least 10 FPS and limiting MPJPE degradation to 5%."
            ),
            hardware="8x RTX 4090 (declared target; no live GPU attached in local demo)",
            constraints={
                "fps_target": 10.0,
                "max_mpjpe_relative_degradation": 0.05,
                "fixed_split": "egolite-demo-v1",
                "seeds": [17, 23, 42],
                "compute_budget_gpu_hours": 24,
                "execution_boundary": "allowlisted entrypoints only",
            },
            acceptance_metrics=[
                AcceptanceMetric(
                    name="FPS",
                    direction="higher_better",
                    threshold=10.0,
                    unit="frames/second",
                    rule="candidate mean >= 10 FPS",
                ),
                AcceptanceMetric(
                    name="MPJPE",
                    direction="lower_better",
                    threshold=0.05,
                    unit="millimetres",
                    rule="relative candidate degradation <= 5%",
                ),
            ],
            candidate_arms=[
                CandidateArm(
                    id="baseline-ltx",
                    name="LTX2B baseline",
                    description="Frozen baseline; included for comparison only",
                ),
                CandidateArm(
                    id="mobilenetv4-tsm-gru",
                    name="MobileNetV4 + causal TSM + GRU",
                    description="Primary streaming candidate",
                ),
                CandidateArm(
                    id="movinet-a0",
                    name="MoViNet A0 Stream",
                    description="Native streaming comparison arm",
                ),
                CandidateArm(
                    id="repvit-tsm-gru",
                    name="RepViT + causal TSM + GRU",
                    description="Efficient transformer comparison arm",
                ),
            ],
        )

    def reset_demo(
        self, scenario: Literal["happy_path", "insufficient_evidence"] = "happy_path"
    ) -> Dict[str, Any]:
        with self.store.transaction():
            now = utc_now()
            generation = "gen_%s" % uuid.uuid4().hex[:12]
            task = TaskRecord(
                id=DEMO_TASK_ID,
                generation=generation,
                title="EgoLite Streaming Perception",
                objective=self._goal().objective,
                stage=Stage.INTAKE,
                risk_level=RiskLevel.R2,
                goal=self._goal(),
                scenario=scenario,
                synthetic_demo=True,
                data_notice=SYNTHETIC_NOTICE,
                owner_agent="research-pi",
                current_agent="research-pi",
                created_at=now,
                updated_at=now,
            )
            self.store.upsert_seed_task(task)
            self.store.append_event(
                task.id,
                task.generation,
                "demo.reset",
                "system",
                Stage.INTAKE,
                {
                    "scenario": scenario,
                    "synthetic": True,
                    "notice": SYNTHETIC_NOTICE,
                    "audit_history_policy": "prior generations remain append-only",
                },
            )
            self.store.append_event(
                task.id,
                task.generation,
                "research.goal.frozen",
                "research-pi",
                Stage.INTAKE,
                {"goal_digest": canonical_sha256(task.goal), "objective": task.objective},
            )
            return {"reset": True, "task": self.task_view(task)}

    @staticmethod
    def _approval_public(approval: Optional[ApprovalRecord]) -> Optional[Dict[str, Any]]:
        if approval is None:
            return None
        public = ApprovalPublic.model_validate(approval.model_dump(exclude={"token_hash"}))
        return public.model_dump(mode="json")

    @staticmethod
    def _status(task: TaskRecord, approval: Optional[ApprovalRecord]) -> str:
        if task.stage == Stage.COMPLETED:
            return "completed"
        if task.stage == Stage.APPROVAL:
            if approval and approval.status == ApprovalStatus.DENIED:
                return "blocked"
            return "waiting_approval"
        if task.stage == Stage.VERIFY and task.gate_result.status == GateStatus.FAIL:
            return "blocked"
        return "running"

    def task_view(self, task: TaskRecord, include_evidence: bool = True) -> Dict[str, Any]:
        approval = self.store.latest_approval(task.id, task.generation)
        evidence = self.store.list_evidence(task.id, task.generation)
        memories = self.store.list_memories(task.id, task.generation) if include_evidence else []
        approval_view = self._approval_public(approval)
        task_payload = task.model_dump(mode="json")
        task_payload.update(
            {
                "status": self._status(task, approval),
                "progress": progress_for(task.stage),
                "pending_approval": approval_view if task.stage == Stage.APPROVAL else None,
                "approval": approval_view,
                "evidence_summary": {
                    "required": sorted(kind.value for kind in REQUIRED_FOR_DECISION),
                    "present": sorted({record.kind.value for record in evidence}),
                    "missing": sorted(
                        kind.value
                        for kind in REQUIRED_FOR_DECISION - {record.kind for record in evidence}
                    ),
                    "gate_status": task.gate_result.status.value,
                    "independent_reviewer": task.gate_result.independent_reviewer,
                },
                "evidence": (
                    [record.model_dump(mode="json") for record in evidence]
                    if include_evidence
                    else []
                ),
                "memories": [record.model_dump(mode="json") for record in memories],
            }
        )
        return task_payload

    def list_tasks(self) -> List[Dict[str, Any]]:
        return [self.task_view(task, include_evidence=False) for task in self.store.list_tasks()]

    def get_task(self, task_id: str) -> Dict[str, Any]:
        return self.task_view(self.store.get_task(task_id))

    def _add_evidence(
        self,
        task: TaskRecord,
        kind: EvidenceKind,
        producer_id: str,
        payload: Dict[str, Any],
    ) -> EvidenceRecord:
        artifact_digest = canonical_sha256(payload)
        record = EvidenceRecord(
            id="evd_%s" % uuid.uuid4().hex,
            task_id=task.id,
            generation=task.generation,
            kind=kind,
            producer_id=producer_id,
            artifact_digest=artifact_digest,
            payload=payload,
            synthetic=task.synthetic_demo,
        )
        self.store.add_evidence(record)
        self.store.append_event(
            task.id,
            task.generation,
            "evidence.recorded",
            producer_id,
            task.stage,
            {
                "evidence_id": record.id,
                "kind": kind.value,
                "artifact_digest": artifact_digest,
                "synthetic": task.synthetic_demo,
            },
        )
        return record

    def _enter_context(self, task: TaskRecord) -> None:
        manifest = {
            "dataset_id": "egolite-demo-v1",
            "split": "frozen-demo-split",
            "records": 24,
            "content_hash_policy": "publish-time full hash; runtime manifest verification",
            "immutable": True,
            "synthetic": True,
            "notice": SYNTHETIC_NOTICE,
        }
        self._add_evidence(task, EvidenceKind.DATASET_MANIFEST, "scout-agent", manifest)

    def _enter_plan(self, task: TaskRecord) -> None:
        config = {
            "schema": "experiment-plan/v1",
            "candidate": "mobilenetv4-tsm-gru",
            "baseline": "baseline-ltx",
            "seeds": [17, 23, 42],
            "gpu_ids": list(range(8)),
            "expected_gpu_hours": 24,
            "metrics": ["FPS", "MPJPE", "latency", "VRAM"],
            "arbitrary_shell": False,
            "synthetic": True,
        }
        self._add_evidence(task, EvidenceKind.CONFIG, "architect-agent", config)

    def _enter_plan_review(self, task: TaskRecord) -> None:
        configs = [
            record
            for record in self.store.list_evidence(task.id, task.generation)
            if record.kind == EvidenceKind.CONFIG
        ]
        if not configs:
            raise ConflictError(
                "plan_evidence_missing",
                "PLAN_REVIEW requires an architect-produced config artifact",
            )
        plan = configs[-1]
        self.store.append_event(
            task.id,
            task.generation,
            "plan.review.passed",
            "reviewer-agent",
            Stage.PLAN_REVIEW,
            {
                "phase": "plan",
                "independent": True,
                "reviewed_producer": plan.producer_id,
                "config_evidence_id": plan.id,
                "config_digest": plan.artifact_digest,
                "verdict": "PASS",
                "findings": [
                    {
                        "severity": "WARN",
                        "code": "modeled_compute_requires_human_approval",
                    }
                ],
                "synthetic": task.synthetic_demo,
            },
        )

    def _approval_contract(self, task: TaskRecord) -> Dict[str, Any]:
        config_path = Path(__file__).resolve().parent / "fixtures" / "egolite-mcp-launch.yaml"
        config_sha256 = hashlib.sha256(config_path.read_bytes()).hexdigest()
        payload = {
            "experiment_id": "%s-%s" % (task.id, task.generation),
            "idempotency_key": "launch-%s" % task.generation,
            "entrypoint": "benchmark_stream",
            "config_path": GPU_CONFIG_RELATIVE_PATH,
            "config_sha256": config_sha256,
            "gpu_ids": list(range(8)),
            "seed": 42,
            "expected_gpu_hours": 24.0,
            "tags": ["synthetic", "ego-lite"],
        }
        scope = "gpu.launch:%s:%s" % (payload["experiment_id"], payload["idempotency_key"])
        digest = canonical_sha256({"action": GPU_APPROVAL_ACTION, "payload": payload})
        return {
            "scope": scope,
            "action": GPU_APPROVAL_ACTION,
            "digest": digest,
            "config_sha256": config_sha256,
            "action_payload": payload,
        }

    def _enter_approval(self, task: TaskRecord) -> ApprovalRecord:
        intent = ActionIntent(
            name=GPU_APPROVAL_ACTION,
            mutates_sandbox=True,
            gpu_count=8,
            expected_gpu_hours=24,
        )
        policy = classify_action(intent)
        contract = self._approval_contract(task)
        approval = build_approval(
            approval_id="apr_%s" % uuid.uuid4().hex,
            task_id=task.id,
            generation=task.generation,
            risk_level=policy.risk_level,
            scope=contract["scope"],
            action=contract["action"],
            digest=contract["digest"],
            config_sha256=contract["config_sha256"],
            action_payload=contract["action_payload"],
            rollback_point=GPU_ROLLBACK_POINT,
            ttl_seconds=900,
        )
        self.store.add_approval(approval)
        self.store.append_event(
            task.id,
            task.generation,
            "approval.requested",
            "research-pi",
            Stage.APPROVAL,
            {
                "approval_id": approval.id,
                "risk_level": policy.risk_level.value,
                "action_digest": approval.action_digest,
                "scope": approval.scope,
                "config_sha256": approval.config_sha256,
                "rollback_point": approval.rollback_point,
                "expires_at": approval.expires_at.isoformat(),
                "reasons": policy.reasons,
            },
        )
        return approval

    def _enter_execute(self, task: TaskRecord) -> None:
        code: Dict[str, Any] = {
            "repository": "ego-lite-demo",
            "commit": text_sha256("synthetic-commit:%s" % task.generation)[:40],
            "entrypoint": "benchmark_stream",
            "allowlisted": True,
            "patch_summary": "Wire MobileNetV4 + causal TSM + GRU candidate arm",
            "synthetic": True,
            "notice": SYNTHETIC_NOTICE,
        }
        code_record = self._add_evidence(task, EvidenceKind.CODE, "runtime-agent", code)
        records = self.store.list_evidence(task.id, task.generation)
        config_digest = next(
            record.artifact_digest for record in records if record.kind == EvidenceKind.CONFIG
        )
        dataset_digest = next(
            record.artifact_digest
            for record in records
            if record.kind == EvidenceKind.DATASET_MANIFEST
        )
        manifest = RunManifest(
            git_commit=code["commit"],
            config_sha256=config_digest,
            dataset_manifest_sha256=dataset_digest,
            environment_lock_sha256=text_sha256("python3.9-fastapi-demo-lock"),
            base_model_sha256=text_sha256("synthetic-ltx2b-baseline"),
            seed=42,
        )
        task.run_manifest_digest = run_manifest_digest(manifest)
        self.store.append_event(
            task.id,
            task.generation,
            "experiment.submitted",
            "runtime-agent",
            Stage.EXECUTE,
            {
                "entrypoint": "benchmark_stream",
                "run_manifest": manifest.model_dump(mode="json"),
                "run_manifest_digest": task.run_manifest_digest,
                "code_evidence_id": code_record.id,
                "execution": "synthetic_demo",
            },
        )

    def _enter_observe(self, task: TaskRecord) -> None:
        log_payload = {
            "run_id": "run_%s" % task.generation,
            "exit_code": 0,
            "records": [
                {"step": 1, "gpu_util_pct": 18, "cpu_util_pct": 94},
                {"step": 2, "gpu_util_pct": 71, "cpu_util_pct": 68},
            ],
            "diagnosis": "publish-time dataset hash avoids repeated runtime hashing",
            "synthetic": True,
            "notice": SYNTHETIC_NOTICE,
        }
        self._add_evidence(task, EvidenceKind.LOG, "runtime-agent", log_payload)
        if task.scenario != "insufficient_evidence":
            trace_payload = {
                "trace_id": "trace_%s" % task.generation,
                "root_span": "research.task",
                "spans": [
                    "agent.route",
                    "skill.invoke",
                    "experiment.submit",
                    "experiment.monitor",
                ],
                "run_manifest_digest": task.run_manifest_digest,
                "synthetic": True,
            }
            self._add_evidence(task, EvidenceKind.TRACE, "runtime-agent", trace_payload)

    def _enter_evaluate(self, task: TaskRecord) -> None:
        fps_baseline = [2.7, 2.9, 2.8, 2.6, 3.0, 2.8, 2.9, 2.7]
        fps_candidate = [11.2, 11.8, 11.5, 11.1, 12.0, 11.7, 11.6, 11.4]
        mpjpe_baseline = [43.1, 42.8, 43.4, 42.9, 43.0, 43.3, 42.7, 43.2]
        mpjpe_candidate = [44.2, 43.9, 44.5, 44.0, 44.1, 44.3, 43.8, 44.2]
        evaluations = [
            evaluate_paired_metric(
                "FPS",
                fps_baseline,
                fps_candidate,
                "higher_better",
                10.0,
                seed=2025,
            ),
            evaluate_paired_metric(
                "MPJPE",
                mpjpe_baseline,
                mpjpe_candidate,
                "lower_better",
                0.05,
                seed=2025,
            ),
        ]
        task.latest_evaluation = evaluations
        metric_payload = {
            "evaluator": "paired_bootstrap/v1",
            "deterministic": True,
            "summary_only": False,
            "raw_samples": {
                "FPS": {"baseline": fps_baseline, "candidate": fps_candidate},
                "MPJPE": {"baseline": mpjpe_baseline, "candidate": mpjpe_candidate},
            },
            "results": [result.model_dump(mode="json") for result in evaluations],
            "synthetic": True,
            "notice": SYNTHETIC_NOTICE,
        }
        self._add_evidence(task, EvidenceKind.METRIC, "evaluation-agent", metric_payload)

    def _enter_verify(self, task: TaskRecord) -> None:
        review_payload = {
            "reviewer_id": "reviewer-agent",
            "reviewed_producers": [
                "architect-agent",
                "evaluation-agent",
                "runtime-agent",
                "scout-agent",
            ],
            "independent": True,
            "verdict": "PASS",
            "checks": {
                "split_frozen": True,
                "raw_metrics_present": True,
                "leakage_detected": False,
                "run_manifest_bound": True,
            },
            "synthetic": True,
        }
        self._add_evidence(task, EvidenceKind.REVIEW, "reviewer-agent", review_payload)

    def _refresh_gate(self, task: TaskRecord) -> None:
        task.gate_result = evidence_gate(self.store.list_evidence(task.id, task.generation))
        event_type = (
            "evidence.gate.passed"
            if task.gate_result.status == GateStatus.PASS
            else "evidence.gate.failed"
        )
        self.store.append_event(
            task.id,
            task.generation,
            event_type,
            "reviewer-agent",
            Stage.VERIFY,
            task.gate_result.model_dump(mode="json"),
        )

    def _enter_decide(self, task: TaskRecord) -> None:
        task.decision = (
            "KEEP"
            if task.latest_evaluation
            and all(result.verdict == "PASS" for result in task.latest_evaluation)
            else "INCONCLUSIVE"
        )
        self.store.append_event(
            task.id,
            task.generation,
            "decision.committed",
            "research-pi",
            Stage.DECIDE,
            {
                "decision": task.decision,
                "gate_status": task.gate_result.status.value,
                "run_manifest_digest": task.run_manifest_digest,
            },
        )

    def _enter_memory(self, task: TaskRecord) -> None:
        review_id = task.gate_result.independent_reviewer or ""
        require_validated_memory(task.gate_result, review_id)
        evidence = self.store.list_evidence(task.id, task.generation)
        evidence_digest = canonical_sha256(sorted(record.artifact_digest for record in evidence))
        records = [
            MemoryRecord(
                id="mem_%s" % uuid.uuid4().hex,
                task_id=task.id,
                generation=task.generation,
                memory_type="episodic",
                statement=(
                    "Repeated runtime dataset hashing can starve GPU workers; this demo trace "
                    "is synthetic and must be replaced by physical-run evidence before reuse."
                ),
                component="dataset-loader",
                evidence_digest=evidence_digest,
                review_id=review_id,
                validated=True,
            ),
            MemoryRecord(
                id="mem_%s" % uuid.uuid4().hex,
                task_id=task.id,
                generation=task.generation,
                memory_type="procedural",
                statement=(
                    "Hash immutable datasets once at publish time and verify the canonical, "
                    "content-addressed manifest at training startup."
                ),
                component="dataset-manifest",
                evidence_digest=evidence_digest,
                review_id=review_id,
                validated=True,
            ),
        ]
        for record in records:
            self.store.add_memory(record)
            self.store.append_event(
                task.id,
                task.generation,
                "memory.validated",
                "memory-agent",
                Stage.MEMORY_SKILL,
                {
                    "memory_id": record.id,
                    "type": record.memory_type,
                    "evidence_digest": evidence_digest,
                    "review_id": review_id,
                },
            )

    def _transition(
        self, task: TaskRecord, target: Stage, approval_token: Optional[str]
    ) -> Dict[str, Any]:
        source = task.stage
        validate_transition(source, target)

        if source == Stage.APPROVAL and target == Stage.EXECUTE:
            approval = self.store.latest_approval(task.id, task.generation)
            if approval is None:
                raise PolicyError("approval_required", "No approval exists for this experiment")
            contract = self._approval_contract(task)
            validate_approval_token(
                approval,
                approval_token,
                task.id,
                task.generation,
                contract["scope"],
                contract["action"],
                contract["digest"],
            )
            consume_approval(approval)
            self.store.save_approval(approval)
            self.store.append_event(
                task.id,
                task.generation,
                "approval.token.consumed",
                approval.approver or "human",
                Stage.APPROVAL,
                {"approval_id": approval.id, "single_use": True, "scope": approval.scope},
            )

        if source == Stage.VERIFY and target == Stage.DECIDE:
            self._refresh_gate(task)
            if task.gate_result.status != GateStatus.PASS:
                old_version = task.version
                task.version += 1
                task.updated_at = utc_now()
                self.store.save_task(task, expected_version=old_version)
                raise ConflictError(
                    "evidence_gate_failed",
                    "Decision is blocked until all raw evidence and independent review are present",
                    task.gate_result.model_dump(mode="json"),
                )

        task.stage = target
        task.current_agent = STAGE_AGENT[target]

        if target == Stage.CONTEXT:
            self._enter_context(task)
        elif target == Stage.PLAN:
            self._enter_plan(task)
        elif target == Stage.PLAN_REVIEW:
            self._enter_plan_review(task)
        elif target == Stage.APPROVAL:
            self._enter_approval(task)
        elif target == Stage.EXECUTE:
            self._enter_execute(task)
        elif target == Stage.OBSERVE:
            self._enter_observe(task)
        elif target == Stage.EVALUATE:
            self._enter_evaluate(task)
        elif target == Stage.VERIFY:
            self._enter_verify(task)
        elif target == Stage.DECIDE:
            self._enter_decide(task)
        elif target == Stage.MEMORY_SKILL:
            self._enter_memory(task)

        old_version = task.version
        task.version += 1
        task.updated_at = utc_now()
        self.store.save_task(task, expected_version=old_version)
        self.store.append_event(
            task.id,
            task.generation,
            "state.transitioned",
            STAGE_AGENT[target],
            target,
            {"from": source.value, "to": target.value, "task_version": task.version},
        )
        return {"from": source.value, "to": target.value}

    def advance(
        self,
        task_id: str,
        target: Optional[Stage] = None,
        approval_token: Optional[str] = None,
    ) -> Dict[str, Any]:
        blocked_error: Optional[ConflictError] = None
        with self.store.transaction():
            task = self.store.get_task(task_id)
            destination = target or next_forward_stage(task.stage)
            if destination is None:
                raise ConflictError(
                    "terminal_task", "Task is already at a terminal stage", {"task_id": task.id}
                )
            try:
                transition = self._transition(task, destination, approval_token)
                response = {"transition": transition, "task": self.task_view(task)}
            except ConflictError as error:
                if error.code != "evidence_gate_failed":
                    raise
                blocked_error = error
                response = {}
        if blocked_error is not None:
            raise blocked_error
        return response

    def autorun(self, task_id: str, approval_token: Optional[str] = None) -> Dict[str, Any]:
        transitions: List[Dict[str, Any]] = []
        token = approval_token
        with self.store.transaction():
            for _ in range(20):
                task = self.store.get_task(task_id)
                if task.stage == Stage.COMPLETED:
                    return {
                        "status": "completed",
                        "paused_reason": None,
                        "transitions": transitions,
                        "task": self.task_view(task),
                    }
                if task.stage == Stage.APPROVAL and not token:
                    return {
                        "status": "paused",
                        "paused_reason": "human_approval_required",
                        "transitions": transitions,
                        "task": self.task_view(task),
                    }
                destination = next_forward_stage(task.stage)
                if destination is None:
                    raise ConflictError("terminal_task", "Task has no forward transition")
                try:
                    transitions.append(self._transition(task, destination, token))
                    if task.stage == Stage.EXECUTE:
                        token = None
                except ConflictError as error:
                    if error.code != "evidence_gate_failed":
                        raise
                    blocked = self.store.get_task(task_id)
                    return {
                        "status": "paused",
                        "paused_reason": "insufficient_evidence",
                        "transitions": transitions,
                        "gate": blocked.gate_result.model_dump(mode="json"),
                        "task": self.task_view(blocked),
                    }
            raise ConflictError(
                "autorun_iteration_limit", "Autorun stopped after reaching its safety iteration cap"
            )

    def decide_approval(
        self, approval_id: str, decision: str, approver: str, expected_digest: str
    ) -> Dict[str, Any]:
        with self.store.transaction():
            approval = self.store.get_approval(approval_id)
            task = self.store.get_task(approval.task_id)
            if task.generation != approval.generation:
                raise PolicyError(
                    "stale_approval",
                    "Approval belongs to an earlier task generation",
                    {
                        "approval_generation": approval.generation,
                        "current_generation": task.generation,
                    },
                )
            try:
                approval, raw_token = decide_approval(
                    approval,
                    decision,
                    approver,
                    expected_digest,
                    hmac_secret=self.approval_hmac_secret,
                )
            except PolicyError:
                if approval.status == ApprovalStatus.EXPIRED:
                    self.store.save_approval(approval)
                raise
            self.store.save_approval(approval)
            self.store.append_event(
                task.id,
                task.generation,
                "approval.%s" % decision,
                approver,
                Stage.APPROVAL,
                {
                    "approval_id": approval.id,
                    "action_digest": approval.action_digest,
                    "risk_level": approval.risk_level.value,
                    "token_issued": raw_token is not None,
                },
            )
            return {
                "approval": self._approval_public(approval),
                "approval_token": raw_token,
                "token_notice": (
                    "Returned once for this response; token is scope-bound and single-use."
                    if raw_token
                    else None
                ),
            }

    def events(self, task_id: str, after_sequence: int = 0, limit: int = 200) -> Dict[str, Any]:
        task = self.store.get_task(task_id)
        events = self.store.list_events(task.id, task.generation, after_sequence, limit)
        return {
            "task_id": task.id,
            "generation": task.generation,
            "append_only": True,
            "hash_chained": True,
            "chain_valid": self.store.verify_event_chain(task.id, task.generation),
            "events": [event.model_dump(mode="json") for event in events],
            "next_after_sequence": events[-1].sequence if events else after_sequence,
        }

    @staticmethod
    def integrations() -> Dict[str, Any]:
        now = datetime.now(timezone.utc)
        definitions = [
            ("hiclaw", "HiClaw / AgentTeams", "collaboration plane", "EGO_HICLAW_URL"),
            ("nacos", "Nacos Skill Registry", "skill registry adapter", "EGO_NACOS_URL"),
            ("higress", "Higress", "credential and MCP gateway", "EGO_HIGRESS_URL"),
        ]
        states = []
        for integration_id, name, role, environment_key in definitions:
            configured = bool(os.getenv(environment_key))
            state = IntegrationState(
                id=integration_id,
                name=name,
                role=role,
                status=(
                    IntegrationTruth.CONFIGURED_UNVERIFIED
                    if configured
                    else IntegrationTruth.NOT_CONFIGURED
                ),
                endpoint_configured=configured,
                checked_at=now,
                detail=(
                    "Endpoint is configured, but this local adapter does not claim a live handshake."
                    if configured
                    else "Optional external adapter is not configured; deterministic local mode remains available."
                ),
            )
            states.append(state.model_dump(mode="json"))
        return {
            "mode": "adapter_metadata_only",
            "truth_policy": (
                "No external integration is reported ready without a verified handshake. "
                "This build intentionally performs no fake cloud calls."
            ),
            "items": states,
        }

    def health(self) -> Dict[str, Any]:
        database_ready = self.store.ping()
        return {
            "status": "ok" if database_ready else "degraded",
            "service": "egoagentos-researchops-api",
            "version": "0.1.0",
            "time": utc_now().isoformat(),
            "mode": "deterministic-local",
            "database": {
                "status": "ready" if database_ready else "unavailable",
                "engine": "sqlite",
                "path": self.store.db_path,
                "audit_events": "append_only_hash_chain",
            },
            "external_integrations": self.integrations(),
        }

    def dashboard(self) -> Dict[str, Any]:
        tasks = self.store.list_tasks()
        active_stages = {task.current_agent for task in tasks if task.stage != Stage.COMPLETED}
        agents = [
            {
                "id": agent_id,
                "name": name,
                "role": role,
                "status": "active" if agent_id in active_stages else "idle",
            }
            for agent_id, name, role in AGENT_ROLES
        ]
        demo = self.store.get_task(DEMO_TASK_ID)
        recent = self.store.recent_events(20, demo.id, demo.generation)
        return {
            "system": {
                "name": "EgoAgentOS ResearchOps",
                "status": "operational" if self.store.ping() else "degraded",
                "mode": "deterministic-local",
                "principle": "Deterministic Core + LLM Residual",
            },
            "demo": {
                "task_id": DEMO_TASK_ID,
                "generation": demo.generation,
                "synthetic": True,
                "notice": SYNTHETIC_NOTICE,
                "scenario": demo.scenario,
            },
            "active_task_id": demo.id,
            "counts": self.store.counts(),
            "tasks": [self.task_view(task, include_evidence=False) for task in tasks],
            "agents": agents,
            "risk_policy": {
                "R0": "read-only; automatic",
                "R1": "bounded sandbox mutation; automatic",
                "R2": "expensive or significant mutation; human approval",
                "R3": "critical action; approval + rollback point + audit",
            },
            "evidence_gate": {
                "required": sorted(kind.value for kind in REQUIRED_FOR_DECISION),
                "independent_review_required": True,
                "llm_summary_counts_as_metric": False,
            },
            "memory_scoring": {
                "weights": MEMORY_WEIGHTS,
                "example_score": score_memory(
                    MemorySignals(
                        semantic=0.9,
                        component=1.0,
                        evidence=0.95,
                        recency=0.8,
                        failure=1.0,
                    )
                ),
            },
            "activity": [event.model_dump(mode="json") for event in recent],
            "integrations": self.integrations(),
        }
