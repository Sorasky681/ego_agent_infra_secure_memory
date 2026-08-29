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
  R2 recovery, compensation, and Skill evidence;
- `store.py`: restartable SQLite checkpoints and a tamper-evident event chain;
- `main.py`: FastAPI operator surface;
- `cli.py`: probe, run, R2, reconcile, and live Docker smoke commands.

The service version is `0.2.0`. Its expected Project Workflow contract is
pinned to official AgentTeams main commit
`223ddc2b8073e4c8b93bcbb15e1d717f196c04d9`; the live trace separately records
the Controller version response and must not present the expected pin as a
runtime image attestation.
