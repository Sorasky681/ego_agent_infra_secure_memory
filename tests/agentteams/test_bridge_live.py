from __future__ import annotations

import json
import hashlib
from datetime import timedelta

import pytest

from apps.agentteams_bridge.errors import BridgeError, UpstreamError
from apps.agentteams_bridge.models import GrantRequest, RunState, StartRunRequest
from apps.agentteams_bridge.transport import TransportFailure
from integrations.agentteams.benchmark_adapter import (
    REQUIRED_TRACE_EVENTS,
    TRACE_SCHEMA_VERSION,
    _bind_scenario_proof,
    _build_verified_trace,
    _write_trace,
)


def _start(bridge):
    return bridge.start_run(
        StartRunRequest(
            ego_task_id="task-live",
            objective="Run a bounded embodied-AI ablation with independent review",
            ack_timeout_seconds=5,
            execution_timeout_seconds=30,
        )
    )


def test_live_start_uses_controller_team_workers_project_and_matrix(bridge, fake_transport) -> None:
    run = _start(bridge)
    assert run.state == RunState.PRE_APPROVAL
    assert run.mode == "live"
    assert len(run.task_graph) == 3
    assert {task.assigned_worker for task in run.task_graph} == {
        "ego-scout",
        "ego-architect",
        "ego-reviewer",
    }
    calls = [(call["method"], call["path"]) for call in fake_transport.calls]
    assert ("GET", "/healthz") in calls
    assert ("GET", "/api/v1/projects") in calls
    assert ("GET", "/_matrix/client/v3/account/whoami") in calls
    assert ("POST", "/api/v1/projects") in calls
    assert any(path.endswith("/replan") and method == "POST" for method, path in calls)
    matrix_call = next(call for call in fake_transport.calls if "/send/m.room.message/" in call["path"])
    assert matrix_call["json"]["com.egoagentos.envelope"]["schema"] == (
        "egoagentos.agentteams-envelope.v2"
    )
    assert matrix_call["json"]["m.mentions"]["user_ids"] == [
        "@ego-research-lead:matrix.fixture.invalid"
    ]
    events = bridge.store.events(run.id)
    assert events["chain_valid"] is True
    assert events["items"][0]["kind"] == "TASK_REQUEST"


@pytest.mark.parametrize(
    ("status", "code", "retryable"),
    [
        (403, "agentteams_forbidden", False),
        (404, "agentteams_not_found", False),
        (409, "agentteams_conflict", True),
    ],
)
def test_official_controller_failures_are_structured(
    bridge, fake_transport, status, code, retryable
) -> None:
    fake_transport.fail_next = (
        "GET",
        "/api/v1/teams/ego-researchops",
        status,
        {"error": "contract fault"},
    )
    with pytest.raises(UpstreamError) as raised:
        bridge.agentteams.get_team("ego-researchops")
    assert raised.value.code == code
    assert raised.value.retryable is retryable
    assert raised.value.details["http_status"] == status


def test_controller_transport_failure_is_structured_and_retryable(
    bridge, fake_transport, monkeypatch
) -> None:
    monkeypatch.setattr(
        fake_transport,
        "request",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(TransportFailure("offline")),
    )
    with pytest.raises(UpstreamError) as raised:
        bridge.agentteams.health()
    assert raised.value.code == "agentteams_unavailable"
    assert raised.value.retryable is True
    assert raised.value.details["http_status"] == 503


def test_completed_preapproval_tasks_pause_at_real_r2_gate(bridge, fake_transport) -> None:
    run = _start(bridge)
    fake_transport.complete_all_with_contracts(run)
    result = bridge.reconcile(run.id)
    assert result.live is True
    assert result.run.state == RunState.WAITING_R2
    assert result.run.checkpoint["accepted_contracts"].keys() == {
        task.task_id for task in run.task_graph
    }
    assert any(action["action"] == "r2_paused" for action in result.actions)
    assert any(call["path"].endswith("/pause") for call in fake_transport.calls)
    assert bridge.store.events(run.id)["chain_valid"] is True


def test_r2_grant_consumes_ego_token_then_resumes_replans_and_never_persists_token(
    bridge, fake_transport
) -> None:
    run = _start(bridge)
    fake_transport.complete_all_with_contracts(run)
    run = bridge.reconcile(run.id).run
    fake_transport.ego_task = {
        **fake_transport.ego_task,
        "stage": "APPROVAL",
        "pending_approval": {"id": "apr-live", "status": "approved"},
    }
    token = "one-time-r2-token-never-persisted"
    updated = bridge.grant_r2(
        run.id,
        GrantRequest(approval_token=token, idempotency_key="grant-live-0001"),
    )
    assert updated.state == RunState.POST_APPROVAL
    assert updated.checkpoint["ego_grant_committed"] is True
    assert len({task.assigned_worker for task in updated.task_graph}) >= 5
    assert any(call["path"].endswith("/resume") for call in fake_transport.calls)
    assert any(call["path"].endswith("/replan") for call in fake_transport.calls)
    persisted = json.dumps(updated.model_dump(mode="json"))
    persisted += json.dumps(bridge.store.events(run.id))
    assert token not in persisted


def test_post_grant_failure_is_fenced_and_retry_does_not_consume_token_again(
    bridge, fake_transport
) -> None:
    run = _start(bridge)
    fake_transport.complete_all_with_contracts(run)
    run = bridge.reconcile(run.id).run
    fake_transport.ego_task = {
        **fake_transport.ego_task,
        "stage": "APPROVAL",
        "pending_approval": {"id": "apr-live", "status": "approved"},
    }
    replan_path = "/api/v1/projects/%s/replan" % run.agentteams_project_id
    fake_transport.fail_next = ("POST", replan_path, 409, {"error": "concurrent write"})
    with pytest.raises(UpstreamError) as raised:
        bridge.grant_r2(
            run.id,
            GrantRequest(approval_token="token-for-compensation", idempotency_key="grant-0002"),
        )
    assert raised.value.code == "agentteams_conflict"
    fenced = bridge.get_run(run.id)
    assert fenced.state == RunState.COMPENSATION_REQUIRED
    assert fenced.checkpoint["ego_grant_committed"] is True
    assert fenced.checkpoint["compensation_retry"]["token_required"] is False
    assert any(call["path"].endswith("/pause") for call in fake_transport.calls[-3:])

    advance_count = sum(call["path"].endswith("/advance") for call in fake_transport.calls)
    recovered = bridge.grant_r2(
        run.id,
        GrantRequest(approval_token="ignored-on-retry", idempotency_key="grant-0002"),
    )
    assert recovered.state == RunState.POST_APPROVAL
    assert sum(call["path"].endswith("/advance") for call in fake_transport.calls) == advance_count


def test_successful_grant_response_without_execute_state_is_fenced(
    bridge, fake_transport, monkeypatch
) -> None:
    run = _start(bridge)
    fake_transport.complete_all_with_contracts(run)
    run = bridge.reconcile(run.id).run
    fake_transport.ego_task = {
        **fake_transport.ego_task,
        "stage": "APPROVAL",
        "pending_approval": {"id": "apr-uncertain", "status": "approved"},
    }
    monkeypatch.setattr(
        bridge.ego,
        "consume_r2_grant",
        lambda *_args, **_kwargs: {"task": {"stage": "APPROVAL"}},
    )
    token = "uncertain-grant-token"
    with pytest.raises(BridgeError) as raised:
        bridge.grant_r2(
            run.id,
            GrantRequest(approval_token=token, idempotency_key="grant-uncertain"),
        )
    assert raised.value.code == "ego_grant_transition_unverified"
    fenced = bridge.get_run(run.id)
    assert fenced.state == RunState.COMPENSATION_REQUIRED
    assert fenced.checkpoint["ego_grant_committed"] is True
    assert fenced.checkpoint["compensation_retry"]["operation"] == (
        "grant-response-uncertain"
    )
    assert token not in json.dumps(fenced.model_dump(mode="json"))


def test_ack_timeout_cancels_and_reassigns_through_official_endpoints(
    bridge, fake_transport, clock
) -> None:
    run = _start(bridge)
    first = run.task_graph[0]
    fake_transport.workflow["nodes"][0]["status"] = "delegated"
    fake_transport.workflow["tasks_detail"] = [
        {
            "task_id": first.task_id,
            "project_id": run.agentteams_project_id,
            "status": "assigned",
            "assigned_to": first.assigned_to,
            "deliverables": [],
        }
    ]
    observed = bridge.reconcile(run.id).run
    assert observed.state == RunState.PRE_APPROVAL
    clock.value += timedelta(seconds=6)
    result = bridge.reconcile(run.id)
    assert any(action["action"] == "reassigned" for action in result.actions)
    replacement = next(task for task in result.run.task_graph if task.origin_task_id == first.task_id)
    assert replacement.task_id.endswith("-r1")
    assert replacement.assigned_worker == "ego-memory-curator"
    paths = [call["path"] for call in fake_transport.calls]
    assert any(path.endswith("/cancel") for path in paths)
    assert any(path.endswith("/replan") for path in paths)


def test_reassigned_attempt_can_reach_r2_without_reopening_superseded_task(
    bridge, fake_transport, clock
) -> None:
    run = _start(bridge)
    original = run.task_graph[0]
    fake_transport.workflow["nodes"][0]["status"] = "delegated"
    bridge.reconcile(run.id)
    clock.value += timedelta(seconds=6)
    reassigned = bridge.reconcile(run.id).run
    replacement = next(
        task for task in reassigned.task_graph if task.origin_task_id == original.task_id
    )

    fake_transport.complete_all_with_contracts(reassigned)
    for node in fake_transport.workflow["nodes"]:
        if node["id"] == original.task_id:
            node["status"] = "blocked"
    fake_transport.workflow["tasks_detail"] = [
        detail
        for detail in fake_transport.workflow["tasks_detail"]
        if detail["task_id"] != original.task_id
    ]
    waiting = bridge.reconcile(run.id).run
    assert waiting.state == RunState.WAITING_R2
    assert original.task_id not in waiting.checkpoint["accepted_contracts"]
    assert replacement.task_id in waiting.checkpoint["accepted_contracts"]

    fake_transport.ego_task = {
        **fake_transport.ego_task,
        "stage": "APPROVAL",
        "pending_approval": {"id": "apr-reassigned", "status": "approved"},
    }
    resumed = bridge.grant_r2(
        run.id,
        GrantRequest(
            approval_token="token-after-real-reassignment",
            idempotency_key="grant-reassigned-1",
        ),
    )
    assert len({task.task_id for task in resumed.task_graph}) == len(resumed.task_graph)
    old = next(task for task in resumed.task_graph if task.task_id == original.task_id)
    assert old.status == "blocked"
    execute = next(task for task in resumed.task_graph if task.stage == "EXECUTE")
    plan_review = next(task for task in resumed.task_graph if task.stage == "PLAN_REVIEW")
    assert execute.depends_on == [plan_review.task_id]


def test_matrix_failure_at_r2_pause_enters_and_recovers_compensation(
    bridge, fake_transport, monkeypatch
) -> None:
    run = _start(bridge)
    fake_transport.complete_all_with_contracts(run)
    original_send = bridge.matrix.send_envelope

    def fail_send(**_kwargs):
        raise UpstreamError("matrix", "send-envelope", 503, "offline")

    monkeypatch.setattr(bridge.matrix, "send_envelope", fail_send)
    fenced = bridge.reconcile(run.id)
    assert fenced.run.state == RunState.COMPENSATION_REQUIRED
    assert fenced.run.checkpoint["compensation_retry"]["operation"] == (
        "approval-required-notify"
    )
    assert any(action["action"] == "compensation_required" for action in fenced.actions)

    monkeypatch.setattr(bridge.matrix, "send_envelope", original_send)
    recovered = bridge.reconcile(run.id)
    assert recovered.run.state == RunState.WAITING_R2
    assert recovered.actions[0]["action"] == "compensation_recovered"
    assert "compensation_retry" not in recovered.run.checkpoint


def test_skill_evidence_distinguishes_assignment_authorization_and_tool_use(
    bridge, fake_transport
) -> None:
    run = _start(bridge)
    fake_transport.spawns_payload = {
        "project_id": run.agentteams_project_id,
        "workers": [
            {
                "worker": "ego-runtime",
                "spawns": [
                    {
                        "session_id": "sub-real-trace",
                        "status": "completed",
                        "spawn": True,
                        "subagent_skills": ["safe-experiment-runner"],
                        "subagent_allowed_tools": ["ego-gpu.launch_experiment"],
                    }
                ],
            }
        ],
    }
    fake_transport.spawn_messages_payload["sub-real-trace"] = {
        "session_id": "sub-real-trace",
        "task": "execute task-live",
        "messages": [
            {
                "seq": 7,
                "kind": "tool_result",
                "role": "assistant",
                "name": "ego-gpu.launch_experiment",
                "content": "accepted",
                "tool_state": "success",
            }
        ],
        "has_more": False,
    }
    payload = bridge.skill_evidence(run.id)
    levels = {item["level"] for item in payload["items"]}
    assert levels == {"DECLARED", "SPAWN_AUTHORIZED", "TOOL_INVOKED"}
    tool = next(item for item in payload["items"] if item["level"] == "TOOL_INVOKED")
    assert tool["tool"] == "ego-gpu.launch_experiment"
    assert tool["message_seq"] == 7
    assert "not_claimed" in payload["claim_boundary"]


def test_verified_benchmark_trace_has_required_real_agentteams_evidence(
    bridge, fake_transport, tmp_path
) -> None:
    run = _start(bridge)
    for node in fake_transport.workflow["nodes"]:
        node["status"] = "delegated"
    bridge.reconcile(run.id)
    fake_transport.complete_all_with_contracts(run)
    run = bridge.reconcile(run.id).run
    fake_transport.ego_task = {
        **fake_transport.ego_task,
        "stage": "APPROVAL",
        "pending_approval": {
            "id": "apr-live-trace",
            "status": "approved",
            "approver": "benchmark-human",
        },
    }
    run = bridge.grant_r2(
        run.id,
        GrantRequest(approval_token="trace-token-not-persisted", idempotency_key="trace-r2-0001"),
    )
    for node in fake_transport.workflow["nodes"]:
        if node["status"] == "pending":
            node["status"] = "delegated"
    bridge.reconcile(run.id)
    fake_transport.complete_all_with_contracts(run)
    run = bridge.reconcile(run.id).run
    assert run.state == RunState.COMPLETED

    fake_transport.spawns_payload = {
        "project_id": run.agentteams_project_id,
        "workers": [
            {
                "worker": "ego-runtime",
                "spawns": [
                    {
                        "session_id": "sub-benchmark-live",
                        "status": "completed",
                        "spawn": True,
                        "subagent_skills": ["safe-experiment-runner"],
                        "subagent_allowed_tools": ["ego-gpu.launch_experiment"],
                    }
                ],
            }
        ],
    }
    fake_transport.spawn_messages_payload["sub-benchmark-live"] = {
        "session_id": "sub-benchmark-live",
        "task": "execute the AgentTeams benchmark task",
        "messages": [
            {
                "seq": 9,
                "kind": "tool_result",
                "role": "assistant",
                "name": "ego-gpu.launch_experiment",
                "content": "accepted",
                "tool_state": "success",
            }
        ],
        "has_more": False,
    }
    fake_transport.ego_task = {
        **fake_transport.ego_task,
        "stage": "COMPLETED",
        "decision": "KEEP",
        "current_agent": "research-pi",
        "gate_result": {
            "status": "pass",
            "independent_reviewer": "ego-reviewer",
        },
    }
    snapshots = [
        {
            "state": run.state.value,
            "workflow_sha256": run.checkpoint["last_workflow_sha256"],
            "actions": [],
        }
    ]
    trace = _build_verified_trace(
        bridge,
        run,
        seed=41,
        scenario_id="trace-contract",
        probe={"controller": {"controller": "dev", "kubeMode": "embedded"}},
        snapshots=snapshots,
    )
    assert trace["schema_version"] == TRACE_SCHEMA_VERSION
    assert trace["source"] == "AgentTeams"
    assert trace["execution_mode"] == "real-agentteams"
    assert trace["synthetic"] is False
    assert len(trace["agents"]) >= 3
    assert {principal["kind"] for principal in trace["principals"]} == {
        "bridge",
        "human",
        "ego",
    }
    assert REQUIRED_TRACE_EVENTS <= {event["type"] for event in trace["events"]}
    assert [event["sequence"] for event in trace["events"]] == list(
        range(1, len(trace["events"]) + 1)
    )
    assert all(
        {"sequence", "type", "actor", "task_id", "correlation_id", "payload"} <= event.keys()
        for event in trace["events"]
    )
    assert all(trace["rxp"].values())
    declared_actors = {
        agent["id"] for agent in trace["agents"]
    } | {agent["matrix_user_id"] for agent in trace["agents"]} | {
        principal["id"] for principal in trace["principals"]
    }
    assert {event["actor"] for event in trace["events"]} <= declared_actors
    assert trace["bridge_event_chain"]["total"] == len(trace["events"])
    assert trace["bridge"]["api_version"] == "0.2.0"
    assert trace["bridge"]["benchmark_adapter_version"] == "rxp-bench/v1"
    assert trace["official_contract"]["main_commit"] == (
        "223ddc2b8073e4c8b93bcbb15e1d717f196c04d9"
    )
    assert _bind_scenario_proof(trace, "happy_path") is False
    assert {
        "unsafe_action.blocked",
        "effect.committed",
        "effect.replayed",
    } <= set(trace["scenario_proof"]["missing_event_types"])
    relative_path, digest = _write_trace(tmp_path, trace)
    assert relative_path == "agentteams-live-trace.json"
    assert digest == hashlib.sha256((tmp_path / relative_path).read_bytes()).hexdigest()
