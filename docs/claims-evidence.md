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
| Web cockpit reflects backend gate truth | verified locally | 9 component/normalization/static-replay tests + production build + completed/approval/mobile screenshots |
| API invokes MCP over HTTP in the default Web replay | not claimed | network client call trace + correlated tool artifact required |
| CPU hashing recovery branch works | synthetic control-flow fixture only | before/after fixture + trace sequence; requires physical-run evidence for a performance claim |
| 8×RTX 4090 experiment ran | not claimed | real scheduler logs + manifests + metric artifacts |
| AgentTeams Matrix collaboration is live | not configured | room transcript envelope + health probe |
| Higress isolates upstream credentials | not configured | route export + positive/negative leak test |
| Nacos Skill is published | not configured | registry version response + package digest |
| official Aliyun SLS Skill queried a trace | not configured | redacted invocation + matching trace ID |

Never copy numeric claims from the unrelated legacy deck. Its old test, cache,
determinism, hash, and rollback claims refer to a different project and absent evidence paths.
