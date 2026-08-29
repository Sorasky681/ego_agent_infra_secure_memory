"""Deterministic test-only fixtures for the independent trace verifier."""

from __future__ import annotations

import hashlib
import json
from typing import Any, Dict, List

from benchmarks.model import Scenario
from benchmarks.trace_verifier import SCENARIO_REQUIRED_EVENTS


def digest(label: str) -> str:
    return hashlib.sha256(label.encode("utf-8")).hexdigest()


def build_trace(scenario: Scenario, seed: int) -> Dict[str, Any]:
    task_id = "ego-task-%s" % scenario.id
    correlation_id = "corr-%s-%d" % (scenario.id, seed)
    intent_digest = digest("intent:%s:%d" % (scenario.id, seed))
    receipt_digest = digest("receipt:%s:%d" % (scenario.id, seed))
    evidence_digest = digest("evidence:%s:%d" % (scenario.id, seed))
    matrix_root = "matrix-event-%s-%d" % (scenario.id, seed)
    workflow_digest = digest("workflow:%s:%d" % (scenario.id, seed))
    chain_head = digest("bridge-head:%s:%d" % (scenario.id, seed))
    events: List[Dict[str, Any]] = []

    def event(event_type: str, actor: str, payload: Dict[str, Any]) -> None:
        events.append(
            {
                "sequence": len(events) + 1,
                "type": event_type,
                "actor": actor,
                "task_id": task_id,
                "correlation_id": correlation_id,
                "payload": payload,
            }
        )

    event(
        "task.created",
        "bridge-main",
        {"intent_digest": intent_digest, "matrix_root": matrix_root},
    )
    event("task.delegated", "bridge-main", {"assignee": "matrix-executor"})
    event("task.accepted", "bridge-main", {"artifact_sha256": digest("accepted")})
    event(
        "skill.invoked",
        "worker-executor",
        {
            "tool": "experiment.execute",
            "session_id": "session-%d" % seed,
            "message_seq": 1,
            "source_endpoint": "/api/v1/spawns/messages",
            "official_response_sha256": digest("skill-response:%d" % seed),
            "matrix_root": matrix_root,
        },
    )
    event(
        "human.approved",
        "human-approver",
        {
            "grant_id": "grant-%s-%d" % (scenario.id, seed),
            "receipt_digest": receipt_digest,
            "matrix_root": matrix_root,
        },
    )
    for event_type in SCENARIO_REQUIRED_EVENTS[scenario.id]:
        actor = "worker-executor" if event_type == "effect.committed" else "bridge-main"
        payload: Dict[str, Any] = {"matrix_root": matrix_root}
        if event_type == "effect.committed":
            payload.update(
                {
                    "effect_id": "effect-%s-%d" % (scenario.id, seed),
                    "idempotency_key": "idem-%s-%d" % (scenario.id, seed),
                }
            )
        if event_type == "task.reassigned":
            payload.update(
                {
                    "from_assignee": "matrix-executor",
                    "to_assignee": "matrix-planner",
                }
            )
        event(event_type, actor, payload)
    event(
        "independent_review.passed",
        "worker-reviewer",
        {"evidence_digest": evidence_digest, "verdict": "PASS"},
    )
    event(
        "task.completed",
        "worker-executor",
        {"matrix_root": matrix_root, "bridge_event_hash": chain_head},
    )
    event(
        "decision.committed",
        "ego-decision",
        {
            "evidence_digest": evidence_digest,
            "matrix_root": matrix_root,
            "verdict": "KEEP" if scenario.id == "happy_path" else "REJECT",
        },
    )
    replay_digest = digest("replay:%s:%d" % (scenario.id, seed))
    return {
        "schema_version": "egoagentos.agentteams-trace/v1",
        "source": "AgentTeams",
        "execution_mode": "real-agentteams",
        "seed": seed,
        "scenario_id": scenario.id,
        "project_id": "project-%s-%d" % (scenario.id, seed),
        "task_id": task_id,
        "correlation_id": correlation_id,
        "trace_id": "trace-%s-%d" % (scenario.id, seed),
        "context_version": 1,
        "agents": [
            {
                "id": "worker-executor",
                "role": "runtime",
                "matrix_user_id": "matrix-executor",
                "source": "GET /api/v1/workers/runtime",
            },
            {
                "id": "worker-reviewer",
                "role": "reviewer",
                "matrix_user_id": "matrix-reviewer",
                "source": "GET /api/v1/workers/reviewer",
            },
            {
                "id": "worker-planner",
                "role": "planner",
                "matrix_user_id": "matrix-planner",
                "source": "GET /api/v1/workers/planner",
            },
        ],
        "principals": [
            {"id": "bridge-main", "kind": "bridge", "source": "bridge identity"},
            {"id": "human-approver", "kind": "human", "source": "RXP grant"},
            {"id": "ego-decision", "kind": "ego", "source": "Ego task state"},
        ],
        "events": events,
        "rxp": {
            "intent_digest": intent_digest,
            "grant_id": "grant-%s-%d" % (scenario.id, seed),
            "receipt_digest": receipt_digest,
            "evidence_digest": evidence_digest,
            "matrix_root": matrix_root,
        },
        "bridge": {
            "api_version": "v1",
            "endpoint": "/api/v1/agentteams/runs/run-fixture",
            "run_id": "run-%s-%d" % (scenario.id, seed),
        },
        "official_contract": {
            "repository": "https://github.com/agentscope-ai/AgentTeams",
            "main_commit": "223ddc2b8073e4c8b93bcbb15e1d717f196c04d9",
            "api_version": "agentteams.io/v1beta1",
            "controller": "fixture-controller",
        },
        "official_response_identifiers": {
            "project_id": "project-%s-%d" % (scenario.id, seed),
            "project_create_sha256": digest("project-create:%d" % seed),
            "matrix_root": matrix_root,
            "approval_matrix_event_id": "approval-event-%d" % seed,
            "workflow_sha256": [workflow_digest],
        },
        "snapshots": [{"state": "COMPLETED", "workflow_sha256": workflow_digest}],
        "bridge_event_chain": {
            "valid": True,
            "total": len(events),
            "head": chain_head,
        },
        "replay": {
            "run_ids": ["replay-a-%d" % seed, "replay-b-%d" % seed],
            "semantic_digests": [replay_digest, replay_digest],
        },
        "truth_boundary": "Synthetic verifier fixture; never external runtime evidence.",
    }


def trace_bytes(scenario: Scenario, seed: int) -> bytes:
    return json.dumps(
        build_trace(scenario, seed),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
