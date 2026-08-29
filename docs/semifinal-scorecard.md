# GOAI Agent Infra semifinal scorecard

Source: [Agent Infra semifinal rules](https://alidocs.dingtalk.com/i/nodes/AR4GpnMqJzYd2LLOhLB1x4zLVKe0xjE3)
read on 2026-08-29. This document is an internal readiness rubric, not an official
judge score.

## Hard requirements

| Requirement | Current `main` evidence | Semifinal release gate |
|---|---|---|
| AgentTeams collaboration base | v1beta1 resources and envelope contract only | a real AgentTeams task must create, dispatch, accept, execute, review, and reach a terminal state |
| at least three distinct Agents | seven identity contracts | at least PI, Runtime, and Reviewer must appear as distinct AgentTeams principals in one correlated run |
| core Skills | six `SKILL.md` packages | runtime discovery, version pin, invocation, result, and failure must be present in the same task trace |
| runnable and verifiable | deterministic local control plane and static replay | clean install, live collaboration run, failure branch, approval recovery, raw trace export, and replay command |
| high-risk governance | scoped API/MCP token and audit chain | R2 action must stop, show exact scope, resume only after grant, reject replay, and preserve rollback evidence |

The current static replay remains useful product documentation, but it is not accepted as
evidence for a live AgentTeams or Skill execution claim.

## Weighted readiness

| Dimension | Weight | Current strengths | Current loss / red-line risk | Target evidence |
|---|---:|---|---|---|
| scenario value and portability | 20 | narrow embodied-AI ResearchOps workflow; explicit completion criteria | synthetic data only; no researcher baseline or external acceptance | one authorised real or openly reproducible experiment, manual baseline, and a second workflow mapping |
| multi-Agent collaboration | 25 | clear Manager/Worker identities and separation of duties | local core path is a fixed handler sequence; no live AgentTeams task, timeout, reassign, or conflict | AgentTeams run ID, room/task events, dynamic replan, timeout/reassign/resume, HITL continuation |
| Skill engineering | 20 | six packaged contracts with safety/failure sections | no runtime discovery or invocation Trace; no lifecycle execution | discovered version digest, invocation I/O refs, evaluation, registry candidate, rollback/retirement record |
| engineering and safety | 30 | deterministic state, SQLite recovery, evidence gate, scoped token, MCP tests, public CI | external collaboration chain is not runnable; limited SLO/fault evidence | RXP conformance, crash recovery, exactly-once effects, raw correlated traces, failure injection report |
| open-source contribution | 5 | public Apache-2.0 repository, locked deps, tests, schemas | no formal release or external adoption evidence | tagged release, protocol specification, conformance suite, reproducible install, issue/feedback trail |

## Non-negotiable truth boundaries

The following claims may only be promoted from `designed` to `verified` when the named
artifact exists and a clean replay checks it:

1. **AgentTeams live**: official controller/team task identifier plus Manager and Worker
   events from the same task.
2. **Skill executed**: discovery record, package/version digest, invocation input/output
   references, and terminal result.
3. **Experiment executed**: protocol Intent, exact one-time Grant, executor Receipt,
   immutable artifacts, evaluator Evidence, and Decision.
4. **Reproducible**: two independent executions agree on the declared determinism level;
   byte identity is required only for artifacts declared byte-deterministic.
5. **Real performance**: data licence/source, sample count, hardware/software environment,
   metric implementation, raw samples, aggregation, and confidence interval.

No static fixture, screenshot, prefilled event, or role label can satisfy these gates.

## Semifinal acceptance benchmark

Every candidate release must pass all safety invariants and meet the quantitative targets
below. A skipped mandatory scenario is a release failure.

| Metric | Target |
|---|---:|
| approval bypass | 0 |
| accepted replay / expired / wrong-scope grants | 0 |
| unauthorised or cross-task state mutation | 0 |
| forged independent review accepted | 0 |
| duplicate external effects under concurrent retry | 0 |
| required trace/evidence field completeness | 100% |
| hash-chain and artifact digest verification | 100% |
| terminal recovery after injected recoverable failure | 100% |
| matrix cells silently omitted from final decision | 0 |
| fixed-seed protocol replay hash agreement | 100% |
| dynamic-routing scenarios taking the required alternate route | 100% |

Latency, token cost, and task-completion rate are reported with distributions and confidence
intervals. They are optimisation metrics, never allowed to compensate for a failed safety
invariant.

## Evidence package required for the semifinal Demo

The live or recorded Demo must fit within eight minutes and show, without cuts that hide the
state transition:

1. AgentTeams task creation, delegation, acceptance, and correlated context.
2. a Skill being discovered and invoked with its exact version digest.
3. an experiment Intent expanding into matrix cells and one grant per executable cell.
4. R2 blocking before execution and continuation after a scope-bound human grant.
5. an injected timeout or conflicting review causing reassign/replan rather than a fixed next
   step.
6. executor Receipt, raw metric Evidence, independent review, and decision gate.
7. a replay/tamper attempt rejected with an auditable reason.
8. restart/resume and the final task, trace, evidence, and protocol export.

The updated deck must also include a clearly marked comparison against the initial-round
submission, a user/pain/value/input-output closure diagram, risk boundary, and a concrete
cross-domain migration recipe. If no official initial-round written feedback is available,
the deck must say that explicitly and label the comparison as a self-audit, not judge feedback.
