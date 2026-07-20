# Project Hunter Canonical Runtime Architecture

Status: Production Canonical for v2.1.x

Project Hunter v2.1.x adopts the current evidence-backed Market Validation runtime as the canonical production architecture. This document implements `docs/ADR/0007-canonical-runtime-option-a.md` and separates production components from experimental and deprecated components without changing runtime behavior.

## Canonical Runtime

The production execution path is:

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

Implementation mapping:

| Runtime Step | Production Implementation | Classification |
| --- | --- | --- |
| CLI | `src/hunter/cli.py` | Production |
| Acquisition | `src/hunter/acquisition/`, `src/hunter/narrative/`, `src/hunter/macro/`, `src/hunter/whale/` | Production |
| Validation | Acquisition validators plus Macro, Whale, and Narrative validation | Production |
| Repositories | `FileAcquisitionRepository`, `MacroRepository`, `WhaleRepository`, graph/economic/scenario repositories, `TimingRepository` | Production |
| EngineValidationSource | `src/hunter/market_validation/acquisition_sources.py` | Production |
| Project Executor | `EvidenceBackedProjectExecutor` | Production |
| Weight Engine | `src/hunter/weights/` | Production |
| Timing | `src/hunter/timing/` | Production |
| Committee | Market Validation committee fields on `ProjectValidationResult` | Production |
| Explainability | `src/hunter/explainability/` over `MarketValidationRun` | Production |
| Reports | `MarketValidationRenderer`, `EvidenceReportRenderer`, timing CLI renderers | Production |

`SourceBackedV1ProjectExecutor` and `DeterministicV1ProjectExecutor` remain import aliases for backward compatibility only. New production code should use `EvidenceBackedProjectExecutor` and `DeterministicProjectExecutor`.

## Component Classification

| Component | Classification | Policy |
| --- | --- | --- |
| Market Validation runtime | Production | Canonical production path |
| Evidence-backed acquisition sources | Production | Canonical bridge from persisted evidence to scoring inputs |
| `src/hunter/weights/` | Production | Canonical weighting implementation |
| `src/hunter/timing/` | Production | Canonical timing implementation |
| Market Validation committee fields | Production | Canonical v2.1 committee output |
| `PipelineOrchestrator` | Experimental | Retained for plugin, persistence, automation, and future migration work |
| Plugin Intelligence Engines | Experimental | Retained for tests and future Option B-style migration only |
| `src/hunter/intelligence/fusion/` | Experimental | Not part of the v2.1 production runtime |
| `src/hunter/opportunity/` | Experimental | Fusion-backed timing model, not production timing in v2.1 |
| Legacy V1 executor names | Deprecated aliases | Preserve imports, do not use for new code |

## Coverage Semantics

Coverage reporting distinguishes two categories:

- Independent production engines: direct persisted evidence sources or direct normalized source families.
- Derived analytical views: deterministic views derived from one or more upstream production evidence sources.

Independent production engine labels:

- valuation
- comparative_valuation
- mispricing
- asymmetry
- developer
- protocol
- news
- social
- narrative
- whale_intelligence
- macro_intelligence
- validation_health

Under ADR 0020, `valuation`, `comparative_valuation`, `mispricing`, and `asymmetry` are required labels but have no currently authorized numeric contract. They remain explicitly unavailable; CoinGecko profile or completeness metrics cannot populate them. Their presence in the coverage taxonomy does not establish availability or semantic authority.

Derived analytical views:

- future_demand
- opportunity_timing
- probability
- pattern_matching
- technology_necessity
- capital_rotation
- necessity_gap
- risk
- committee

Coverage percentages remain calculated over the same required evidence surface as before. The reporting labels are clarified so derived views are not mistaken for separately executed engines.

`necessity_gap` likewise remains explicitly unavailable pending a later accepted contract. Technology-graph centrality and `technology_necessity` cannot substitute for it.

## Timing Policy

Production Timing is `src/hunter/timing/`.

Experimental Timing is `src/hunter/opportunity/`.

Only strict-known, compatible, persisted `TimingAssessment` records produced by `OpportunityTimingEvidenceEngine` may feed Market Validation coverage, committee fields, explainability, and reports. Selection preserves actual effective, recorded, known, and generated times. Latest/current state and economic-graph, scenario, Fusion, Opportunity, Dashboard, Operational Corpus, or report values cannot substitute. The Fusion-backed `src/hunter/opportunity/` package remains available for tests and future architecture work, but it is not the production timing implementation.

## Committee Policy

The production committee output in v2.1.x is the deterministic committee decision and committee confidence produced on `ProjectValidationResult` by the evidence-backed Market Validation runtime.

`src/hunter/committee/` remains an experimental/persisted committee engine. It is retained for historical tests, dashboard persistence contracts, and future consolidation, but it is not the current production committee decision path for Market Validation.

## Migration Policy

Do not migrate production execution to `PipelineOrchestrator` unless a future ADR explicitly replaces `docs/ADR/0007-canonical-runtime-option-a.md`.

Allowed consolidation work:

- Rename legacy identifiers to production-neutral names.
- Preserve backward-compatible aliases.
- Improve documentation and reporting labels.
- Add characterization tests for the current runtime.

Disallowed consolidation work:

- Changing scoring formulas.
- Changing weights.
- Changing timing calculations.
- Replacing `EvidenceBackedProjectExecutor` with `PipelineOrchestrator`.
- Removing experimental modules.
- Reclassifying derived views as independent engines.

## Future V3 Direction

Future V3 work should either continue hardening the evidence-backed production runtime or explicitly schedule a separate migration decision. Until then, the production runtime remains the evidence-backed Market Validation path defined in this document.
