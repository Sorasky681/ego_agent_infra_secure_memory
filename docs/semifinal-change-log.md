# 复赛迭代变更记录

比较基线：初版公开实现（`545b6a6` 至 `435bbb9`）与当前复赛集成分支。本文记录代码与
证据的变化，不把设计完成写成外部部署完成。

## 一页结论

初版的核心价值是可运行的 synthetic ResearchOps 控制面与静态 judge replay；复赛迭代
把它扩展为四个可独立验收的基础设施层：RXP 实验协议、fail-closed benchmark、可执行
Skill runtime、可持久恢复的 AgentTeams bridge，并增加 PostgreSQL 真实数据库 profile。

最大剩余缺口没有被文档掩盖：当前没有官方 AgentTeams live stack、真实 Worker/Matrix
事件和逐场景任务绑定，所以 bridge 只达到 `contract-verified`，canonical target benchmark
为 `SKIP`，不能申报“已接通”或“动态多 Agent 已跑通”。

## 初版 → 复赛差异

| 领域 | 初版 | 复赛新增/修改 | 可复核证据 | 尚未证明 |
|---|---|---|---|---|
| 实验确定性 | approval token、RunManifest、七类 EvidenceGate；一个任务内的控制语义 | RXP/1 将完整实验矩阵拆成 per-cell Intent → Grant → Receipt → Evidence → Decision，并记录 missing decisions | `protocols/rxp/`、committed schemas、canonical/Merkle vectors、protocol tests | 随机模型结果的科学正确性、任意 GPU 的字节确定性 |
| Benchmark | 静态 judge replay 与普通单元测试 | 14 个版本化场景、负对照、deterministic core、AgentTeams target、独立 oracle、置信区间、persistent evidence bundle、release replay | `benchmarks/`、committed raw JSON/Markdown/hash、benchmark tests | 当前 AgentTeams target 真实通过；committed target 是 70/70 `SKIP` |
| AgentTeams | CRD/resource template 与 message envelope；没有 runtime bridge | 官方 commit 契约锁；Project/TeamHarness/Matrix bridge；动态 replan；timeout reassign；restart/resume；compensation；R2 恢复；artifact digest 验收 | `apps/agentteams_bridge/`、`integrations/agentteams/`、`tests/agentteams/`、live runbook | 官方服务、真实 Matrix room、3+ Worker 真实协作与场景 fault injection |
| Trace 真值 | 可读 audit/event 模型 | `egoagentos.agentteams-trace/v1` schema；project/task/correlation/context 绑定；3+ Worker、Skill、HITL、review、Decision、RXP 五链与 official response 校验 | benchmark-owned schema/verifier；adapter 自报值不作为真值 | 外部系统实际产生的合格 trace |
| Skill 工程化 | 6 个 `SKILL.md` 合同 | 文件系统 discovery、package digest/version pin、typed invocation、idempotent correlation、failure trace、canary/retire/rollback、FastAPI endpoints | `skill_runtime/`、`apps/api/skill_runtime_api.py`、`tests/skills/` | 真实 AgentTeams Worker 已安装并成功调用这些 Skill |
| 数据持久化 | SQLite 开发状态 | PostgreSQL 16 store、事务回滚、optimistic concurrency、immutable audit trigger、commit-only notify、migration replay | `apps/api/migrations/postgres/`、`tests/postgres/`、recovery runbook | PolarDB-PG、云端 PITR、跨区容灾 |
| Judge 体验 | GitHub Pages 静态回放 | RXP protocol API 与 judge-facing cockpit；仍保留清晰的 synthetic 标签 | API/Web tests、production build、openapi/docs | 页面是 live AgentTeams、MCP、GPU 或云服务 |
| Claim 管理 | README 中的 target/current 区分 | claim ledger、scorecard、live runbook、target trace schema 和 `SKIP` release semantics | `docs/claims-evidence.md` 与本组复赛文档 | 任何缺证据的部署或性能主张 |

## AgentTeams 核心链的具体升级

### 以前

- Agent 身份和资源配置说明“谁应该做什么”；
- envelope 说明“消息应该长什么样”；
- deterministic local handler 可以演示业务状态，但不是 AgentTeams；
- 没有官方 Project/task 的可恢复映射，也没有 live evidence gate。

### 现在

- bridge 只通过官方 Project Workflow/TeamHarness/Matrix surface 观察协作事实，不自行伪写
  Worker ACK、submit、accept 或 terminal state；
- 所有消息绑定 `ego_task_id`、`project_id`、`task_id`、`trace_id`、`correlation_id`、
  `context_version`、attempt 与 causation；
- conflict、stale context 和 revision mismatch 进入 replan；ACK/execute timeout 进入
  cancel + `replacementTaskId` + bounded reassignment；
- R2 先 pause，消费 scope-bound Ego token 后才 resume/replan；token 不进入 Matrix 或数据库；
- 上游已修改、下游通知失败时进入 durable `COMPENSATION_REQUIRED`，而不是假装回滚成功；
- declared result envelope 与 primary artifact 都重新计算 SHA-256；Reviewer 必须独立且 PASS；
- Skill 证据分为 `DECLARED`、`SPAWN_AUTHORIZED`、`TOOL_INVOKED`，不把资源声明叫作调用；
- benchmark adapter 未配置 live 服务或 per-scenario binding 时返回小写 `skip`，不制造事件。

### 仍需 live 验收

1. 在官方 AgentTeams pin 或兼容 release 上部署 Team/Worker/Manager；
2. 使用非 synthetic Ego task 和真实 Matrix credential 跑通至少 3 个不同 Worker；
3. 为 14 个 canonical scenario 分别执行 fault driver，不能复用一条 generic trace；
4. 持久化每个 trial 的 trace/manifest/artifacts 并执行 `make benchmark-release`；
5. 只有 release gate 无 `FAIL`、`ERROR`、`SKIP` 后，才更新 live claim。

## 与评分权重的变化关系

| 权重项 | 复赛迭代带来的增量 | 当前最重要的下一条证据 |
|---|---|---|
| 场景价值与可迁移性 20% | 把单任务 demo 提升为矩阵级实验承诺和可迁移 adapter contract | 真实/公开实验 + 手工 baseline + 第二领域 mapping |
| 多 Agent 协作 25% | 从资源模板升级为可执行、可恢复、动态路由 bridge | 官方 live 3+ Worker correlated trace |
| Skill 工程化 20% | 从静态包升级为 digest-pinned runtime 与 lifecycle | Worker spawn/tool result + rollback live trace |
| 工程实现与安全审计 30% | 增加 RXP、独立 oracle、fault corpus、PostgreSQL 与 evidence bundle | live fault injection + exactly-once external effect proof |
| 开源贡献 5% | 增加协议、schema、adapter、benchmark 和 runbook | tagged release 与外部复用/反馈 |

## 2026-08-29 验证快照

| 命令 | 结果 | 结论边界 |
|---|---|---|
| `make test-agentteams check-agentteams` | 20 tests passed；Ruff、mypy 通过；official lock shape 通过离线检查 | 证明 bridge/fixture contract 与静态 pin 形状；**没有**验证 upstream bytes 或 live 服务 |
| `make test-rxp test-skills` | RXP 26 tests、Skill 6 tests passed；schema drift check 通过 | 证明 reference/local runtime 行为，不是分布式部署或 Worker live invocation |
| `make test-benchmark` | benchmark 20 tests passed；Ruff、mypy 与 2-repetition local strict run 通过 | local strict 允许 target capability gap 为 `SKIP`；它不是 AgentTeams release gate |
| `python3 scripts/verify_submission.py` | `PASS` | 证明提交包静态约束；不证明外部系统在线 |
| 1-repetition `--release-gate agentteams-rxp-target` | exit 1；0 pass、0 fail、0 error、14 skip | 正确阻止 release；缺 live 配置时没有 synthetic PASS |

四份复赛文档另通过 `git diff --check`、本地 Markdown link resolution、code-fence balance、
final newline 与 tab 检查。

## Claim 变更

| 旧表述风险 | 当前允许表述 |
|---|---|
| “EgoAgentOS 使用 AgentTeams 完成了实验” | “EgoAgentOS 提供面向官方 AgentTeams 的可执行 bridge；合同测试已通过，live 尚未验收” |
| “多 Agent 动态协作已证明” | “replan/reassign/recovery 逻辑已通过本地 fault contract；仍需官方事件链” |
| “Skill 已在 Worker 中调用” | “本地 Skill runtime 已调用；AgentTeams `TOOL_INVOKED` 证据尚缺” |
| “RXP 让 AI 实验确定” | “RXP 固化实验承诺、授权、验收和完整性边界，不保证随机训练结果或科学结论” |
| “RXP 是类似 MCP/A2A 的标准协议” | “RXP/1 是本项目提出并实现的协议草案/reference implementation，尚无标准地位或外部采用证明” |
| “benchmark 已通过” | “local deterministic-core 有可复核结果；AgentTeams target 当前全 `SKIP`，release gate 未通过” |

RXP 与相邻系统的精确边界见 [`protocols/RXP-comparison.md`](protocols/RXP-comparison.md)，
完整评分状态见 [`semifinal-scorecard.md`](semifinal-scorecard.md)。
