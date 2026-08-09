# GOAI Agent Infra mapping

Verified against the public Agent Infra track page on 2026-08-09.

| Requirement / score | EgoAgentOS evidence |
|---|---|
| Real, reusable scenario (25%) | embodied-AI lab ResearchOps; goal→experiment→decision→learning |
| ≥3 Agents + AgentTeams basis | 1 Manager + 6 Worker identities, typed envelope and v1beta1 deployment templates; live Matrix not configured |
| Context and shared state | frozen goal/config/data references + SQLite source of truth + generation-scoped audit events |
| Skill is mandatory (25%) | six packaged Skills with I/O, trigger, failure, safety, version, reuse |
| Tool integration | typed repo/data/GPU/metrics MCP contracts; no universal shell |
| Result verification | deterministic metrics + independent Reviewer + seven-kind gate |
| Approval / rollback / audit | R0–R3 policy, scoped single-use approvals, R3 rollback-point contract, immutable audit |
| Experience accumulation | semantic/episodic/procedural memory → reviewed Skill candidate |
| At least 2 context capabilities | shared state, validated memory, and hash-chained audit/replay |
| Engineering / auditability (20%) | documented Compose plus verified native app, tests, logs, replay, explicit claims |
| Open contribution (5%) | Apache-2.0 code, schemas, Skill packages, adapters, fixtures, docs |

## Stage deliverables

- Initial round (deadline 2026-08-16 23:59 UTC+8): ≤500 Chinese-character project
  summary and PPT/PDF are mandatory; code is optional but included here as evidence.
- Semifinal: updated proposal, executable AgentTeams package, runnable Demo/video.
- Final: pitch deck, live Demo, accessible repository or equivalent engineering package.

The design uses AgentTeams as its target collaboration basis. This initial repository
ships its identities, envelopes, and renderable deployment resources, but the included
local replay uses deterministic role handlers and does not claim a live Matrix room.
Nacos, Higress, the official Aliyun SLS Skill, PostgreSQL/PolarDB, and object storage
remain deployment contracts whose UI state is never upgraded without real evidence.

Official source: <https://www.goaihz.com/tracks?track=infra>
