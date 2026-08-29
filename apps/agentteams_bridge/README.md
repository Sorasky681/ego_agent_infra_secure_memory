# AgentTeams bridge service

This service turns the official AgentTeams Controller and TeamHarness state
into a durable EgoAgentOS collaboration ledger. It is a live adapter, not the
static browser replay.

See [`../../integrations/agentteams/README.md`](../../integrations/agentteams/README.md)
for the version pin, deployment steps, failure semantics, API workflow, and
truth boundary.

Key modules:

- `clients.py`: official Controller, Matrix, and EgoAgentOS HTTP clients;
- `service.py`: dispatch, reconcile, artifact validation, timeout/reassignment,
  R2 recovery, typed evidence finalization, compensation, and Skill evidence;
- `store.py`: restartable SQLite checkpoints plus tamper-evident event and upstream-receipt chains;
- `main.py`: FastAPI operator surface;
- `cli.py`: probe, run, R2, reconcile, and live Docker smoke commands.

The service version is `0.3.0`. Its expected Project Workflow contract is
pinned to official AgentTeams main commit
`223ddc2b8073e4c8b93bcbb15e1d717f196c04d9`; the live trace separately records
the Controller version response and must not present the expected pin as a
runtime image attestation.

## Live completion and export boundary

A live run can attach only to an EgoAgentOS task created with `synthetic=false`; the task's team,
trace, correlation id, context version, objective, and initial stage must match exactly. Completed
AgentTeams TaskMeta entries are accepted only after their declared result-envelope and primary
artifact bytes pass digest validation. Metric and reviewer artifacts have additional typed
contracts. The bridge then sends the seven resulting evidence records to the EgoAgentOS terminal
finalization API and verifies `COMPLETED`, Evidence Gate `pass`, and a real terminal decision before
it marks its own run complete.

`GET /api/v1/agentteams/runs/{run_id}/receipts` exports the append-only receipt chain. It contains
the raw, secret-free Matrix message request and response, official AgentTeams project/artifact/
completion responses, the reviewer decision, and the EgoAgentOS finalization receipt. A reused
receipt key is accepted only when its canonical payload is identical.

`GET /api/v1/agentteams/runs/{run_id}/acceptance-input-index` cross-indexes those receipts with
accepted metric artifacts, bridge events, Ego task/events, and Skill evidence. This is deliberately
**not** an acceptance bundle: `bundle_assembled` remains `false`. A separate collector still has to
fetch both services, redact, write `acceptance-input.json`, assemble the immutable files, and run
the offline verifier. Contract tests use injected transports and do not constitute a live official
AgentTeams or GPU run.
