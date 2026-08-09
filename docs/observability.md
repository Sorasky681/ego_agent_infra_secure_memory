# Observability and replay

The shipped local implementation stores generation-scoped, append-only SQLite audit
events and verifies their SHA-256 hash chain. It also records an explicitly synthetic
`trace` evidence artifact for the happy-path run. It does **not** emit OpenTelemetry
spans or claim a live Agent/Skill/MCP trace.

The target platform profile uses the following span vocabulary when an OTel exporter and
real Agent/MCP adapters are added:

```text
research.task
├── agent.route
├── skill.invoke
├── mcp.tool.call
├── experiment.submit
├── experiment.monitor
├── evaluation.compute
├── evidence.verify
├── decision.commit
└── memory.write
```

Target attributes use an `ego.*` namespace until a stable external semantic convention
fully covers the domain:

- `ego.task.id`, `ego.agent.id`, `ego.stage`
- `ego.skill.name`, `ego.skill.version`
- `ego.tool.name`, `ego.tool.call.id`, `ego.policy.result`
- `ego.plan.digest`, `ego.run.id`, `ego.run.manifest.sha256`
- `ego.risk.level`, `ego.approval.id`
- `ego.evidence.kind`, `ego.evidence.sha256`

An exporter must preserve standard trace/span IDs, service name, duration, error status,
and GenAI model/token fields where applicable, and redact trace/log/metric payloads. This
is a deployment contract, not a claim about the local SQLite event stream.

## Replay contract

A judge selects a local decision and walks backward through gate result, review, raw
metric, run manifest event, configuration, dataset manifest, and frozen goal. Replay
checks artifact digests, generation isolation, and audit-chain order. The deterministic
EgoLite fixture can be reset from INTAKE without external services. Tool calls and Skill
invocations enter this chain only in an execution profile that actually invokes them.

## Infra metrics

- task and tool completion rate;
- evidence and trace completeness;
- unsafe action block and approval-bypass rate (target: 0 bypasses);
- experiment reproducibility and failure recovery rate;
- GPU-hour efficiency and Agent token/cost accounting when real adapters are enabled.
