# Higress credential and routing boundary

Higress is optional in the local demo and recommended in the platform profile. Its
purpose here is narrow: give Workers consumer tokens while keeping upstream LLM, MCP,
Git, and cloud credentials at the gateway.

The product UI reports one of four truth states:

- `not_configured`: required URL/token absent;
- `configured`: configuration exists but no live probe succeeded;
- `reachable`: gateway responded, but policy behavior has not been verified;
- `verified`: positive routing and negative credential-leak tests passed recently.

`credential-policy.yaml` is the intended policy contract, not proof that a particular
gateway instance enforces it. Deployment evidence must include redacted route export,
request trace, and a negative test before a presentation may say “verified”.

Official reference: <https://higress.ai/en/docs/>

