# GOAI Agent Infra mapping

Verified against the public Agent Infra track page on 2026-08-09.

| Requirement / score | EgoAgentOS evidence |
|---|---|
| Real, reusable scenario (25%) | embodied-AI lab ResearchOps; goal→experiment→decision→learning |
| ≥3 Agents + AgentTeams basis | official v1beta1 resources for 7 Workers (including Team Leader), 1 Team, and 1 infrastructure Manager; executable Controller/Matrix bridge; local contract fixtures explicitly do not count as live |
| Context and shared state | frozen goal/config/data references + SQLite source of truth + generation-scoped audit events |
| Skill is mandatory (25%) | six packaged Skills with I/O, trigger, failure, safety, version, reuse |
| Tool integration | typed repo/data/GPU/metrics MCP contracts; no universal shell |
| Result verification | deterministic metrics + independent Reviewer + seven-kind gate |
| Approval / rollback / audit | R0–R3 policy, scoped single-use approvals, R3 rollback-point contract, immutable audit |
| Experience accumulation | semantic/episodic/procedural memory → reviewed Skill candidate |
| At least 2 context capabilities | shared state, validated memory, and hash-chained audit/replay |
| Engineering / auditability (20%) | documented Compose plus verified native app, durable bridge checkpoints, official workflow/artifact/spawn reads, hash-chain trace, contract/fault tests, explicit live boundary |
| Open contribution (5%) | Apache-2.0 code, schemas, Skill packages, adapters, fixtures, docs |

## Stage deliverables

- Initial round (deadline 2026-08-16 23:59 UTC+8): ≤500 Chinese-character project
  summary and PPT/PDF are mandatory; code is optional but included here as evidence.
- Semifinal: updated proposal, executable AgentTeams package, runnable Demo/video.
- Final: pitch deck, live Demo, accessible repository or equivalent engineering package.

The repository now ships an opt-in executable AgentTeams path: it creates and replans a
real Controller Project, sends the Team Leader a structured Matrix envelope, maps
TeamHarness task lifecycle and declared artifacts into EgoAgentOS, pauses at the real R2
recovery chain, and implements bounded reassignment/compensation. The included local Web
replay still uses deterministic role handlers and does not claim a live Matrix room.
AgentTeams contract tests use conspicuously labelled fixtures; a semifinal live claim
requires the acceptance evidence in `docs/agentteams-live-runbook.md`.
Nacos, Higress, the official Aliyun SLS Skill, PostgreSQL/PolarDB, and object storage
remain deployment contracts whose UI state is never upgraded without real evidence.

Official source: <https://www.goaihz.com/tracks?track=infra>
