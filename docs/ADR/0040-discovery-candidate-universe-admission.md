# ADR 0040: Governed Discovery Candidate Universe Admission

## Status

**Proposed — independent architecture audit and owner acceptance pending.** This document authorizes no implementation, activation, new ranking authority, or runtime wiring. Draft under Issue #666, based on merged ADPR-0013 v0.2 (PR #668, merge `da2ff77fa8ede25891231eeaaf92227ff2b25c6b`) and exact-HEAD independent re-audit recorded at https://github.com/fafa33/Project-Hunter/issues/666#issuecomment-6558455131. ADR number 0040 is provisional pending canonical index collision verification at acceptance.

## Context

Discovery Candidate Registry identifies, screens and queues emerging assets, but the canonical Market Validation runner evaluates a configured project universe rather than the Discovery queue. Direct queue-to-score wiring would bypass ADR 0003–0005, 0016 and 0020. The existing minimum of 50 configured projects must not be silently weakened. Admission to a universe is distinct from identity resolution and from the availability or sufficiency of production evidence; an included candidate may produce `INSUFFICIENT_EVIDENCE`. ADPR-0013 compared alternatives A–F, separated membership ownership from execution shape, and found D+E worth investigating, not pre-approved.

## Proposed Decision — Subject to Independent Review and Owner Acceptance

1. **Authority and ownership.** Candidate Registry and existing identity services retain identity, deduplication, lifecycle, screening and queue ownership. A narrowly scoped, governed **universe-membership decision at the existing Market Validation input/configuration boundary** (ADPR option D) is proposed; it may determine membership only. The existing ADR 0020 input-assembly authority alone decides production input acceptance/missingness, and the canonical Market Validation runner alone decides score, rank and champion. No independent admission service, second candidate lifecycle, scoring service, or orchestration system is introduced. If the existing boundary cannot enforce this separation, this ADR must be revised rather than silently extending ADR 0020 authority.
2. **Execution isolation.** New candidates are evaluated, if authorized, in a **separately versioned, bounded candidate universe run** (ADPR option E), never silently concatenated into the baseline production run. Baseline config/run identity and its ranks/champion remain unchanged. Candidate-run ranks and champion, if generated, are local to that run and must not be advertised as globally comparable or production-promoted. No candidate-run score is a calibrated 10x–1000x probability or buy recommendation.
3. **Membership contract.** An immutable, content-addressed membership manifest binds exact canonical economic entity and chain/contract representation, source Registry reference, policy/config version, deterministic candidate set and stable-key processing order, known/effective cutoff, source evidence references/digests, run identity, recorded/known timestamps, decision reason, and supersession lineage. A decision is a projection (`included`, `not_included`, `unavailable`), **not** a new Registry lifecycle. Identity `ambiguous`, `conflict`, `rejected`, `unresolved` or Registry `rejected/archived` cannot qualify. **`probable` is not eligible** unless a later accepted amendment defines positive proof and conflict resolution; no inferred equivalence.
4. **Strict-known replay and fail-closed behavior.** Do not infer historical Registry snapshots from current-state/upsert rows. Missing known-time or conflicting provenance produces `unavailable`/non-inclusion at that cutoff. Evidence arriving later cannot retroactively qualify a prior manifest. Deduplication is by canonical entity and representation under existing identity authority. Selection under capacity is stable-key deterministic, permutation-invariant, and explicitly defers unprocessed candidates; Discovery screening priority is never production admission rank.
5. **Input sufficiency and minimum universe.** Inclusion is **not** production evidence acceptance. ADR 0020 alone supplies missingness and `INSUFFICIENT_EVIDENCE` behavior; accepted ADR 0022/23/24/26/28 do not prove runtime activation, and ADR 0027 is Proposed as of drafting. **The existing 50-project minimum remains binding.** A candidate-only run that cannot satisfy this contract is **disabled**, not exempted; any different run-size semantics require a separately accepted contract amendment and proof. A mixed run is not an implicit fallback.
6. **Persistence and ownership.** Domain service authorizes the membership projection before persistence; repositories only store/retrieve immutable records per ADR 0009. No direct provider, CLI or repository authority to promote candidates. Exact schema, transaction/atomicity and storage adapter require focused contract verification before implementation approval; this proposal does not assume the Registry supports historical snapshots.
7. **Activation and rollback.** Default is the existing static universe plus Discovery watch queue (A/F fallback). No production execution, scheduling, automatic publication or activation until an implementation Issue, focused adversarial tests, exact-HEAD proof, independent review and owner-approved activation. Shadow-only evaluation precedes any separately authorized canary. Disable candidate run without mutating baseline or erasing receipts; fail closed on missing proofs.

## Acceptance Gates and Open Evidence

**Not yet proven; these are acceptance prerequisites, not claimed successes:** (a) exact current runtime status of ADR 0022–0028 input wiring and ADR 0027 disposition; (b) precise existing Market Validation input/configuration owner contract and its ability to host membership without accepting evidence or duplicating authority; (c) Registry identity/provenance and historical persistence semantics against ADR 0009; (d) candidate-run minimum-50 compatibility, bounded operational cost and deterministic run contract; (e) a real strict-known candidate/evidence example and explicit canary/rollback thresholds. If (b) or (d) fails, reconsider D/E under the original ADPR alternatives; do not approve this ADR on unsupported assumptions.

After acceptance, focused hostile regressions must cover ambiguous/probable and duplicate cross-chain identities, post-cutoff evidence, Registry lifecycle conflicts, missing valuation-family inputs, queue permutation, quota deferral, immutable replay, candidate-run baseline invariance, local-only rank/champion, 50-project minimum and rollback. These tests have **not** been run for this draft.

## Consequences

- Preserves Discovery watch coverage while preventing hypothesis-to-production promotion.
- Adds a bounded manifest and candidate-run execution concept, requiring separately governed implementation, persistence, operational controls and cost verification.
- Does not alter canonical proof outcome, baseline ranking, scoring, timing, evidence sufficiency, or Registry lifecycle authority.
- Retains possible loss of early candidate-run evaluation until minimum-universe and historical-evidence constraints are proven; this is preferable to silently bypassing contracts.

## Alternatives Considered

- **A — Static configured universe:** safest default and rollback; fails automated early admission.
- **B — Direct Discovery queue concatenation:** rejected for identity, strict-known replay and authority bypass.
- **C — New subordinate admission service:** viable only with stronger proof; creates unnecessary additional authority surface if D works.
- **D — Existing input/configuration boundary:** selected as proposed membership location, conditional on proving no input-acceptance contamination.
- **E — Separate versioned candidate run:** selected as proposed execution shape; avoids changing baseline, but has explicit 50-project and cost blockers.
- **F — Manual governed config amendment:** safe fallback if automation cost or contracts block D+E; manual latency remains.

Reopen this decision if canonical owner separation, minimum-50 compatibility, strict-known replay or run isolation cannot be proven, or if a later accepted ADR changes a relevant authority boundary. No merge or implementation is authorized by Proposed status.

## Traceability

- Issue: https://github.com/fafa33/Project-Hunter/issues/666
- Preparation: `docs/architecture-records/ADPR-0013-discovery-candidate-admission.md`
- Prior independent audit: https://github.com/fafa33/Project-Hunter/issues/666#issuecomment-6558172180
- Exact-HEAD delta audit: https://github.com/fafa33/Project-Hunter/issues/666#issuecomment-6558455131
- Binding ADRs: 0003–0005, 0009, 0016, 0020–0026, 0028; ADR 0027 Proposed at drafting.
