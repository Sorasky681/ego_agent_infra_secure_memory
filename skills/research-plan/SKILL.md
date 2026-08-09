---
name: research-plan
description: Compile a frozen research goal and bounded context into a falsifiable, budgeted experiment DAG.
---

# Research Plan

Use when a `ResearchGoal` has been frozen and a versioned `ContextBundle` exists.
Do not use this Skill to approve, execute, or judge the plan.

## Contract

- Inputs: frozen goal digest, context digest, candidate hypotheses, fixed data split,
  metric specifications, seeds, hardware ceiling, GPU-hour budget, rollback target.
- Output: an `ExperimentPlan` with a baseline arm, candidate arms, dependency DAG,
  falsification criteria, estimated cost, risk classification, and canonical digest.
- Call condition: both input digests verify and every metric declares unit, direction,
  split, aggregation, and acceptance threshold.
- Dependencies: policy engine, provenance canonicalizer, repository snapshot tool.

## Procedure

1. Reject mutable or missing goal/context references.
2. State each hypothesis so a failed result can falsify it.
3. Freeze the baseline, data split, evaluator version, seeds, and comparison direction.
4. Estimate GPU-hours and classify R0–R3 without rounding down at boundaries.
5. Attach an explicit rollback or stop condition to every mutating arm.
6. Canonicalize the plan and emit its digest for independent review.

## Failure and safety

- `E_SCHEMA`: required field or metric semantics missing; stop.
- `E_BUDGET`: estimated resources exceed the frozen ceiling; return a smaller proposal.
- `E_UNFALSIFIABLE`: no measurable rejection condition; return to Architect.
- Never self-approve. Never alter a baseline after review. Never embed credentials.

## Verification and reuse

Rebuild the digest twice, validate the JSON schema, and confirm the Architect and
Reviewer identities differ. The contract is domain-independent and can plan robotics,
vision, language-model, and systems experiments.

