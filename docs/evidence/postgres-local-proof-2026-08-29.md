# Local PostgreSQL contract proof — 2026-08-29

Truth boundary: this is a local disposable Docker PostgreSQL result. It is not evidence of
PolarDB provisioning, managed backup, failover, or PITR.

## Runtime

```text
PostgreSQL 16.14 on aarch64-unknown-linux-musl
postgres:16-alpine
schema migration: 001_control_plane.sql
installed audit triggers: 4
```

## Reproduction

```bash
docker run --name egoagentos-pg-contract \
  -e POSTGRES_DB=egoagentos_test \
  -e POSTGRES_USER=egoagentos_test \
  -e POSTGRES_PASSWORD='<local disposable value>' \
  -p 127.0.0.1:55439:5432 -d postgres:16-alpine

EGO_TEST_POSTGRES_URL='postgresql://egoagentos_test:<redacted>@127.0.0.1:55439/egoagentos_test' \
  uv run --python 3.9 --extra dev pytest tests/postgres -q
```

Observed result:

```text
..........                                                               [100%]
10 passed
```

The ten tests cover the full API persistence path, cross-record rollback, task row
serialization, stale-version rejection, eight concurrent audit writers, same-ID tenant
isolation, direct predecessor/tamper rejection, commit-only `LISTEN/NOTIFY`, migration
replay/checksum drift, least-privilege grants/RLS, and concurrent idempotency.

The executable assertions are the evidence source; this note is only an index to the
command and observed environment.
