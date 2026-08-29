import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Callable

import psycopg
import pytest
from fastapi.testclient import TestClient

from apps.api.errors import ConflictError, ControlPlaneError
from apps.api.main import create_app
from apps.api.models import ApprovalStatus, Stage
from apps.api.postgres_store import PostgresStore, STAGE_EVENT_CHANNEL
from apps.api.service import DEMO_TASK_ID, ResearchOpsService
from apps.api.store_factory import create_store


def _pause_for_approval(client: TestClient) -> dict[str, Any]:
    response = client.post("/api/v1/tasks/%s/autorun" % DEMO_TASK_ID, json={})
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["status"] == "paused"
    return payload["task"]["pending_approval"]


def _approve(client: TestClient, approval: dict[str, Any]) -> str:
    response = client.post(
        "/api/v1/approvals/%s/decision" % approval["id"],
        json={
            "decision": "approved",
            "approver": "postgres-contract-test",
            "expected_digest": approval["action_digest"],
        },
    )
    assert response.status_code == 200, response.text
    return str(response.json()["approval_token"])


def test_factory_and_full_api_contract_use_real_postgres(
    postgres_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("EGO_DATABASE_URL", postgres_url)
    store = create_store()
    assert isinstance(store, PostgresStore)
    assert store.engine == "postgresql"
    assert "@" not in store.location

    with TestClient(create_app(database_url=postgres_url)) as client:
        health = client.get("/api/v1/health").json()
        assert health["database"] == {
            "status": "ready",
            "engine": "postgresql",
            "location": store.location,
            "audit_events": "trigger_immutable_predecessor_guarded_hash_chain",
        }

        client.post("/api/v1/demo/reset", json={}).raise_for_status()
        approval = _pause_for_approval(client)
        token = _approve(client, approval)
        completed = client.post(
            "/api/v1/tasks/%s/autorun" % DEMO_TASK_ID,
            json={"approval_token": token},
        )
        assert completed.status_code == 200, completed.text
        assert completed.json()["status"] == "completed"
        assert completed.json()["task"]["stage"] == "COMPLETED"
        events = client.get("/api/v1/tasks/%s/events" % DEMO_TASK_ID).json()
        assert events["chain_valid"] is True
        assert len(events["events"]) > 10

    counts = store.counts()
    assert counts["tasks"] == 1
    assert counts["approvals"] >= 1
    assert counts["evidence"] >= 7
    assert counts["validated_memories"] == 2


def test_transaction_rolls_back_all_control_plane_writes(
    postgres_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = PostgresStore(postgres_url)
    service = ResearchOpsService(store)
    service.reset_demo("happy_path")
    paused = service.autorun(DEMO_TASK_ID)
    pending = paused["task"]["pending_approval"]
    decision = service.decide_approval(
        pending["id"], "approved", "postgres-rollback", pending["action_digest"]
    )

    def fail_before_execution_evidence(*_args: object) -> None:
        raise RuntimeError("simulated executor handoff failure")

    monkeypatch.setattr(service, "_enter_execute", fail_before_execution_evidence)
    with pytest.raises(RuntimeError, match="simulated executor handoff failure"):
        service.advance(DEMO_TASK_ID, approval_token=decision["approval_token"])

    task = store.get_task(DEMO_TASK_ID)
    approval = store.latest_approval(task.id, task.generation)
    assert task.stage == Stage.APPROVAL
    assert approval is not None and approval.status == ApprovalStatus.APPROVED
    assert not any(record.kind.value == "code" for record in store.list_evidence(task.id, task.generation))
    assert not any(
        event.event_type == "approval.token.consumed"
        for event in store.list_events(task.id, task.generation, limit=1000)
    )
    assert store.verify_event_chain(task.id, task.generation) is True


def test_optimistic_version_and_row_lock_serialize_two_services(postgres_url: str) -> None:
    first = ResearchOpsService(PostgresStore(postgres_url))
    second = ResearchOpsService(PostgresStore(postgres_url))
    barrier = threading.Barrier(2)

    def advance(service: ResearchOpsService) -> tuple[str, Any]:
        barrier.wait()
        try:
            return "ok", service.advance(DEMO_TASK_ID, Stage.CONTEXT)
        except ControlPlaneError as error:
            return "error", error.code

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(advance, (first, second)))

    assert sorted(result[0] for result in results) == ["error", "ok"]
    assert [result[1] for result in results if result[0] == "error"] == ["illegal_transition"]

    stale = first.store.get_task(DEMO_TASK_ID)
    current = first.store.get_task(DEMO_TASK_ID)
    current.version += 1
    first.store.save_task(current, expected_version=current.version - 1)
    stale.version += 1
    with pytest.raises(ConflictError) as caught:
        first.store.save_task(stale, expected_version=stale.version - 1)
    assert caught.value.code == "task_version_conflict"


def test_concurrent_event_writers_form_one_linear_hash_chain(postgres_url: str) -> None:
    stores = [PostgresStore(postgres_url) for _ in range(8)]
    ResearchOpsService(stores[0])
    task = stores[0].get_task(DEMO_TASK_ID)
    initial = len(stores[0].list_events(task.id, task.generation, limit=1000))
    barrier = threading.Barrier(len(stores))

    def append(item: tuple[int, PostgresStore]) -> str:
        index, store = item
        barrier.wait()
        return store.append_event(
            task.id,
            task.generation,
            "concurrency.probe",
            "writer-%s" % index,
            task.stage,
            {"writer": index},
        ).event_hash

    with ThreadPoolExecutor(max_workers=len(stores)) as executor:
        hashes = list(executor.map(append, enumerate(stores)))

    events = stores[0].list_events(task.id, task.generation, limit=1000)
    assert len(events) == initial + len(stores)
    assert len(set(hashes)) == len(stores)
    assert stores[0].verify_event_chain(task.id, task.generation) is True


def test_tenants_can_reuse_ids_without_cross_task_mutation(postgres_url: str) -> None:
    local_store = PostgresStore(postgres_url, tenant_id="local")
    other_store = PostgresStore(postgres_url, tenant_id="research-team-b")
    local = ResearchOpsService(local_store)
    ResearchOpsService(other_store)

    local_generation = local_store.get_task(DEMO_TASK_ID).generation
    other_before = other_store.get_task(DEMO_TASK_ID)
    assert other_before.generation != local_generation

    local.advance(DEMO_TASK_ID, Stage.CONTEXT)
    assert local_store.get_task(DEMO_TASK_ID).stage == Stage.CONTEXT
    assert other_store.get_task(DEMO_TASK_ID).stage == Stage.INTAKE
    assert local_store.counts()["tasks"] == 1
    assert other_store.counts()["tasks"] == 1


def test_database_rejects_bad_predecessor_update_delete_and_truncate(postgres_url: str) -> None:
    store = PostgresStore(postgres_url)
    ResearchOpsService(store)
    task = store.get_task(DEMO_TASK_ID)

    with psycopg.connect(postgres_url) as connection:
        with pytest.raises(psycopg.errors.IntegrityConstraintViolation, match="predecessor"):
            connection.execute(
                """
                INSERT INTO audit_events(
                    id, tenant_id, task_id, generation, event_type, actor, stage,
                    payload_json, previous_hash, event_hash, created_at
                ) VALUES (
                    'evt_bad_predecessor', 'local', %s, %s, 'tamper', 'attacker', NULL,
                    '{}'::jsonb, %s, %s, now()
                )
                """,
                (task.id, task.generation, "1" * 64, "2" * 64),
            )

    statements = (
        "UPDATE audit_events SET actor='tampered' WHERE tenant_id='local'",
        "DELETE FROM audit_events WHERE tenant_id='local'",
        "TRUNCATE audit_events",
    )
    for statement in statements:
        with psycopg.connect(postgres_url) as connection:
            with pytest.raises(psycopg.errors.IntegrityConstraintViolation, match="immutable"):
                connection.execute(statement)


def test_notify_is_commit_ordered_and_rollback_is_silent(postgres_url: str) -> None:
    store = PostgresStore(postgres_url)
    ResearchOpsService(store)
    task = store.get_task(DEMO_TASK_ID)
    before = len(store.list_events(task.id, task.generation, limit=1000))

    with store.stage_event_listener() as listener:
        with store.transaction():
            committed = store.append_event(
                task.id,
                task.generation,
                "notify.committed",
                "runtime-agent",
                task.stage,
                {"commit": True},
            )
            assert list(listener.notifies(timeout=0.05, stop_after=1)) == []

        notifications = list(listener.notifies(timeout=2.0, stop_after=1))
        assert len(notifications) == 1
        assert notifications[0].channel == STAGE_EVENT_CHANNEL
        payload = json.loads(notifications[0].payload)
        assert payload["id"] == committed.id
        assert payload["event_hash"] == committed.event_hash

        with pytest.raises(RuntimeError, match="force rollback"):
            with store.transaction():
                store.append_event(
                    task.id,
                    task.generation,
                    "notify.rolled_back",
                    "runtime-agent",
                    task.stage,
                    {"commit": False},
                )
                raise RuntimeError("force rollback")
        assert list(listener.notifies(timeout=0.1, stop_after=1)) == []

    events = store.list_events(task.id, task.generation, limit=1000)
    assert len(events) == before + 1
    assert not any(event.event_type == "notify.rolled_back" for event in events)


def test_migrations_replay_cleanly_and_idempotent_requests_execute_once(postgres_url: str) -> None:
    first = PostgresStore(postgres_url)
    second = PostgresStore(postgres_url)
    assert first.ping() and second.ping()
    with psycopg.connect(postgres_url, row_factory=psycopg.rows.dict_row) as connection:
        migrations = connection.execute(
            "SELECT version, sha256 FROM schema_migrations ORDER BY version"
        ).fetchall()
    assert [row["version"] for row in migrations] == ["001_control_plane.sql"]
    assert all(len(row["sha256"]) == 64 for row in migrations)

    first_app = create_app(database_url=postgres_url)
    second_app = create_app(database_url=postgres_url)
    barrier = threading.Barrier(2)
    original_first: Callable[..., Any] = first_app.state.service.reset_demo
    original_second: Callable[..., Any] = second_app.state.service.reset_demo

    def delayed(original: Callable[..., Any], scenario: str = "happy_path") -> Any:
        time.sleep(0.05)
        return original(scenario)

    first_app.state.service.reset_demo = lambda scenario="happy_path": delayed(
        original_first, scenario
    )
    second_app.state.service.reset_demo = lambda scenario="happy_path": delayed(
        original_second, scenario
    )

    with TestClient(first_app) as first_client, TestClient(second_app) as second_client:

        def reset(client: TestClient) -> dict[str, Any]:
            barrier.wait()
            response = client.post(
                "/api/v1/demo/reset",
                json={"scenario": "happy_path"},
                headers={"Idempotency-Key": "postgres-concurrent-reset"},
            )
            assert response.status_code == 200, response.text
            return response.json()

        with ThreadPoolExecutor(max_workers=2) as executor:
            responses = list(executor.map(reset, (first_client, second_client)))

    assert responses[0]["task"]["generation"] == responses[1]["task"]["generation"]
    assert sum(bool(response.get("idempotent_replay")) for response in responses) == 1


def test_security_roles_are_least_privilege_and_rls_scopes_tenants(postgres_url: str) -> None:
    store = PostgresStore(postgres_url)
    ResearchOpsService(store)
    security_sql = (
        Path(__file__).resolve().parents[2] / "deploy/postgres/security_roles.sql"
    ).read_text(encoding="utf-8")

    with psycopg.connect(postgres_url) as connection:
        connection.execute(security_sql)
        privileges = connection.execute(
            """
            SELECT
              has_table_privilege('egoagentos_runtime', 'audit_events', 'SELECT') AS can_read,
              has_table_privilege('egoagentos_runtime', 'audit_events', 'INSERT') AS can_insert,
              has_table_privilege('egoagentos_runtime', 'audit_events', 'UPDATE') AS can_update,
              has_table_privilege('egoagentos_runtime', 'tasks', 'DELETE') AS can_delete_task,
              has_table_privilege('egoagentos_auditor', 'tasks', 'SELECT') AS auditor_read,
              has_table_privilege('egoagentos_auditor', 'tasks', 'INSERT') AS auditor_insert
            """
        ).fetchone()
        assert privileges == (True, True, False, False, True, False)

        connection.execute(
            """
            INSERT INTO tasks(id, tenant_id, generation, version, task_json, created_at, updated_at)
            SELECT 'other-tenant-task', 'other', generation, version, task_json, created_at, updated_at
              FROM tasks WHERE id=%s
            """,
            (DEMO_TASK_ID,),
        )
        connection.execute("SET ROLE egoagentos_runtime")
        connection.execute("SELECT set_config('egoagentos.tenant_id', 'local', true)")
        visible = connection.execute("SELECT id FROM tasks ORDER BY id").fetchall()
        assert visible == [(DEMO_TASK_ID,)]
        predecessor = connection.execute(
            """
            SELECT event_hash FROM audit_events
             WHERE task_id=%s ORDER BY sequence DESC LIMIT 1
            """,
            (DEMO_TASK_ID,),
        ).fetchone()
        assert predecessor is not None
        connection.execute(
            """
            INSERT INTO audit_events(
                id, tenant_id, task_id, generation, event_type, actor, stage,
                payload_json, previous_hash, event_hash, created_at
            )
            SELECT 'evt_runtime_role_probe', tenant_id, id, generation,
                   'security.runtime.insert', 'runtime-role', task_json->>'stage',
                   '{}'::jsonb, %s, %s, now()
              FROM tasks WHERE id=%s
            """,
            (predecessor[0], "3" * 64, DEMO_TASK_ID),
        )


def test_migration_checksum_drift_fails_closed(postgres_url: str) -> None:
    PostgresStore(postgres_url)
    with psycopg.connect(postgres_url) as connection:
        connection.execute(
            "UPDATE schema_migrations SET sha256=%s WHERE version='001_control_plane.sql'",
            ("0" * 64,),
        )
    with pytest.raises(RuntimeError, match="checksum differs"):
        PostgresStore(postgres_url)
