# Verification report — 2026-08-09

All workload/model/hardware values below the control plane are explicitly synthetic.
This report records commands run against the current workspace; it is not evidence of a
live GPU, AgentTeams/Matrix, Higress, Nacos, or Aliyun deployment.

## Automated checks

| Surface | Command | Current result |
|---|---|---|
| API/domain | `make test-api` | 23 passed |
| API static analysis | `make check-api` | Ruff PASS; MyPy PASS across 12 source files |
| MCP tool plane | `pytest mcp_servers/tests` in the pinned Python 3.12 project | 21 passed |
| API → GPU MCP contract | `pytest tests/integration` in the MCP project | 2 passed |
| MCP static analysis | `ruff check mcp_servers/src mcp_servers/tests tests/integration` | PASS |
| Web | `npm --prefix apps/web test` | 3 files / 9 tests passed |
| Web production build | `npm --prefix apps/web run build` | PASS; Pages profile JS 371.48 kB / 117.01 kB gzip |
| Submission policy | `python3 scripts/verify_submission.py --json` | 8 fail-closed checks PASS |
| Compose schema | `docker compose config --quiet` | PASS; services: backend, web |

`make test-api check-api` was also repeated with a brand-new
`UV_PROJECT_ENVIRONMENT` under `/tmp`; uv created the environment, installed 34 locked
packages, and reproduced the same 23-test/Ruff/MyPy result without using global pytest.

MCP tests include descriptor-relative no-follow recursion, concurrent directory
replacement rejection, config-byte digest binding, immutable dataset publication,
provider-secret redaction, fixed-seed bootstrap, scoped approval replay rejection, and
an actual loopback Streamable HTTP `initialize` + `tools/list` exchange.

The cross-runtime tests configure one shared HMAC secret, compare the FastAPI pending
approval to the GPU MCP dry-run action/scope/digest, execute the packaged synthetic path
through a capturing runner once, then reject a second consumption. They do not claim an
API-to-MCP HTTP client or external gateway.

## Fresh-database browser replay

The Web app was run on a fresh SQLite database and exercised in Chromium:

1. INTAKE displayed the `SYNTHETIC DEMO` label and gate `HOLD`.
2. Autorun paused at APPROVAL. R2, 24 modeled GPU-hours, exact digest, expiry, and the
   baseline configuration rollback pointer were visible; advance controls were disabled.
3. After approval, manual steps reached VERIFY with all 7 evidence kinds present while
   the UI correctly remained `HOLD`.
4. The next transition ran the backend gate, changed it to `PASS`, and committed `KEEP`.
5. The replay reached COMPLETED, showed `VERIFIED DECISION · KEEP · GATE BOUND`, two
   validated memories, and a 1/3 human-gated Skill candidate.
6. Reset created a new generation and returned to an approval-locked state; the prior
   browser token did not unlock it.
7. Chromium reported no console errors. At 375×812 the page had no horizontal body
   overflow and the mobile navigation remained off-canvas until opened.

Screenshots from this run:

- `submission/screenshots/approval-gate.png`
- `submission/screenshots/completed-decision.png`
- `submission/screenshots/mobile-approval.png`

The negative fixture was also replayed through the API. It stopped at VERIFY with
`paused_reason=insufficient_evidence`, gate `fail`, missing kind `trace`, and no Decision.

The GitHub Pages profile was also built locally with `VITE_STATIC_DEMO=true` and the
`/ego_agent_infra/` base path. Browser QA observed zero API requests, zero console errors,
a deterministic R2-to-KEEP replay, matching `index.html`/`404.html`, and no horizontal
overflow at 375 px. This profile is a browser-memory fixture and does not claim a hosted
control plane, MCP call, approval signature, or GPU execution.

## Submission-artifact QA

- The portal summary is 488 characters after trimming, below the 500-character limit.
- The proposal contains 16 slides and 16 source-note blocks, with no placeholders or
  legacy-project claims. Template-fidelity and template-plan checks both pass.
- The PPTX was rendered and inspected page by page. The layout checker reports only the
  source template's intentional decorative-circle bleed on slides 1, 10, and 11; no text,
  screenshot, or content frame leaves the canvas.
- The 16-page PDF was built from the verified 1920×1080 slide renders because this host
  lacks the deck's CJK font. A 144 dpi PDF round-trip matched all source-page dimensions;
  mean channel deviation was approximately 2/255, and the PDF contact sheet was visually
  inspected.

## Build limitation observed in this environment

`docker compose build --pull` reached base-image metadata resolution, then Docker Hub
timed out for `python:3.9-slim`, `node:22-alpine`, and `nginx:1.27-alpine`. No repository
code layer failed. Compose configuration validates, and the native pinned-dependency
replay above passed; a container image build remains to be repeated on a network that can
reach Docker Hub.
