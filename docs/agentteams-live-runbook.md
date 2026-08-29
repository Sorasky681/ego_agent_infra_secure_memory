# AgentTeams live acceptance runbook

Use this runbook only for an installed official AgentTeams stack. Local unit
fixtures do not satisfy any live checkpoint.

## 1. Pin and verify

1. Check out `agentscope-ai/AgentTeams` commit
   `223ddc2b8073e4c8b93bcbb15e1d717f196c04d9` or a later release verified to
   contain the same Project Workflow endpoints.
2. Run `python integrations/agentteams/scripts/verify_official_contract.py`.
3. Record the official checkout commit and Controller deployment/image digest
   with the evaluation artifact.

## 2. Install and provision

Follow the official AgentTeams local or Kubernetes installation guide. Do not
copy installer-generated secrets into this repository.

Stage each EgoAgentOS Skill as a complete package in the Manager's Worker Skill
library, render `agentteams-resources.yaml.tmpl`, and apply it through the
official `agentteams-apply.sh` / `agt apply -f` path. Then verify:

```bash
agt get teams ego-researchops -o json
agt get workers ego-research-lead -o json
agt get workers ego-scout -o json
agt get workers ego-architect -o json
agt get workers ego-runtime -o json
agt get workers ego-evaluator -o json
agt get workers ego-reviewer -o json
agt get workers ego-memory-curator -o json
```

Acceptance requires Team `phase=Active`, `leaderReady=true`, and every member
ready. Separately inspect each Worker's canonical persistent Skill directory;
`spec.skills` alone is not runtime-load proof.

The Team Leader must expose the official TeamHarness coordination surface
(`project-management`, `task-management`, and `team-coordination`). EgoAgentOS
Skill assignment is separate: discovery in a Worker spec is `DECLARED`, a spawn
record is `SPAWN_AUTHORIZED`, and only a successful official spawn
`tool_result` is `TOOL_INVOKED`.

## 3. Configure least privilege

The Controller bearer token must be authorized for the target Team's project
read/write endpoints. The Matrix token must belong to a user already present in
the Team room. Workers receive gateway consumer access, not upstream provider
secrets. Never print either token in test output.

The bundled Compose profile publishes the operator API on loopback only. It
does not implement public-user authentication; any shared or remote deployment
must place it behind an authenticated operator ingress and must not expose port
8010 directly.

Set:

```text
AGENTTEAMS_CONTROLLER_URL
AGENTTEAMS_AUTH_TOKEN
AGENTTEAMS_MATRIX_URL
AGENTTEAMS_MATRIX_ACCESS_TOKEN
EGO_API_URL
EGO_AGENTTEAMS_BRIDGE_DB
```

## 4. Probe

Run:

```bash
python -m apps.agentteams_bridge.cli probe --team ego-researchops
```

The response may say `live: true` only after real Controller health, Project
API, Team, and readiness responses. Save it in the benchmark workspace.

## 5. Start and observe

Use a non-synthetic Ego task. The bridge intentionally rejects the bundled
EgoLite task. Start a run through the HTTP API or CLI. Confirm that:

1. Controller `POST /api/v1/projects` returned the same project ID persisted by
   the bridge;
2. Controller `replan` holds a cycle-free pre-R2 DAG;
3. Matrix returned an `event_id` for the structured `TASK_REQUEST`;
4. Team Leader delegates with TeamHarness;
5. Workers ACK, submit content-addressed artifacts, and the Leader accepts
   them;
6. workflow polls expose those transitions.

The bridge always scopes Project API calls with `?team=`. Official Controller
writes enforce `requireSameTeam` plus `checkProjectAccess` and perform their
storage update with an internal ETag/If-Match conditional write. A returned
`409` is therefore surfaced as a retryable structured conflict; it is not
silently retried over newer state.

Run reconcile periodically. Restarting the bridge is safe: the SQLite
checkpoint is reloaded and Controller workflow is fetched again. Use
`POST /api/v1/agentteams/recover` after restart.

## 6. R2 recovery

When all pre-R2 artifacts validate, the bridge pauses the Controller project
and enters `WAITING_R2`. A chat reply does nothing. Approve through EgoAgentOS,
then send the one-time scoped token to the bridge R2 endpoint with an
idempotency key.

The order is fixed:

1. consume the EgoAgentOS token for `APPROVAL → EXECUTE`;
2. persist only the receipt hash and grant ID;
3. resume the Controller project;
4. apply the post-R2 DAG;
5. publish `APPROVAL_GRANTED` to Matrix.

If steps 3–5 fail after token consumption, the bridge pauses the project and
enters `COMPENSATION_REQUIRED`. Retry with the same idempotency key; the bridge
does not consume the token again.

Notification failures after Controller replan/pause/complete are also durable
compensation states. `POST /api/v1/agentteams/runs/{run_id}/reconcile` retries
only the recorded recovery operation. A malformed 2xx grant response is treated
as consumed-but-unverified and fenced for manual inspection; the token is never
optimistically reused.

## 7. Fault acceptance

Exercise at least:

- ACK timeout → task cancel with `replacementTaskId` → alternate Worker replan;
- execution timeout → cancellation and bounded reassignment;
- stale `context_version` → conflict and replan;
- artifact digest mismatch → no acceptance;
- Controller 409 → structured retryable conflict, no overwrite;
- Controller 403/404 → structured non-retryable permission/not-found failure;
- Matrix failure after mutation → project pause compensation fence;
- bridge restart → persisted recovery without duplicate project creation;
- R2 token retry → no token reuse and no token in SQLite/events.

## 8. Benchmark evidence

Set `AGENTTEAMS_BENCHMARK_LIVE=1` only for this live stack. The canonical
benchmark inputs do not contain an Ego task ID or R2 token. Bind each scenario
to a separately prepared, non-synthetic task through an uncommitted file:

```json
{
  "happy_path": {
    "ego_task_id": "real-task-id",
    "objective": "bounded live objective",
    "approval_token": "one-time scoped token"
  }
}
```

Set its path with `AGENTTEAMS_BENCHMARK_BINDINGS_FILE` and restrict its file
permissions. Without both the live opt-in and a per-scenario binding, the
adapter returns lowercase `skip` and writes no trace.

A `pass` requires the adapter to write `agentteams-live-trace.json` using schema
`egoagentos.agentteams-trace/v1`. Verify the returned SHA-256 against file
bytes. The trace must contain at least three agents and ordered events for task
creation, delegation, acceptance, Skill/tool invocation, human approval,
completion, independent review, and final decision, plus RXP correlation
digests and official response identifiers. It also contains `principals` for
the bridge, human, and Ego decision actor. The benchmark-owned normative schema
is `benchmarks/schemas/agentteams-rxp-trace-v1.schema.json`; semantic authority
belongs to `benchmarks.trace_verifier.verify_trace_bytes`, not an adapter
boolean or a second integration-local schema.

The 14 canonical scenarios are fail-closed: each needs its own fault events.
Every PASS also needs top-level `replay.run_ids` and
`replay.semantic_digests` for at least two distinct live runs whose semantic
digests agree. Even `happy_path` additionally needs a blocked unsafe action and
exactly one committed effect. A generic successful terminal run cannot satisfy
another scenario and is returned as `error` with the missing event types.
Contract fixtures never produce `execution_mode=real-agentteams`.

If any item is absent, the correct benchmark result is `ERROR` or `SKIP`, not a
synthetic PASS.
