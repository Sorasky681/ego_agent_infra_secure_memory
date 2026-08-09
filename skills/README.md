# EgoAgentOS Skills

The six packages in this directory are reusable research operations capabilities,
not one-off prompts. Each package follows the portable Agent Skill layout expected
by Nacos Skill Registry (`SKILL.md` plus optional `scripts/`, `references/`, and
`assets/`). Only `name` and `description` are treated as portable frontmatter.
`egoagentos.skill.yaml` is an explicitly project-specific extension for risk,
version, idempotency, and evidence policy.

| Skill | Primary users | Deterministic boundary |
|---|---|---|
| `research-plan` | PI, Architect, Reviewer | goal/plan schema and budget checks |
| `dataset-manifest` | Scout, Runtime | canonical dataset manifest digest |
| `safe-experiment-runner` | Runtime | entrypoint allowlist, approval scope |
| `ablation-analyzer` | Evaluator, Reviewer | comparisons and fixed-seed bootstrap |
| `evidence-gate` | Reviewer, PI | required kinds, digests, independence |
| `research-memory` | Scout, Memory Curator | validated-only writes and ranking |

Publication is a separate action: local packages are `draft` until a configured
registry confirms upload, review, and online status.

