# ADR 0008: Plugin SDK Architecture

## Status

Accepted.

## Implementation Status

The Plugin SDK architecture defined by this ADR is accepted and binding.

External module-path plugins currently execute in-process.

The following capabilities must not be represented as implemented until they are completed and verified through the approved development lifecycle:

- process isolation;
- capability isolation;
- restricted filesystem access;
- restricted network access;
- restricted database access;
- enforced resource limits;
- complete failure isolation;
- sandboxed execution.

Accepted architecture does not imply completed implementation.

## Context

Project Hunter must remain extensible without allowing external capabilities to erode its core architecture.

New data sources, analytical domains, optional capabilities, and external integrations are expected. Allowing those capabilities to modify Hunter Core directly would make evidence handling, deterministic execution, replay safety, compatibility, security, and runtime authority harder to audit.

The Plugin SDK provides a controlled extension boundary.

It allows external and future first-party capabilities to integrate through explicit contracts rather than directly modifying:

- canonical runtime behavior;
- production analytical authority;
- persistence ownership;
- evidence models;
- identity authority;
- repository boundaries;
- configuration authority;
- security boundaries.

Without a stable plugin boundary, integrations could create hidden runtime paths, duplicate canonical truth, bypass evidence or trust controls, introduce unsupported side effects, or depend on unstable Hunter internals.

## Decision

Project Hunter adopts a versioned Plugin SDK as the approved extension boundary for external capabilities and future plugin-hosted first-party capabilities.

Plugins must integrate through approved contracts.

A plugin may:

- declare metadata;
- declare capabilities;
- declare dependencies;
- declare configuration requirements;
- participate in the approved lifecycle;
- consume approved context;
- emit approved acquisition or analytical outputs;
- report explicit failure or unavailable states.

A plugin must not:

- redefine canonical runtime architecture;
- replace production analytical authority;
- mutate canonical state outside approved service-owned paths;
- bypass canonical identity or registry authority;
- bypass evidence or trust controls;
- write directly to analytical persistence;
- receive unrestricted repository access;
- receive unrestricted database access;
- receive unrestricted filesystem access;
- receive unrestricted network access;
- mutate global configuration;
- mutate schemas;
- create hidden scheduling or automation behavior;
- create a competing source of truth.

## Plugin Lifecycle

The approved plugin lifecycle is:

1. Discover.
2. Validate.
3. Initialize.
4. Execute.
5. Shut down.

Each lifecycle stage must have explicit behavior.

A plugin must not execute before successful validation.

Validation failure must prevent loading.

Execution failure must produce an explicit failure or unavailable state.

Shutdown must be attempted where applicable, including after execution failure.

## Contract Versioning

Plugin SDK contract versioning is separate from individual plugin versioning.

The SDK contract version governs:

- metadata shape;
- lifecycle expectations;
- capability declarations;
- configuration contracts;
- approved context surfaces;
- evidence interfaces;
- output interfaces;
- validation rules;
- compatibility behavior;
- security expectations.

A plugin version identifies the individual plugin implementation.

Breaking SDK contract changes require a new major contract version.

Additive compatible changes may use a minor contract version.

Corrective changes that do not alter compatibility may use a patch contract version.

Unsupported contract versions must fail validation with explicit reasons.

They must not be silently reinterpreted.

## Capability Declaration

Every plugin must declare the capabilities it requires and the capabilities it provides.

Capability declarations must be explicit, versioned where required, and validated before execution.

A plugin must not use undeclared capabilities.

Capability approval does not automatically grant access to:

- repositories;
- databases;
- filesystems;
- networks;
- credentials;
- schemas;
- schedulers;
- automation;
- canonical analytical outputs.

Access must be limited to the approved context and interfaces for the declared capability.

## Canonical Boundary Preservation

Plugins must preserve the authority boundaries defined by Project Hunter's canonical documents and accepted ADRs.

Plugins must not bypass:

- canonical candidate and entity identity;
- registry lifecycle authority;
- evidence provenance;
- evidence sufficiency;
- trust evaluation;
- conflict preservation;
- replay cutoffs;
- repository purification;
- service-owned persistence authorization;
- canonical production analytical authority.

A plugin-hosted component does not acquire production authority merely because it is:

- installed;
- discovered;
- validated;
- executable;
- persisted;
- automated;
- displayed;
- included in reports.

Production analytical authority requires an explicit accepted ADR when the plugin changes or replaces a canonical production output.

## Data Acquisition Plugins

Acquisition-style plugins may retrieve and normalize external observations through approved interfaces.

They must preserve:

- source identity;
- provenance;
- observation time;
- acquisition time;
- freshness;
- retry state;
- unavailable state;
- parser or adapter version where applicable;
- source-specific references.

External provider responses must not be treated as canonical facts merely because acquisition succeeded.

Acquisition plugins must not persist authoritative analytical state directly.

## Analytical Plugins

Analytical plugins may consume approved persisted inputs and emit approved analytical outputs through sanctioned execution contracts.

They must preserve:

- deterministic behavior;
- explicit evidence lineage;
- replay safety;
- missing evidence;
- conflicts;
- output versioning;
- explainability.

Analytical plugins must not:

- call external providers during analytical execution unless the approved architecture explicitly classifies that action as acquisition;
- load repositories directly;
- persist findings directly;
- infer authority from caller-supplied payloads;
- replace canonical scoring, ranking, timing, valuation, committee, recommendation, portfolio, or trading behavior without a later accepted ADR.

## Security Boundary

The accepted target security boundary applies to every plugin regardless of trust level.

Once fully implemented, a plugin must receive only:

- its declared configuration;
- its approved capability surface;
- its approved execution context;
- explicitly granted resources.

A plugin must not receive unrestricted access to:

- Hunter repositories;
- Hunter databases;
- the host filesystem;
- the host network;
- process execution;
- environment variables;
- credentials;
- schema mutation;
- global configuration mutation;
- scheduler or automation control.

Security controls must be enforced by the runtime boundary rather than relying only on plugin self-declaration.

Until those controls are implemented and verified, in-process plugins must be treated as trusted code with incomplete isolation.

## Failure Isolation

Plugin failure must remain explicit and contained.

A plugin failure must not silently:

- corrupt another plugin;
- corrupt Hunter Core;
- mutate canonical state;
- alter persisted evidence;
- alter production analytical outputs;
- advance checkpoints incorrectly;
- suppress required unavailable states;
- trigger undeclared retries;
- create hidden fallback behavior.

Failure handling must preserve deterministic and auditable outcomes.

## Compatibility

The Plugin SDK must define an explicit compatibility policy.

Plugins built against the current major contract version are expected to remain compatible within that major version.

Support for a previous major version may exist only during an explicit compatibility window.

Compatibility adapters must be versioned and must not silently reinterpret unsupported plugin behavior.

Backward compatibility is a managed SDK responsibility, not an implicit promise hidden in Hunter Core.

## Consequences

- External integrations receive a stable and reviewable extension path.
- Hunter Core remains protected from direct integration-specific modification.
- Plugin metadata, capabilities, lifecycle, configuration, compatibility, and security expectations become explicit.
- Unsupported contract versions fail safely.
- Plugin authors must declare dependencies and capabilities.
- Plugin-hosted components remain subject to the same evidence, replay, determinism, identity, and authority requirements as first-party components.
- Acquisition plugins must preserve provenance, freshness, retries, and unavailable states.
- Analytical plugins must consume approved inputs and emit outputs through approved contracts.
- Plugin installation or execution does not create production analytical authority.
- Complete security isolation remains an implementation obligation until verified.
- Future sandboxing, capability enforcement, compatibility adapters, and trust tiers may be added without changing the core decision recorded by this ADR.
- Major changes to plugin authority or security boundaries require a later accepted ADR.

## Alternatives Considered

### Allow integrations to modify Hunter Core directly

Rejected because it would couple external capabilities to core internals and increase the risk of bypassing evidence, trust, persistence, replay, and production-authority boundaries.

### Treat every capability as a first-party Intelligence Engine

Rejected because this would not provide a general extension contract for acquisition, optional services, external integrations, compatibility, lifecycle, and security.

### Use ad hoc adapter modules without a formal SDK

Rejected because lifecycle, validation, capability discovery, versioning, failure handling, compatibility, and security expectations would remain inconsistent.

### Allow plugins to write directly to repositories or analytical persistence

Rejected because direct writes would bypass service-owned authority and create unsupported or non-reproducible canonical state.

### Treat trusted plugins as exempt from the SDK boundary

Rejected because architectural boundaries must not depend on informal trust classifications.

### Claim sandboxing before it is implemented

Rejected because accepted target architecture must not be misrepresented as current runtime capability.

### Delay the Plugin SDK decision until external integrations exist

Rejected because the extension boundary must be defined before integrations depend on unstable core internals.