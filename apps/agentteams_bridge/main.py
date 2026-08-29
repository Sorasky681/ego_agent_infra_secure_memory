"""FastAPI surface for the live AgentTeams bridge."""

from __future__ import annotations

import uuid
from typing import Any, Dict, Optional

from fastapi import FastAPI, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from .clients import AgentTeamsClient, EgoClient, MatrixClient
from .errors import BridgeError
from .models import GrantRequest, StartRunRequest
from .service import AgentTeamsBridge
from .settings import BridgeSettings
from .store import build_bridge_store


def build_service(settings: Optional[BridgeSettings] = None) -> AgentTeamsBridge:
    resolved = settings or BridgeSettings.from_env()
    return AgentTeamsBridge(
        build_bridge_store(
            database_url=resolved.database_url,
            migration_database_url=resolved.migration_database_url,
            sqlite_path=resolved.database_path,
        ),
        AgentTeamsClient(
            resolved.agentteams_base_url,
            token=resolved.agentteams_auth_token,
            timeout=resolved.request_timeout_seconds,
        ),
        MatrixClient(
            resolved.matrix_base_url,
            access_token=resolved.matrix_access_token,
            timeout=resolved.request_timeout_seconds,
        ),
        EgoClient(
            resolved.ego_base_url,
            timeout=resolved.request_timeout_seconds,
        ),
    )


def create_app(service: Optional[AgentTeamsBridge] = None) -> FastAPI:
    application = FastAPI(
        title="EgoAgentOS AgentTeams Bridge",
        version="0.3.0",
        description=(
            "Durable bridge to the official AgentTeams Controller, TeamHarness workflow, "
            "and Matrix delivery plane. Dry-run responses are never reported as live."
        ),
    )
    application.state.bridge = service or build_service()

    @application.middleware("http")
    async def request_id(request: Request, call_next: Any) -> Any:
        request.state.request_id = request.headers.get("X-Request-ID") or "req_%s" % uuid.uuid4().hex
        response = await call_next(request)
        response.headers["X-Request-ID"] = request.state.request_id
        return response

    @application.exception_handler(BridgeError)
    async def bridge_error(request: Request, error: BridgeError) -> JSONResponse:
        payload = error.as_dict()
        payload["request_id"] = request.state.request_id
        return JSONResponse(status_code=error.status_code, content={"error": payload})

    @application.exception_handler(RequestValidationError)
    async def validation_error(request: Request, error: RequestValidationError) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content={
                "error": {
                    "code": "invalid_request",
                    "message": "Request validation failed",
                    "retryable": False,
                    "details": {"violations": error.errors()},
                    "request_id": request.state.request_id,
                }
            },
        )

    @application.get("/api/v1/agentteams/health")
    def health(request: Request, team: str = Query(default="ego-researchops")) -> Dict[str, Any]:
        return request.app.state.bridge.probe_live(team)

    @application.post("/api/v1/agentteams/runs", status_code=201)
    def start(body: StartRunRequest, request: Request) -> Dict[str, Any]:
        run = request.app.state.bridge.start_run(body)
        return {
            "run": run.model_dump(mode="json"),
            "live": run.mode == "live",
            "truth": "LIVE" if run.mode == "live" else "DRY_RUN_ONLY",
        }

    @application.get("/api/v1/agentteams/runs/{run_id}")
    def get_run(run_id: str, request: Request) -> Dict[str, Any]:
        return request.app.state.bridge.get_run(run_id).model_dump(mode="json")

    @application.post("/api/v1/agentteams/runs/{run_id}/reconcile")
    def reconcile(run_id: str, request: Request) -> Dict[str, Any]:
        return request.app.state.bridge.reconcile(run_id).model_dump(mode="json")

    @application.post("/api/v1/agentteams/runs/{run_id}/r2-grant")
    def grant(run_id: str, body: GrantRequest, request: Request) -> Dict[str, Any]:
        return request.app.state.bridge.grant_r2(run_id, body).model_dump(mode="json")

    @application.get("/api/v1/agentteams/runs/{run_id}/events")
    def events(run_id: str, request: Request) -> Dict[str, Any]:
        return request.app.state.bridge.store.events(run_id)

    @application.get("/api/v1/agentteams/runs/{run_id}/receipts")
    def receipts(run_id: str, request: Request) -> Dict[str, Any]:
        payload = request.app.state.bridge.store.receipts(run_id)
        return {
            **payload,
            "schema": "egoagentos.agentteams-upstream-receipts/v1",
            "append_only": True,
            "contains_raw_matrix_messages": any(
                item["source"] == "matrix" and item["kind"] == "raw-message"
                for item in payload["items"]
            ),
        }

    @application.get("/api/v1/agentteams/runs/{run_id}/acceptance-input-index")
    def acceptance_input_index(run_id: str, request: Request) -> Dict[str, Any]:
        return request.app.state.bridge.acceptance_input_index(run_id)

    @application.get("/api/v1/agentteams/runs/{run_id}/skill-evidence")
    def skills(run_id: str, request: Request) -> Dict[str, Any]:
        return request.app.state.bridge.skill_evidence(run_id)

    @application.post("/api/v1/agentteams/recover")
    def recover(request: Request) -> Dict[str, Any]:
        items = request.app.state.bridge.recover_active()
        return {"items": items, "total": len(items)}

    return application


app = create_app()
