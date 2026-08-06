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
- Hostile-audit finding visibility: a `CHANGES_REQUIRED` verdict's summary,
  structured findings, and rationale are now surfaced in the GitHub status
  description, the workflow run log, and `GITHUB_STEP_SUMMARY`, instead of
  being discarded after picking the bare outcome. This closes a second real
  gap found on this gate's own installation PR: once the token-budget fix
  above let the audit actually run, the gate returned `CHANGES_REQUIRED` with
  no findings visible anywhere on GitHub to act on.
- Deterministic, lossless diff chunking (`chunking.py`), authoritative
  governance context resolved via the GitHub API at the exact base commit
  instead of a hardcoded brief (`context.py`), cross-chunk aggregation with
  fail-closed coverage guarantees (`aggregate.py`), a post-audit (not just
  pre-audit) freshness re-check, strict structured-verdict schema
  validation, and an adaptive completion-token budget. Closes five
  merge-blocking findings from an independent review of this gate. Also
  fixes a rate-limit failure mode the chunking work itself introduced and
  that a live re-verification against PR #200's own diff caught directly:
  116 sequential per-chunk calls exhausted Groq's tokens-per-minute *rate*
  (not any single request's size) after 2-3 calls, failing 113/116 chunks
  with HTTP 429; `run_llm_audit` now retries a per-minute 429 with the
  provider's suggested backoff (a per-day quota 429 is never retried --
  no CI-job-bounded wait can outlast a daily reset), and duration parsing
  now handles Groq's full `<h>h<m>m<s>s` form after a live run showed the
  prior seconds-only parser silently dropping an `"44m"` prefix.
- A second round of fixes for five further merge-blocking findings:
  outer diff truncation is removed entirely (a retrieved diff exceeding the
  coarse sanity bound now fails closed rather than silently truncating
  before coverage accounting ever sees the loss); one minimal, additional
  LLM call performs cross-chunk consistency synthesis after every chunk
  succeeds, over chunk summaries/findings only, reusing the exact same
  schema/budget/retry machinery as a per-chunk call
  (`llm_audit.run_synthesis_review`, `aggregate.apply_synthesis`); the
  context manifest's `included_chars` now reports the real, post-budget
  content length actually sent to the model instead of the pre-budget
  attempted length, and a mandatory document squeezed to zero characters by
  the budget fails closed; `validate_audit_payload` rejects unknown
  top-level/finding fields, `CHANGES_REQUIRED` with only non-blocking
  findings, and any `finish_reason` other than `"stop"`; and
  `parse_audit_response` no longer extracts JSON embedded in surrounding
  prose. A further live re-verification then caught a genuine miscalibration
  the new context fail-closed check exposed: the real canonical hierarchy's
  12 mandatory documents need 11,758 characters to all fit in full, not the
  2,000-character budget chosen in the prior round -- both
  `context.DEFAULT_TOTAL_CHAR_BUDGET` (12,500) and
  `llm_audit.PROMPT_TOKEN_BUDGET` (8,500) were raised together so the larger
  context budget does not starve the per-chunk diff budget back into
  excessive chunk counts.
- Closes the two remaining false-approval paths identified in a further
  independent architecture review: (1) the cross-chunk synthesis call
  previously reasoned only over each chunk's one-line prose summary, so a
  contradiction could hide behind two individually unremarkable summaries;
  every chunk, document-section, and synthesis response now also extracts
  structured architectural evidence (`llm_audit.ARCHITECTURAL_EVIDENCE_CATEGORIES`:
  entities introduced, ownership declarations, authority changes, dependency
  changes, persistence/replay contracts, canonical interfaces, affected
  ADRs/contracts, exported APIs, cross-file references), which is now
  mandatory on every response and is what synthesis primarily reasons over
  (`aggregate.describe_chunks_for_synthesis`). (2) authoritative governance
  documents were resolved in full at the exact base commit but only a small,
  bounded excerpt was ever actually reviewed by the audit -- a resolved
  document is not the same as a reviewed one. Every mandatory document's
  full text is now deterministically, losslessly split into sections
  (`chunking.split_document_into_chunks`, reusing the existing diff-chunking
  algorithm generically) and every section is reviewed against the pull
  request's structured evidence (`llm_audit.run_document_review`); any
  failed or unreviewed section fails the whole review closed
  (`aggregate.aggregate_document_chunk_outcomes`/`apply_document_review`,
  `DocumentReviewManifest`), and the resulting coverage manifest's
  `bytes_reviewed` reflects only bytes that passed through an actual review
  call, never bytes that were merely retrieved.
- Deterministic multi-provider LLM failover (`llm_audit._resolve_providers`,
  `_call_chat_completion`), closing the gate's single-provider dependency.
  Live re-verification against PR #200's own current diff (workflow run
  31107051102) hit Groq's tokens-per-day quota after only 1/50 diff chunks
  succeeded and correctly published `REVIEW_FAILED` rather than a false
  approval -- but analysis showed the real problem is architectural, not a
  one-off: this repository's diff/document sizes require far more tokens per
  full review (this PR's diff alone chunks into ~50 requests at up to
  ~8,500 tokens each) than a single Groq on-demand account's entire daily
  quota (100,000 tokens), and that per-chunk budget is already sized against
  a hard, provider-imposed per-request ceiling (12,000 TPM), so reducing
  invocation count alone cannot close the gap without either exceeding that
  ceiling or reducing the governance context each chunk is judged against.
  `HUNTER_LLM_API_KEY`, `GROQ_API_KEY`, and `OPENAI_API_KEY` were already the
  three documented provider secrets, but were previously a first-match-wins
  exclusive choice with no runtime recovery if the chosen one failed; every
  configured secret is now an independently-triable provider, tried in that
  same fixed priority order for every LLM call the gate makes (each diff
  chunk, the synthesis call, and each document-review section
  independently), with `REVIEW_FAILED` occurring only once every configured
  provider has failed for that specific call. 100% backward compatible for
  every existing single-secret configuration.

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
