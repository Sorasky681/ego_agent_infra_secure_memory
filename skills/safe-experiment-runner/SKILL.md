---
name: safe-experiment-runner
description: Submit reproducible experiments through typed allowlisted entrypoints and scoped approval checks.
---

# Safe Experiment Runner

Use only after plan review and risk classification. This Skill orchestrates a run; the
MCP server performs external process or scheduler calls.

## Contract

- Inputs: plan/arm digest, allowlisted entrypoint enum, config reference and digest,
  dataset manifest digest, seed, GPU IDs, expected GPU-hours, idempotency key, and a
  scoped approval token when policy requires one.
- Outputs: run ID, trace ID, immutable `RunManifest`, status, log/artifact references.
- Dependencies: GPU MCP, policy engine, approval verifier, artifact store.

## Procedure

Validate every digest; classify the exact action; verify approval scope, expiry, and
single-use status; construct an argument array with `shell=false`; record the manifest;
submit; monitor; capture exit code, logs, resource metrics, and structured errors.

## Failure and safety

- `E_APPROVAL_REQUIRED`, `E_APPROVAL_SCOPE`, `E_APPROVAL_EXPIRED`, `E_APPROVAL_REPLAY`:
  do not submit.
- `E_ENTRYPOINT`: unknown command; fail closed.
- `E_RESOURCE`: queue or return a bounded retry proposal; never silently add GPUs/time.
- `E_OOM`: preserve logs and propose a reviewed config change.
- Arbitrary shell, path traversal, secret output, main-branch writes, and external
  publication are forbidden. MCP annotations are hints, not authorization.

## Verification and reuse

The same idempotency key returns the same run or structured conflict. Verify that the
manifest digest binds code, config, dataset, environment, model, and seed. Scheduler
adapters may target local subprocess, Slurm, Kubernetes, or cloud jobs without changing
the Skill contract.

