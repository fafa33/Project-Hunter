# Post-PR161 Issue #159 Canonical Comparative Valuation Audit

## Verdict

**APPROVED** — the merged implementation is consistent with ADR 0026 and the boundaries it fixes. No blocking finding was substantiated. Four non-blocking findings are recorded below; none require further action beyond what this report itself records.

## Audited repository state

- `main` HEAD at time of audit: `04d12fc` (merge of PR #168).
- PR #161 scope, independently confirmed via `git diff --stat`: five new, purely additive files — `src/hunter/comparative_valuation/{__init__,models,repository,service}.py` and `tests/test_comparative_valuation_v1.py`. No existing file was modified. No later PR (#163 Mispricing, #165 Asymmetry, #167/#168 doc coherence) has touched `src/hunter/comparative_valuation/` since; `git log main -- src/hunter/comparative_valuation/` shows only the two PR #161 commits (`678e01c`, `e73e261`). The package at current `main` is therefore byte-identical to the merged PR #161 head.
- Merged PR head: `e73e261` ("Address PR #161 review: service-owned replay, explicit missingness, peer uniqueness"), merged via `5df7ff4`.
- Governing documents applied: `docs/AI_REVIEW_PROTOCOL.md` (this is an implementation/contribution review, not an ADPR-readiness audit — `docs/ARCHITECTURE_AUDIT_PROTOCOL.md` explicitly excludes implementation review from its own scope and defers to `docs/AI_REVIEW_PROTOCOL.md`), `docs/DEVELOPMENT_GOVERNANCE.md` Stage 5 (Architecture Review) and Stage 6 (Review Report — this document is that report), and ADR 0026 as the controlling architectural authority.

## Independence

This audit was performed by a session with no authorship relationship to PR #161 (authored by `freebuff-web[bot]`, merged by the repository owner). Every finding below was independently re-derived from the source at the audited revision and from live, independent execution in this session — not accepted from the PR's own description of itself.

## Method

1. Read ADR 0026 in full and extracted every individual invariant it fixes.
2. Read `models.py`, `repository.py`, and `service.py` in full, line by line, tracing each ADR 0026 invariant to the exact enforcing code.
3. Independently confirmed PR #161's exact file scope via `git diff --stat` against its merge base, and confirmed no later PR touched the package.
4. Grepped the full `src/hunter` tree for any import of, or reference to, `comparative_valuation`/`ComparativeValuation` outside the package itself, to test for hidden coupling or downstream authority leakage. Confirmed the only real (non-comment) external references are later, out-of-scope additions in `historical_acquisition/pipeline.py` (a domain-name string list), not anything added by PR #161 and not an import of this package's classes.
5. Installed `pytest`, `ruff`, `black`, and `mypy` in this session and independently reran, from scratch, the exact quality gates the PR claims: `ruff check`, `black --check`, `mypy` (both package-scoped and full-source-tree), the package's own test file, and the ten-file cross-authority valuation-family regression set the PR cites.
6. Grepped for non-deterministic time sources (`datetime.now`, `utcnow`, `time.time`) that could break replay determinism.

## ADR 0026 invariant-by-invariant verification

| ADR 0026 requirement | Enforcing code | Verified |
|---|---|---|
| `CanonicalComparativeValuationService` is sole write authority; repository has no write/apply method | `repository.py` defines only point reads, history reads, universe-scoped queries, unresolved-conflict queries, and migration registration — no `save`/`persist`/`insert` method exists anywhere in the class. All five `persist_*`/`assess` writes live exclusively in `service.py` and open their own session. `test_repository_has_no_replay_selection_methods` additionally asserts the repository exposes none of the five `strict_known_*` replay methods. | Confirmed |
| No consumption of `hunter.valuation`, `hunter.mispricing`, `hunter.asymmetry`, `hunter.market_validation`, or Assembled Fundamental Evidence (ADR 0025) | `grep "^from hunter\|^import hunter"` across all four package files shows imports only of `hunter.comparative_valuation.*`, `hunter.market_facts.repository`, `hunter.persistence.*`, and `hunter.value_capture.*` (native evidence authorities ADR 0026 explicitly authorizes as input). Zero import of any Valuation, Mispricing, Asymmetry, Market Validation, or Evidence Assembly module. | Confirmed |
| Fixed 365-day horizon, `fully_diluted_supply` basis, equal peer weighting, unweighted-median reference, no trimming, positive-cheaper residual sign, `valuation-mispricing` correlation group | All fixed as module constants in `models.py` (`MINIMUM_ELIGIBLE_PEERS`, `REFERENCE_STATISTIC`, `REQUIRED_CORRELATION_GROUP`, etc.) and re-asserted as hard `__post_init__` invariants on `PeerUniversePolicyRecord`/`ComparativeMetricObservationRecord`/`ComparativeValuationAssessmentRecord` (e.g. `accounting_horizon_days != 365` raises; `outlier_treatment != "none"` raises; `equal_peer_weighting is not True` raises). `REQUIRED_CORRELATION_GROUP = "valuation-mispricing"` is the same literal string already used, under the same ADR 0021 citation, by `hunter.valuation`, `hunter.valuation_methodology`, and `hunter.mispricing` — not an invented identifier. | Confirmed |
| No calibrated normalization; `comparative_valuation` never reaches `AVAILABLE` in this foundation | `NORMALIZATION_UNAVAILABLE` is the only legal `NormalizationStatus` value (`Literal["unavailable"]` — the type itself has no other member). `ComparativeValuationAssessmentRecord.__post_init__` unconditionally raises if `availability_state == "AVAILABLE"`. `normalized_value` must be `None`. Independently exercised by `test_raw_observation_is_available_while_normalization_is_unavailable` and `test_complete_evaluation_persists_raw_median_and_residual` (both assert the terminal state is `UNAVAILABLE_UNCALIBRATED_NORMALIZATION`, never `AVAILABLE`). | Confirmed |
| ≥3 eligible peers; 100% decision and observation coverage; peer uniqueness by economic entity, not count alone | `assess()` computes `decision_representations` vs `candidate_representations` and `peer_observation_representations` vs `eligible_peer_representations` as **set equality plus length equality** (service.py, both the eligibility-decision-coverage gate and the observation-coverage gate) — not a bare count comparison. `PeerUniverseSnapshot.__post_init__` independently rejects duplicate `entity_id` among candidates and rejects the target appearing as its own peer. Covered by `test_incomplete_decision_coverage_is_unavailable`, `test_indeterminate_decision_fails_coverage_gate`, `test_fewer_than_minimum_eligible_peers_is_unavailable`, `test_assess_uses_only_replay_eligible_non_superseded_records`, `test_assess_observation_coverage_uses_only_current_observation`. | Confirmed |
| Raw residual `ln(peer_median/target)`, positive-cheaper | `_raw_log_residual` implements exactly this formula and rejects non-positive inputs; `_unweighted_median` implements median-with-mean-of-two-central for even cohorts. Covered by `test_complete_evaluation_persists_raw_median_and_residual` and `test_even_cohort_median_is_mean_of_two_central_values`. | Confirmed |
| Missing/incompatible/conflicted evidence produces `indeterminate`/`excluded`, never a repaired pass; missingness is explicit, never zero-confidence | `_eligibility_dimensions` records a per-dimension `passed=None` (missing) vs `False` (hard fail) vs `True`; any `False` forces `excluded`, any remaining `None` forces `indeterminate`. `ComparativeValuationAssessmentRecord.__post_init__` requires every confidence field to be **blank** (not `"0"`) whenever `availability_state` is not `AVAILABLE`/`UNAVAILABLE_UNCALIBRATED_NORMALIZATION`, and requires them **bounded and weakest-component-capped** otherwise. Covered by `test_unavailable_assessment_persists_explicit_missingness` and `test_confidence_decomposition_and_weakest_component_rule`. | Confirmed |
| Strict-known replay is service-owned, cutoff-safe, known-safe, non-conflicted, non-superseded | `_replay_eligible`/`_strict_known` (service.py) implement exactly this filter; the repository performs no replay selection (see above). No `datetime.now()`/`utcnow()`/`time.time()` call exists anywhere in the package — every timestamp is caller-supplied, so replay is fully deterministic. Covered by `test_strict_known_replay_excludes_future_known_records`, `test_strict_known_replay_reproduces_identical_assessment`, `test_service_strict_known_decision_and_observation_replay`, `test_strict_known_decision_replaces_superseded_predecessor`. | Confirmed |
| Append-only correction: one root per `logical_id`, strictly-later chronology, no branching successors | `_authorize_correction` (service.py), executed inside `BEGIN IMMEDIATE` before every write, enforces all three rules and mirrors the already-accepted `hunter.valuation`/`hunter.valuation_methodology` pattern (including the audit-F1-derived hardened-transaction fix). Covered by `test_correction_and_supersession_lineage`, `test_branching_correction_is_prohibited`, `test_correction_requires_later_chronology`, `test_divergent_duplicate_is_rejected`, `test_insert_identical_reproduces_same_record_ids`. | Confirmed |
| Prohibited downstream composition (Mispricing, Asymmetry, Opportunity, ranking, scoring, recommendation) is absent, not merely undocumented | `test_prohibited_cross_authority_composition_is_absent` asserts the service exposes none of `mispricing`/`asymmetry`/`opportunity`/`rank`/`recommend`/`score`/`portfolio`/`normalize`; independently confirmed by this audit's own `grep` of the package's imports and public API — no such method or import exists anywhere in the four package files. | Confirmed |

## Non-blocking findings

### F-001 — Stale test-count evidence in the PR's own Quality Gate Results

- **Evidence:** `git show 678e01c:tests/test_comparative_valuation_v1.py | grep -c "^def test_"` → 24. `git show e73e261:tests/test_comparative_valuation_v1.py | grep -c "^def test_"` → 31. Independently running `pytest tests/test_comparative_valuation_v1.py -q` on the actual merged head returns **31 passed**, and the cited 10-file cross-authority regression set returns **270 passed** — not the PR body's claimed "24 passed" and "263 passed."
- **Location:** PR #161 description, "Quality Gate Results" section.
- **Category:** Evidence integrity / documentation accuracy.
- **Decision impact:** None on correctness — the actual, reproducible coverage is *higher* than claimed (7 additional tests were added by the review-fix commit `e73e261` and never re-captured in the description). The discrepancy is explained exactly by the pre-fix vs. post-fix commit boundary, not by any flakiness or hidden failure.
- **Consequence if ignored:** A future reader trusting the PR description's literal numbers would under-count the real verification evidence for the merged revision. Not a correctness risk, but a governance/evidence-integrity gap: Stage 3 (Local Verification) and Stage 7 (Final Validation) evidence should reflect the exact reviewed revision, not a pre-revision snapshot.
- **Required action:** None to the code. Recorded here as the accurate count for the merged revision.
- **Blocks merge:** NO — the PR is already merged, no unsafe behavior results, and this audit's independent re-execution supersedes the stale figures.

### F-002 — Dead/duplicate code: `repository.py::_aware`

- **Evidence:** `grep -n "_aware(" src/hunter/comparative_valuation/*.py` shows `_aware` defined in both `repository.py` (line 423) and `service.py` (line 1668); only the `service.py` copy is ever called (lines 1335, 1337).
- **Location:** `src/hunter/comparative_valuation/repository.py:423-426`.
- **Category:** Maintainability / unnecessary code.
- **Decision impact:** None — unreachable, harmless.
- **Consequence if ignored:** Negligible; a future reader could mistake it for load-bearing.
- **Required action:** Optional cleanup: remove the unused `repository.py` copy.
- **Blocks merge:** NO.

### F-003 — Dead code: `service.py::_observation_for`

- **Evidence:** `grep -rn "_observation_for("` across the package shows only the definition at line 1615; no call site exists anywhere in `service.py` or the test file.
- **Location:** `src/hunter/comparative_valuation/service.py:1615-1621`.
- **Category:** Maintainability / unnecessary abstraction.
- **Decision impact:** None.
- **Consequence if ignored:** Negligible.
- **Required action:** Optional cleanup: remove the unused helper.
- **Blocks merge:** NO.

### F-004 — `effective_at` provenance diverges between the available and unavailable `assess()` branches

- **Evidence:** In the raw-available branch (`assess()`, service.py ~line 696), the persisted `ComparativeValuationAssessmentRecord.effective_at` is the caller-supplied `effective_at` argument. In every early-return unavailable branch, `_persist_unavailable_assessment` (which takes no `effective_at` parameter) hardcodes `effective_at=universe.cutoff` instead (service.py line 1262). No validation anywhere in `assess()` requires `effective_at == universe.cutoff` on the call. Every test in the suite passes `effective_at=NOW` identically to the universe's own cutoff, so this divergence is never exercised.
- **Location:** `src/hunter/comparative_valuation/service.py`, `assess()` (~line 696) vs. `_persist_unavailable_assessment` (~line 1262).
- **Category:** Persistence/provenance internal consistency.
- **Decision impact:** None demonstrated under any currently exercised or currently authorized calling pattern (ADR 0026 does not itself specify the `assess()` function signature, only the record's semantics; a universe snapshot's own `effective_at` is separately pinned to equal its `cutoff` by `PeerUniverseSnapshot.__post_init__`).
- **Consequence if ignored:** If a future caller ever invoked `assess()` with an `effective_at` that genuinely differs from the referenced universe's `cutoff` (e.g., a scheduled re-check of an older, still-valid universe snapshot), the persisted assessment's `effective_at` field would carry different semantics depending on which availability branch is taken, which could silently confuse a later `strict_known_assessment(effective_as_of=...)` caller. This is a latent internal-consistency gap, not an observed defect.
- **Required action:** Recommended, not required: either constrain `assess()` to reject `effective_at != universe.cutoff`, or make `_persist_unavailable_assessment` accept and use the caller's `effective_at` for symmetry, plus a regression test exercising the divergent case.
- **Blocks merge:** NO.

## Findings matrix

| Finding | Category | Decision impact | Consequence if ignored | Blocks merge | Evidence |
|---|---|---|---|---|---|
| F-001 | Evidence integrity | None (actual coverage is higher, not lower) | Reader under-trusts real verification depth | NO | Independent `pytest` re-run: 31/270 vs. claimed 24/263 |
| F-002 | Maintainability | None | Negligible reader confusion | NO | `grep` shows no call site |
| F-003 | Maintainability | None | Negligible reader confusion | NO | `grep` shows no call site |
| F-004 | Persistence/provenance consistency | None demonstrated | Latent metadata ambiguity if `effective_at != universe.cutoff` is ever used | NO | Line-level trace of both `assess()` branches |

## Independent verification actually performed (not trusted from the PR description)

- `ruff check src/hunter/comparative_valuation/ tests/test_comparative_valuation_v1.py` — all checks passed.
- `black --check src/hunter/comparative_valuation/ tests/test_comparative_valuation_v1.py` — 5 files unchanged.
- `mypy src/hunter/comparative_valuation/` — success, no issues in 4 source files.
- `mypy src/hunter` (full tree, current `main`) — success, no issues in 496 source files.
- `pytest tests/test_comparative_valuation_v1.py -q` — **31 passed**.
- `pytest tests/test_valuation_v1.py tests/test_valuation_methodology_v1.py tests/test_valuation_authority_v1.py tests/test_value_capture_v3_5_0.py tests/test_value_capture_authority_boundary.py tests/test_market_facts_v3_4_0.py tests/test_canonical_evidence_assembly.py tests/test_canonical_market_validation_persistence.py tests/test_persistence_contracts.py tests/test_comparative_valuation_v1.py -q` (the exact 10-file cross-authority set the PR cites) — **270 passed**.
- `grep` of the full `src/hunter` tree for `comparative_valuation`/`ComparativeValuation` outside the package — confirmed no coupling introduced by PR #161; the only other real (non-comment) reference (`historical_acquisition/pipeline.py`'s domain-name list) was added by a later, out-of-scope PR and does not import this package.
- `grep` for `datetime.now`/`utcnow`/`time.time()` in the package — none found.

## Disposition

**APPROVED.** No Class-C-equivalent (blocking) finding was substantiated against any of the twelve items requested for verification: the repository is a mechanical read boundary with zero write or replay-selection logic; the service is the sole write and replay authority; replay is fully strict-known and deterministic (no non-deterministic time source, cutoff/known-safe filtering, non-superseded-only); no import-level coupling with Valuation, Mispricing, Asymmetry, or Market Validation exists; the foundation is structurally incapable of emitting `AVAILABLE` or a Mispricing/Asymmetry/Opportunity/ranking/scoring capability; no hidden normalization exists (`NORMALIZATION_UNAVAILABLE` is the sole legal value and is enforced at the type level); missingness is explicit and never encoded as zero confidence; no replay shortcut was found; peer uniqueness is enforced by set equality, not count; and the five record families, service methods, and repository surface are proportionate to what ADR 0026 itself mandates, with only two small pieces of genuinely dead code found on close inspection (F-002, F-003) and no unjustified abstraction layer.

The four recorded findings are non-blocking: three are trivial (a stale self-reported test count that undercounts real coverage, and two unused private functions), and the fourth (F-004) is a latent, currently-unexercised internal-consistency gap worth a follow-up test and either a validation or a symmetry fix, not a correction to already-merged behavior.
