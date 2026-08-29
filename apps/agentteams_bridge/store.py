"""Durable bridge state and tamper-evident event ledger."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
import uuid
from pathlib import Path
from typing import Any, Dict, List

from .errors import BridgeError
from .models import BridgeRun, CollaborationEnvelope, RunState, canonical_json, utc_now


ZERO_HASH = "0" * 64


class BridgeStore:
    def __init__(self, path: str) -> None:
        self.path = path
        self._lock = threading.RLock()
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(path, check_same_thread=False)
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA foreign_keys = ON")
        self._connection.execute("PRAGMA journal_mode = WAL")
        self._create_schema()

    def _create_schema(self) -> None:
        with self._connection:
            self._connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS bridge_runs (
                    id TEXT PRIMARY KEY,
                    ego_task_id TEXT NOT NULL,
                    agentteams_project_id TEXT NOT NULL UNIQUE,
                    team TEXT NOT NULL,
                    trace_id TEXT NOT NULL,
                    correlation_id TEXT NOT NULL,
                    context_version INTEGER NOT NULL,
                    state TEXT NOT NULL,
                    mode TEXT NOT NULL,
                    objective TEXT NOT NULL,
                    task_graph_json TEXT NOT NULL,
                    checkpoint_json TEXT NOT NULL,
                    ack_timeout_seconds INTEGER NOT NULL,
                    execution_timeout_seconds INTEGER NOT NULL,
                    max_reassignments INTEGER NOT NULL,
                    version INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_bridge_runs_task
                    ON bridge_runs(ego_task_id);
                CREATE TABLE IF NOT EXISTS bridge_events (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_id TEXT NOT NULL UNIQUE,
                    run_id TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    envelope_json TEXT NOT NULL,
                    previous_hash TEXT NOT NULL,
                    event_hash TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(run_id) REFERENCES bridge_runs(id)
                );
                CREATE INDEX IF NOT EXISTS idx_bridge_events_run
                    ON bridge_events(run_id, sequence);
                """
            )

    @staticmethod
    def _row_to_run(row: sqlite3.Row) -> BridgeRun:
        return BridgeRun.model_validate(
            {
                "id": row["id"],
                "ego_task_id": row["ego_task_id"],
                "agentteams_project_id": row["agentteams_project_id"],
                "team": row["team"],
                "trace_id": row["trace_id"],
                "correlation_id": row["correlation_id"],
                "context_version": row["context_version"],
                "state": row["state"],
                "mode": row["mode"],
                "objective": row["objective"],
                "task_graph": json.loads(row["task_graph_json"]),
                "checkpoint": json.loads(row["checkpoint_json"]),
                "ack_timeout_seconds": row["ack_timeout_seconds"],
                "execution_timeout_seconds": row["execution_timeout_seconds"],
                "max_reassignments": row["max_reassignments"],
                "version": row["version"],
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
            }
        )

    def create_run(self, run: BridgeRun) -> BridgeRun:
        with self._lock, self._connection:
            try:
                self._connection.execute(
                    """
                    INSERT INTO bridge_runs (
                        id, ego_task_id, agentteams_project_id, team, trace_id,
                        correlation_id, context_version, state, mode, objective,
                        task_graph_json, checkpoint_json, ack_timeout_seconds,
                        execution_timeout_seconds, max_reassignments, version,
                        created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        run.id,
                        run.ego_task_id,
                        run.agentteams_project_id,
                        run.team,
                        run.trace_id,
                        run.correlation_id,
                        run.context_version,
                        run.state.value,
                        run.mode,
                        run.objective,
                        canonical_json(
                            [task.model_dump(mode="json") for task in run.task_graph]
                        ),
                        canonical_json(run.checkpoint),
                        run.ack_timeout_seconds,
                        run.execution_timeout_seconds,
                        run.max_reassignments,
                        run.version,
                        run.created_at.isoformat(),
                        run.updated_at.isoformat(),
                    ),
                )
            except sqlite3.IntegrityError as error:
                raise BridgeError(
                    "run_conflict",
                    "A bridge run already owns this AgentTeams project",
                    details={"project_id": run.agentteams_project_id},
                ) from error
        return run

    def get_run(self, run_id: str) -> BridgeRun:
        with self._lock:
            row = self._connection.execute(
                "SELECT * FROM bridge_runs WHERE id = ?", (run_id,)
            ).fetchone()
        if row is None:
            raise BridgeError(
                "run_not_found", "Bridge run was not found", status_code=404, details={"id": run_id}
            )
        return self._row_to_run(row)

    def update_run(self, run: BridgeRun, *, expected_version: int) -> BridgeRun:
        updated = run.model_copy(update={"version": expected_version + 1, "updated_at": utc_now()})
        with self._lock, self._connection:
            cursor = self._connection.execute(
                """
                UPDATE bridge_runs SET
                    state = ?, task_graph_json = ?, checkpoint_json = ?, version = ?, updated_at = ?
                WHERE id = ? AND version = ?
                """,
                (
                    updated.state.value,
                    canonical_json(
                        [task.model_dump(mode="json") for task in updated.task_graph]
                    ),
                    canonical_json(updated.checkpoint),
                    updated.version,
                    updated.updated_at.isoformat(),
                    updated.id,
                    expected_version,
                ),
            )
            if cursor.rowcount != 1:
                raise BridgeError(
                    "run_version_conflict",
                    "Bridge run was concurrently modified; reload before retrying",
                    retryable=True,
                    details={"run_id": run.id, "expected_version": expected_version},
                )
        return updated

    def append_event(
        self, run_id: str, envelope: CollaborationEnvelope
    ) -> Dict[str, Any]:
        envelope_payload = envelope.model_dump(mode="json", by_alias=True)
        created_at = envelope.created_at.isoformat()
        event_id = "evt_%s" % uuid.uuid4().hex
        with self._lock, self._connection:
            row = self._connection.execute(
                "SELECT event_hash FROM bridge_events WHERE run_id = ? ORDER BY sequence DESC LIMIT 1",
                (run_id,),
            ).fetchone()
            previous_hash = row["event_hash"] if row is not None else ZERO_HASH
            hash_payload = {
                "event_id": event_id,
                "run_id": run_id,
                "kind": envelope.kind.value,
                "envelope": envelope_payload,
                "previous_hash": previous_hash,
                "created_at": created_at,
            }
            event_hash = hashlib.sha256(canonical_json(hash_payload).encode("utf-8")).hexdigest()
            cursor = self._connection.execute(
                """
                INSERT INTO bridge_events (
                    event_id, run_id, kind, envelope_json, previous_hash, event_hash, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event_id,
                    run_id,
                    envelope.kind.value,
                    canonical_json(envelope_payload),
                    previous_hash,
                    event_hash,
                    created_at,
                ),
            )
            if cursor.lastrowid is None:
                raise BridgeError("event_sequence_missing", "SQLite did not return an event sequence")
            sequence = cursor.lastrowid
        return {
            "sequence": sequence,
            "event_id": event_id,
            "run_id": run_id,
            "kind": envelope.kind.value,
            "envelope": envelope_payload,
            "previous_hash": previous_hash,
            "event_hash": event_hash,
            "created_at": created_at,
        }

    def events(self, run_id: str) -> Dict[str, Any]:
        self.get_run(run_id)
        with self._lock:
            rows = self._connection.execute(
                "SELECT * FROM bridge_events WHERE run_id = ? ORDER BY sequence", (run_id,)
            ).fetchall()
        items: List[Dict[str, Any]] = []
        expected_previous = ZERO_HASH
        chain_valid = True
        for row in rows:
            envelope = json.loads(row["envelope_json"])
            hash_payload = {
                "event_id": row["event_id"],
                "run_id": row["run_id"],
                "kind": row["kind"],
                "envelope": envelope,
                "previous_hash": row["previous_hash"],
                "created_at": row["created_at"],
            }
            expected_hash = hashlib.sha256(
                canonical_json(hash_payload).encode("utf-8")
            ).hexdigest()
            if row["previous_hash"] != expected_previous or row["event_hash"] != expected_hash:
                chain_valid = False
            expected_previous = row["event_hash"]
            items.append(
                {
                    "sequence": row["sequence"],
                    "event_id": row["event_id"],
                    "kind": row["kind"],
                    "envelope": envelope,
                    "previous_hash": row["previous_hash"],
                    "event_hash": row["event_hash"],
                    "created_at": row["created_at"],
                }
            )
        return {"items": items, "total": len(items), "chain_valid": chain_valid}

    def active_runs(self) -> List[BridgeRun]:
        terminal = (RunState.BLOCKED.value, RunState.COMPLETED.value)
        with self._lock:
            rows = self._connection.execute(
                "SELECT * FROM bridge_runs WHERE state NOT IN (?, ?) ORDER BY created_at",
                terminal,
            ).fetchall()
        return [self._row_to_run(row) for row in rows]
