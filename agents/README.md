# Agent identity contracts

These seven identities map EgoAgentOS onto the AgentTeams Manager–Worker model.
`research-pi` is the Manager/Control Flow identity. The remaining identities are
Workers/Task Flow identities. Matrix messages are collaboration envelopes only;
the API database remains the source of truth.

Every identity declares the fields requested by the GOAI Agent Infra handbook:
name, role, capabilities, inputs, outputs, dependencies, decision boundary, and
trace. `identity.schema.json` is the machine-checkable contract.

Separation of duties is enforced by the deterministic control plane:

- the architect cannot approve its own plan;
- the runtime worker cannot evaluate its own output;
- the evaluator cannot mutate checkpoints or raw evidence;
- the reviewer cannot launch the experiment it reviews;
- the memory curator can only promote evidence already marked as validated.

