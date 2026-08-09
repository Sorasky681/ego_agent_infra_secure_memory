# AgentTeams integration profile

AgentTeams (formerly HiClaw) is the collaboration plane; EgoAgentOS remains the
research control plane. Every collaboration envelope carries a `task_id` and trace ID so
humans can observe and intervene. A deployment may dedicate a room/project to a task,
but the room is never the source of truth.

## Mapping

| EgoAgentOS concern | AgentTeams mapping |
|---|---|
| task control flow | Research PI Manager |
| bounded task flow | six Worker identities in `../../agents/` |
| collaboration | AgentTeams-managed rooms, correlated by `task_id` envelope |
| context transfer | `message-envelope.schema.json` references immutable API objects |
| state tracking | API/DB task stage and append-only audit events |
| human intervention | Matrix observation plus scoped API approval |
| external credentials | Higress consumer token; no upstream secret in a Worker |

The integration is intentionally fail-closed. A Worker message proposes a typed command;
only the API may persist state or execute a transition. Chat text cannot approve R2/R3,
replace raw metrics, or satisfy the evidence gate.

## Deployment procedure (not executed by the local replay)

1. Install AgentTeams from the official project and verify its Matrix/Higress services.
2. Render `agentteams-resources.yaml.tmpl` with the real model and gateway base URL.
3. Stage the six Skill directories in the Manager's Worker Skill library, then apply the
   rendered resources in document order with the official `agentteams-apply.sh`/`agt apply`.
4. Configure `AGENTTEAMS_BASE_URL`, `AGENTTEAMS_MATRIX_HOMESERVER`, and a consumer token.
5. Configure the endpoint metadata. The current API changes only from `not_configured`
   to `configured_unverified`; a future adapter must capture a real request/response
   probe before any surface may say `verified`.
6. Start a task and pass only envelopes that validate against
   `message-envelope.schema.json`. If the installed version supports task/project room
   allocation, use `task_id` as the room/project correlation key.

The repository never ships API keys, Matrix admin passwords, or the credentials printed
by a local AgentTeams installer.

No step above is part of the default API/Web replay, and this repository does not include
or invoke the AgentTeams controller CLI. The resource renderer validates template
substitution only; it is not a live deployment test.

Official architecture reference: <https://hiclaw.io/>

The declarative template follows `agentteams.io/v1beta1` fields documented in the
official repository at commit `fd14305b118b60d7f7eabc7a00c326546510cc9f`
(inspected 2026-08-09). Re-validate it against the installed controller version.
