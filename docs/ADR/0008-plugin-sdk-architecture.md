# ADR 0008: Plugin SDK Architecture

## Status

Accepted as target architecture. This ADR is not an implementation record: external module-path plugins currently execute in-process and must not be treated as sandboxed or capability-isolated until the SDK boundary described here is implemented and verified.

## Context

Project Hunter must remain extensible without allowing external capabilities to erode its core architecture. New data sources, analytical domains, and integrations are expected, but unrestricted changes to Hunter Core would make evidence handling, determinism, replay safety, and runtime boundaries harder to audit.

The Plugin SDK exists to give external and future first-party capabilities a stable extension path. It lets contributors add capabilities through explicit contracts instead of directly modifying the core runtime, persistence boundaries, scoring path, or evidence models.

The architecture separates responsibilities:

- Core Runtime owns canonical execution contracts, production scoring boundaries, deterministic identity, orchestration boundaries, persistence integration points, and compatibility guarantees.
- Intelligence Engines own domain-specific collection, normalization, analysis, confidence modeling, and Intelligence generation.
- Data Acquisition owns source-specific retrieval, normalization, provenance capture, freshness, retries, and unavailable states.
- Plugin SDK owns plugin metadata, contract versioning, capability declarations, lifecycle, validation, security boundaries, compatibility rules, and conformance expectations.
- External Integrations own interaction with third-party systems but must enter Hunter only through approved acquisition, evidence, intelligence, or plugin contracts.

Without this separation, integrations could bypass the Dynamic Asset Registry, Evidence Intelligence Layer, Trust Layer, or canonical runtime contracts and produce unsupported conclusions, hidden side effects, or non-reproducible outputs.

## Decision

Project Hunter uses a versioned Plugin SDK as the approved extension boundary for external capabilities and future plugin-hosted first-party capabilities.

Plugins must integrate through stable plugin contracts rather than directly modifying Hunter Core. A plugin may declare capabilities, initialize, execute, emit approved Intelligence or acquisition outputs through sanctioned interfaces, and shut down through the documented lifecycle. It must not mutate core runtime state, bypass canonical repositories, write directly to analytical persistence, redefine evidence models, or replace production scoring boundaries.

Plugin lifecycle is:

1. Discover.
2. Validate.
3. Initialize.
4. Execute.
5. Shut down.

Contract versioning is separate from individual plugin versioning. The Plugin SDK contract version governs metadata shape, lifecycle expectations, capability declarations, evidence interfaces, validation rules, and compatibility behavior. Plugin versions describe the individual plugin implementation. Breaking SDK contract changes require a new major contract version; additive compatible changes use minor versions.

Backward compatibility is expected for plugins built against the current major contract version and, during an explicit compatibility window, the immediately prior major contract version. Unsupported contract versions fail validation with named reasons rather than being silently reinterpreted.

Plugins must preserve these boundaries:

- They must not bypass the Dynamic Asset Registry for candidate identity, lifecycle, or market-wide discovery state.
- They must not bypass the Evidence Intelligence Layer for evidence-backed claims, provenance, conflict handling, or unavailable states.
- They must not bypass the Trust Layer for source reliability, identity confidence, conflict status, freshness, or missing-evidence treatment.
- They must not bypass Canonical Runtime contracts, including `EvidenceBackedProjectExecutor` as the production deep-analysis scoring boundary unless a future ADR explicitly changes that boundary.

The target SDK security boundary applies to every plugin regardless of trust level. Once implemented, a plugin receives only its declared configuration and approved context surface. It must not receive repository handles, arbitrary database access, unrestricted filesystem access, unrestricted network access, schema-mutation rights, or configuration-mutation rights outside its own declared contract.

Failure isolation is part of the target SDK contract. Plugin validation failures prevent loading; execution failures produce explicit failure or unavailable states; shutdown still runs where applicable; one plugin failure must not silently corrupt another plugin, Hunter Core, persisted evidence, or canonical analytical outputs.

## Consequences

- External integrations gain a stable, reviewable extension path without direct modification of Hunter Core.
- Hunter Core remains responsible for canonical runtime contracts, deterministic identity, persistence boundaries, and production scoring semantics.
- The Plugin SDK becomes the compatibility layer between external capabilities and Hunter's evidence-first architecture.
- Plugin authors must declare capabilities, contract versions, dependencies, configuration schema, and lifecycle behavior explicitly.
- Plugin validation must reject unsupported contract versions, undeclared capabilities, invalid metadata, dependency errors, and security-boundary violations before unsafe execution once the SDK boundary is implemented.
- Plugin-hosted Intelligence Engines remain subject to the same evidence, confidence, determinism, and identity stabilization requirements as first-party engines.
- Acquisition-style plugins must preserve source provenance, freshness, retry behavior, and unavailable states instead of treating external provider responses as authoritative facts.
- Backward compatibility becomes a managed SDK concern instead of an implicit promise hidden in core implementation details.
- Future extensions can add capability taxonomy entries, trust tiers, compatibility adapters, or sandboxing implementations without changing Hunter's canonical runtime boundaries.
- Plugin SDK evolution must remain documentation-backed and, for major boundary changes, ADR-backed.

## Alternatives Considered

- Allow integrations to modify Hunter Core directly. Rejected because it would couple external capabilities to core runtime internals and increase the risk of bypassing evidence, trust, persistence, and replay boundaries.
- Treat every new capability as a first-party Intelligence Engine only. Rejected because it does not provide a stable external integration contract, versioning model, security boundary, or compatibility layer for third-party and optional capabilities.
- Use ad hoc adapter modules without a formal SDK. Rejected because informal adapters would leave lifecycle, validation, capability discovery, failure handling, and compatibility expectations inconsistent.
- Allow plugins to write directly to repositories or analytical persistence. Rejected because direct writes would bypass canonical contracts, make audit trails harder to preserve, and risk non-deterministic or unsupported persisted state.
- Delay Plugin SDK decisions until external integrations are implemented. Rejected because the extension boundary must exist before integrations depend on unstable core internals.
