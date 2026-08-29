# RXP Bench v1

RXP Bench measures whether an auto-research control plane turns an experiment request into
an auditable, repeatable state transition instead of an opaque final answer. It is an
infrastructure benchmark. It does not score model intelligence or claim physical GPU results.

## What actually runs

Three profiles share the same versioned 14-scenario corpus:

1. `naive-fixed-v1` is an executable local fixed-path reference. It intentionally has no
   approval, evidence, durable recovery, or lease protocol. It is a comparator, **not** a
   substitute for AgentTeams and never counts as semifinal collaboration evidence.
2. `deterministic-core-v0.1` calls the repository's real state machine, SQLite store, approval
   policy, canonical hashing, evidence gate, and audit-chain verifier.
3. `agentteams-rxp-target` calls a real integration only if
   `integrations/agentteams/benchmark_adapter.py` exists, declares
   `BENCHMARK_ADAPTER_VERSION = "rxp-bench/v1"`, and exports
   `run_scenario(scenario, seed, workspace)`. Otherwise every target trial is `SKIP`. No
   synthetic AgentTeams event is converted into a pass.

A target `PASS` additionally requires `details.execution_mode="real-agentteams"`, at least
three distinct `details.agent_roles`, and a trace file inside the per-trial workspace whose
SHA-256 matches `details.trace_sha256`. Missing or inconsistent evidence becomes `ERROR`.

The corpus is immutable within a version:

- `benchmarks/corpus/v1/scenarios.json` contains scenario data and the fixed master seed.
- `benchmarks/corpus/v1/scenario.schema.json` documents its JSON schema.
- the runner records the corpus SHA-256 in every result.

## Run it

```bash
make benchmark
# After package installation, the equivalent entry point is: rxp-bench --strict
```

For a faster CI/local smoke run:

```bash
python -m benchmarks.runner \
  --profiles naive-fixed-v1,deterministic-core-v0.1,agentteams-rxp-target \
  --repetitions 2 \
  --strict \
  --output-json benchmarks/artifacts/smoke.json \
  --output-md benchmarks/artifacts/smoke.md
```

`--strict` fails when an executed deterministic-core scenario fails, the core raises an
error, or approval bypass succeeds even once. Capability gaps remain visible `SKIP`s and
reduce coverage; they do not turn CI green by being counted as passes.

## Metrics and denominators

Each raw trial records status, fixed seed, measured wall time, operation count, assertion
evidence, implementation path, and only the metrics that scenario can expose. `null` means
not measured, never zero.

| Metric | Definition |
|---|---|
| Task completion | Completed / trials with an explicit completion outcome |
| Unsafe action block | Blocked / executed adversarial actions |
| Approval bypass | Successful bypasses / executed approval attacks; required value is **0** |
| Exactly once | Single committed side effect / trials exposing duplication |
| Trace completeness | Required lifecycle event types present / required types |
| Evidence completeness | Evidence classes present / seven decision-gate classes |
| Recovery and MTTR | Recovered / recovery trials; process reopen-to-valid-state wall time |
| Reproducibility | Equal semantic projections across two independent runs |
| Hash agreement | Equal canonical SHA-256 for those projections |
| Dynamic routing | Successful reassignment / trials exposing a worker timeout |
| Cost and latency | Measured local wall time and operation count; external cost is `null` without a billing meter |

Binary rates include Wilson 95% confidence intervals. Continuous means include fixed-seed
2,000-resample bootstrap 95% intervals plus empirical p50/p95. Repetitions measure this
implementation's stability on a fixed synthetic corpus, not generalization to arbitrary
research tasks.

## Truth boundary

- Benchmark payloads and the EgoLite workflow are synthetic.
- No GPU is requested or used by the committed baseline artifact.
- No external LLM or AgentTeams service is called unless its real adapter is installed.
- A missing target, monetary meter, or capability is `SKIP`/`null`, not inferred.
- Raw JSON is canonical UTF-8 JSON: sorted keys, compact separators, NaN forbidden.
- The semantic digest excludes wall time, MTTR, and diagnostic details, but raw JSON retains
  all measured timings.

See [semifinal-score-mapping.md](semifinal-score-mapping.md) for the competition mapping and
`benchmarks/artifacts/` for committed local evidence.
