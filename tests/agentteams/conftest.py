from __future__ import annotations

import hashlib
import json
import urllib.parse
from datetime import datetime, timezone
from typing import Any, Dict, List, Mapping, Optional

import pytest

from apps.agentteams_bridge.clients import AgentTeamsClient, EgoClient, MatrixClient
from apps.agentteams_bridge.models import canonical_json
from apps.agentteams_bridge.service import AgentTeamsBridge
from apps.agentteams_bridge.store import BridgeStore
from apps.agentteams_bridge.transport import HTTPResponse


WORKER_SKILLS = {
    "ego-research-lead": ["evidence-gate", "research-plan"],
    "ego-scout": ["research-memory"],
    "ego-architect": ["research-plan", "ablation-analyzer"],
    "ego-runtime": ["safe-experiment-runner", "dataset-manifest"],
    "ego-evaluator": ["ablation-analyzer"],
    "ego-reviewer": ["evidence-gate"],
    "ego-memory-curator": ["research-memory"],
}


class MutableClock:
    def __init__(self) -> None:
        self.value = datetime(2026, 8, 29, 10, 0, tzinfo=timezone.utc)

    def __call__(self) -> datetime:
        return self.value


class FakeTransport:
    def __init__(self) -> None:
        self.calls: List[Dict[str, Any]] = []
        self.project_id = ""
        self.team = {
            "name": "ego-researchops",
            "teamName": "ego-researchops",
            "phase": "Active",
            "workerMembers": [
                {"name": name, "role": "team_leader" if name == "ego-research-lead" else "worker"}
                for name in WORKER_SKILLS
            ],
            "leaderName": "ego-research-lead",
            "teamRoomID": "!ego-team:matrix.fixture.invalid",
            "leaderDMRoomID": "!ego-leader:matrix.fixture.invalid",
            "leaderReady": True,
            "readyWorkers": 7,
            "totalWorkers": 7,
        }
        self.workflow: Dict[str, Any] = self._workflow([])
        self.ego_task: Dict[str, Any] = {
            "id": "task-live",
            "stage": "INTAKE",
            "synthetic_demo": False,
            "pending_approval": None,
        }
        self.artifacts: Dict[str, bytes] = {}
        self.spawns_payload: Dict[str, Any] = {"project_id": "", "workers": []}
        self.spawn_messages_payload: Dict[str, Dict[str, Any]] = {}
        self.fail_next: Optional[tuple[str, str, int, Any]] = None

    def _workflow(self, nodes: List[Dict[str, Any]]) -> Dict[str, Any]:
        edges = []
        for index in range(1, len(nodes)):
            edges.append(
                {"source": nodes[index - 1]["id"], "target": nodes[index]["id"], "conditional": False}
            )
        return {
            "project_id": self.project_id,
            "title": "fixture",
            "status": "active",
            "plan_type": "dag",
            "team_id": "ego-researchops",
            "mode": "project",
            "nodes": nodes,
            "edges": edges,
            "next": [],
            "interrupts": [],
            "tasks_detail": [],
        }

    @staticmethod
    def _response(status: int, payload: Any) -> HTTPResponse:
        if isinstance(payload, bytes):
            body = payload
        elif isinstance(payload, str):
            body = payload.encode()
        else:
            body = json.dumps(payload).encode()
        return HTTPResponse(status=status, headers={"content-type": "application/json"}, body=body)

    def request(
        self,
        method: str,
        url: str,
        *,
        headers: Optional[Mapping[str, str]] = None,
        json_body: Any = None,
        timeout: float = 15.0,
    ) -> HTTPResponse:
        parsed = urllib.parse.urlparse(url)
        path = parsed.path
        self.calls.append(
            {"method": method, "url": url, "path": path, "headers": dict(headers or {}), "json": json_body}
        )
        if self.fail_next and method == self.fail_next[0] and path == self.fail_next[1]:
            _, _, status, payload = self.fail_next
            self.fail_next = None
            return self._response(status, payload)
        if path == "/healthz":
            return self._response(200, "ok")
        if path == "/api/v1/version":
            return self._response(200, {"controller": "dev", "kubeMode": "embedded"})
        if path == "/api/v1/projects" and method == "GET":
            return self._response(200, {"projects": [], "total": 0})
        if path == "/_matrix/client/v3/account/whoami" and method == "GET":
            return self._response(
                200,
                {
                    "user_id": "@ego-bridge:matrix.fixture.invalid",
                    "device_id": "fixture-device",
                },
            )
        if path == "/api/v1/projects" and method == "POST":
            self.project_id = json_body["project_id"]
            self.workflow["project_id"] = self.project_id
            self.spawns_payload["project_id"] = self.project_id
            return self._response(
                201,
                {
                    "project_id": self.project_id,
                    "title": json_body["title"],
                    "status": "active",
                    "team_id": json_body["team_id"],
                    "plan_type": "dag",
                },
            )
        if path == "/api/v1/teams/ego-researchops":
            return self._response(200, self.team)
        if path.startswith("/api/v1/workers/") and path.endswith("/ensure-ready"):
            name = path.split("/")[4]
            return self._response(200, {"name": name, "phase": "Ready"})
        if path.startswith("/api/v1/workers/"):
            name = path.split("/")[4]
            return self._response(
                200,
                {
                    "name": name,
                    "phase": "Running",
                    "state": "Running",
                    "skills": WORKER_SKILLS[name],
                    "matrixUserID": "@%s:matrix.fixture.invalid" % name,
                    "roomID": "!%s:matrix.fixture.invalid" % name,
                    "team": "ego-researchops",
                    "role": "team_leader" if name == "ego-research-lead" else "worker",
                },
            )
        if path == "/api/v1/tasks/task-live" and method == "GET":
            return self._response(200, self.ego_task)
        if path == "/api/v1/tasks/task-live/advance" and method == "POST":
            self.ego_task = {
                **self.ego_task,
                "stage": "EXECUTE",
                "pending_approval": None,
            }
            return self._response(200, {"task": self.ego_task})
        if "/workflow" in path and method == "GET":
            return self._response(200, self.workflow)
        if path.endswith("/replan") and method == "POST":
            previous = {node["id"]: node for node in self.workflow.get("nodes", [])}
            nodes = []
            for task in json_body["tasks"]:
                prior = previous.get(task["taskId"], {})
                status = task.get("status", prior.get("status", "planned"))
                normalized = {
                    "planned": "pending",
                    "assigned": "delegated",
                    "in_progress": "in-progress",
                    "submitted": "in-progress",
                    "cancelled": "blocked",
                }.get(status, status)
                nodes.append(
                    {
                        "id": task["taskId"],
                        "name": task["title"],
                        "status": normalized,
                        "assignee": task.get("assignedTo"),
                    }
                )
            self.workflow = self._workflow(nodes)
            self.workflow["project_id"] = self.project_id
            return self._response(200, self.workflow)
        if path.endswith("/pause") and method == "POST":
            self.workflow["status"] = "paused"
            return self._response(200, self.workflow)
        if path.endswith("/resume") and method == "POST":
            self.workflow["status"] = "active"
            return self._response(200, self.workflow)
        if path.endswith("/complete") and method == "POST":
            self.workflow["status"] = "completed"
            return self._response(200, self.workflow)
        if path.endswith("/cancel") and method == "POST":
            task_id = path.split("/")[-2]
            for node in self.workflow["nodes"]:
                if node["id"] == task_id:
                    node["status"] = "blocked"
            return self._response(200, self.workflow)
        if path.endswith("/artifact") and method == "GET":
            artifact_path = urllib.parse.parse_qs(parsed.query)["path"][0]
            if artifact_path not in self.artifacts:
                return self._response(404, {"error": "fixture artifact missing"})
            return self._response(200, self.artifacts[artifact_path])
        if path.endswith("/spawns") and method == "GET":
            return self._response(200, self.spawns_payload)
        if "/spawns/" in path and path.endswith("/messages") and method == "GET":
            session_id = path.split("/")[-2]
            return self._response(200, self.spawn_messages_payload[session_id])
        if "/send/m.room.message/" in path and method == "PUT":
            return self._response(200, {"event_id": "$fixture-event-%d" % len(self.calls)})
        return self._response(404, {"error": "unhandled fixture route", "path": path})

    def complete_all_with_contracts(self, run: Any) -> None:
        nodes = []
        details = []
        for task in run.task_graph:
            primary = "shared/tasks/%s/output.bin" % task.task_id
            envelope_path = "shared/tasks/%s/result.ego-envelope.json" % task.task_id
            content = ("output:%s" % task.task_id).encode()
            self.artifacts[primary] = content
            envelope = {
                "schema": "egoagentos.agentteams-result.v1",
                "task_id": task.task_id,
                "project_id": run.agentteams_project_id,
                "trace_id": run.trace_id,
                "context_version": run.context_version,
                "status": "SUCCESS",
                "artifact_refs": [primary],
                "conflicts": [],
                "suggested_worker": None,
                "review_verdict": "PASS" if task.stage in {"PLAN_REVIEW", "VERIFY"} else None,
                "independent_review": task.stage in {"PLAN_REVIEW", "VERIFY"},
                "output_sha256": hashlib.sha256(content).hexdigest(),
            }
            self.artifacts[envelope_path] = canonical_json(envelope).encode()
            nodes.append(
                {
                    "id": task.task_id,
                    "name": task.title,
                    "status": "completed",
                    "assignee": task.assigned_to,
                }
            )
            details.append(
                {
                    "task_id": task.task_id,
                    "project_id": run.agentteams_project_id,
                    "status": "completed",
                    "assigned_to": task.assigned_to,
                    "result_status": "SUCCESS",
                    "deliverables": [
                        {"type": "file", "path": primary},
                        {"type": "file", "path": envelope_path},
                    ],
                    "result_path": "shared/tasks/%s/result.md" % task.task_id,
                }
            )
        self.workflow = self._workflow(nodes)
        self.workflow["project_id"] = run.agentteams_project_id
        self.workflow["tasks_detail"] = details


@pytest.fixture
def fake_transport() -> FakeTransport:
    return FakeTransport()


@pytest.fixture
def clock() -> MutableClock:
    return MutableClock()


@pytest.fixture
def bridge(fake_transport: FakeTransport, clock: MutableClock) -> AgentTeamsBridge:
    return AgentTeamsBridge(
        BridgeStore(":memory:"),
        AgentTeamsClient(
            "http://agentteams.fixture.invalid", token="controller-token", transport=fake_transport
        ),
        MatrixClient(
            "http://matrix.fixture.invalid",
            access_token="matrix-token",
            transport=fake_transport,
        ),
        EgoClient("http://ego.fixture.invalid", transport=fake_transport),
        clock=clock,
    )
