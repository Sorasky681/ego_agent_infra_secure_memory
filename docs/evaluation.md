# Evaluation

The submission evaluates both the research workload and the infrastructure that governs
it. A candidate model may fail its research threshold without making the infrastructure
evaluation fail.

## Research metrics

The shipped EgoLite evaluator computes FPS and MPJPE from raw paired samples. The Web
experiment table also reports fixture latency and VRAM values for context. PA-MPJPE and
missing-pose rate remain target metric contracts; they are not emitted by this fixture.
All included numbers are synthetic, not 8×RTX 4090 measurements.

For paired observations, the evaluator calculates candidate-minus-baseline deltas and a
fixed-seed paired bootstrap confidence interval. It rejects non-finite values, mismatched
lengths/splits, and unknown directions. An LLM can explain the resulting artifact but
cannot compute or edit it.

## Infrastructure metrics

| Metric | Definition |
|---|---|
| Evidence completeness | required verified kinds / 7 |
| Trace completeness | required verified trace evidence / configured trace contract |
| Approval bypass | R2/R3 executed without valid scoped approval |
| Unsafe action block | prohibited actions rejected / prohibited actions attempted |
| Reproducibility | identical manifests yielding identical deterministic artifacts |
| Recovery rate | recoverable failures reaching a reviewed terminal outcome |
| Tool success | successful typed calls / all typed calls, grouped by failure class |

## Evidence gate

The decision gate requires verified `code`, `config`, `dataset_manifest`, `log`,
`metric`, `trace`, and `review`; a raw metric artifact; reviewer independence; and closed
provenance digests. Any failure is a machine-readable FAIL, not a soft warning.

## Test layers

- unit: state, policy, hashing, evaluator, evidence, memory;
- integration: approval, decision, generation isolation, audit chain, persistence;
- contract: AgentTeams messages, Skill packages, MCP tools, platform health;
- Web: approval controls, decision lock, synthetic labeling, loading/error states;
- replay: reset and reproduce the complete local control-plane scenario.
