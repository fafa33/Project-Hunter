# Architecture Impact Analysis: ADR 0022 (Canonical Valuation Methodology)

Companion document to `docs/ADR/0022-canonical-valuation-methodology.md`. This analysis explains why the ADR must be adopted before any valuation implementation begins, and traces its effect on every downstream engine named in ADR 0021's implementation order plus Opportunity Assessment.

## Why this ADR must precede implementation

ADR 0021 authorized the *evidence and record contracts* beneath valuation but was explicit that it authorizes no numeric methodology: *"No formula is authorized for estimating fair value... merely to fill a field."* Its own implementation-order step 3 names "a separate methodology ADR" as a literal precondition for implementing `CanonicalValuationService` — not a recommended practice, a stated blocker.

Three independent lines of repository evidence confirm this blocker is real, not procedural:

1. **No methodology exists anywhere.** Grepped `src/hunter/` for `CanonicalValuationService`, `ValuationMethodologySnapshot`, `FairValueEstimateRecord`: zero matches. There is nothing to review, audit, or extend — a methodology ADR is the only way this decision can be made auditable rather than embedded silently inside a future PR's code review.
2. **No entity satisfies the evidence chain today.** `configs/market_fact_sources.yaml`'s one enabled identity binding (Bitcoin) has no value-capture rule by design. `configs/value_capture_sources.yaml` has no identity-scoped bindings at all. Writing code against an undefined entity class would force an implicit, unreviewed entity-class decision into the implementation PR itself — exactly the failure mode ADR 0020 (aliasing, cutoff backfill, undocumented substitution) was written to close, applied one layer up the stack.
3. **The correlation-group and anti-double-counting policy has no owner yet.** ADR 0021 requires `valuation` and `mispricing` to share one declared correlation group with a combined-weight cap *before* either can be scored together. That cap must be declared by a methodology, not invented inside `CanonicalValuationService`'s implementation.

Absent this ADR, an implementer has two choices, both prohibited: invent a formula ad hoc (violates ADR 0021 directly), or defer every methodology decision to code-review discussion (violates this repository's own established governance sequence — issue → sprint spec → implementation → independent audit — used without exception for #88, #95, and both hardening PRs). This ADR is the only artifact that can legitimately unblock implementation.

## Effect on Comparative Valuation

ADR 0021 implementation-order step 4 (`CanonicalComparativeValuationService`) is untouched in scope by ADR 0022 — no peer-selection mechanism, denominator, or cohort policy is defined here. However, ADR 0022's Comparability Rules and Peer-Selection Principles sections **constrain the space** any future Comparative Valuation methodology ADR must work within: a peer must satisfy this ADR's own entity-class Scope criteria (same value-capture-rule-bearing class, same fundamental-evidence-chain requirement), and comparability is gated on identical `ValuationMethodologySnapshot` ID/version. This means Comparative Valuation cannot be authored independently of this ADR even though it is architecturally separate — its eligible universe is a subset of whatever entity population this ADR's methodology eventually validates. Practically: Comparative Valuation's own future ADR is now unblockable in *principle* the moment 0022 is accepted, but unblockable in *practice* only once at least a small population of entities has been validated under 0022, since a comparable cohort of size one is not a cohort.

## Effect on Mispricing

This is the most direct dependency in the graph. ADR 0021's `mispricing` row is defined as `(fair_value_p50 - observed_market_price) / observed_market_price` — it has no meaning without a `FairValueEstimateRecord` to reference. ADR 0022 gives Mispricing a concrete, versioned target for the first time: once `CanonicalMispricingService` (ADR 0021 step 5) is implemented, it will consume exact-version `FairValueEstimateRecord`s produced under this ADR's methodology plus `ObservedMarketFactRecord`s already available from Issue #88. ADR 0022 also fixes the `valuation`+`mispricing` correlation group (`valuation-mispricing`) that ADR 0021 required but left undeclared — this is a direct, load-bearing contribution to Mispricing's own future contract, not merely a prerequisite. Mispricing cannot begin implementation until ADR 0022 is accepted **and** at least one `FairValueEstimateRecord` exists to reference; it does not require Comparative Valuation or Asymmetry to exist first.

## Effect on Asymmetry

Weaker, indirect dependency. ADR 0021's `asymmetry` row permits (does not require) referencing "a compatible `FairValueEstimateRecord`... only under the declared anti-double-counting policy." ADR 0022 does not define scenario sets, probability policy, or payoff models — that remains entirely the future Asymmetry methodology ADR's scope (ADR 0021 step 6). What ADR 0022 does contribute is the *shape* of the anti-double-counting constraint Asymmetry must satisfy if it chooses to reference fair value: it cannot double-count the same value-capture flow that already produced Mispricing's upside (ADR 0021's own rule, now concretely instantiable once `FairValueEstimateRecord` exists). Asymmetry can, in principle, be developed without ever referencing this ADR's output (it can run on scenario/probability evidence alone) — but if it does choose to reference fair value, ADR 0022's confidence-decomposition and correlation-group rules become binding on it.

## Effect on Opportunity Assessment

**None, directly — and this must be stated explicitly because it is the most likely point of confusion.** Opportunity Assessment remains classified experimental under ADR 0016/0017, and ADR 0018 has already explicitly rejected the two most obvious mappings (`valuation_discount` ← Market Validation `valuation`; `relative_valuation` ← Market Validation `comparative_valuation`), with the stated rationale that reuse "would feed Market Validation analysis back into Opportunity and overlap `hunter_score`, ranking, and committee evidence." ADR 0022 does not reverse, weaken, or revisit that rejection. Completing this ADR's methodology and even fully implementing `CanonicalValuationService` does not, by itself, make any Opportunity factor eligible — ADR 0017's production-promotion gate and ADR 0018's per-factor decision matrix both require a **separate future scoring ADR** with its own full anti-double-counting analysis before any linkage could be authorized. Any implementer who treats ADR 0022's acceptance as implicitly unblocking Opportunity Assessment would be acting outside this ADR's authorization and outside ADR 0017/0018's explicit governance.

## Effect on other downstream engines

- **Market Validation (`hunter.market_validation`)**: unaffected until ADR 0022's own implementation issue reaches Milestone 5 (input-adapter wiring). No file under `market_validation/` is touched by this ADR itself.
- **Investment Committee / ranking**: unaffected. Committee fields are computed from Market Validation's `ProjectValidationResult`, which does not gain a populated `valuation` field until Milestone 5's independent audit passes.
- **`hunter.historical` / `hunter.backtest`**: gains a new consumer (the calibration/leakage-testing requirement in ADR 0022) but requires no modification — the ADR explicitly directs reuse of `bias_controls`/`cutoff`/`replay` rather than a new harness, to avoid duplicating already-audited leakage-safety logic.
- **Dashboard, automation, scheduler**: unaffected — ADR 0022 introduces no new consumer of any of these, consistent with ADR 0016's operational/presentation boundary and both prerequisite issues' non-goals.

## Summary dependency statement

Canonical Valuation (this ADR) is a genuine, hard prerequisite for Mispricing, and a soft/constraining prerequisite for Comparative Valuation and (conditionally) Asymmetry. It is **not** a prerequisite for Opportunity Assessment in any sense recognized by current governing ADRs — any future linkage requires independent authorization this ADR does not and cannot grant.
