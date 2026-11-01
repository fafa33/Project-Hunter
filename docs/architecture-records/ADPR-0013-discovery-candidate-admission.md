# ADPR-0013 — Discovery Candidate Admission to Canonical Market Validation

## Metadata
- ADPR ID: ADPR-0013 (proposed; verify allocation at review)
- Status: READY_FOR_REVIEW (not approved)
- Version: 0.1
- Author: Project Hunter architecture preparation for Issue #666
- Reviewers: independent architecture audit pending
- Created: 2026-11-01
- Related Issue: #666; parent #135 finding F-135-01
- Planned ADR: Discovery Candidate Admission Authority (not accepted)
- Baseline main: `1353e868bc802a11daf83e438d8a32816b8bcc94`

## Executive Summary
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
