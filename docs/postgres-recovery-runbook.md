# PostgreSQL / PolarDB-PG data and recovery runbook

## Evidence boundary

PostgreSQL is the production data path. SQLite remains only the zero-service developer
fallback. Both runtime surfaces preserve an explicit synchronous store contract:

- the control plane selects PostgreSQL with a `postgresql://` or `postgres://`
  `EGO_DATABASE_URL`; leaving it unset selects the SQLite `EGO_DB_PATH` fallback;
- the AgentTeams bridge selects its PostgreSQL JSONB checkpoint/event/receipt store with
  `EGO_AGENTTEAMS_DATABASE_URL`; leaving it unset selects its SQLite development store;
- both PostgreSQL paths are exercised together by the real local PostgreSQL 16.14 suite.
- PolarDB for PostgreSQL is a compatibility target. No cloud instance, backup policy,
  failover, or point-in-time recovery (PITR) has been executed without project credentials.

The API redacts user information from the health response and reports only
`host[:port]/database`.

## Local startup

Generate a local-only secret and keep it in the ignored `.env` file:

```bash
cp .env.example .env
openssl rand -hex 32
# Paste that value into EGO_POSTGRES_PASSWORD in .env.
docker compose up --build
curl --fail http://127.0.0.1:8000/api/v1/health
```

The default Compose path is PostgreSQL 16 with a health-gated API dependency. For the
SQLite developer path, leave `EGO_DATABASE_URL` unset and start the API directly:

```bash
EGO_DB_PATH=/tmp/egoagentos.sqlite3 uv run uvicorn apps.api.main:app --port 8000
```

For a remote PostgreSQL-compatible service, URL-encode credentials and require TLS:

```bash
EGO_DATABASE_URL='postgresql://USER:PASSWORD@HOST:5432/DB?sslmode=require' \
  EGO_DATABASE_MIGRATION_MODE=verify \
  uv run uvicorn apps.api.main:app --host 0.0.0.0 --port 8000
```

Use `apply` only for a migration-owner startup or the local Compose profile. Restricted
runtime replicas should use `verify`, which performs an exact read-only comparison of all
packaged migration versions and checksums.

The bridge uses its own migration-owner URL so its restricted runtime never needs DDL:

```bash
EGO_AGENTTEAMS_DATABASE_URL='postgresql://BRIDGE_RUNTIME:REDACTED@HOST:5432/DB?sslmode=require' \
  EGO_AGENTTEAMS_MIGRATION_DATABASE_URL='postgresql://MIGRATION_OWNER:REDACTED@HOST:5432/DB?sslmode=require' \
  uv run uvicorn apps.agentteams_bridge.main:app --host 0.0.0.0 --port 8010
```

## Schema and concurrency invariants

In `apply` mode, the API takes a migration advisory lock, creates `schema_migrations`, and
applies each packaged SQL migration once in one transaction. The migrations create the
task, approval, evidence, memory-candidate, validated-memory, idempotency, and audit
schema plus database-enforced ledger boundaries. Each applied file records a SHA-256
checksum; packaged SQL drift fails startup rather than silently changing the meaning of
an already-applied version. In `verify` mode no DDL is attempted and any missing,
unexpected, or mismatched migration fails startup.

PostgreSQL enforces these boundaries:

1. Task writes use `WHERE version = expected_version` optimistic concurrency.
2. Service mutations lock the task/approval row before state or token transitions.
3. Idempotency keys use a transaction-scoped advisory lock before cache lookup.
4. Every audit stream `(tenant, task, generation)` uses a transaction-scoped advisory
   lock. A database trigger independently rejects a predecessor other than the current
   stream head, including direct SQL writes.
5. `UPDATE`, `DELETE`, and `TRUNCATE` of `audit_events` are rejected by triggers.
6. `evidence`, `memory_candidates`, and validated `memories` are append-only; their
   `UPDATE`, `DELETE`, and `TRUNCATE` operations are rejected by database triggers.
7. The `ego_stage_events` notification is emitted by an `AFTER INSERT` trigger and becomes
   visible to `LISTEN` consumers only after commit. A rolled-back event is silent.

`NOTIFY` is a low-latency wake-up, not the durable queue. Consumers checkpoint the audit
`sequence` and replay committed rows after reconnect; `stage_event_listener()` therefore
uses a dedicated session connection and must not be placed behind transaction pooling.

The optional [security role SQL](../deploy/postgres/security_roles.sql) creates separate
NOLOGIN runtime, auditor, evidence-writer, and memory-curator roles, least-privilege
grants, and tenant RLS policies without embedding a password. Evidence Writer can mutate
only the evidence ledger; Memory Curator can mutate only `memory_candidates`, never
validated memory. A deployment owner must create LOGIN identities from its secret
manager, grant these roles, and set `egoagentos.tenant_id` per connection.
The RLS helper returns no tenant when that setting is absent, so runtime access fails closed.
The bridge has a separate
[`egoagentos_bridge_runtime`](../deploy/postgres/agentteams_bridge_security.sql) role; it can
update run/checkpoint state but cannot update, delete, truncate, or disable triggers on the
event and receipt ledgers.

## Verified integration test

Against an explicit disposable test database:

```bash
EGO_TEST_POSTGRES_URL='postgresql://USER:PASSWORD@127.0.0.1:5432/TEST_DB' \
  make test-postgres
```

The suite recreates only the `public` schema of that explicit test database. The verified
2026-08-29 result is **27/27 PASS on local PostgreSQL 16.14**. It covers:

- full API completion, atomic rollback, optimistic concurrency, tenant isolation,
  idempotency contention, durable event cursors, commit-ordered `LISTEN/NOTIFY`, and
  fresh migration/checksum replay;
- runtime/auditor/evidence-writer/memory-curator roles, RLS, candidate-only memory curation,
  and database-enforced append-only evidence/memory/audit ledgers;
- AgentTeams JSONB checkpoints, CAS/per-run advisory locks, restart recovery, serialized
  event chains, receipt idempotency/uniqueness, append-only bridge ledgers, and concurrent
  migration initialization;
- the generic PostgreSQL fixture for the PolarDB preflight contract and fresh-schema gates.

CI provides an isolated `postgres:16-alpine` service and runs the same suite. This result is
not PolarDB provisioning, provider identity, managed backup, PITR, failover, or cloud IAM
evidence.

## Logical backup and restore drill

Use a restricted operator identity and a fresh destination database:

```bash
pg_dump --format=custom --no-owner --file=egoagentos.dump "$EGO_DATABASE_URL"
createdb egoagentos_restore
pg_restore --exit-on-error --no-owner --dbname=egoagentos_restore egoagentos.dump
```

Before cutover:

1. Point a non-production API at the restored database and check `/api/v1/health`.
2. Confirm every expected migration exists in `schema_migrations`.
3. Replay `/api/v1/tasks/{task_id}/events` for representative generations and require
   `chain_valid=true`.
4. Compare table counts and artifact digests; do not compare only HTTP availability.
5. Change the connection secret through the deployment secret manager, then retain the
   old database read-only until acceptance completes.

## PITR and PolarDB boundary

Self-managed PostgreSQL PITR requires WAL archiving and a tested base backup. Managed
PolarDB recovery must be configured and rehearsed through the cloud backup policy and a
temporary restored cluster. This repository cannot prove retention, RPO, RTO, regional
failover, or PITR without the target account and a recovery drill. Required evidence for
that future claim is: backup-policy export, restore job ID, restored-cluster endpoint,
event-chain verification report, measured RPO/RTO, and teardown record.
