# Changelog

## v3.5.2 - 2026-07-25

Issue #88 hardening release. Closes the two follow-up gaps identified in the
independent post-remediation architecture analysis of the observed-market-facts
foundation (Issue #88) and its relationship to the fundamental-evidence
foundation (Issue #95).

### Changed

- `hunter.market_facts`: `raw_payload_hash` is now validated as a well-formed
  `sha256:`-prefixed 64-hex digest on `MarketFactAcquisitionResult`,
  `ObservedMarketFactRecord`, and `MarketFactAvailabilityEvent`, closing a
  previously-unenforced "malformed hashes" acceptance criterion.
- `hunter.value_capture`: `SupplyAndValueCaptureService` now cross-validates
  every `SupplyBasisSnapshot.observed_market_fact_ids`/`observed_market_fact_versions`
  reference against real `hunter.market_facts.ObservedMarketFactRecord` entries
  for existence, version match, identity compatibility, temporal compatibility,
  and quality/conflict state, closing a referential-integrity gap between the
  two completed evidence foundations.

### Documentation

- Proposed ADR 0022 (Canonical Valuation Methodology) and opened Issue #107,
  defining the methodology required by ADR 0021 before `CanonicalValuationService`
  implementation may begin. Documentation only; no runtime, schema, or
  migration change.

No persistence schema, migration ID, or ADR was changed by the code changes in
this release. `valuation`, `comparative_valuation`, `mispricing`, and
`asymmetry` remain unavailable in Market Validation, unchanged.

## v1.0.0 - 2026-07-11

Project Hunter V1 stable release.

### Completed Architecture

- Project Constitution and architecture boundaries.
- Plugin Architecture.
- Pipeline Orchestrator.
- Deterministic Execution Identity.
- Persistence Contracts and SQL Repository Layer.
- Pipeline Persistence Integration.
- Operational Attempts and Run Lifecycle.
- Automation and Scheduler Layer.
- Dashboard Foundation.

### Completed Analytical Engines

- Macro Intelligence Engine.
- Whale Intelligence Engine.
- Developer Intelligence Engine.
- Protocol Intelligence Engine.
- News Intelligence Engine.
- Narrative Intelligence Engine.
- Social Intelligence Engine.
- On-chain Intelligence Engine.
- Cross-Engine Intelligence Fusion Layer.
- Opportunity Timing Engine.
- Probability Engine.
- Pattern Matching Engine.
- Technology Necessity and Capital Rotation Engine.
- Investment Committee Engine.

### Completed Platform Capabilities

- Ranking integration.
- Report rendering.
- Backtesting summaries.
- Alert evaluation surface.
- CLI validation surface.
- End-to-end runtime validation.

### Verification

- Ruff passed.
- Black check passed.
- mypy passed.
- Full pytest suite passed.
- End-to-end deterministic runtime validation passed.

### Release Status

- V1 baseline is officially released and frozen.
- Maintenance branch: `release/v1`.
- Future development continues on `main`.
