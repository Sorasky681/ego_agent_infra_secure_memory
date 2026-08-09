# Judge replay runbook

The final commands are also summarized in the root README. This flow does not require a
GPU, AgentTeams, Nacos, Higress, or cloud credentials.

1. Start the API and Web app with the documented local command.
2. Open the task cockpit for `ego-lite-001`; confirm the `SYNTHETIC DEMO` marker.
3. Reset, then choose “Run to next gate”. Deterministic role handlers advance the
   persisted task to APPROVAL and attribute each audit event to its identity contract.
4. Inspect the R2 approval: exact action digest, modeled GPU-hours, expiry, and rollback
   pointer. Advance controls are disabled while no scoped token exists.
5. Approve the displayed digest. The raw token is returned once and held only in the
   current browser session; automated policy tests cover invalid/scope/replay rejection.
6. Continue the replay. Inspect the explicitly synthetic low-GPU/high-CPU samples and
   the recorded manifest-based diagnosis. Do not describe this as a live MCP or GPU call.
7. Inspect raw baseline/candidate metric artifacts and deterministic comparison.
8. Open the evidence ledger and control-plane audit. A 7/7 count alone remains `HOLD`
   until the backend gate runs and the independent review passes; only then is the fixed
   local Decision committed.
9. Advance through DECIDE/ARCHIVE/MEMORY_SKILL. Inspect the validated failure/procedure;
   the Skill is a candidate/draft, not falsely marked published.
10. Reset and replay; confirm a new generation and a clean, generation-scoped event
    stream. The test suite separately proves canonical hash determinism.

## Optional live integration checks

Configure only services available to the team. The local integration route reports
metadata as `not_configured` or `configured_unverified`; it performs no handshake.
Separately, the repository's MCP servers support stdio and an explicit loopback
Streamable HTTP transport smoke. Neither result makes AgentTeams, Higress, Nacos, cloud,
or a GPU scheduler live.
