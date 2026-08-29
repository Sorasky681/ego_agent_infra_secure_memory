# Claim ledger

This file is the presentation safety rail. A claim moves to “verified” only when its
evidence URI is produced by the current repository and replayed successfully.

| Claim | Current state | Required evidence |
|---|---|---|
| deterministic ResearchOps state machine runs end to end | verified locally, synthetic workload | 23 API/domain tests + isolated replay to `COMPLETED` |
| R2 execution cannot bypass human approval | verified locally | API negative replay returned `approval_required`; consumed approval replay returned `approval_already_decided` |
| decision requires 7/7 verified evidence kinds | verified locally | happy path gate `pass` at 7/7; missing-trace path stopped at `VERIFY` with no Decision |
| included metric comparison is deterministic | verified against synthetic fixture | fixed-seed evaluator unit tests + replayed raw sample artifact |
| MCP path/shell/approval boundaries | verified locally | 21 MCP tests + Ruff; descriptor-relative no-follow scans; four servers and seven typed tools |
| Streamable HTTP MCP transport | verified on loopback | automated initialize + `tools/list` test for repo server; stdio remains default |
| API approval is accepted once by GPU MCP | verified as a cross-runtime contract test | shared `egoap1` HMAC contract; exact dry-run digest/scope; one fake-runner launch; replay rejected |
| RXP/1 canonical experiment-acceptance protocol is executable | verified locally against synthetic fixtures | 26 protocol tests; committed schemas; byte-identical CLI replay; canonical/Merkle known vectors |
| RXP detects omitted matrix decisions and rejects scope/expiry/replay/tampering | verified in the reference implementation | Cartesian-plan validation, `missing_decisions`, concurrent SQLite replay test, causal/root mutation tests |
| RXP is persisted by the FastAPI control plane or a distributed transparency service | not claimed | API adapter, durable document/artifact store, serializable distributed replay registry, externally checkpointed root required |
| Web cockpit reflects backend gate truth | verified locally | 9 component/normalization/static-replay tests + production build + completed/approval/mobile screenshots |
| PostgreSQL store preserves the control-plane contract | verified on local PostgreSQL 16 | real-container suite: full API, atomic rollback, optimistic concurrency, linear audit chain, immutable trigger, commit-only notify, migration replay |
| PolarDB-PG deployment or PITR completed | not claimed | cloud endpoint handshake, backup policy, restore job, chain replay, measured RPO/RTO required |
| API invokes MCP over HTTP in the default Web replay | not claimed | network client call trace + correlated tool artifact required |
| CPU hashing recovery branch works | synthetic control-flow fixture only | before/after fixture + trace sequence; requires physical-run evidence for a performance claim |
| 8×RTX 4090 experiment ran | not claimed | real scheduler logs + manifests + metric artifacts |
| AgentTeams bridge contract/state/fault behavior | verified with explicit contract fixtures; not live | `tests/agentteams`: official pins, real-endpoint call shapes, result digests, reassign, R2, compensation, skill-evidence levels, trace truth gates |
| AgentTeams Matrix collaboration is live | not verified on this host | real Controller version/health, active Team and ready Workers, Project/workflow IDs, Matrix event IDs, official spawn/tool trace, content-addressed artifacts, scoped R2 receipt, final trace hash |
| canonical 14-scenario AgentTeams benchmark passes | not claimed | one scenario-specific live trace per seed; generic completion is rejected and missing live bindings return `skip` |
| Higress isolates upstream credentials | not configured | route export + positive/negative leak test |
| Nacos Skill is published | not configured | registry version response + package digest |
| official Aliyun SLS Skill queried a trace | not configured | redacted invocation + matching trace ID |

Never copy numeric claims from the unrelated legacy deck. Its old test, cache,
determinism, hash, and rollback claims refer to a different project and absent evidence paths.
