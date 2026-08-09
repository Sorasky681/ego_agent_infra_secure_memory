# EgoAgentOS MCP v2 tool plane

This directory contains four independent, policy-enforced MCP servers for the
EgoAgentOS ResearchOps proof of concept:

| Server | Tools | Authoritative behavior |
| --- | --- | --- |
| `repo` | `repo_snapshot`, `repo_read_files` | Read-only; one configured root; no followed symlinks; bounded UTF-8 reads; credential-path denial and content redaction. |
| `dataset` | `dataset_create_manifest`, `dataset_verify_manifest` | Canonical per-file SHA-256 manifest; immutable first publication at the fixed `.egoagentos-manifest.json` name; full re-verification. |
| `gpu` | `gpu_launch_experiment`, `gpu_job_status` | Dry-run by default; three enum entrypoints; config-byte digest binding; argv only; `shell=False`; GPU-hour risk classification; exact-action HMAC approvals; idempotent launch keys. |
| `metrics` | `metrics_compare_paired` | Pure deterministic paired comparison and a 2,000-iteration bootstrap with fixed seed `20260809`. |

The servers target Python 3.12 and pin the official Python SDK to `mcp==2.0.0`.
They use the v2 high-level API (`from mcp.server import MCPServer`). Tool
annotations are UI/client hints only. Every relevant rule is enforced again in
the Python service implementation; no security decision trusts annotations.

## Honest execution boundary

This is a **local synthetic/dry-run tool plane**. It does not claim a connection
to live GPUs, a scheduler, HiClaw, AgentTeams, Nacos, Higress, or cloud services.
The GPU server's optional execution mode launches only
`egoagentos_mcp.synthetic_worker`; it performs no training and uses no GPU. GPU
IDs are recorded as requested evidence but are not consumed by that worker.

There is no generic shell tool. A caller cannot supply an executable, command,
shell flag, environment map, working directory, or Python module. Even metadata
tags that contain shell syntax remain single argv values because process launch
always uses an argument list and `shell=False`.

## Install and test

From the repository root:

```bash
uv sync --python 3.12 --project mcp_servers --extra dev
uv run --python 3.12 --project mcp_servers pytest mcp_servers/tests
```

## Transport and launch commands

Each process gets only the trusted root and capabilities it needs. All commands
below default to MCP stdio, so stdout is reserved for protocol traffic.

```bash
EGO_MCP_REPO_ROOT=/absolute/path/to/repository \
  uv run --python 3.12 --project mcp_servers ego-repo-mcp

EGO_MCP_DATASET_ROOT=/absolute/path/to/datasets \
  uv run --python 3.12 --project mcp_servers ego-dataset-mcp

EGO_MCP_WORKSPACE_ROOT=/absolute/path/to/workspace \
  uv run --python 3.12 --project mcp_servers ego-gpu-mcp

uv run --python 3.12 --project mcp_servers ego-metrics-mcp
```

For an explicit local deployment profile, the same processes expose MCP Streamable HTTP
at `/mcp`. Use a distinct port per server:

```bash
EGO_MCP_TRANSPORT=streamable-http \
EGO_MCP_HOST=127.0.0.1 \
EGO_MCP_PORT=8021 \
EGO_MCP_REPO_ROOT=/absolute/path/to/repository \
  uv run --python 3.12 --project mcp_servers ego-repo-mcp
```

The submission verification includes a loopback initialize/`tools/list` smoke. Binding
outside loopback requires an authenticated gateway and is a deployment responsibility;
Streamable HTTP support alone does not claim Higress or AgentTeams is connected.

The same servers can be launched as modules:

```bash
uv run --python 3.12 --project mcp_servers python -m egoagentos_mcp.repo_server
uv run --python 3.12 --project mcp_servers python -m egoagentos_mcp.dataset_server
uv run --python 3.12 --project mcp_servers python -m egoagentos_mcp.gpu_server
uv run --python 3.12 --project mcp_servers python -m egoagentos_mcp.metrics_server
```

## Trusted-root and data rules

- Roots are operator configuration, never tool arguments. They must be absolute,
  existing directories and must not themselves be symlinks.
- Tool paths are POSIX-style relative paths. Absolute paths, `..`, backslashes,
  NUL bytes, and symlink components are rejected after canonical containment
  checking with `realpath` semantics.
- Repository recursion never follows symlinks. Explicit reads also reject
  credential-like filenames and redact common assignments, JSON secrets, bearer
  values, and private-key blocks.
- Dataset manifests reject every symlink and special filesystem node. Once published,
  changed dataset bytes cannot overwrite the prior manifest to hide drift. A manifest
  contains no timestamp or absolute host path, so identical bytes produce an
  identical tree hash and manifest hash.
- Errors serialize as `{"ok":false,"error":{"code":..., ...}}`; error details
  pass through the same redaction boundary and never echo approval tokens.

## Synthetic GPU preview and opt-in execution

`gpu_launch_experiment` takes a strict `LaunchRequest` whose `config_sha256` must match
the resolved config bytes. Its `entrypoint` is one
of `train_pose`, `eval_pose`, or `benchmark_stream`; these names map to fixed
modes of the packaged synthetic worker. `dry_run` defaults to `true` and returns
the exact argv, risk decision, deterministic action digest, run ID, and approval
scope without launching a process.

To permit the harmless local worker, an operator must opt in:

```bash
EGO_MCP_WORKSPACE_ROOT=/absolute/path/to/workspace \
EGO_MCP_ENABLE_SYNTHETIC_LOCAL_EXECUTION=1 \
  uv run --python 3.12 --project mcp_servers ego-gpu-mcp
```

R1 means one requested GPU and at most 2 expected GPU-hours. R2 means multiple
requested GPUs or more than 2 expected GPU-hours. R2 execution additionally
requires a scoped approval token. Dry-run still reports R2 but does not consume
an approval because it launches nothing.

## Approval tokens

The server validates but never mints approvals through MCP. The included FastAPI
control plane can mint the shared `egoap1` contract after a human approves the exact
GPU `action_digest`, `approval_scope`, and config digest. A separate operator process
may also import `HMACApprovalManager.issue()`. Tokens are:

- HMAC-SHA256 signed with a minimum 32-byte secret;
- bound to the exact action, scope, and canonical request digest;
- valid for at most 15 minutes;
- consumed once using an atomic replay ledger.

Configure execution-side validation with a durable replay directory:

```bash
EGO_MCP_WORKSPACE_ROOT=/absolute/path/to/workspace \
EGO_MCP_ENABLE_SYNTHETIC_LOCAL_EXECUTION=1 \
EGO_MCP_APPROVAL_HMAC_SECRET='replace-with-at-least-32-random-bytes' \
EGO_MCP_APPROVAL_REPLAY_DIR=/absolute/path/to/private/replay-ledger \
  uv run --python 3.12 --project mcp_servers ego-gpu-mcp
```

The replay directory should be private to the server identity. An idempotent
retry of an already launched exact request returns the existing job record and
does not launch again; the token is not re-used for another action.

The API and GPU server must receive the same operator-generated
`EGO_MCP_APPROVAL_HMAC_SECRET`. If the API secret is empty, it deliberately issues an
opaque `egoap_` token for its UI-only replay; that fallback cannot pass MCP validation.
`tests/integration/test_api_gpu_mcp_approval.py` proves the configured cross-runtime
contract without claiming an API-to-MCP network client or an external gateway.

## Scope limitations

- In-process job status is demonstration state, not a durable scheduler.
- The dataset manifest writer is local filesystem evidence, not an object-store
  publication mechanism.
- The fixed-seed bootstrap is reproducible statistical evidence, not a claim
  that a metric or threshold is scientifically appropriate for every task.
- Production integration needs separate service identity, authorization,
  observability, scheduler adapters, and secret management at the deployment
  boundary.
