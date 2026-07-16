# ADR 0009: Repository Purification

## Status

Accepted.

## Context

Foundation Sprint F2 implemented and verified the Repository Purification architecture for the Discovery subsystem. The change clarified a permanent boundary that Hunter needs across future subsystems: repositories are persistence adapters, not authority owners or domain decision engines.

Before this decision, repository mutation paths could become convenient places for lifecycle checks, identity projection, conflict handling, checkpoint updates, or timestamp policy. That pattern makes authority harder to audit and creates a risk that providers, engines, CLI paths, automation, or tests could bypass service-owned validation.

Hunter's evidence-first and replay-safe architecture requires every authoritative mutation to have one clear owner. Domain decisions must happen before persistence, and persistence must store already-authorized state without changing its meaning.

## Decision

Hunter permanently adopts this architectural flow for authoritative state changes:

```text
Provider
↓
Service
↓
Repository
↓
Persistence
```

Providers own:

- acquisition;
- normalization;
- observation production.

Providers never:

- persist state;
- validate authority;
- mutate registry state.

Services own all domain decisions, including:

- authority;
- provenance;
- lineage;
- conflict detection;
- canonical projection;
- identifier ownership;
- metadata precedence;
- lifecycle validation;
- replay validation;
- checkpoint authorization;
- transaction orchestration.

Repositories own only:

- persistence;
- loading;
- deterministic transaction execution;
- schema;
- indexes;
- migrations.

Repositories never:

- perform business logic;
- perform merge decisions;
- validate authority;
- validate provenance;
- resolve identity;
- perform replay logic;
- create timestamps;
- create checkpoints;
- detect conflicts.

Repository mutation APIs that would create authoritative state must reject direct public use or accept only service-owned persistence plans that already contain the authoritative decision. Internal repository persistence primitives must store supplied state deterministically and must not add hidden domain policy.

This ADR records the implemented Foundation Sprint F2 pattern as the permanent architectural pattern for future implementation work. It does not claim that Tokenomics, Historical, On-chain, or future engines already implement this boundary; it establishes the pattern those implementations must follow when they add or refactor authoritative persistence.

## Consequences

- Repository responsibilities are simpler and more auditable.
- Services become the single place to review authority, provenance, lineage, identity, lifecycle, conflict, replay, and checkpoint rules.
- Persistence becomes more deterministic because repositories store supplied state instead of deriving domain state.
- Transaction boundaries become easier to test because service-owned persistence plans define the full authoritative mutation.
- Bypass tests become clearer because direct repository mutation attempts must fail.
- The pattern is reusable across Discovery, Tokenomics, Historical, On-chain, Registry, and future Intelligence Engines as those subsystems evolve.
- Future implementations must not add repository-owned trust, identity, conflict, lifecycle, replay, timestamp, checkpoint, or merge logic for convenience.

## Alternatives Considered

- Keep domain decisions inside repositories. Rejected because repositories would remain mixed authority and persistence components, making bypasses, hidden business rules, and replay-sensitive timestamp behavior harder to audit.
- Allow providers or engines to write directly to repositories. Rejected because acquisition and analysis components must not own durable authoritative state or bypass service validation.
- Use caller-supplied flags or tokens to authorize repository mutations. Rejected because caller-controlled authority is forgeable and weakens the service boundary.
- Permit standalone checkpoint writes as a convenience API. Rejected because checkpoints must advance only as part of the successful authoritative mutation they describe.
- Apply the pattern only to Discovery. Rejected as a long-term architecture because Hunter needs one reusable authority boundary for current and future subsystems.
