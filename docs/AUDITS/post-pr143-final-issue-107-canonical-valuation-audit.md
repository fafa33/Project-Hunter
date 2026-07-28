# Post-PR143 Final Issue #107 Canonical Valuation Audit

## Verdict

**APPROVED** — for the implementation scope this audit covers (Milestones 1–4, the production entry point, and the read-only status query).

**Issue #107 must remain open.** This audit's `APPROVED` verdict satisfies Required Delivery Sequence item 7's audit gate, but a second, independent, currently unmet gate — a real, evidence-backed successful fair-value estimate, per Issue #135 Finding 1's binding clarification of the Completion Criterion — blocks closure. See "Issue #107 disposition" below.

## Audited repository state

- Canonical `main` HEAD: `a2e4f5984750b51a19ef4cc8f3e1248005e5f2b8`
- Merged PRs in scope: #110 (Milestone 1), #122 (Milestone 2), #123 (Milestone 3), #124 (Milestone 3 hardening), #125 (Milestone 4), #129 (real-evidence-backed validation and prohibited-methodology tests), #131 (production entry point, `hunter valuation-authority`), #143 (read-only `status` query)
- No file under `docs/ADR/` or `docs/architecture-records/` was touched by any PR in scope.
- No CI is configured in this repository (`.github/workflows/ci.yml` defines the gate; no status checks are currently reported against pull requests in this environment) — all verification below was executed directly against the audited HEAD, not inferred from a CI badge.

## Independent verification method

This audit was performed by the same session that authored PR #143 (the read-only `status` query) in a prior turn, but had no authorship relationship to Milestones 1–4 (#110, #122–#125), the real-evidence remediation (#129), or the production entry point (#131) — all of that code predates this session and was written by earlier, separate sessions. Per `docs/AI_REVIEW_PROTOCOL.md`, the PR #143 overlap is disclosed here rather than concealed; for that specific PR this audit is a second, independent re-verification rather than a first review (a full independent line-by-line audit of PR #143 was separately requested and performed earlier in this session, reaching the same findings reported here). For every other file audited, independence is genuine: every finding below was re-derived directly from the source and from live execution in this turn, not accepted from the implementation reports' own claims.

Evidence gathered independently, not assumed from prior reports:

1. Direct line-by-line review of `src/hunter/valuation_methodology/service.py` (Milestone 2), `src/hunter/valuation/service.py` (Milestone 3), and `src/hunter/historical/valuation_calibration.py` (Milestone 4), tracing every ADR 0022 invariant claimed by the implementation reports against the actual code, not the reports' descriptions of it.
2. A live, manual, non-pytest execution of the full production entry point (`hunter valuation-authority run`) against freshly seeded fixture evidence, producing a real `ValuationMethodologySnapshot` and `FairValueEstimateRecord`/`ValuationAssessmentRecord`, then independently querying them back through the new `status` operation and inspecting the raw JSON output.
3. Direct SQL inspection of the tracked `data/data_ops.sqlite` to independently re-confirm the real, currently-persisted Sky evidence chain's actual field values (not the reports' summary of them).
4. A live network reachability test against `api.coingecko.com` from this audit environment.
5. Two independent, full, from-scratch runs of the complete `pytest` suite on the audited HEAD (not one run trusted twice).
6. Fresh `ruff check .`, `black --check .`, and `mypy` runs on the audited HEAD, with the `black` exceptions independently cross-checked against `origin/main` to confirm they pre-date and are unrelated to any PR in scope.

## Final verification

### 1. Milestone 1 — entity registration and evidence acquisition

Verified, with an explicitly disclosed, non-blocking limitation carried forward from the original implementation.

Direct SQL inspection of `data/data_ops.sqlite` on the audited HEAD confirms Sky's evidence chain is real and persisted: `FundamentalEvidenceRecord` (`57ddd15569f7348f72b6a0498fd8fff6d0fae0e61d0906f856625cc9ed2e5e47`), `ValueCaptureRuleSnapshot` (`f774f946b80c11244faddeb69f4d40e01a501226c036df5497bc3dbc00020abd`), `SupplyBasisSnapshot`, and three `ObservedMarketFactRecord`s all exist and are independently retrievable through the existing strict-known read APIs, satisfying Milestone 1's literal acceptance criterion ("at least one entity's complete evidence chain is independently verifiable... via the existing strict-known read APIs").

The accounting period is 30 days (`2026-06-25T17:40:48Z`–`2026-07-25T17:40:48Z`), `amount` is `None`, and `rate_or_proportion` is `None` — independently re-confirmed by this audit's own direct query, not taken from any prior report. This is disclosed, not concealed, in every implementation report since the Milestone 1 remediation comment on the issue, and is the evidence-availability gap this audit's disposition section addresses; it is not a code defect.

### 2. Milestone 2 — `ValuationMethodologySnapshot`

Verified.

`permitted_model_identifier` and `horizon_days` are fixed module constants (`PERMITTED_MODEL_IDENTIFIER = "discounted-value-capture-flow-v1"`, `REQUIRED_HORIZON_DAYS = 365`, `src/hunter/valuation_methodology/models.py`) injected by `CanonicalValuationMethodologyAuthority.persist_methodology` and never accepted as caller-supplied arguments — no code path in the public API can override either value, independently confirmed by reading the full method signature and body.

Correction-lineage authorization (`_authorize_correction`) rejects a second independent root record for the same `logical_id`, rejects a missing/wrong-type predecessor, rejects non-advancing `recorded_at`/`known_at`, and rejects branching successors — all enforced inside a `BEGIN IMMEDIATE` transaction before any write, closing the same race condition class the code's own comments attribute to a prior audit finding (F1) and correctly reuse rather than reimplement.

### 3. Milestone 3 — `CanonicalValuationService`

Verified.

Traced directly against ADR 0022's Prohibited Methodologies section: `market_facts` are fetched and validated (strict-known, quality, conflict, versioning) but their `value` field is never read into the discount/valuation arithmetic — only `record_id`/`semantic_version` are dereferenced for provenance. The discount rate (`CANONICAL_DISCOUNT_RATE = Decimal("0.15")`) is a fixed, versioned constant, not derived from any market observation. No path from `spot_price`, `market_capitalization`, or `fully_diluted_valuation` into the fair-value output exists in this file.

`p10 <= p50 <= p90` was independently re-derived, not merely trusted: `rule.rate_or_proportion` is range-checked to `[0, 1]` at the `hunter.value_capture` layer (`src/hunter/value_capture/models.py:357`, "rate_or_proportion must be between 0 and 1") before this service ever consumes it, and `raw_flow` is checked non-negative (line 266) — together these guarantee `p50_unit >= 0`, which combined with `uncertainty` clamped to `[0, 1]` (lines 280–282) makes the `p10_unit`/`p90_unit` construction (lines 283–284) sound. This audit specifically tested the hypothesis that a negative rate could invert the invariant and confirmed it is structurally impossible given the upstream range check.

`confidence_base` is the `min()` of all four input confidences (entity-link, evidence, rule, supply), matching the required-test description exactly. Repository-bypass rejection, append-only correction with branching-lineage rejection, and the `BEGIN IMMEDIATE` transaction pattern mirror Milestone 2's, correctly reused rather than duplicated with drift.

The horizon/accounting-period check (lines 203–215) requires the evidence's accounting period to fall fully inside the lookback window **and** to be exactly `horizon_days` long — this is the exact, correctly-implemented gate that fails Sky's real 30-day evidence closed, independently confirmed by this audit's own live execution (below) and by re-reading the raised error text against the code.

### 4. Milestone 4 — historical replay and leakage validation

Verified.

`ValuationCalibrationHarness.replay` reconstructs each input record's lineage independently (via the same strict-known repository methods `CanonicalValuationService` itself uses) and cross-checks the independently reconstructed lineage against what the replayed estimate declares (`_cross_check_lineage`) — a genuine double-check, not a trivial tautology, since the two code paths query the repositories separately rather than sharing a single lookup. Leakage is independently re-verified per record (`_verify_no_leakage`, using the shared, already-audited `timestamp_valid_at` primitive from `hunter.historical.cutoff`) rather than trusting the write-time checks alone.

### 5. Production entry point and status query (PR #131, #143)

Verified, including by direct manual execution (not solely by trusting the test suite).

This audit constructed real fixture evidence, ran `hunter.valuation_authority.command.main(["run", ...])` for `methodology` and `estimate` operations directly (not through pytest), and confirmed real `ValuationMethodologySnapshot`/`FairValueEstimateRecord`/`ValuationAssessmentRecord` records were correctly persisted and returned. It then queried the same records back through the new `status` operation and confirmed the returned JSON exactly matches the records just created (`record_id`, `logical_id`, full field set, correctly ISO-8601-serialized timestamps, correctly list-serialized tuple fields). `_status` calls only the pre-existing `strict_known_methodology`/`strict_known_fair_value_estimate`/`strict_known_valuation_assessment` repository methods and performs no write, no authorization decision, and no interpretation of the data — confirmed by reading the full function body; `CanonicalValuationRepository`/`ValuationMethodologyRepository` still expose no public write method.

Unknown-target and missing-required-field manifests were independently tested live and confirmed to fail closed with clear errors, consistent with the existing module's error-handling convention for `methodology`/`estimate`.

### 6. Real-evidence blocker independently re-confirmed

Verified as currently blocking, not resolved.

- Direct SQL query against the tracked `data/data_ops.sqlite` on the audited HEAD reproduces exactly what every implementation report since Milestone 1 has disclosed: Sky's accounting period is 30 days, `amount` is `None`, `rate_or_proportion` is `None`.
- `curl` from this audit environment to `https://api.coingecko.com/api/v3/ping` fails with `CONNECT tunnel failed, response 403` (outbound proxy policy denial), independently reproducing the network blocker every prior report attributes to environment policy, not code.
- No other real, qualifying (exact-365-day, populated amount and rate) disclosure exists for any currently-registered entity. `ADPR-0002` (merged, `IN_RESEARCH`) independently surveyed 13 real protocols and found none satisfy this shape natively; it deliberately makes no recommendation or selection, by explicit task scope.

### 7. Deterministic tests and quality gates

Verified, independently, twice.

- `ruff check .` — all checks passed (rerun fresh on the audited HEAD).
- `black --check .` — 5 pre-existing files require reformatting (`src/hunter/committee/repository.py`, `src/hunter/committee/engine.py`, `src/hunter/discovery/repository.py`, `tests/test_operational_status.py`, `tests/test_dashboard_api.py`); independently confirmed identical on `origin/main` before any PR in this audit's scope, and none of the 5 files are touched by any PR in scope. No file this audit's scope touches requires reformatting.
- `mypy` — success, no issues in 589 source files.
- Full `pytest` suite — run independently twice, from scratch, on the audited HEAD: **1271 passed, 0 failed** both times (610–621s each). No flakiness observed between runs.
- `tests/test_valuation_authority_v1.py` specifically — 21 passed, individually verified by name (not just by count).

## Blocking findings

None.

## Non-blocking observations

- `src/hunter/valuation_authority/command.py`'s new `_json_safe` helper duplicates (rather than imports) `src/hunter/valuation/repository.py`'s existing private `_json_safe`. Both are correct and independently self-contained, consistent with this codebase's established convention of small, independently-owned orchestration/repository modules that do not share private cross-module utilities; the duplication is a minor maintainability nit, not a correctness or authority-boundary issue. Not required to be resolved before this audit's approval.

## Issue #107 disposition

This audit's `APPROVED` verdict on Milestones 1–4, the production entry point, and the status query satisfies Required Delivery Sequence item 7's audit gate for the implementation this issue scoped.

**Issue #107 must nevertheless remain open.** Its Completion Criterion requires "a fair-value estimate for the first supported entity class... with demonstrated leakage-safe, deterministic historical replay, independently audited." Issue #135 Finding 1 — the repository's own later, explicit, authoritative clarification of this same completion criterion — states directly: "Existing real-data validation proves truthful fail-closed behavior, not a successful real fair-value estimate," and requires, before Issue #107 may close: (1) acquisition of real, qualifying (exact-365-day, populated amount and rate) evidence; (2) persistence of that evidence; (3) execution of `CanonicalValuationService` through the production entry point against it; (4) independent query and replay of the resulting records; (5) an independent final audit; (6) closure "only when its current structured-valuation completion criterion is truthfully satisfied."

Steps (2)–(5) are architecturally and operationally ready today — every piece of software required for them exists, is correct, and is independently verified by this audit. Step (1) is not achievable by further implementation: it requires either a future decision selecting among `ADPR-0002`'s enumerated disclosure-architecture-classification options (an architectural decision this audit is not authorized to make), or independently verified discovery of a genuinely different real, qualifying disclosure (a research task outside this audit's scope and this environment's network access).

**Issue #107 remains open, blocked on real qualifying evidence, not on implementation.** No further code change is required or recommended by this audit. The next action against Issue #107 is not a pull request; it is either a future architecture-decision session, or the operator independently registering a genuinely qualifying real entity through the existing, unmodified `hunter valuation-evidence acquire` path.
