# GOAI Agent Infra semifinal submission checklist

The DingTalk rules page is live; reopen it before upload and treat the portal's current
file/type/size fields as authoritative.

## Mandatory-rule gate

- [x] At least three functional Agents: seven explicit principals with independent review.
- [x] Real AgentTeams integration path: official API contract lock plus executable bridge.
- [x] Dynamic collaboration evidence: create/decompose/delegate/accept/execute/verify,
  conflict, timeout, reassignment, R2 recovery, compensation, restart, terminal state.
- [x] Runnable Skills: discover/load/invoke trace plus SemVer, digest pin, canary,
  activation, rollback, and retirement; unsafe generic runner fails closed.
- [x] Approval, rollback, audit, idempotency, observability, and tenant controls have code
  and tests rather than diagram-only claims.
- [x] Initial-feedback changes are marked in red on slide 2 and mapped to evidence.
- [x] Risks, portability, closure diagram, and explicit SKIP boundaries are present.
- [ ] Live AgentTeams target proof. Leave SKIP unless an official endpoint and same-run
  evidence are actually captured.

## Final artifacts

- [x] `EgoAgentOS_GOAI_Agent_Infra_复赛方案.pptx` (16 inherited/editable slides).
- [x] `EgoAgentOS_GOAI_Agent_Infra_复赛方案.pdf` (16 pages).
- [x] `project-summary-zh.txt` (verify the portal's character counter remains ≤500).
- [x] `demo-script-8min.md`.
- [x] `semifinal-evidence-index.md`.
- [x] deterministic `semifinal-local-proof.json` plus checksum.
- [ ] optional ≤8 minute public/unlisted demo video and captions.
- [ ] final deterministic ZIP and `.sha256` generated after the last commit.

## Before upload

```bash
make demo-proof
make test
make verify
make package
```

- [ ] Reopen the ZIP and confirm PPTX, PDF, code, docs, proof, benchmark artifacts, and
  lock files are included.
- [ ] Confirm no `.env`, credentials, local database, private data, or production key is
  inside the ZIP.
- [ ] Confirm PPTX/PDF/proof/package hashes match their sidecars/index.
- [ ] Open GitHub repository and static Demo URL in a signed-out/incognito browser.
- [ ] Confirm GitHub Pages was built from the final commit; otherwise describe it as an
  earlier static fixture, not this revision.
- [ ] Reopen the DingTalk semifinal rule page and the submission portal immediately
  before submission; record the successful upload state separately from a saved draft.
