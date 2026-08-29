# EgoAgentOS semifinal evidence index

Date: 2026-08-29  
Track: GOAI 2026 Agent Infra semifinal  
Truth rule: a design, fixture, or contract test is never promoted to a live-platform claim.

## Fastest judge replay

```bash
make demo-proof
make test
make verify
```

`make demo-proof` is the short deterministic entry point. `make test` is the full local
suite. `make benchmark-release EVIDENCE_DIR=/new/empty/persistent/path` is deliberately
fail-closed: without live AgentTeams evidence it exits non-zero and records SKIP.

## Rule-to-evidence map

| Semifinal dimension | Executable/reviewable evidence | Current status |
|---|---|---|
| Scenario and portability (20%) | `docs/competition-mapping.md`, `docs/architecture.md`, RXP schemas, SQLite/PostgreSQL Store contract | local verified; PolarDB/PITR not run |
| Multi-Agent collaboration (25%) | `apps/agentteams_bridge/`, `integrations/agentteams/official-contract.lock.json`, `tests/agentteams/` | contract/fault tests PASS; live target SKIP |
| Skills (20%) | `skills/`, `skill_runtime/`, `/api/v1/skills`, `/invoke`, invocation trace | 6 discovered / 3 executable; lifecycle tests PASS |
| Engineering/security (30%) | one-time Grant, MatrixLedger, PostgreSQL immutable audit, strict benchmark, negative control, recovery | local proofs PASS |
| Open source (5%) | Apache-2.0 repo, PPTX/PDF, demo script, proof, deterministic ZIP | ready locally; current revision not claimed deployed |

## Evidence ladder

| Claim | Artifact or command | Status/boundary |
|---|---|---|
| RXP causal closure | `submission/evidence/semifinal-local-proof.json` | PASS; 2/2 cells, 23 entries, public synthetic key |
| RXP verifier/API | `protocols/rxp/`, `/api/v1/rxp/demo`, `/api/v1/rxp/verify` | executable; task-store persistence not yet wired |
| Skill discovery/invoke | `skill_runtime/`, `/api/v1/skills` | 6 packages; 3 allowlisted handlers |
| Fault benchmark | `benchmarks/artifacts/2026-08-29-local-cpu.*` | 5 repetitions, 210 trials, independent oracle |
| Dynamic collaboration bridge | `apps/agentteams_bridge/`, `tests/agentteams/` | contract/fault proof PASS; live Controller absent |
| PostgreSQL profile | `docs/evidence/postgres-local-proof-2026-08-29.md` | real PostgreSQL 16 Docker tests 10/10 PASS |
| Judge-facing UI | `submission/screenshots/semifinal-rxp-cockpit.png` | static fixture, no backend/GPU/signature claim |
| Release gate | `make benchmark-release EVIDENCE_DIR=...` | no live target → expected non-zero/SKIP |

## Frozen identifiers

- Semifinal proof SHA-256:
  `fbb49491dcaefbeb31921b6801df300b2b60dfe93500b0308bc4f93804098c26`
- RXP demo SHA-256:
  `178a24b303f13a480262498cd793fba6fe63570ceedb27928805b7c321362524`
- RXP ledger root:
  `sha256:2e313a284dcaaa6542d9d81919fc22bb61ab2015e18659f2dc0d323cbad47fd3`
- Benchmark semantic digest:
  `59a39f466a0506fdc6246cd13860283a314e8881c58ddda7a5f9930c9b561d80`
- Semifinal PPTX SHA-256:
  `d77efcfd56f1733d4a9e88b6ec4c0b489c9fcc0b56c17f858aded62a305114ee`
- Semifinal PDF SHA-256:
  `b845c146dcaecd9e82fbe64ea790e38169a2a968d96f87345ab396c7a8a5c43a`
- Cockpit screenshot SHA-256:
  `13ab0e7a43e5f7197664f3f9f61a9273271104cfe972f07fce39aed1a4a652ff`

## Explicit non-claims

- No live AgentTeams Controller/Team/Matrix same-run trace is present.
- No live GPU job or real model-improvement result is present.
- No PolarDB deployment, PITR restoration, measured RPO/RTO, or cloud IAM proof is present.
- The public RXP demo key has no production trust or key-custody meaning.
- The current application container image build was blocked at Docker Hub metadata by a
  network timeout; it is not marked verified.
- GitHub Pages proves only the static judge replay. API, AgentTeams, PostgreSQL, and GPU
  capabilities require their own local or deployed profile evidence.
