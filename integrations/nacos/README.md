# Nacos Skill Registry adapter contract

Nacos 3.2.0+ can store, version, review, publish, and distribute Agent Skill packages.
EgoAgentOS exports each directory under `../../skills/` as a ZIP whose root contains
`SKILL.md`. Optional scripts and project-specific metadata travel with the package.

The adapter maps local states to registry states without pretending a local directory is
already published:

| EgoAgentOS | Nacos lifecycle |
|---|---|
| candidate | no remote version yet |
| draft | draft |
| security_review | reviewing / pipeline |
| published | online |
| retired | offline |

Publishing requires a validated procedural memory, at least three verified successes,
independent review, human approval, package digest, and a successful Nacos response.
`risk_level`, `idempotency_key`, and evidence fields in `egoagentos.skill.yaml` are
EgoAgentOS extensions, not official Nacos schema fields.

Until `NACOS_BASE_URL` and credentials are configured and a live upload/query probe
succeeds, the UI must show `not_configured` or `configured`, never `verified`.

Official reference: <https://nacos.io/en/docs/latest/manual/user/ai/skill-registry/>

