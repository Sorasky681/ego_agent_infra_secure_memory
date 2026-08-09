# EgoLite deterministic demo

This fixture is a **synthetic control-plane demo**. It proves orchestration,
authorization, evidence, evaluation, replay, and memory behavior without claiming a
real 8×RTX 4090 run or real EgoLite model quality.

The scenario deliberately contains a recoverable infrastructure failure:

1. a reviewed R2 experiment waits for a human approval bound to the plan digest;
2. the first synthetic resource sample shows low GPU utilization and high CPU/hash time;
3. the Runtime Agent invokes the `dataset-manifest` procedure and records the diagnosis;
4. the deterministic evaluator compares baseline and candidate fixtures;
5. the independent Reviewer completes the seven-kind evidence ledger;
6. only then can the PI commit a decision and the Memory Curator write a procedure.

Run it through the API/Web UI or use the commands in `docs/demo-runbook.md`.

