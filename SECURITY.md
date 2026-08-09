# Security policy

Please report vulnerabilities privately to the repository maintainers before public
disclosure. Do not include real cloud credentials, private datasets, or exploit payloads
that could affect a shared lab system.

The local demo is non-actuating and uses synthetic fixtures. Do not connect a real GPU
scheduler, repository write token, model publisher, or robot control interface until the
relevant allowlist, scoped approval, rollback, audit, and negative tests pass.

Supported security behavior is documented in `docs/security.md`. A configured adapter is
not automatically a verified security boundary.

