---
name: ablation-analyzer
description: Compute deterministic baseline comparisons, paired confidence intervals, and threshold verdict inputs.
---

# Ablation Analyzer

Use after every required run is terminal and the fixed evaluation split is available.
LLMs may explain results but must not calculate or alter raw metrics.

## Contract

- Inputs: baseline/candidate sample vectors, metric name/unit/direction, fixed split
  digest, evaluator version, bootstrap seed and resample count, acceptance threshold.
- Outputs: raw values, mean delta, 95% paired bootstrap interval, effect direction,
  seed consistency, deterministic verdict input, and artifact SHA-256.
- Dependencies: versioned evaluator code and metrics MCP.

## Procedure

Reject unpaired, missing, non-finite, or split-mismatched inputs. Compute per-sample
candidate minus baseline deltas. Use a fixed pseudo-random seed for paired resampling.
Apply metric direction and threshold mechanically; emit raw and aggregate artifacts
before any narrative explanation.

## Failure and safety

- `E_UNPAIRED`, `E_NONFINITE`, `E_SPLIT`, `E_DIRECTION`: no verdict.
- Never impute missing results without a declared method.
- Never rewrite raw input artifacts or select a more favorable seed after inspection.

## Verification and reuse

Golden fixtures must reproduce byte-identical output. Both lower-is-better and
higher-is-better metrics are supported, so the Skill applies beyond embodied AI.

