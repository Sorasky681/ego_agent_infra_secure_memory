# Contributing

Contributions should preserve the project's trust boundaries.

1. Open an issue describing the control-plane or adapter change and its failure modes.
2. Add or update the typed contract before changing behavior.
3. Add a negative test for every new mutation, approval, path, credential, or evidence
   surface.
4. Keep model output outside deterministic authorization and calculation paths.
5. Mark fixtures and screenshots as synthetic unless backed by a current evidence URI.
6. Run backend tests, frontend tests/build, and the local judge replay.

Skills follow SemVer and require a changelog plus evidence-linked review before registry
publication. Platform adapters must return honest truth states and redact credentials.

