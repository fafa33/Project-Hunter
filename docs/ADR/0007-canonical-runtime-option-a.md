# ADR 0007: Canonical Runtime Option A

## Status

Accepted.

## Context

Project Hunter has two important execution surfaces:

- the evidence-backed Market Validation runtime, which is the current production scoring and reporting path;
- the `PipelineOrchestrator`, plugin, persistence, automation, and Intelligence Engine path, which remains valuable for experimental and future migration work.

Without a recorded architecture decision, future contributors could accidentally treat the experimental orchestration path as the production Market Validation path or create a second competing scoring runtime.

## Decision

Project Hunter adopts Option A: keep the evidence-backed Market Validation runtime as the canonical production architecture for v2.1.x and classify `PipelineOrchestrator`, plugin Intelligence Engines, Intelligence Fusion, and fusion-backed opportunity timing as experimental unless a future ADR explicitly changes that boundary.

Production execution remains:

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
```

## Consequences

- `EvidenceBackedProjectExecutor` remains the production deep-analysis scoring boundary.
- `PipelineOrchestrator` remains supported for plugin, persistence, automation, and future migration work, but it does not replace the production Market Validation path.
- New production changes must not alter scoring formulas, weights, timing calculations, committee semantics, or report semantics unless a future ADR explicitly approves that migration.
- Experimental modules remain available for tests and future architecture work.
- Documentation must distinguish production runtime components from experimental components.

## Alternatives Considered

- Replace the production Market Validation runtime with `PipelineOrchestrator`.
- Maintain both runtimes as equally canonical production paths.
- Remove experimental pipeline and plugin modules until they become production.
- Leave the runtime classification as prose-only guidance without an ADR.

## Reasoning

Option A preserves the validated production path while allowing future architecture work to continue. It minimizes migration risk, protects reproducible scoring and reports, and gives future contributors a clear decision record to amend if the production boundary changes.
