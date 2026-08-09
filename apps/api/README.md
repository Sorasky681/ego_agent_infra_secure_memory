# EgoAgentOS ResearchOps API

FastAPI + SQLite control plane for the EgoLite competition demo. The workflow is a strict
state machine; policy, approvals, provenance hashes, evaluation, evidence verification, and
audit persistence are deterministic Python rather than LLM assertions.

The seeded values are always labelled **SYNTHETIC DEMO DATA**. HiClaw, Nacos, and Higress are
optional adapter metadata and are never reported as live unless a future adapter performs a
verified handshake.

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

