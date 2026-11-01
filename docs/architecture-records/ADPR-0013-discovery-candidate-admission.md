# ADPR-0013 — Discovery Candidate Admission to Canonical Market Validation

## Metadata
- ADPR ID: ADPR-0013 (proposed; verify allocation at review)
- Status: READY_FOR_REVIEW (not approved)
- Version: 0.2
- Author: Project Hunter architecture preparation for Issue #666
- Reviewers: Claude Code independent audit (v0.1 ADPR_REVISION_REQUIRED); targeted v0.2 re-audit READY_FOR_ADR_WITH_MINOR_FINDINGS at `25bdb15e21a773b19f183ccbcce631b04e603bc7` (before this editorial clarification)
- Created: 2026-11-01
- Related Issue: #666; parent #135 finding F-135-01
- Planned ADR: Discovery Candidate Admission Authority (not accepted)
- Original baseline main: `1353e868bc802a11daf83e438d8a32816b8bcc94`; v0.2 base: `394aee4f63fb6d78e9f33be1031ba27bc9a2effb`

## Executive Summary
**Version note:** Sections from v0.1 below are preserved as historical preparation only; wherever they recommend Option C, separate admission statuses, or NEEDS_REVISION, the v0.2 remediation and its revised assessment govern. No architecture option or ADR has been accepted.

The Discovery registry screens and queues candidates, but the canonical Market Validation CLI loads a fixed configured universe and its runner iterates that universe. The proposed solution is a **separate deterministic admission decision**, not Discovery-generated scoring, with explicit pending/unavailable states. No production mutation is authorized by this ADPR. The owner wants early 10x–1000x hypotheses preserved without converting them into calibrated probabilities or authoritative rankings.

## Problem Statement
**Current:** `src/hunter/discovery/engine.py::run_market_discovery` seeds configured projects, discovers providers, screens and refreshes the queue. `src/hunter/cli.py::_market_validation` loads `configs/market_validation.yaml`; `src/hunter/market_validation/runner.py::MarketValidationRunner.run` evaluates `config.project_universe`, not the discovery queue.

**Desired:** A discovered candidate remains visible in a durable, non-authoritative watch state while its identity and evidence are incomplete. Only an explicitly admitted candidate can enter the existing Market Validation production runtime.

**Decision:** Who authorizes candidate admission, on what evidence, at which cutoff, and how does that decision become a deterministic configured-universe input without creating another scoring/ranking authority?

**In scope:** Candidate admission semantics, immutable lineage, identity, provenance, cutoff, replay, missingness, bounded integration and rollback.

**Out of scope:** Opportunity score/probabilities, direct buy recommendations, new timing or ranking engine, valuation-family activation (#389/#386), provider automation, full repository redesign.

## Problem Validation and Evidence Inventory
| ID | Source | Finding | Confidence / limit |
|---|---|---|---|
| E1 | `docs/ADR/0016-runtime-analytical-authority.md` | Only EvidenceBackedProjectExecutor + Market Validation are canonical production analytical authority; promotion requires accepted ADR | Binding architecture |
| E2 | `docs/CANONICAL_RUNTIME_ARCHITECTURE.md` | Discovery and screening are not automatically production-promoted | Binding architecture |
| E3 | `src/hunter/discovery/engine.py` | `run_market_discovery` refreshes candidate queue | Targeted code trace, main SHA above |
| E4 | `src/hunter/cli.py`, `src/hunter/market_validation/runner.py` | Production CLI loads configured universe; runner iterates it | Targeted code trace |
| E5 | `src/hunter/market_validation/configuration.py` | At least 50 configured projects required | Targeted code trace |
| E6 | Issue #135 F-135-01 checkpoint | 42 focused tests passed and configured 50-project CLI completed on earlier exact HEAD | Does not prove new candidate admission or live liquidity |
| E7 | `docs/ARCHITECTURE_DECISION_PREPARATION_GUIDE.md`; `docs/ARCHITECTURE_AUDIT_PROTOCOL.md` | ADPR, independent audit and accepted ADR precede architectural mutation | Binding governance |

## Assumptions
| ID | Assumption | Falsification | Impact |
|---|---|---|---|
| A1 | Candidate registry can expose a stable canonical identity plus provenance at an effective cutoff | Ambiguous or mutable representation cannot be resolved deterministically | Admission must remain unavailable |
| A2 | Existing acquisition/evidence authorities can supply a qualifying candidate's inputs | No evidence bundle is available at cutoff | Candidate remains watch-only; no score |
| A3 | A narrow admission service can reuse existing approved runtime without changing score meaning | Existing contract requires altering scoring semantics | Reopen decision; no silent change |

## Constraints and Architectural Dimensions
- **Constitution/governance:** Existing Issue → ADPR → independent audit → ADR → governed implementation; no parallel orchestrator, no automatic merge.
- **Authority:** Discovery owns discovery/lifecycle, not `hunter_score`, canonical Timing, ranking or committee conclusions. Market Validation retains those existing authorities under ADR 0007/0016.
- **Identity:** Canonical candidate, economic entity, chain/contract representation and context under ADR 0003–0005; ambiguous and duplicate representations fail closed.
- **Evidence:** Persisted approved sources only, provenance and conflicts explicit under ADR 0002; absence is UNKNOWN, not negative or positive evidence.
- **Time:** Distinguish observed/effective, recorded and known times; strict-known cutoff and replay; no future leakage.
- **Persistence:** Provider → Service → Repository → Persistence; append-only decision and supersession, no mutable in-place promotion.
- **Compatibility:** Preserve configured 50-project runs; no implicit expansion, hidden defaults, changed weights, ranking, timing, or committee behavior.
- **Security:** No provider-supplied candidate identifiers treated as trusted commands; dedup and bounded input; fail-closed malformed evidence.
- **Performance:** Bounded admission batch; avoid CI/rerun loops and scanning the full repository.
- **Consumers:** Watch queue may show PENDING/INSUFFICIENT/REJECTED; only admitted candidates can be passed to canonical production validation. Watch priority is not a production rank.

## Candidate Options
### A — Keep static configured universe
- Authority: unchanged Market Validation config; Discovery remains independent.
- Persistence/replay: current paths only; no new admission record.
- Evidence/provenance: current configured inputs.
- Advantages: no architecture migration, lowest immediate risk.
- Disadvantages: newly discovered opportunities are not admitted automatically.
- Failure mode: late detection and manual lag.
- Reversibility: trivial; no change.
- Dependencies: none.

### B — Directly concatenate Discovery queue into Market Validation
- Authority: Discovery queue effectively selects production input without accepted admission.
- Persistence/replay: queue lifecycle may not represent a strict-known immutable decision.
- Evidence/provenance: screening alone is not sufficient proof of validated evidence.
- Advantages: quick wiring, broad coverage.
- Disadvantages: identity ambiguity, stale/future evidence, authority bypass, accidental promotion.
- Failure mode: invalid or duplicated assets receive canonical ranking.
- Reversibility: difficult after published results.
- Dependencies: violates ADR 0016 without amendment.

### C — Service-owned deterministic admission decision (recommended for independent review)
- Authority: a single explicitly designated admission service, **separate from and subordinate to** canonical Market Validation scoring; exact service owner and approval contract must be fixed by ADR.
- Boundaries: Discovery supplies canonical candidate references; identity/evidence authorities validate eligibility; admission outputs only ADMITTED, PENDING, INSUFFICIENT, REJECTED, or UNAVAILABLE, never score or recommendation.
- Persistence/replay: append-only versioned decision receipt binding candidate representation, cutoff, evidence IDs/digests, policy version and supersession lineage; no retrospective future evidence.
- Evidence/provenance: approved persisted sources, conflicts and missingness explicit.
- Compatibility: default remains configured universe; any candidate expansion is explicit, versioned and auditable, preserving the existing scoring path and minimum 50-project rule.
- Advantages: early watch coverage plus safe eventual production evaluation.
- Disadvantages: requires ADR, independent audit, evidence contracts, tests and activation gates.
- Failure modes: stale/ambiguous identity, silent double admission, eligibility drift, overloading configured universe.
- Migration: shadow read-only eligibility assessment first; canary with exact receipts; no production writes until approved.
- Reversibility: disable new admission path; retain append-only audit trail.
- Open dependencies: proof of source authority, exact owner, evidence availability, replay and bounded scale.

## Comparative Analysis
| Criterion | A static | B direct queue | C controlled |
|---|---|---|---|
| Existing authority compliance | Yes | No | Conditional on accepted ADR |
| Early candidate retention | Yes in Discovery only | Yes | Yes |
| Production admission | Manual/static | Unsafe | Evidence-gated |
| Identity and replay | Existing config | Unproven | Explicitly required |
| Evidence integrity | Existing | Unproven | Fail closed |
| Implementation effort | None | Low | Moderate |
| Reversibility | High | Low | High with shadow/canary |
| Operational complexity | Low | Low initially, high incident risk | Moderate, bounded |
| Decision quality | Safe but incomplete | Unacceptable | Promising, not yet validated |

## Falsification and Rejected Options
- **A:** Does not meet automatic admission goal; retain as safe rollback/default.
- **B (rejected):** A queued item with ambiguous contract identity or only a volume spike can reach authoritative ranking; violates E1/E2.
- **C:** Reject if one entity maps to conflicting contract representations, evidence is known only after cutoff, duplicate candidates alter results, missing sources become inferred scores, or output differs by queue permutation. A failed case must produce explicit non-admission and preserve baseline outputs. These are proposed hostile tests, **not tests already executed**.

## Risks
| Risk | Likelihood | Impact | Mitigation | Residual |
|---|---|---|---|---|
| False candidate promotion | Medium | High | Identity/evidence gate, shadow canary | Real provider edge cases |
| Replay leakage | Medium | High | Strict-known cutoff, immutable receipts | Source historical completeness |
| Duplicate representation | Medium | High | Canonical economic entity + contract scope | Cross-chain mapping ambiguity |
| Candidate starvation | Medium | Medium | Durable watch state and recheck triggers | No guaranteed fresh source |
| Performance / universe growth | Medium | Medium | Bounded batches, admission quotas | Production capacity unknown |

## Open Questions
| Question | Blocking? | Owner | Required evidence |
|---|---|---|---|
| Which existing service is authorized to own admission? | Yes | Architecture decision | ADR 0002–0005/0007/0016 contract mapping |
| What persisted evidence proves a newly discovered asset is evaluable? | Yes | Evidence authority | One real strict-known non-synthetic example |
| What exact versioned admission receipt and supersession contract? | Yes | ADR author | Contract + hostile replay tests |
| How are queue updates rechecked without bypassing admission? | Yes | ADR author | Deterministic trigger/operational plan |
| What are acceptable canary and rollback thresholds? | Yes | Owner/architecture review | Bounded activation plan |

## Constitution and Governance Review
No new scoring, recommendation, timing, or proof authority is proposed. Option C must be assessed against Project Constitution, ADR 0002–0009, ADR 0016 and Development Governance in independent audit. This preliminary check is not an independent audit verdict.

## Quality Assessment
Problem and options: documented. Authority, provenance, replay, rollback: proposed but not yet empirically demonstrated. Independent audit, qualifying real evidence, exact service-owner contract, historical cutoff and canary proof: outstanding. No invented quality score.

## Architecture Readiness
- Outcome: NEEDS_REVISION pending source-owner mapping and independent audit.
- Missing evidence: real candidate/evidence admission example, service ownership proof, replay and operational constraints.
- Unresolved conflicts: no accepted ADR currently authorizes new production admission.

## ADR Readiness
- Outcome: NEEDS_REVISION; **NOT approved for ADR acceptance or implementation**.
- Proposed title: Canonical Discovery Candidate Admission Authority.
- ADR must fix: single admission owner, canonical identity, evidence sufficiency, immutable decision, cutoff/replay, correction, activation/canary/rollback, bounded consumers and regression criteria.
- ADR must leave open: 10x–1000x calibrated probabilities, Opportunity ranking, valuation-family composition, provider automation.

## Final Recommendation
Advance option C through independent architecture audit, resolve material findings, and obtain explicit owner approval of the eventual ADR. Keep production static until accepted. Preserve early-stage candidates in the existing Discovery queue without implying that watch status is a buy signal.

## Decision History
| Date | State | Change | Author |
|---|---|---|---|
| 2026-11-01 | READY_FOR_REVIEW draft | First evidence-bound ADPR candidate for #666 | Project Hunter preparation |

## Traceability
- Issue: https://github.com/fafa33/Project-Hunter/issues/666
- Parent: https://github.com/fafa33/Project-Hunter/issues/135
- ADPR: this file, pending review
- Independent audit: pending
- ADR: not created or accepted
- Implementation PR: none
- Merge commit: none

## Immutability and Supersession
Once approved, substantive changes require a superseding ADPR; do not silently rewrite accepted reasoning.

## Independent Audit Remediation — Version 0.2 (F-001–F-006)

This section supersedes the conflicting preliminary recommendation, assumption A2, option C ownership wording, comparison and readiness assertions above. It is a **preparation amendment**, not an accepted ADR. Source audit: https://github.com/fafa33/Project-Hunter/issues/666#issuecomment-6558031606. Audited v0.1 at merge `394aee4f63fb6d78e9f33be1031ba27bc9a2effb`, blob `863576704a19565a54e8d4228fed964a6c315c66`. Scope: targeted F-001–F-006 only.

### F-001 — Canonical authority and input-availability map

| Authority | Existing contract / implication for admission |
|---|---|
| ADR 0001 | Discovery-first retention and compatible configured seed are separate from production acceptance |
| ADR 0003 | Candidate Registry owns candidate lifecycle, screening and queue; Market Validation must not re-own identity or lifecycle |
| ADR 0004 / 0005 | Identity resolution and economic entity / chain representation govern deduplication; ambiguous or conflicting identities cannot silently qualify |
| ADR 0007 / 0016 | Existing canonical Market Validation owns scoring, ranking, timing and committee decisions within a single run/configuration/effective boundary |
| ADR 0009 | Existing persistence authority governs durable records; proposed receipts must fit its contract, not invent an independent store |
| ADR 0020 | **Market Validation input assembly alone** accepts or marks missing production inputs; admission must never assert evidence sufficiency for scoring. Four inputs (valuation, comparative_valuation, mispricing, asymmetry) are currently unavailable without authorized formulas; missing required inputs produce `INSUFFICIENT_EVIDENCE`. Strict-known-time applies |
| ADR 0021 / 0025 | Valuation evidence and assembly authorities constrain source acceptance; candidate admission cannot invent or substitute unavailable valuation evidence |
| ADR 0027 | Composition was **Proposed at audit time**; it is not accepted authority merely because a document exists. Verify current status at ADR drafting and preserve disabled-by-default behavior until authorized |
| ADR 0028 | **Accepted, Revision 9** (architectural authority only; implementation/activation separately governed). Supporting evidence-assembly boundaries remain subordinate to canonical production input acceptance; verify runtime implementation and applicability before ADR acceptance |
| ADR 0022 / 0023 / 0024 / 0026 | **Accepted architectural decisions** for valuation methodology, supply-basis tolerance, valuation scalar semantics and comparative methodology. Acceptance does not prove those inputs are wired into production Market Validation. ADR 0027 remains **Proposed**; verify runtime availability independently rather than treating the historical ADR 0020 unavailable-input statement as a fresh runtime measurement |

**Corrected A2:** There is no demonstrated currently available full qualifying production evidence bundle for a newly discovered candidate. Discovery identity eligibility, universe inclusion eligibility and production input sufficiency are three distinct decisions. An eligible candidate can be included in a bounded validation run yet legitimately yield `INSUFFICIENT_EVIDENCE`. Neither `ADMITTED` nor a watch state promises an authoritative score, rank, champion or investment recommendation. The admission decision may determine **universe membership only**, never whether a production engine input is accepted. No additional admission service may duplicate ADR 0020 input assembly.

### F-002 — Candidate ownership and state mapping

| Existing owner | Owns | Must not own |
|---|---|---|
| Discovery Candidate Registry / identity authorities | Candidate record, identity outcomes (`exact/probable/ambiguous/conflict/rejected/unresolved`), lifecycle (`discovered/identified/evidence_pending/screenable/analyzable/ranked/deep_research/rejected/archived`), queue, recheck | Production evidence acceptance or canonical scoring |
| Governed universe/configuration boundary (owner placement **to be decided** by ADR) | Explicit immutable membership manifest, source reference, policy/cutoff, run identity | New candidate lifecycle, identity adjudication or score |
| Market Validation input assembly (ADR 0020) | Input availability, sufficiency and missingness at strict-known cutoff | Candidate creation/merge or Registry lifecycle mutation |
| Market Validation runner | Existing rank, sector rank, champion, committee outcomes within run | Admission eligibility, candidate discovery or shadow ranking authority |

**Do not introduce a second admission-state machine.** Preliminary `ADMITTED/PENDING/INSUFFICIENT/REJECTED/UNAVAILABLE` are withdrawn as independent persisted lifecycle statuses. Proposed universe membership is an **immutable decision/result projection**, not a Registry lifecycle transition: `included` with manifest reference, `not_included` with reason, or `unavailable` due to evidence/identity failure; it cannot change Registry `rejected` or `analyzable`. A Registry `rejected/archived` or identity `ambiguous/conflict/rejected/unresolved` must not become `included` by inference. `probable` identity needs explicit ADR policy; do not assume acceptance. Runner `INSUFFICIENT_EVIDENCE` remains a **production evaluation result**, not an admission state. Proposed receipt uniqueness: canonical entity + representation + manifest policy version + strict-known cutoff, with deduplication and supersession, subject to ADR acceptance.

### F-003 — Normalized alternatives and independent dimensions

**Dimension 1: membership owner.** Registry remains discovery/identity authority. Candidate-universe membership could be (C) an additional subordinate admission service, (D) governed inside the existing Market Validation input-assembly boundary without transferring input authority, or (F) manual versioned config amendment. D still must not turn input assembly into candidate creation/identity adjudication. The exact owner requires explicit ADR decision.

**Dimension 2: execution shape.** (M) merge admitted candidates into the baseline run; (E) run a segregated, versioned candidate universe, without merging candidate ranks/champion into baseline. These are independent of membership owner and must not be hidden in option C.

| Option | Ownership / promotion | Ranking and champion effect | Replay and evidence | Cost / risk / rollback | Viability |
|---|---|---|---|---|---|
| A — Static only | Existing configured universe | Baseline unchanged | Existing config contract | Lowest cost; misses automated admission; immediate rollback | Viable safe default |
| B — Queue concatenation | Discovery implicitly selects production | Joint rank/champion may change without authority | Unproven identity, known-time, input acceptance | Low initial effort, high correctness risk; hard to undo published outcomes | Rejected |
| C — Subordinate service membership | New service owns only immutable universe membership; Registry and ADR 0020 unchanged | **If merged**, rank, sector rank and champion can change; must not claim baseline preserved | Versioned manifest, strict-known snapshot, evidence digests | New service/authority surface and failure modes; shadow-only until ADR | Conditional; higher complexity |
| D — Input-boundary membership | Existing Market Validation input-assembly boundary governs inclusion, not discovery identity | **If merged**, same rank/champion displacement as C | Reuse strict-known input assembly, explicit separate membership receipt | Smaller new authority surface; risk mixing inclusion with input sufficiency | Conditional; test ownership separation |
| E — Segregated versioned run | C or D membership, separate candidate universe run | Baseline rank/champion **unchanged by construction**; candidate run ranks are local and must not be compared as global | Distinct run ID, config digest, manifest version, cutoff, no cross-run champion promotion | Additional bounded run and operating cost; disable candidate run to rollback | Conditional; preferred execution shape for evaluation, not yet approved |
| F — Watch + governed manual config | Registry watch retained, authorized operator versions config | **If merged**, baseline run ranks/champion change explicitly; original run reproducible by original config | Versioned config and audit trail; no new admission service | Low engineering risk; manual latency and missed opportunities | Viable fallback; manual governance cost |

**Corrected baseline statement:** Baseline is preserved only when the exact original universe/configuration/run is unchanged, or when a separately versioned candidate run is executed. In-run additions necessarily can change overall and sector ranks and champion because `_rank` sorts the complete `project_universe`. No cross-run rank or champion comparison is valid without a separately accepted comparison contract. A new universe manifest must bind run ID, canonical identities, exact member set, policy/config versions, effective/known cutoff and source digests. The existing minimum-50 configured universe is not silently reduced or replaced; a candidate-only run requires explicit ADR decision on whether/how the current configuration minimum applies. Do not invent an exemption.

### F-004 — Bounded selection without a second rank

No selection by Discovery priority or screening score for production inclusion. Candidate queue priority is score-derived and cannot act as an authoritative admission order. Any bounded processing limit must use a stable canonical key and documented policy/cutoff, with pagination or deterministic deferral, and prove permutation invariance; processing order is not opportunity rank. If capacity cannot process all eligible candidates at the same cutoff, defer explicitly and do not pretend exhaustive discovery or evaluation. Candidate-only shadow outputs cannot become production rankings by relabeling.

### F-005 — Known-time snapshots and Registry mutability

The audit observed `first_seen_at`, `last_seen_at` and queue `updated_at`, suggesting current-state/upsert semantics; repository persistence semantics were **not** fully traced. Do not assume the Registry itself provides historical known-time replay. Any membership manifest must snapshot exact identity representation, source evidence references/digests, observed/effective/recorded/known times, cutoff, policy version, immutable content digest and supersession lineage. Missing known-time evidence => no historical inclusion, not inferred retroactive eligibility. Validate existing repository storage contract before specifying persistence.

### F-006 — Reconsideration conditions and falsification

- **A** remains default and fallback; reconsider when verified newly discovered candidates are persistently excluded despite accepted identity and usable evidence.
- **B** is rejected under ADR 0003/0016/0020; reconsider only if an accepted ADR explicitly changes authority and replay contracts (not merely for implementation convenience).
- **C** remains conditional; reject if the service duplicates Registry lifecycle or ADR 0020 evidence acceptance, or creates unbounded state/operational cost.
- **D** remains conditional; reject if inclusion logic contaminates production input sufficiency or exceeds the existing input-assembly owner contract.
- **E** remains conditional; reject if cross-run ranks are treated as equivalent, if 50-project compatibility is bypassed, or if run lineage is not exact and replayable.
- **F** remains a viable manual fallback; reconsider as primary if automation cost/risk exceeds verified incremental discovery benefit.

Hostile tests required *after* an ADR, not claimed executed: duplicate entity across chains; ambiguous/probable identity; post-cutoff evidence; queue permutation; Registry status vs membership projection; missing four ADR 0020 inputs; same-run rank/champion displacement; separated-run baseline invariance; bounded quota deferral; failure rollback; historical immutable replay. A successful candidate inclusion is **not** proof of evaluable scores or 10x–1000x calibrated odds.

### Revised assessment and decision readiness

- **Architecture readiness:** `READY_FOR_REVIEW` of this revised preparation, **not independently verified**.
- **ADR readiness:** `NEEDS_REVISION` until targeted independent re-audit resolves F-001–F-003 and identifies whether remaining owner, membership, and run-shape choices are sufficiently evidenced for decision. No accepted ADR or implementation authorization.
- **Comparative direction for audit:** Prefer investigating **D + E** (existing input-boundary ownership plus segregated versioned candidate run) before adding a new service C; this is a preparation hypothesis, not an architectural decision. Retain A/F as fallback and reject B under existing authorities. D+E is invalid if ownership separation or minimum-50 contract cannot be satisfied without accepted change.
- **Missing proof:** real strict-known candidate identity/evidence example; existing persistence and run-contract confirmation; ADR 0027 current acceptance status and ADR 0022–0028 actual runtime wiring (ADR 0028 is Accepted, Rev 9); operational cost and canary thresholds. These remain explicitly open and must not be invented.
- **Re-audit scope:** Only F-001–F-006 and any directly consequential new contradiction; follow `docs/ARCHITECTURE_AUDIT_PROTOCOL.md` materiality rules, no full-repository rediscovery.

### Revision history

| Date | Version | Action |
|---|---|---|
| 2026-11-01 | 0.2 | Addressed independent audit F-001–F-006 in bounded preparation amendment; independent re-audit pending |

### Targeted re-audit and editorial clarification

- Targeted re-audit of v0.2 at `25bdb15e21a773b19f183ccbcce631b04e603bc7`: `READY_FOR_ADR_WITH_MINOR_FINDINGS`, F-001–F-006 resolved. Evidence: https://github.com/fafa33/Project-Hunter/issues/666#issuecomment-6558172180.
- This subsequent change clarifies **N-001** (accepted ADR 0022/23/24/26/28 versus proposed ADR 0027, without claiming runtime activation) and **N-002** (v0.1 sections explicitly historical/superseded). No option is selected and no ADR is accepted. Because the HEAD changed, the prior verdict is bound to the earlier HEAD; this editorial change requires an exact-HEAD delta check before relying on it for merge.
