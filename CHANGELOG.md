# Changelog

## v3.6.0 - 2026-08-05

### Added

- **Hunter Governance Review** mandatory merge gate
  (`docs/HUNTER_GOVERNANCE_REVIEW.md`): a repository feature combining a
  deterministic governance engine, an LLM hostile architecture audit, and a
  decision engine. The gate resolves the exact source HEAD / target BASE pair,
  re-verifies it at decision time, and publishes the required `Hunter
  Governance Review` status check. It is fail-closed: `CHANGES_REQUIRED` and
  `REVIEW_FAILED` publish a failing check, are never converted into approval,
  and are never skipped silently.
- PR-event workflow (`.github/workflows/hunter-governance-review.yml`) covering
  `opened`, `reopened`, `synchronize`, `ready_for_review`,
  `converted_to_draft`, `edited`, `review_requested`, and
  `review_request_removed`, plus `workflow_dispatch`.
- Reconciliation workflow (`.github/workflows/hunter-governance-reconcile.yml`)
  that re-evaluates open pull requests on protected-branch pushes and on a
  30-minute schedule, so target-branch advancement invalidates stale approvals.
- Stdlib-only gate engine under `scripts/hunter_governance_review/` and unit
  tests in `tests/test_hunter_governance_review.py`.
- LLM audit prompt token budget (`PROMPT_CHAR_BUDGET`, `MAX_COMPLETION_TOKENS`
  in `llm_audit.py`): the full assembled prompt is now bounded to a fixed
  character budget derived from the pinned default model's actual provider
  rate limit, with the diff absorbing whatever budget remains after the PR
  body, changed-file list, findings, and governance brief are rendered. This
  closes a real gap found during the gate's own live installation validation:
  the prior, disconnected 150,000-character diff cap produced a request Groq
  rejected outright (HTTP 413, TPM limit 12,000, requested 27,258) on this
  gate's own installation PR.

### Changed

- Removed the fail-open, comment-only "Adversarial AI Review" job from
  `.github/workflows/dependency-review.yml`; the mandatory Hunter Governance
  Review gate supersedes it.

### Documentation

- Added `docs/HUNTER_GOVERNANCE_REVIEW.md` and documented the merge gate in
  `docs/CI.md`.

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
