# ADR 0007: Canonical Runtime Option A

## Status

Accepted.

## Context

Project Hunter contains multiple execution and analytical surfaces with different maturity, authority, and operational roles.

The evidence-backed Market Validation runtime is the validated production scoring and reporting path.

The repository also contains experimental and supporting surfaces, including:

- `PipelineOrchestrator`;
- plugin lifecycle and execution;
- Intelligence Engines;
- Intelligence Fusion;
- fusion-backed Opportunity Timing;
- automation;
- generic persistence;
- operational projections;
- research-oriented analytical modules.

Without an explicit architectural decision, future contributors could mistakenly:

- treat an experimental path as the production analytical runtime;
- introduce a second canonical scoring path;
- assign production authority to persistence, automation, or presentation;
- allow similar field names to substitute for differently defined outputs;
- create incompatible score, timing, committee, ranking, or reporting semantics.

## Decision

Project Hunter adopts Option A as the current production analytical authority model.

The evidence-backed Market Validation runtime remains the canonical production analytical path unless a later accepted ADR explicitly changes that authority.

The canonical production execution path is:

```text
CLI
-> Acquisition
-> Validation
-> Repositories
-> EngineValidationSource
-> EvidenceBackedProjectExecutor
-> Weight Engine
-> Production Timing
-> Committee Fields
-> Explainability
-> Reports

Within this boundary:

* EvidenceBackedProjectExecutor remains the production deep-analysis composition and scoring boundary.
* Market Validation scoring, timing, committee fields, explainability, and reporting retain their existing production meanings.
* PipelineOrchestrator, plugin orchestration, Intelligence Fusion, fusion-backed Opportunity Timing, and other research-oriented analytical surfaces remain non-canonical unless explicitly promoted by a later accepted ADR.
* The presence of code, tests, records, repositories, automation jobs, dashboards, or persisted outputs does not by itself establish production analytical authority.
* No experimental or operational output may substitute for a canonical production output merely because names, fields, or values appear similar.
* No second canonical production owner may exist for the same semantic output.

ADR 0016 extends and clarifies this decision without superseding it.

Production Authority Boundary

Production analytical authority belongs to the complete Market Validation runtime, not to an isolated helper, repository, model, table, report, or interface.

Authority is determined by the approved runtime composition and output semantics.

The following do not independently acquire production analytical authority:

* repositories;
* persistence schemas;
* JSONL records;
* automation jobs;
* scheduler configuration;
* plugin registration;
* Dashboard fields;
* desktop presentation;
* operational corpus records;
* experimental analytical models;
* test fixtures;
* caller-supplied values.

A persisted or displayed value remains non-authoritative when its originating runtime is non-authoritative.

Experimental Boundary

Experimental components may be implemented, tested, persisted, automated, and displayed when clearly classified.

They must not:

* replace the Market Validation runtime;
* alter production scoring semantics;
* alter production timing semantics;
* alter production committee semantics;
* alter production ranking semantics;
* alter production report semantics;
* write competing canonical outputs;
* be presented as production conclusions;
* create an implicit migration through adoption rather than an explicit architectural decision.

Experimental outputs must remain distinguishable from canonical production outputs in code, storage, documentation, reports, APIs, and user interfaces.

Promotion Requirement

An experimental analytical surface may become production only through a later accepted ADR.

The promotion decision must define, where applicable:

* the exact semantic output being promoted;
* its single authority owner;
* approved evidence and persisted-input boundaries;
* identity and context scope;
* replay and historical-cutoff behavior;
* deterministic identity and versioning;
* missing-evidence and conflict behavior;
* persistence authorization;
* compatibility and migration strategy;
* cutover and rollback rules;
* allowed consumers;
* retirement of any replaced authority;
* verification and historical-validation requirements.

Promotion must not create parallel canonical authority.

If a promoted runtime replaces an existing production output, the later ADR must explicitly amend or supersede the affected part of this ADR and any subsequent authority ADRs.

Consequences

* EvidenceBackedProjectExecutor remains the production deep-analysis composition and scoring boundary.
* The evidence-backed Market Validation path remains the sole canonical production analytical runtime.
* PipelineOrchestrator remains available for experimental orchestration, plugin execution, persistence integration, automation support, and future migration research.
* Intelligence Engines may produce approved descriptive findings without becoming the canonical investment-scoring runtime.
* Experimental modules remain available for implementation, testing, research, and controlled comparison.
* New production changes must not silently alter scoring formulas, weights, timing calculations, committee semantics, ranking semantics, explainability, or report semantics.
* Documentation must distinguish canonical production components from experimental, operational, and presentation components.
* Persistence, automation, operational records, and user interfaces remain downstream of analytical authority.
* Future migration remains possible, but only through an explicit, reviewed, replay-safe, and compatibility-aware architectural decision.

Alternatives Considered

Replace Market Validation immediately with PipelineOrchestrator

Rejected because the experimental orchestration path did not have an approved migration, compatibility, replay, cutover, and historical-validation plan sufficient to replace the validated production runtime.

Maintain both paths as equally canonical production runtimes

Rejected because parallel canonical runtimes would create competing owners for scoring, timing, committee, ranking, and reporting semantics.

Promote components individually when implementation exists

Rejected because code, tests, persistence, automation, or presentation do not establish analytical authority.

Remove experimental pipeline and plugin modules

Rejected because these components remain useful for research, integration, controlled experimentation, and future architecture work.

Leave runtime classification in informal documentation

Rejected because prose-only guidance would not provide a durable architectural boundary and could allow silent runtime drift.

Reasoning

Option A preserves the validated production path while allowing future architectural work to continue safely.

The decision separates implementation existence from production authority.

This protects:

* reproducible scoring;
* stable timing semantics;
* stable committee semantics;
* explainability;
* report compatibility;
* replay safety;
* migration discipline;
* single ownership of analytical outputs.

A future architecture may replace or promote parts of the current runtime, but that change must be explicit, evidence-backed, compatibility-aware, and recorded through an accepted ADR.