---
name: research-memory
description: Retrieve and persist evidence-linked semantic, episodic, and procedural research memory.
---

# Research Memory

Use for context retrieval or after an archived decision. Memory is not chat history and
unverified model output is never a fact.

## Contract

- Search inputs: query, component tags, current failure signals, time horizon, limit.
- Search output: ranked entries with evidence references and score components.
- Write inputs: memory kind, structured content, source decision, passing gate digest,
  evidence references, independent review.
- Write output: immutable validated memory entry and optional Skill candidate.

## Ranking

`0.45 semantic + 0.20 component + 0.15 evidence + 0.10 recency + 0.10 failure`.
Each component is normalized to `[0,1]`; return the component breakdown so ranking is
auditable. Failure similarity is intentionally rewarded during incident diagnosis.

## Failure and safety

- `E_UNVALIDATED`: evidence gate is absent/failed; refuse write.
- `E_SOURCE_MISSING`: an evidence reference cannot be verified; refuse write.
- `E_TENANT_SCOPE`: query crosses authorization boundary; fail closed.
- Never store secrets, raw private data, speculation, or a summary without provenance.

## Skill promotion

A procedural memory may become a Skill candidate after three independently verified
successes. Publication still requires human review and registry confirmation; proposal
does not mean online.

## Verification and reuse

Tests must expose every score component, reject unvalidated writes, and prove tenant
filters run before ranking. The evidence-linked memory contract can be reused across
research domains even when the semantic retrieval backend is replaced.
