---
name: evidence-gate
description: Block research decisions until required artifacts, digests, raw metrics, and independent review verify.
---

# Evidence Gate

Invoke before any KEEP, REVERT, ITERATE, or INCONCLUSIVE decision is committed.

## Contract

- Inputs: task/plan/run IDs, evidence ledger, raw metric artifact, provenance manifest,
  independent result review, gate policy version.
- Output: PASS or FAIL with required/present/missing kinds, digest failures, reviewer
  independence result, metric validity, checked-at time, and canonical result digest.
- Required evidence kinds: `code`, `config`, `dataset_manifest`, `log`, `metric`,
  `trace`, `review`.

## Procedure

Verify identifiers and SHA-256 bytes; check all seven kinds; require structured raw
metrics from the deterministic evaluator; require a reviewer identity distinct from
the planner/executor; validate provenance closure; emit a signed/hashed gate result.

## Failure and safety

Any missing kind, digest mismatch, narrative-only metric, non-independent review, or
unbound artifact is FAIL. There is no warning-only bypass. A failed gate returns the
task to PLAN through the control plane and preserves the complete audit trail.

## Verification and reuse

Negative tests remove each required kind individually, corrupt every digest class,
replace raw metrics with prose, and reuse the executor as reviewer. All must fail.

