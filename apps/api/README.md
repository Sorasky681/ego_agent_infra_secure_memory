# EgoAgentOS ResearchOps API

FastAPI control plane with PostgreSQL as the production persistence path and SQLite as a
zero-service developer fallback for the EgoLite competition demo. The workflow is a strict
state machine; policy, approvals, provenance hashes, evaluation, evidence verification, and
audit persistence are deterministic Python rather than LLM assertions.

The seeded values are always labelled **SYNTHETIC DEMO DATA**. HiClaw, Nacos, and Higress are
optional adapter metadata and are never reported as live unless a future adapter performs a
verified handshake.

## External live task and finalization contract

`POST /api/v1/tasks` is separate from the demo reset path. Its strict request requires
`synthetic: false`, an immutable AgentTeams `live_source` binding, a frozen `ResearchGoal`, and
an exact R2/R3 `execution_contract`. Omitting `synthetic`, sending `true`, reusing a task id, or
submitting an action payload without its matching config digest fails closed. A live task never
receives the demo's generated artifacts or modeled metrics.

After the AgentTeams pre-approval DAG completes, the bridge advances only the legal
`INTAKE -> CONTEXT -> PLAN -> PLAN_REVIEW -> APPROVAL` path. The human decision still produces a
scope-bound, expiring, single-use token; consuming it is the only way to enter `EXECUTE`.

Two typed evidence routes are available after that point:

- `POST /api/v1/tasks/{task_id}/evidence` ingests one version- and generation-bound record;
- `POST /api/v1/tasks/{task_id}/finalize` atomically ingests the remaining evidence and advances
  `EXECUTE -> OBSERVE -> EVALUATE -> VERIFY -> DECIDE -> ... -> COMPLETED`.

The terminal route requires exactly the seven evidence kinds, successful AgentTeams receipts,
a GPU receipt for metric evidence, raw paired samples whose digest matches, byte-for-byte
deterministic metric recomputation, and an independent PASS review binding the exact non-review
evidence digests. `KEEP`, `DROP`, or `INCONCLUSIVE` is derived from the recomputed results; it is
not accepted from the caller. Any missing item, stale version, digest mismatch, failed receipt,
or forged reviewer rolls the transaction back without terminal progress.

## Run locally

From the repository root:

```bash
python3.9 -m venv .venv
. .venv/bin/activate
pip install -e '.[dev]'
EGO_DB_PATH=/tmp/egoagentos.sqlite3 uvicorn apps.api.main:app --reload
```

Open `http://127.0.0.1:8000/docs` or check:

```bash
curl http://127.0.0.1:8000/api/v1/health
curl http://127.0.0.1:8000/api/v1/dashboard
```

The happy-path demo is deliberately two-part:

1. `POST /api/v1/demo/reset`, then `POST /api/v1/tasks/ego-lite-001/autorun` pauses at R2 approval.
2. Approve `pending_approval.id` with its exact `action_digest`. The response returns a
   scope-bound one-time token. Send that token as `approval_token` to `autorun` to complete.

To demonstrate an evidence failure, reset with
`{"scenario":"insufficient_evidence"}`. That run deliberately omits trace evidence and pauses
at `VERIFY`; the response states the exact missing artifact without claiming success.

Every mutating route accepts `Idempotency-Key`. Reusing a key with a different body returns a
structured conflict. API errors use `{"error":{"code", "message", "details", "request_id"}}`.

At `MEMORY_SKILL`, the Memory Curator can append only `memory_candidates`. A separate
deterministic `memory-validator` actor checks the completed Evidence Gate and creates validated
memory. PostgreSQL `GRANT`/RLS policy denies the Curator direct `memories` inserts, and database
triggers reject update/delete/truncate on evidence, candidate, and validated-memory ledgers.

Set `EGO_DATABASE_URL=postgresql://...` to use the real psycopg backend. It implements the
same store contract with atomic transactions, optimistic task versions, row locks, an
append-only database trigger, stream-scoped advisory locks, durable event cursors, and
commit-ordered `ego_stage_events` notifications. Use `EGO_DATABASE_MIGRATION_MODE=apply` only
with a migration-capable owner; restricted runtime roles use `verify`, which checks the exact
migration checksum map without attempting DDL. The four least-privilege roles are runtime,
auditor, evidence writer, and memory curator. See the
[database runbook](../../docs/postgres-recovery-runbook.md) for migration, RLS, test, backup,
and recovery procedures.

`apps/api/polardb_preflight.py` provides a fail-closed live acceptance command for a writer,
read-only node, roles, RLS, append-only triggers, JSONB/pgvector capability, migration state,
and transactional `LISTEN/NOTIFY`. Passing its generic PostgreSQL fixture tests is not proof of
a PolarDB deployment, backup policy, failover, or PITR restore; those remain `NOT RUN` until a
real cloud acceptance manifest and resulting evidence bundle are captured.

## External adapter truth states

Setting `EGO_HICLAW_URL`, `EGO_NACOS_URL`, or `EGO_HIGRESS_URL` changes the corresponding state
from `not_configured` to `configured_unverified`, never to a fabricated `ready` state. Configure
browser origins with comma-separated `EGO_CORS_ORIGINS`.

## Container

Build from the repository root so the Dockerfile can read `pyproject.toml`:

```bash
docker build -f apps/api/Dockerfile -t egoagentos-api .
docker run --rm -p 8000:8000 -v egoagentos-data:/data egoagentos-api
```

The image runs as the non-root `egoagentos` user and includes a `/api/v1/health` health check.
The repository Compose file starts PostgreSQL 16 by default and requires
`EGO_POSTGRES_PASSWORD` from the ignored `.env`; it does not contain a production secret.
