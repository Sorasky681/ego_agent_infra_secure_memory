"""Durable, fail-closed orchestration across real AgentTeams services."""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple

from pydantic import ValidationError

from .clients import AgentTeamsClient, EgoClient, MatrixClient
from .errors import BridgeError, LiveAgentTeamsUnavailable
from .models import (
    OFFICIAL_MAIN_COMMIT,
    BridgeRun,
    CollaborationEnvelope,
    EnvelopeKind,
    GrantRequest,
    ReconcileResult,
    ResearchTaskSpec,
    RunState,
    SkillEvidence,
    SkillEvidenceLevel,
    StartRunRequest,
    TaskDetail,
    WorkerResultEnvelope,
    WorkflowResponse,
    canonical_sha256,
    utc_now,
)
from .store import BridgeStore


PRE_APPROVAL_STAGES = {"CONTEXT", "PLAN", "PLAN_REVIEW"}
POST_APPROVAL_STAGES = {
    "EXECUTE",
    "OBSERVE",
    "EVALUATE",
    "VERIFY",
    "MEMORY_SKILL",
}
TERMINAL_NODE_STATUSES = {"completed", "revision", "blocked"}
ACTIVE_NODE_STATUSES = {"delegated", "in-progress"}
SUCCESS_RESULT_STATUSES = {"SUCCESS", "SUCCESS_WITH_NOTES"}

ROLE_PLAN: Tuple[Tuple[str, str, str, Tuple[str, ...]], ...] = (
    ("context", "CONTEXT", "ego-scout", ("research-memory",)),
    ("plan", "PLAN", "ego-architect", ("research-plan", "ablation-analyzer")),
    ("plan-review", "PLAN_REVIEW", "ego-reviewer", ("evidence-gate",)),
    ("execute", "EXECUTE", "ego-runtime", ("safe-experiment-runner", "dataset-manifest")),
    ("observe", "OBSERVE", "ego-runtime", ("safe-experiment-runner",)),
    ("evaluate", "EVALUATE", "ego-evaluator", ("ablation-analyzer",)),
    ("verify", "VERIFY", "ego-reviewer", ("evidence-gate",)),
    ("memory", "MEMORY_SKILL", "ego-memory-curator", ("research-memory",)),
)


def _safe_project_id(task_id: str, context_version: int) -> str:
    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", task_id).strip("-._") or "task"
    digest = hashlib.sha256((task_id + ":" + str(context_version)).encode("utf-8")).hexdigest()[:8]
    return "ego-%s-v%d-%s" % (slug[:36], context_version, digest)


def _iso_now(clock: Callable[[], datetime]) -> str:
    return clock().astimezone(timezone.utc).isoformat()


def _status_to_raw(status: str) -> str:
    return {
        "pending": "planned",
        "delegated": "assigned",
        "in-progress": "in_progress",
        "completed": "completed",
        "revision": "revision",
        "blocked": "blocked",
    }.get(status, status)


class AgentTeamsBridge:
    def __init__(
        self,
        store: BridgeStore,
        agentteams: AgentTeamsClient,
        matrix: MatrixClient,
        ego: EgoClient,
        *,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        self.store = store
        self.agentteams = agentteams
        self.matrix = matrix
        self.ego = ego
        self.clock = clock

    def probe_live(self, team_name: str) -> Dict[str, Any]:
        if not self.matrix.token:
            raise LiveAgentTeamsUnavailable(
                "AGENTTEAMS_MATRIX_ACCESS_TOKEN is required for live dispatch"
            )
        if not self.agentteams.health():
            raise LiveAgentTeamsUnavailable("AgentTeams /healthz did not return ok")
        version = self.agentteams.version()
        project_api = self.agentteams.probe_project_api()
        matrix_identity = self.matrix.whoami()
        team = self.agentteams.get_team(team_name)
        if team.phase != "Active" or not team.leaderReady:
            raise LiveAgentTeamsUnavailable(
                "AgentTeams Team is not active with a ready Leader",
                details={
                    "team": team_name,
                    "phase": team.phase,
                    "leader_ready": team.leaderReady,
                    "ready_workers": team.readyWorkers,
                    "total_workers": team.totalWorkers,
                },
            )
        if team.readyWorkers < team.totalWorkers:
            raise LiveAgentTeamsUnavailable(
                "AgentTeams Team has non-ready members",
                details={
                    "team": team_name,
                    "ready_workers": team.readyWorkers,
                    "total_workers": team.totalWorkers,
                },
            )
        return {
            "live": True,
            "contract": {
                "repository": "https://github.com/agentscope-ai/AgentTeams",
                "main_commit": OFFICIAL_MAIN_COMMIT,
                "api_version": "agentteams.io/v1beta1",
                "required_endpoints": [
                    "GET /api/v1/projects",
                    "POST /api/v1/projects",
                    "POST /api/v1/projects/{id}/pause",
                    "POST /api/v1/projects/{id}/resume",
                    "POST /api/v1/projects/{id}/replan",
                    "POST /api/v1/projects/{id}/tasks/{taskId}/cancel",
                    "GET /api/v1/projects/{id}/workflow",
                    "GET /api/v1/projects/{id}/spawns",
                ],
            },
            "controller": version,
            "matrix": {
                "user_id": matrix_identity.get("user_id"),
                "device_id": matrix_identity.get("device_id"),
            },
            "project_count": project_api.get("total", len(project_api.get("projects", []))),
            "team": team.model_dump(mode="json"),
        }

    def _load_workers(
        self, team_name: str, worker_names: Iterable[str]
    ) -> Dict[str, Dict[str, Any]]:
        workers: Dict[str, Dict[str, Any]] = {}
        for name in sorted(set(worker_names)):
            worker = self.agentteams.ensure_worker_ready(name)
            if worker.phase not in {"Running", "Ready"}:
                raise LiveAgentTeamsUnavailable(
                    "AgentTeams Worker is not running",
                    details={"worker": name, "phase": worker.phase},
                )
            if worker.team and worker.team != team_name:
                raise LiveAgentTeamsUnavailable(
                    "AgentTeams Worker belongs to a different Team",
                    details={"worker": name, "expected_team": team_name, "actual": worker.team},
                )
            workers[name] = worker.model_dump(mode="json")
        return workers

    def _build_task_graph(
        self,
        project_id: str,
        workers: Dict[str, Dict[str, Any]],
        *,
        stages: Sequence[str],
    ) -> List[ResearchTaskSpec]:
        tasks: List[ResearchTaskSpec] = []
        previous: Optional[str] = None
        for suffix, stage, worker_name, skills in ROLE_PLAN:
            if stage not in stages:
                continue
            worker = workers[worker_name]
            task_id = "%s-%s" % (project_id, suffix)
            task = ResearchTaskSpec(
                task_id=task_id,
                title="%s · %s" % (stage, suffix.replace("-", " ").title()),
                stage=stage,
                assigned_worker=worker_name,
                assigned_to=str(worker["matrixUserID"]),
                depends_on=[previous] if previous else [],
                expected_skills=list(skills),
            )
            tasks.append(task)
            previous = task_id
        return tasks

    @staticmethod
    def _controller_tasks(tasks: Sequence[ResearchTaskSpec]) -> List[Dict[str, Any]]:
        return [
            {
                "taskId": task.task_id,
                "title": task.title,
                "assignedTo": task.assigned_to,
                "dependsOn": task.depends_on,
                "status": task.status,
            }
            for task in tasks
        ]

    def _envelope(
        self,
        run: BridgeRun,
        kind: EnvelopeKind,
        body: Dict[str, Any],
        *,
        attempt: int = 1,
        causation_id: Optional[str] = None,
    ) -> CollaborationEnvelope:
        return CollaborationEnvelope.build(
            task_id=run.ego_task_id,
            project_id=run.agentteams_project_id,
            trace_id=run.trace_id,
            correlation_id=run.correlation_id,
            context_version=run.context_version,
            kind=kind,
            sender="egoagentos-bridge",
            recipient="agentteams-team-leader",
            attempt=attempt,
            causation_id=causation_id,
            body=body,
        )

    def _leader_context(self, run: BridgeRun) -> Tuple[str, str]:
        team = self.agentteams.get_team(run.team)
        leader = self.agentteams.get_worker(team.leaderName)
        return team.teamRoomID, leader.matrixUserID

    def _send(self, run: BridgeRun, envelope: CollaborationEnvelope) -> str:
        room_id, leader_matrix_id = self._leader_context(run)
        event_id = self.matrix.send_envelope(
            room_id=room_id,
            leader_matrix_id=leader_matrix_id,
            envelope=envelope.model_dump(mode="json", by_alias=True),
            transaction_id=envelope.envelope_id,
        )
        self.store.append_event(run.id, envelope)
        return event_id

    @staticmethod
    def _task_request_body(
        run: BridgeRun, workflow: WorkflowResponse
    ) -> Dict[str, Any]:
        return {
            "objective": run.objective,
            "controller_workflow_sha256": canonical_sha256(
                workflow.model_dump(mode="json")
            ),
            "task_graph": [task.model_dump(mode="json") for task in run.task_graph],
            "execution_contract": {
                "runtime": "AgentTeams TeamHarness",
                "required_flow": [
                    "projectflow resolve_project",
                    "taskflow delegate_task",
                    "taskflow ack_task",
                    "taskflow submit_task",
                    "taskflow check_task",
                    "projectflow accept_task_result",
                ],
                "result_envelope_suffix": ".ego-envelope.json",
                "result_schema": "egoagentos.agentteams-result.v1",
                "no_chat_approval": True,
            },
        }

    def start_run(self, request: StartRunRequest) -> BridgeRun:
        project_id = _safe_project_id(request.ego_task_id, request.context_version)
        run_id = "atrun_%s" % uuid.uuid4().hex
        if request.mode == "dry_run":
            placeholder_workers = {
                worker: {"matrixUserID": "@%s:fixture.invalid" % worker}
                for _, _, worker, _ in ROLE_PLAN
            }
            graph = self._build_task_graph(
                project_id, placeholder_workers, stages=sorted(PRE_APPROVAL_STAGES)
            )
            return self.store.create_run(
                BridgeRun(
                    id=run_id,
                    ego_task_id=request.ego_task_id,
                    agentteams_project_id=project_id,
                    team=request.team,
                    trace_id=request.trace_id,
                    correlation_id=request.correlation_id,
                    context_version=request.context_version,
                    state=RunState.PROVISIONING,
                    mode="dry_run",
                    objective=request.objective,
                    task_graph=graph,
                    checkpoint={
                        "truth": "DRY_RUN_ONLY",
                        "live": False,
                        "reason": "No AgentTeams, Matrix, or EgoAgentOS request was made",
                    },
                    ack_timeout_seconds=request.ack_timeout_seconds,
                    execution_timeout_seconds=request.execution_timeout_seconds,
                    max_reassignments=request.max_reassignments,
                )
            )

        live_probe = self.probe_live(request.team)
        ego_task = self.ego.get_task(request.ego_task_id)
        if bool(ego_task.get("synthetic_demo")):
            raise BridgeError(
                "synthetic_task_rejected",
                "Live AgentTeams work cannot be attached to the synthetic EgoLite demo task",
                details={"task_id": request.ego_task_id},
            )
        team = self.agentteams.get_team(request.team)
        required_workers = [worker for _, _, worker, _ in ROLE_PLAN]
        required_workers.append(team.leaderName)
        workers = self._load_workers(request.team, required_workers)
        graph = self._build_task_graph(project_id, workers, stages=sorted(PRE_APPROVAL_STAGES))
        intent_digest = canonical_sha256(
            {
                "ego_task_id": request.ego_task_id,
                "project_id": project_id,
                "objective": request.objective,
                "context_version": request.context_version,
                "task_graph": [task.model_dump(mode="json") for task in graph],
            }
        )
        run = BridgeRun(
            id=run_id,
            ego_task_id=request.ego_task_id,
            agentteams_project_id=project_id,
            team=request.team,
            trace_id=request.trace_id,
            correlation_id=request.correlation_id,
            context_version=request.context_version,
            state=RunState.PROVISIONING,
            mode="live",
            objective=request.objective,
            task_graph=graph,
            checkpoint={
                "truth": "LIVE",
                "live_probe": live_probe,
                "workers": workers,
                "node_status": {},
                "accepted_contracts": {},
                "reassignments": {},
                "ego_grant_committed": False,
                "intent_digest": intent_digest,
                "bridge_api_version": "0.2.0",
                "official_main_commit": OFFICIAL_MAIN_COMMIT,
            },
            ack_timeout_seconds=request.ack_timeout_seconds,
            execution_timeout_seconds=request.execution_timeout_seconds,
            max_reassignments=request.max_reassignments,
        )
        create_response = self.agentteams.create_project(
            project_id=project_id,
            title="EgoAgentOS · %s" % request.objective[:120],
            team=request.team,
            requester="egoagentos:%s" % request.ego_task_id,
            source_room_id=team.teamRoomID,
        )
        checkpoint = dict(run.checkpoint)
        checkpoint["project_create_response_sha256"] = canonical_sha256(create_response)
        checkpoint["project_create_identifier"] = create_response.get("project_id")
        run = run.model_copy(update={"checkpoint": checkpoint})
        run = self.store.create_run(run)
        try:
            workflow = self.agentteams.replan(
                project_id, request.team, self._controller_tasks(graph)
            )
            envelope = self._envelope(
                run,
                EnvelopeKind.TASK_REQUEST,
                self._task_request_body(run, workflow),
            )
            matrix_event_id = self._send(run, envelope)
            checkpoint = dict(run.checkpoint)
            checkpoint["dispatch_matrix_event_id"] = matrix_event_id
            checkpoint["matrix_root"] = matrix_event_id
            checkpoint["last_workflow_sha256"] = canonical_sha256(
                workflow.model_dump(mode="json")
            )
            run = run.model_copy(update={"state": RunState.PRE_APPROVAL, "checkpoint": checkpoint})
            return self.store.update_run(run, expected_version=run.version)
        except Exception as error:
            try:
                self.agentteams.pause(project_id, request.team, "bridge dispatch failed")
            except Exception:
                pass
            checkpoint = dict(run.checkpoint)
            checkpoint["compensation_reason"] = str(error)
            checkpoint["compensation_retry"] = {
                "operation": "start-dispatch",
                "resume_state": RunState.PRE_APPROVAL.value,
                "token_required": False,
            }
            run = run.model_copy(
                update={"state": RunState.COMPENSATION_REQUIRED, "checkpoint": checkpoint}
            )
            self.store.update_run(run, expected_version=run.version)
            if isinstance(error, BridgeError):
                error.details.setdefault("bridge_run_id", run.id)
                error.details.setdefault("compensation_operation", "start-dispatch")
                raise
            raise BridgeError(
                "bridge_dispatch_failed",
                "AgentTeams project exists but live dispatch did not complete",
                status_code=502,
                retryable=True,
                details={
                    "bridge_run_id": run.id,
                    "compensation_operation": "start-dispatch",
                    "cause": str(error),
                },
            ) from error

    def get_run(self, run_id: str) -> BridgeRun:
        return self.store.get_run(run_id)

    @staticmethod
    def _detail_by_id(workflow: WorkflowResponse) -> Dict[str, TaskDetail]:
        return {detail.task_id: detail for detail in workflow.tasks_detail}

    @staticmethod
    def _deliverable_paths(detail: TaskDetail) -> List[str]:
        paths: List[str] = []
        for item in detail.deliverables:
            if isinstance(item, str):
                paths.append(item)
            elif isinstance(item, dict) and isinstance(item.get("path"), str):
                paths.append(item["path"])
        if detail.result_path:
            paths.append(detail.result_path)
        return paths

    def _validate_task_contract(
        self, run: BridgeRun, detail: TaskDetail
    ) -> Tuple[WorkerResultEnvelope, str]:
        paths = self._deliverable_paths(detail)
        envelope_paths = [path for path in paths if path.endswith(".ego-envelope.json")]
        if len(envelope_paths) != 1:
            raise BridgeError(
                "result_envelope_missing",
                "Completed AgentTeams task must declare exactly one .ego-envelope.json artifact",
                details={"task_id": detail.task_id, "deliverables": paths},
            )
        raw = self.agentteams.task_artifact(
            run.agentteams_project_id, run.team, detail.task_id, envelope_paths[0]
        )
        try:
            payload = json.loads(raw.decode("utf-8"))
            envelope = WorkerResultEnvelope.model_validate(payload)
        except (UnicodeDecodeError, json.JSONDecodeError, ValidationError) as error:
            raise BridgeError(
                "result_envelope_invalid",
                "AgentTeams result envelope is not valid JSON/schema",
                details={"task_id": detail.task_id, "error": str(error)},
            ) from error
        expected = {
            "task_id": detail.task_id,
            "project_id": run.agentteams_project_id,
            "trace_id": run.trace_id,
            "context_version": run.context_version,
        }
        actual = {key: getattr(envelope, key) for key in expected}
        if actual != expected:
            raise BridgeError(
                "context_conflict",
                "Worker result correlation or context version does not match the live run",
                details={"expected": expected, "actual": actual},
            )
        if envelope.status not in SUCCESS_RESULT_STATUSES or envelope.conflicts:
            raise BridgeError(
                "worker_reported_conflict",
                "Worker result requested revision or reported a conflict",
                details={
                    "task_id": detail.task_id,
                    "status": envelope.status,
                    "conflicts": envelope.conflicts,
                    "suggested_worker": envelope.suggested_worker,
                },
            )
        task_spec = next((task for task in run.task_graph if task.task_id == detail.task_id), None)
        if task_spec and task_spec.stage in {"PLAN_REVIEW", "VERIFY"}:
            if not envelope.independent_review or envelope.review_verdict != "PASS":
                raise BridgeError(
                    "independent_review_not_passed",
                    "Reviewer task must carry independent_review=true and review_verdict=PASS",
                    details={
                        "task_id": detail.task_id,
                        "review_verdict": envelope.review_verdict,
                        "independent_review": envelope.independent_review,
                    },
                )
        if not envelope.artifact_refs:
            raise BridgeError(
                "primary_artifact_missing",
                "Result envelope must bind output_sha256 to at least one declared artifact",
                details={"task_id": detail.task_id},
            )
        primary = envelope.artifact_refs[0]
        if primary not in paths:
            raise BridgeError(
                "undeclared_primary_artifact",
                "Result envelope primary artifact is not declared in AgentTeams TaskMeta",
                details={"task_id": detail.task_id, "path": primary},
            )
        primary_bytes = self.agentteams.task_artifact(
            run.agentteams_project_id, run.team, detail.task_id, primary
        )
        actual_digest = hashlib.sha256(primary_bytes).hexdigest()
        if actual_digest != envelope.output_sha256:
            raise BridgeError(
                "artifact_digest_mismatch",
                "Declared output_sha256 does not match the AgentTeams artifact bytes",
                details={
                    "task_id": detail.task_id,
                    "expected": envelope.output_sha256,
                    "actual": actual_digest,
                },
            )
        return envelope, hashlib.sha256(raw).hexdigest()

    def _observe_statuses(
        self, run: BridgeRun, workflow: WorkflowResponse
    ) -> Tuple[BridgeRun, List[Dict[str, Any]]]:
        checkpoint = dict(run.checkpoint)
        statuses = dict(checkpoint.get("node_status", {}))
        actions: List[Dict[str, Any]] = []
        now = _iso_now(self.clock)
        for node in workflow.nodes:
            previous = statuses.get(node.id)
            if not previous or previous.get("status") != node.status:
                statuses[node.id] = {"status": node.status, "since": now}
                actions.append(
                    {
                        "action": "status_observed",
                        "task_id": node.id,
                        "from": previous.get("status") if previous else None,
                        "to": node.status,
                    }
                )
                envelope = self._envelope(
                    run,
                    EnvelopeKind.TASK_UPDATE,
                    {
                        "agentteams_task_id": node.id,
                        "previous_status": previous.get("status") if previous else None,
                        "status": node.status,
                        "assignee": node.assignee,
                        "source": "GET /api/v1/projects/{id}/workflow?includeTasks=true",
                    },
                )
                self.store.append_event(run.id, envelope)
        checkpoint["node_status"] = statuses
        checkpoint["last_workflow_sha256"] = canonical_sha256(
            workflow.model_dump(mode="json")
        )
        return run.model_copy(update={"checkpoint": checkpoint}), actions

    def _seconds_in_status(self, run: BridgeRun, task_id: str) -> float:
        record = run.checkpoint.get("node_status", {}).get(task_id, {})
        since = record.get("since")
        if not since:
            return 0.0
        parsed = datetime.fromisoformat(str(since).replace("Z", "+00:00"))
        return (self.clock().astimezone(timezone.utc) - parsed.astimezone(timezone.utc)).total_seconds()

    def _candidate_workers(
        self, run: BridgeRun, task: ResearchTaskSpec
    ) -> List[Tuple[str, Dict[str, Any]]]:
        workers: Dict[str, Dict[str, Any]] = run.checkpoint.get("workers", {})
        candidates: List[Tuple[str, Dict[str, Any]]] = []
        for name, payload in workers.items():
            if name == task.assigned_worker or payload.get("role") == "team_leader":
                continue
            skills = set(payload.get("skills", []))
            expected = set(task.expected_skills)
            if expected and not expected.intersection(skills):
                continue
            candidates.append((name, payload))
        return sorted(candidates, key=lambda item: item[0])

    def _reassign(
        self,
        run: BridgeRun,
        workflow: WorkflowResponse,
        task: ResearchTaskSpec,
        *,
        reason: str,
        suggested_worker: Optional[str] = None,
    ) -> Tuple[BridgeRun, Dict[str, Any]]:
        checkpoint = dict(run.checkpoint)
        counts = dict(checkpoint.get("reassignments", {}))
        origin = task.origin_task_id or task.task_id
        count = int(counts.get(origin, 0))
        if count >= run.max_reassignments:
            checkpoint["blocked_reason"] = "reassignment budget exhausted for %s" % origin
            blocked = run.model_copy(update={"state": RunState.BLOCKED, "checkpoint": checkpoint})
            return blocked, {
                "action": "blocked",
                "task_id": task.task_id,
                "reason": checkpoint["blocked_reason"],
            }
        other_active = [
            node.id
            for node in workflow.nodes
            if node.id != task.task_id and node.status == "in-progress"
        ]
        downstream_started = [
            candidate.task_id
            for candidate in run.task_graph
            if task.task_id in candidate.depends_on and candidate.status != "planned"
        ]
        if other_active or downstream_started:
            checkpoint["pending_replan"] = {
                "task_id": task.task_id,
                "reason": reason,
                "blocked_by_active": other_active,
                "blocked_by_downstream": downstream_started,
            }
            return run.model_copy(update={"checkpoint": checkpoint}), {
                "action": "replan_deferred",
                "task_id": task.task_id,
                "active": other_active,
                "downstream": downstream_started,
            }
        candidates = self._candidate_workers(run, task)
        if suggested_worker:
            candidates.sort(key=lambda item: item[0] != suggested_worker)
        if not candidates:
            checkpoint["blocked_reason"] = "no alternate AgentTeams Worker is available"
            return run.model_copy(
                update={"state": RunState.BLOCKED, "checkpoint": checkpoint}
            ), {"action": "blocked", "task_id": task.task_id, "reason": checkpoint["blocked_reason"]}
        worker_name, worker = candidates[count % len(candidates)]
        replacement_id = "%s-r%d" % (origin, count + 1)
        current_node = next((node for node in workflow.nodes if node.id == task.task_id), None)
        current_status = current_node.status if current_node else task.status
        if current_status in ACTIVE_NODE_STATUSES:
            self.agentteams.cancel_task(
                run.agentteams_project_id,
                run.team,
                task.task_id,
                reason,
                replacement_id,
            )
            current_status = "blocked"
        graph: List[ResearchTaskSpec] = []
        for existing in run.task_graph:
            updates: Dict[str, Any] = {}
            if existing.task_id == task.task_id:
                updates["status"] = _status_to_raw(current_status)
            if task.task_id in existing.depends_on:
                updates["depends_on"] = [
                    replacement_id if dependency == task.task_id else dependency
                    for dependency in existing.depends_on
                ]
            graph.append(existing.model_copy(update=updates))
        replacement = task.model_copy(
            update={
                "task_id": replacement_id,
                "title": "%s · reassignment %d" % (task.title, count + 1),
                "assigned_worker": worker_name,
                "assigned_to": str(worker["matrixUserID"]),
                "attempt": count + 2,
                "status": "planned",
                "origin_task_id": origin,
            }
        )
        graph.append(replacement)
        self.agentteams.replan(
            run.agentteams_project_id, run.team, self._controller_tasks(graph)
        )
        counts[origin] = count + 1
        checkpoint["reassignments"] = counts
        checkpoint.pop("pending_replan", None)
        updated = run.model_copy(update={"task_graph": graph, "checkpoint": checkpoint})
        conflict = self._envelope(
            updated,
            EnvelopeKind.CONFLICT,
            {
                "agentteams_task_id": task.task_id,
                "reason": reason,
                "replacement_task_id": replacement_id,
                "replacement_worker": worker_name,
            },
            attempt=replacement.attempt,
        )
        self.store.append_event(updated.id, conflict)
        replan = self._envelope(
            updated,
            EnvelopeKind.REPLAN,
            {
                "replaced": task.task_id,
                "replacement": replacement.model_dump(mode="json"),
                "task_graph_sha256": canonical_sha256(
                    [item.model_dump(mode="json") for item in graph]
                ),
                "controller_operation": "POST /api/v1/projects/{id}/replan",
            },
            attempt=replacement.attempt,
            causation_id=conflict.envelope_id,
        )
        try:
            matrix_event_id = self._send(updated, replan)
        except Exception as error:
            try:
                self.agentteams.pause(
                    updated.agentteams_project_id,
                    updated.team,
                    "replan notification failed; compensation fence",
                )
            except Exception:
                pass
            checkpoint = dict(updated.checkpoint)
            checkpoint["compensation_reason"] = str(error)
            checkpoint["compensation_retry"] = {
                "operation": "replan-notify",
                "resume_state": run.state.value,
                "token_required": False,
                "replacement_task_id": replacement_id,
                "replacement_worker": worker_name,
                "reason": reason,
            }
            updated = updated.model_copy(
                update={"state": RunState.COMPENSATION_REQUIRED, "checkpoint": checkpoint}
            )
            self.store.append_event(
                updated.id,
                self._envelope(
                    updated,
                    EnvelopeKind.COMPENSATION,
                    {
                        "fence": "AgentTeams project paused",
                        "operation": "replan-notify",
                        "reason": str(error),
                        "replacement_task_id": replacement_id,
                        "retry": "POST /api/v1/agentteams/runs/{run_id}/reconcile",
                    },
                    attempt=replacement.attempt,
                    causation_id=conflict.envelope_id,
                ),
            )
            return updated, {
                "action": "compensation_required",
                "operation": "replan-notify",
                "task_id": replacement_id,
                "reason": str(error),
            }
        return updated, {
            "action": "reassigned",
            "from_task": task.task_id,
            "to_task": replacement_id,
            "to_worker": worker_name,
            "matrix_event_id": matrix_event_id,
        }

    def _first_conflict(
        self, run: BridgeRun, workflow: WorkflowResponse
    ) -> Optional[Tuple[ResearchTaskSpec, str, Optional[str]]]:
        details = self._detail_by_id(workflow)
        accepted = dict(run.checkpoint.get("accepted_contracts", {}))
        for task in self._effective_tasks(run):
            node = next((item for item in workflow.nodes if item.id == task.task_id), None)
            if node is None:
                continue
            if node.status in {"revision", "blocked"}:
                detail = details.get(task.task_id)
                suggested = None
                if detail and detail.summary:
                    suggested = None
                return task, "AgentTeams reported terminal %s" % node.status, suggested
            if node.status == "delegated" and self._seconds_in_status(run, task.task_id) > run.ack_timeout_seconds:
                return task, "ACK timeout exceeded", None
            if (
                node.status == "in-progress"
                and self._seconds_in_status(run, task.task_id) > run.execution_timeout_seconds
            ):
                return task, "execution timeout exceeded", None
            if node.status != "completed" or task.task_id in accepted:
                continue
            detail = details.get(task.task_id)
            if detail is None:
                return task, "completed node has no scoped AgentTeams TaskMeta", None
            if detail.result_status not in SUCCESS_RESULT_STATUSES:
                return task, "result_status=%s" % detail.result_status, None
            try:
                envelope, artifact_hash = self._validate_task_contract(run, detail)
            except BridgeError as error:
                suggested = None
                if error.code == "worker_reported_conflict":
                    suggested = error.details.get("suggested_worker")
                return task, "%s: %s" % (error.code, error.message), suggested
            accepted[task.task_id] = {
                "result_envelope_sha256": artifact_hash,
                "output_sha256": envelope.output_sha256,
                "review_verdict": envelope.review_verdict,
                "independent_review": envelope.independent_review,
                "accepted_at": _iso_now(self.clock),
            }
            run.checkpoint["accepted_contracts"] = accepted
            self.store.append_event(
                run.id,
                self._envelope(
                    run,
                    EnvelopeKind.ARTIFACT_ACCEPTED,
                    {
                        "agentteams_task_id": task.task_id,
                        "result_envelope_sha256": artifact_hash,
                        "output_sha256": envelope.output_sha256,
                        "source": "AgentTeams declared artifact endpoint",
                    },
                ),
            )
        return None

    @staticmethod
    def _effective_tasks(
        run: BridgeRun, stages: Optional[set[str]] = None
    ) -> List[ResearchTaskSpec]:
        """Return only the newest attempt for each logical task.

        Controller cancellation deliberately leaves the replaced node in the
        DAG as a terminal audit record.  Counting that superseded node would
        make a successful replacement unable to reach the R2 or completion
        gate.  Attempts are grouped by ``origin_task_id`` and resolved by the
        explicit attempt counter, with graph order as a deterministic tie
        breaker.
        """

        latest: Dict[str, Tuple[int, int, ResearchTaskSpec]] = {}
        for index, task in enumerate(run.task_graph):
            if stages is not None and task.stage not in stages:
                continue
            origin = task.origin_task_id or task.task_id
            candidate = (task.attempt, index, task)
            previous = latest.get(origin)
            if previous is None or candidate[:2] > previous[:2]:
                latest[origin] = candidate
        return [item[2] for item in sorted(latest.values(), key=lambda value: value[1])]

    def _all_stage_tasks_completed(
        self, run: BridgeRun, workflow: WorkflowResponse, stages: set[str]
    ) -> bool:
        nodes = {node.id: node.status for node in workflow.nodes}
        accepted = run.checkpoint.get("accepted_contracts", {})
        tasks = self._effective_tasks(run, stages)
        return bool(tasks) and all(
            nodes.get(task.task_id) == "completed" and task.task_id in accepted for task in tasks
        )

    def _recover_compensation(
        self, run: BridgeRun, workflow: WorkflowResponse
    ) -> Tuple[BridgeRun, Dict[str, Any]]:
        retry = run.checkpoint.get("compensation_retry") or {}
        operation = retry.get("operation")
        if operation == "resume-replan-notify":
            return run, {
                "action": "operator_gate",
                "operation": operation,
                "required_action": (
                    "repeat POST /api/v1/agentteams/runs/{run_id}/r2-grant with the "
                    "same idempotency key; the approval token will not be consumed again"
                ),
            }
        if operation not in {
            "start-dispatch",
            "replan-notify",
            "approval-required-notify",
            "terminal-notify",
        }:
            return run, {
                "action": "operator_gate",
                "operation": operation or "unknown",
                "required_action": "inspect the durable compensation checkpoint",
            }

        if operation == "start-dispatch":
            envelope = self._envelope(
                run,
                EnvelopeKind.TASK_REQUEST,
                self._task_request_body(run, workflow),
            )
            matrix_event_id = self._send(run, envelope)
            self.agentteams.resume(run.agentteams_project_id, run.team)
            next_state = RunState.PRE_APPROVAL
        elif operation == "replan-notify":
            replacement_id = retry.get("replacement_task_id")
            replacement = next(
                (task for task in run.task_graph if task.task_id == replacement_id), None
            )
            envelope = self._envelope(
                run,
                EnvelopeKind.REPLAN,
                {
                    "recovered": True,
                    "reason": retry.get("reason"),
                    "replacement": (
                        replacement.model_dump(mode="json") if replacement else None
                    ),
                    "task_graph_sha256": canonical_sha256(
                        [task.model_dump(mode="json") for task in run.task_graph]
                    ),
                    "controller_operation": "already committed before compensation fence",
                },
                attempt=replacement.attempt if replacement else 1,
            )
            matrix_event_id = self._send(run, envelope)
            self.agentteams.resume(run.agentteams_project_id, run.team)
            requested_state = str(retry.get("resume_state", RunState.PRE_APPROVAL.value))
            next_state = (
                RunState(requested_state)
                if requested_state
                in {RunState.PRE_APPROVAL.value, RunState.POST_APPROVAL.value}
                else RunState.PRE_APPROVAL
            )
        elif operation == "approval-required-notify":
            envelope = self._envelope(
                run,
                EnvelopeKind.APPROVAL_REQUIRED,
                {
                    "risk_level": "R2",
                    "project_status": workflow.status,
                    "recovered": True,
                    "required_action": "Approve in EgoAgentOS; chat text is not a grant",
                },
            )
            matrix_event_id = self._send(run, envelope)
            next_state = RunState.WAITING_R2
        else:
            envelope = self._envelope(
                run,
                EnvelopeKind.TERMINAL,
                {
                    "agentteams_status": workflow.status,
                    "accepted_contracts": run.checkpoint.get("accepted_contracts", {}),
                    "recovered": True,
                    "claim_boundary": (
                        "AgentTeams collaboration completed. Scientific KEEP/DROP remains "
                        "an EgoAgentOS evidence-gate decision."
                    ),
                },
            )
            matrix_event_id = self._send(run, envelope)
            next_state = RunState.COMPLETED

        checkpoint = dict(run.checkpoint)
        checkpoint.pop("compensation_reason", None)
        checkpoint.pop("compensation_retry", None)
        if operation == "start-dispatch":
            checkpoint["dispatch_matrix_event_id"] = matrix_event_id
            checkpoint["matrix_root"] = matrix_event_id
        run = run.model_copy(update={"state": next_state, "checkpoint": checkpoint})
        return run, {
            "action": "compensation_recovered",
            "operation": operation,
            "matrix_event_id": matrix_event_id,
            "state": next_state.value,
        }

    def reconcile(self, run_id: str) -> ReconcileResult:
        run = self.store.get_run(run_id)
        if run.mode != "live":
            raise BridgeError(
                "dry_run_not_reconcilable",
                "A dry-run plan has no live AgentTeams workflow to reconcile",
                status_code=409,
                details={"truth": "DRY_RUN_ONLY"},
            )
        if run.state in {RunState.BLOCKED, RunState.COMPLETED}:
            return ReconcileResult(run=run, live=True, actions=[])
        workflow = self.agentteams.workflow(run.agentteams_project_id, run.team)
        if workflow.project_id != run.agentteams_project_id or workflow.team_id != run.team:
            raise BridgeError(
                "workflow_identity_conflict",
                "AgentTeams workflow is not bound to the persisted run identity",
                details={
                    "expected_project": run.agentteams_project_id,
                    "actual_project": workflow.project_id,
                    "expected_team": run.team,
                    "actual_team": workflow.team_id,
                },
            )
        if run.state == RunState.COMPENSATION_REQUIRED:
            run, action = self._recover_compensation(run, workflow)
            run = self.store.update_run(run, expected_version=run.version)
            return ReconcileResult(
                run=run,
                workflow_sha256=canonical_sha256(workflow.model_dump(mode="json")),
                actions=[action],
                live=True,
            )
        run, actions = self._observe_statuses(run, workflow)
        conflict = self._first_conflict(run, workflow)
        if conflict is not None:
            task, reason, suggested = conflict
            run, action = self._reassign(
                run, workflow, task, reason=reason, suggested_worker=suggested
            )
            actions.append(action)
        elif run.state == RunState.PRE_APPROVAL and self._all_stage_tasks_completed(
            run, workflow, PRE_APPROVAL_STAGES
        ):
            paused = self.agentteams.pause(
                run.agentteams_project_id,
                run.team,
                "EgoAgentOS R2 approval required before execution",
            )
            run = run.model_copy(update={"state": RunState.WAITING_R2})
            envelope = self._envelope(
                run,
                EnvelopeKind.APPROVAL_REQUIRED,
                {
                    "risk_level": "R2",
                    "project_status": paused.status,
                    "required_action": "Approve in EgoAgentOS; chat text is not a grant",
                    "resume_chain": [
                        "consume scoped EgoAgentOS approval token",
                        "POST AgentTeams project resume",
                        "POST AgentTeams project replan with post-approval DAG",
                        "Matrix APPROVAL_GRANTED event",
                    ],
                },
            )
            try:
                event_id = self._send(run, envelope)
                actions.append({"action": "r2_paused", "matrix_event_id": event_id})
            except Exception as error:
                checkpoint = dict(run.checkpoint)
                checkpoint["compensation_reason"] = str(error)
                checkpoint["compensation_retry"] = {
                    "operation": "approval-required-notify",
                    "resume_state": RunState.WAITING_R2.value,
                    "token_required": True,
                }
                run = run.model_copy(
                    update={
                        "state": RunState.COMPENSATION_REQUIRED,
                        "checkpoint": checkpoint,
                    }
                )
                self.store.append_event(
                    run.id,
                    self._envelope(
                        run,
                        EnvelopeKind.COMPENSATION,
                        {
                            "fence": "AgentTeams project remains paused",
                            "operation": "approval-required-notify",
                            "reason": str(error),
                            "retry": "POST /api/v1/agentteams/runs/{run_id}/reconcile",
                        },
                    ),
                )
                actions.append(
                    {
                        "action": "compensation_required",
                        "operation": "approval-required-notify",
                        "reason": str(error),
                    }
                )
        elif run.state == RunState.POST_APPROVAL and self._all_stage_tasks_completed(
            run, workflow, POST_APPROVAL_STAGES
        ):
            completed = self.agentteams.complete(run.agentteams_project_id, run.team)
            run = run.model_copy(update={"state": RunState.COMPLETED})
            envelope = self._envelope(
                run,
                EnvelopeKind.TERMINAL,
                {
                    "agentteams_status": completed.status,
                    "accepted_contracts": run.checkpoint.get("accepted_contracts", {}),
                    "claim_boundary": (
                        "AgentTeams collaboration completed. Scientific KEEP/DROP remains "
                        "an EgoAgentOS evidence-gate decision."
                    ),
                },
            )
            try:
                event_id = self._send(run, envelope)
                actions.append({"action": "agentteams_completed", "matrix_event_id": event_id})
            except Exception as error:
                checkpoint = dict(run.checkpoint)
                checkpoint["compensation_reason"] = str(error)
                checkpoint["compensation_retry"] = {
                    "operation": "terminal-notify",
                    "resume_state": RunState.COMPLETED.value,
                    "token_required": False,
                }
                run = run.model_copy(
                    update={
                        "state": RunState.COMPENSATION_REQUIRED,
                        "checkpoint": checkpoint,
                    }
                )
                self.store.append_event(
                    run.id,
                    self._envelope(
                        run,
                        EnvelopeKind.COMPENSATION,
                        {
                            "fence": "AgentTeams project is terminal",
                            "operation": "terminal-notify",
                            "reason": str(error),
                            "retry": "POST /api/v1/agentteams/runs/{run_id}/reconcile",
                        },
                    ),
                )
                actions.append(
                    {
                        "action": "compensation_required",
                        "operation": "terminal-notify",
                        "reason": str(error),
                    }
                )
        run = self.store.update_run(run, expected_version=run.version)
        return ReconcileResult(
            run=run,
            workflow_sha256=canonical_sha256(workflow.model_dump(mode="json")),
            actions=actions,
            live=True,
        )

    def _post_approval_graph(self, run: BridgeRun) -> List[ResearchTaskSpec]:
        workers: Dict[str, Dict[str, Any]] = run.checkpoint.get("workers", {})
        existing_post = [
            task for task in run.task_graph if task.stage in POST_APPROVAL_STAGES
        ]
        post = existing_post or self._build_task_graph(
            run.agentteams_project_id, workers, stages=sorted(POST_APPROVAL_STAGES)
        )
        accepted = run.checkpoint.get("accepted_contracts", {})
        pre = [
            task.model_copy(update={"status": "completed"})
            if task.task_id in accepted
            else task
            for task in run.task_graph
            if task.stage in PRE_APPROVAL_STAGES
        ]
        effective_pre = self._effective_tasks(run, PRE_APPROVAL_STAGES)
        stage_order = {stage: index for index, stage in enumerate(
            ("CONTEXT", "PLAN", "PLAN_REVIEW")
        )}
        effective_pre.sort(key=lambda task: (stage_order.get(task.stage, -1), task.attempt))
        if effective_pre and post:
            post[0] = post[0].model_copy(
                update={"depends_on": [effective_pre[-1].task_id]}
            )
        return pre + post

    def grant_r2(self, run_id: str, request: GrantRequest) -> BridgeRun:
        run = self.store.get_run(run_id)
        if run.mode != "live":
            raise BridgeError("dry_run_grant_forbidden", "Cannot grant a dry-run plan")
        if run.state not in {RunState.WAITING_R2, RunState.COMPENSATION_REQUIRED}:
            raise BridgeError(
                "run_not_waiting_for_r2",
                "Bridge run is not at the R2 recovery gate",
                details={"state": run.state.value},
            )
        checkpoint = dict(run.checkpoint)
        if run.state == RunState.COMPENSATION_REQUIRED:
            operation = (checkpoint.get("compensation_retry") or {}).get("operation")
            if operation not in {
                "approval-required-notify",
                "resume-replan-notify",
            }:
                raise BridgeError(
                    "compensation_requires_reconcile",
                    "This compensation is not an R2 token recovery",
                    details={
                        "operation": operation,
                        "required_action": (
                            "POST /api/v1/agentteams/runs/{run_id}/reconcile"
                        ),
                    },
                )
        ego_grant_committed = bool(checkpoint.get("ego_grant_committed"))
        if not ego_grant_committed:
            ego_task = self.ego.get_task(run.ego_task_id)
            if ego_task.get("stage") != "APPROVAL" or not ego_task.get("pending_approval"):
                raise BridgeError(
                    "ego_task_not_at_approval",
                    "EgoAgentOS task must expose a pending APPROVAL before R2 recovery",
                    details={"stage": ego_task.get("stage")},
                )
            pending_approval = ego_task.get("pending_approval") or {}
            checkpoint["grant_id"] = pending_approval.get("id")
            checkpoint["grant_approver"] = pending_approval.get("approver")
            response = self.ego.consume_r2_grant(
                run.ego_task_id, request.approval_token, request.idempotency_key
            )
            advanced_task = response.get("task") if isinstance(response, dict) else None
            advanced_stage = (
                advanced_task.get("stage") if isinstance(advanced_task, dict) else None
            )
            checkpoint["ego_grant_committed"] = True
            checkpoint["ego_grant_response_sha256"] = canonical_sha256(response)
            checkpoint["ego_grant_observed_stage"] = advanced_stage
            checkpoint["ego_grant_idempotency_key_sha256"] = hashlib.sha256(
                request.idempotency_key.encode("utf-8")
            ).hexdigest()
            # Persist immediately: the token is consumed, while the token itself is never stored.
            run = run.model_copy(update={"checkpoint": checkpoint})
            run = self.store.update_run(run, expected_version=run.version)
            if advanced_stage != "EXECUTE":
                checkpoint = dict(run.checkpoint)
                checkpoint["compensation_reason"] = (
                    "EgoAgentOS returned success without an EXECUTE task state"
                )
                checkpoint["compensation_retry"] = {
                    "operation": "grant-response-uncertain",
                    "token_required": False,
                    "observed_stage": advanced_stage,
                }
                run = run.model_copy(
                    update={
                        "state": RunState.COMPENSATION_REQUIRED,
                        "checkpoint": checkpoint,
                    }
                )
                self.store.update_run(run, expected_version=run.version)
                raise BridgeError(
                    "ego_grant_transition_unverified",
                    "R2 receipt was persisted but the EgoAgentOS EXECUTE transition was not verified",
                    retryable=False,
                    details={"observed_stage": advanced_stage, "token_reusable": False},
                )
        graph = self._post_approval_graph(run)
        try:
            self.agentteams.resume(run.agentteams_project_id, run.team)
            self.agentteams.replan(
                run.agentteams_project_id, run.team, self._controller_tasks(graph)
            )
            run = run.model_copy(update={"task_graph": graph, "state": RunState.POST_APPROVAL})
            envelope = self._envelope(
                run,
                EnvelopeKind.APPROVAL_GRANTED,
                {
                    "risk_level": "R2",
                    "ego_grant_committed": True,
                    "approval_token_persisted": False,
                    "post_approval_task_graph_sha256": canonical_sha256(
                        [task.model_dump(mode="json") for task in graph]
                    ),
                    "resume_source": "EgoAgentOS scoped approval token",
                },
            )
            event_id = self._send(run, envelope)
            checkpoint = dict(run.checkpoint)
            checkpoint["approval_granted_matrix_event_id"] = event_id
            checkpoint.pop("compensation_reason", None)
            checkpoint.pop("compensation_retry", None)
            run = run.model_copy(update={"checkpoint": checkpoint})
            return self.store.update_run(run, expected_version=run.version)
        except Exception as error:
            try:
                self.agentteams.pause(
                    run.agentteams_project_id,
                    run.team,
                    "post-grant recovery failed; compensation fence",
                )
            except Exception:
                pass
            checkpoint = dict(run.checkpoint)
            checkpoint["compensation_reason"] = str(error)
            checkpoint["compensation_retry"] = {
                "operation": "resume-replan-notify",
                "token_required": False,
                "ego_grant_committed": True,
            }
            run = run.model_copy(
                update={"state": RunState.COMPENSATION_REQUIRED, "checkpoint": checkpoint}
            )
            run = self.store.update_run(run, expected_version=run.version)
            self.store.append_event(
                run.id,
                self._envelope(
                    run,
                    EnvelopeKind.COMPENSATION,
                    {
                        "fence": "AgentTeams project paused",
                        "reason": str(error),
                        "retry": "repeat r2-grant with the same idempotency key; token is not reused",
                    },
                ),
            )
            raise

    def skill_evidence(self, run_id: str) -> Dict[str, Any]:
        run = self.store.get_run(run_id)
        if run.mode != "live":
            raise BridgeError(
                "dry_run_has_no_skill_trace",
                "Dry-run fixtures do not prove AgentTeams Skill discovery or invocation",
            )
        evidence: List[SkillEvidence] = []
        workers: Dict[str, Dict[str, Any]] = run.checkpoint.get("workers", {})
        for worker_name, worker in sorted(workers.items()):
            endpoint = "/api/v1/workers/%s" % worker_name
            digest = canonical_sha256(worker)
            for skill in worker.get("skills", []):
                evidence.append(
                    SkillEvidence(
                        worker=worker_name,
                        skill=str(skill),
                        level=SkillEvidenceLevel.DECLARED,
                        source_endpoint=endpoint,
                        source_sha256=digest,
                    )
                )
        spawns = self.agentteams.spawns(run.agentteams_project_id, run.team)
        spawns_payload = spawns.model_dump(mode="json")
        spawns_digest = canonical_sha256(spawns_payload)
        for worker_group in spawns.workers:
            for spawn in worker_group.spawns:
                for skill in spawn.subagent_skills:
                    evidence.append(
                        SkillEvidence(
                            worker=worker_group.worker,
                            skill=skill,
                            level=SkillEvidenceLevel.SPAWN_AUTHORIZED,
                            session_id=spawn.session_id,
                            source_endpoint="/api/v1/projects/{id}/spawns",
                            source_sha256=spawns_digest,
                        )
                    )
                messages = self.agentteams.spawn_messages(
                    run.agentteams_project_id, run.team, spawn.session_id
                )
                message_payload = messages.model_dump(mode="json")
                message_digest = canonical_sha256(message_payload)
                for message in messages.messages:
                    if message.kind == "tool_result" and message.tool_state == "success":
                        evidence.append(
                            SkillEvidence(
                                worker=worker_group.worker,
                                tool=message.name or "unknown",
                                level=SkillEvidenceLevel.TOOL_INVOKED,
                                session_id=spawn.session_id,
                                message_seq=message.seq,
                                source_endpoint=(
                                    "/api/v1/projects/{id}/spawns/{sessionId}/messages"
                                ),
                                source_sha256=message_digest,
                            )
                        )
        return {
            "live": True,
            "project_id": run.agentteams_project_id,
            "items": [item.model_dump(mode="json") for item in evidence],
            "claim_boundary": {
                "DECLARED": "Worker CR spec.skills contains the Skill assignment",
                "SPAWN_AUTHORIZED": "official spawn trace contains subagent_skills",
                "TOOL_INVOKED": "official spawn message stream contains a successful tool_result",
                "not_claimed": (
                    "A Skill assignment alone is not claimed as execution; a tool result is not "
                    "claimed as a specific Skill unless independently linked by task artifacts/trace."
                ),
            },
        }

    def recover_active(self) -> List[Dict[str, Any]]:
        results: List[Dict[str, Any]] = []
        for run in self.store.active_runs():
            compensation_operation = (
                (run.checkpoint.get("compensation_retry") or {}).get("operation")
                if run.state == RunState.COMPENSATION_REQUIRED
                else None
            )
            if (
                run.mode != "live"
                or run.state == RunState.WAITING_R2
                or compensation_operation == "resume-replan-notify"
            ):
                results.append(
                    {
                        "run_id": run.id,
                        "state": run.state.value,
                        "action": "operator_gate",
                        "operation": compensation_operation,
                    }
                )
                continue
            try:
                result = self.reconcile(run.id)
                results.append(
                    {
                        "run_id": run.id,
                        "state": result.run.state.value,
                        "actions": result.actions,
                    }
                )
            except BridgeError as error:
                results.append(
                    {"run_id": run.id, "state": run.state.value, "error": error.as_dict()}
                )
        return results
