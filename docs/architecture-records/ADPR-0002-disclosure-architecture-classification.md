# ADPR-0002 — Disclosure Architecture Classification

## Metadata

- ADPR ID: `ADPR-0002`
- Status: `IN_RESEARCH`
- Version: 1.2 (revised again in response to a second, fresh independent architecture audit of Version 1.1; see Revision Note (Version 1.2) below)
- Author: AI preparation session (Claude Code), on behalf of repository investigation into Issue #107's operational blocker
- Reviewers: not yet assigned
- Created: 2026-07-28
- Approved: not yet approved
- Related Epic: not yet created
- Related Issue: not yet created (candidate follow-on to Issue #107; this ADPR is not itself a request to reopen or modify Issue #107)
- Planned or produced ADR: none produced by this record; option enumeration only, by explicit task scope
- Supersedes: not applicable
- Superseded by: not applicable

## Revision Note (Version 1.1)

An independent architecture audit of Version 1.0 (conducted after this record's initial merge) concluded `REQUIRES ADPR REVISION` and identified: four genuinely missing architectural options; two options presented as independent peers despite being composites of other options; one option mislabeled under the wrong decision axis; several hidden assumptions not surfaced; two dead-end risks present in the option analysis but not named as such; taxonomy gaps around the hypothetical qualifying shape and mixed/partial-attribution edge cases; and a self-assessed Quality Assessment that did not fully hold up under independent re-derivation. Version 1.1 corrected exactly those findings.

## Revision Note (Version 1.2)

A second, fresh, independent architecture audit — explicitly instructed to ignore prior conclusions and re-derive everything from current repository state — was performed against Version 1.1 and concluded `REQUIRES ADPR REVISION` again, on different grounds than the first audit: (1) no option had a distinctly labeled "Implementation" analysis field, despite this being one of eight required per-option dimensions; (2) a systematic, self-contradicting misattribution of ADR 0022's "exhaustive Valuation Inputs list" concept to ADR 0021 throughout the Constraints, Evidence Inventory, Comparative Analysis, Risks, Open Questions, and ADR Readiness sections — independently verified by direct text search: ADR 0021 contains no "Valuation Inputs" section and no "exhaustive" language anywhere; that section and language exist only in ADR 0022; (3) the Interaction Diagram never explicitly named Market Validation (ADR 0016) as a boundary, even though it is the eventual consumer of `ValuationAssessmentRecord`; (4) whether hierarchical/multi-level classification is a genuinely independent option was left implicit rather than explicitly evaluated. Version 1.2 corrects exactly these four findings: adds an explicit "Implementation Impact" field to every one of the (now 22) options; corrects every misattributed ADR 0021/0022 reference; adds Market Validation to the Interaction Diagram as an explicit, unchanged downstream boundary; and adds hierarchical/multi-level classification as new Option 3H, since independent evaluation found it is not fully representable by the existing granularity or multiplicity options. It does not change ADPR-0002's scope, convert it into an ADR, select or rank any option, or make any architectural decision beyond what these four corrections require. Since this record was never `APPROVED`, this revision amends it directly rather than creating a new superseding ADPR, per `docs/architecture-records/README.md`'s immutability rules (which apply only after approval).

## Executive Summary

Issue #107 (Canonical Valuation Methodology, ADR 0022) is software-complete for Milestones 1–4. Its sole remaining blocker is that the one entity currently evidenced in the repository (Sky) discloses its value-capture flow across a shape that does not satisfy ADR 0022's exact-365-day, single-record, amount-plus-rate requirement. A prior, independently-conducted 13-protocol survey within this same investigation (Sky plus 12 other real protocols spanning lending, DEX, derivatives, liquid staking, restaking, native asset, oracle, DePIN, interoperability, yield, and stablecoin/application-business categories) found the identical structural gap in every case: real disclosures separate amount, period, and rate across independently-timed channels rather than binding them into one immutable record. Hunter currently has no architectural capability to classify *how* a source structures its disclosure before attempting acquisition or methodology selection — it can only discover this by attempting the full valuation pipeline and observing failure, which conflates unsupported disclosure architecture with missing evidence, provider failure, and parser limitation.

This ADPR explores, without selecting, the option space for a new "Disclosure Architecture Classification" capability whose sole purpose is to classify disclosure *structure* — never to acquire evidence, combine evidence, normalize data, calculate valuation, or change ADR 0022. It presents four decision axes (classification timing, record first-class status, classification granularity level, and multiplicity handling) — Decision Axis 4 further decomposed, per the revision audit, into two nested sub-questions rather than four flat peers — a taxonomy of disclosure architectures actually observed in evidence gathered during this investigation plus an explicitly-hypothetical qualifying reference shape, and an analysis of interaction with Discovery, Market Facts, Value Capture, and Valuation without altering any existing authority boundary. Version 1.1 adds five options the initial version's search missed (a Discovery-integrated timing option, a non-canonical operational/diagnostic persistence option, an evidence-type-level granularity option, an economic-claim-level granularity option relocated from a mislabeled multiplicity option, and a temporal/versioned multiplicity option distinct from simultaneous multiplicity), for 21 options total across the four axes; Version 1.2 adds one further option (hierarchical/multi-level classification, Option 3H), per its own Revision Note above, for 22 options total.

Per explicit task scope, this record makes **no recommendation, ranking, or selection**. Readiness outcome: Architecture Readiness is `READY` (the problem, evidence, dimensions, and option set are complete); ADR Readiness is `BLOCKED` — not because evidence is insufficient, but because no option has been selected by design, and ADR creation requires a selected option this record deliberately does not provide. An independent audit of Version 1.0 concluded `REQUIRES ADPR REVISION`, identifying option-completeness and internal-consistency gaps; this is Version 1.1, correcting exactly those findings without changing this record's scope, adding an ADR, or selecting an option.

## Problem Statement

### Current condition

Hunter's valuation pipeline (`hunter.value_capture` → `CanonicalValuationMethodologyAuthority` → `CanonicalValuationService`) assumes, without a preceding classification step, that a qualifying entity's evidence will arrive as one `FundamentalEvidenceRecord` whose own `accounting_period_start`/`accounting_period_end` exactly equal the methodology's fixed 365-day horizon, paired with a `ValueCaptureRuleSnapshot` carrying a populated `rate_or_proportion`. When a real source's disclosure does not take this shape, the only observable outcome is `CanonicalValuationAuthorityError` raised deep inside `CanonicalValuationService.estimate_fair_value` — after acquisition has already been attempted, after any provider/network issues have already been ruled in or out, and after a human operator has already hand-authored a manifest. There is no earlier point at which Hunter can say "this source's disclosure architecture is unsupported" as distinct from "this specific acquisition attempt failed" or "this specific evidence record happens to be incomplete this cycle."

### Desired condition

A capability exists (in some as-yet-unselected form, per this record's scope) that can classify a source's disclosure *structure* — independent of its content, independent of which specific entity it describes, and independent of any valuation methodology — early enough to distinguish, before or without needing a full acquisition-and-valuation attempt, among: unsupported disclosure architecture, incomplete disclosure, unavailable (unreachable) disclosure, and conflicting disclosure.

### Decision required

None, by this record's explicit scope. This ADPR enumerates the architecturally distinct ways such a capability could be designed, evaluated against consistent criteria, without choosing among them. A future decision session must select among the enumerated options (or a composite of them) before an ADR can be written.

### In scope

- Enumeration of disclosure architecture types observed in real evidence gathered during this investigation.
- Enumeration of options for: classification timing relative to acquisition; whether classification becomes a first-class persisted record; the level (entity/representation/provider/source/evidence-chain) at which classification applies; and how multiplicity (one subject exhibiting more than one disclosure architecture) is handled.
- Analysis of how such a capability would interact with Discovery, Market Facts, Value Capture, and Valuation without changing any existing authority boundary.
- Risk, unknown, and prerequisite identification for whichever option a future decision selects.

### Out of scope

- Selecting, ranking, or recommending an option.
- Any change to ADR 0022, ADR 0021, ADR 0020, ADR 0024, or any other accepted ADR.
- Any change to Issue #107 or its Definition of Done.
- Evidence fusion, combination, scoring, probability, heuristics, or machine learning of any kind.
- New provider classes or acquisition architecture.
- Any production code change. No `src/` file is modified by this record.
- Writing the ADR itself.

## Problem Validation

The problem is real and not already resolved by an accepted canonical document, for the following reasons, each checked against existing canonical authority:

- ADR 0021's five-layer evidence boundary (observed facts, fundamental evidence, fair-value estimates, comparative analysis, scenario evidence) defines *what* evidence means once it exists, not *how* a source structures its production of that evidence over time. No existing layer classifies disclosure structure.
- ADR 0022's Scope section (entity-class criteria) and Valuation Inputs section (exhaustive input-family list) define eligibility criteria applied to already-acquired evidence. They presuppose a compatible disclosure exists; they do not classify whether one does before acquisition is attempted.
- ADR 0009 (Provider → Service → Repository → Persistence) defines authority separation for already-defined record types. It does not itself create or preclude a new classification authority; a new authority would still need to respect it (addressed under Constraints and Architectural Dimensions below).
- ADR 0004 (Trust Layer Before Intelligence) establishes the closest existing precedent — classification-before-intelligence for identity/trust — but is explicitly scoped to identity resolution and source reliability, not to disclosure *structure*.
- No existing implementation (`hunter.value_capture`, `hunter.market_facts`, `hunter.valuation`, `hunter.valuation_methodology`, `hunter.valuation_authority`) contains any code path that inspects or classifies disclosure structure independent of attempting acquisition/valuation.

Canonical sources checked: `docs/ADR/0004-trust-layer.md`, `docs/ADR/0005-entity-model.md`, `docs/ADR/0009-repository-purification.md`, `docs/ADR/0020-canonical-market-validation-input-authority.md`, `docs/ADR/0021-canonical-valuation-evidence-authority.md`, `docs/ADR/0022-canonical-valuation-methodology.md`, `docs/ADR/0024-valuation-scalar-semantics-boundary.md`, `configs/value_capture_sources.yaml`, `configs/market_fact_sources.yaml`, `src/hunter/value_capture/*`, `src/hunter/valuation/service.py`, `src/hunter/valuation_methodology/*`, `src/hunter/valuation_authority/command.py`, GitHub Issue #107.

## Motivation

*(Answers Q9 of the governing task: would this reduce future false implementation work, reduce architecture dead ends, and improve deterministic reasoning?)*

The evidence gathered during this same investigation supports yes on all three, with a specific, demonstrated example rather than a speculative claim: determining that Sky's real disclosure could not satisfy ADR 0022 required a full cycle of entity registration, evidence acquisition, methodology implementation, and service-level rejection before the gap was visible as a *structural* one rather than an operational one (network reachability, provider failure, or a transient missing-evidence state were each live hypotheses at different points and had to be individually ruled out — see PR #130's diagnostic workflow and Phase 1–2 of the CVEA epic conducted earlier in this investigation). A classification capability that could answer "does this source's disclosure architecture even admit the possibility of ADR 0022 compatibility" before committing to a full acquisition-and-valuation cycle would have shortened that determination materially, and would generalize to every future entity Hunter attempts to register, not just Sky. Absent this capability, each future candidate entity risks repeating the same multi-stage discovery-by-failure process this investigation had to perform manually.

**Revision note (audit finding):** this claim is option-dependent, not a property of "this capability" generally. Only post-acquisition-capable variants (Option 1B, the post-acquisition half of 1C, evidence-chain-level granularity 3E) could have delivered the Sky-specific benefit described above — Option 1A's own Disadvantages field states pre-acquisition classification "cannot detect the specific content-level mismatch this investigation actually found for Sky." Pre-acquisition-only variants would instead have delivered a narrower, earlier-but-coarser benefit (ruling out sources whose *registered* capability set structurally cannot satisfy ADR 0022, such as today's `verified-onchain-value-capture`, before any acquisition attempt). Both benefits are real; neither is delivered by every enumerated option, and this record does not select which one (or both) a future decision should prioritize.

## Existing Architecture

Relevant current authority, ownership, and boundaries, as verified directly against `main`:

- **Discovery** (ADR 0001) identifies candidate entities/protocols. It does not evaluate evidence or disclosure structure.
- **Trust/Identity Resolution** (ADR 0004, ADR 0005) resolves entity/representation identity and source reliability before intelligence. It does not classify disclosure structure.
- **`hunter.market_facts`** (ADR 0021 layer 1): `CoinGeckoObservedMarketFactProvider` is a real, automated, network-calling provider; `ObservedMarketFactService` owns validation and persistence; `ObservedMarketFactRepository` is mechanical.
- **`hunter.value_capture`** (ADR 0021 layer 2): `RegisteredValueCaptureProvider` accepts a `payload` **verbatim from an operator-authored manifest** for both `official_disclosure` and `onchain_observation` source types — there is no automated fetch/parse implementation for either; `SupplyAndValueCaptureService` owns validation, conflict detection, and persistence authorization; `SupplyAndValueCaptureRepository` is mechanical, per ADR 0009.
- **`hunter.valuation_methodology`** (`CanonicalValuationMethodologyAuthority`) is the sole write authority for `ValuationMethodologySnapshot`, fixed to `discounted-value-capture-flow-v1` and a 365-day horizon per ADR 0022.
- **`hunter.valuation`** (`CanonicalValuationService`) is the sole write authority for `FairValueEstimateRecord`/`ValuationAssessmentRecord`, and is where the current, only rejection point for disclosure-shape mismatch lives (`src/hunter/valuation/service.py`, the exact-equality accounting-period check).
- **`hunter.valuation_authority`** (production CLI, added for Issue #107 Requirement 9) orchestrates the above two authorities from an operator-supplied manifest; it performs no classification of its own.
- **Registered sources today** (`configs/value_capture_sources.yaml`): `sky-protocol-tokenomics-disclosure` (`official_disclosure`, enabled, capabilities include `rule:buyback_and_burn`); `verified-onchain-value-capture` (`onchain_observation`, enabled, capabilities limited to `evidence:onchain_observation` and three `supply:*` capabilities — no `rule:*` capability); `official-tokenomics-disclosure` (`official_disclosure`, **disabled**, template/placeholder).

No existing component owns, or could without modification be repurposed to own, disclosure-structure classification without either (a) blurring into `CanonicalValuationService`'s own authority (violating ADR 0009's single-clear-owner principle) or (b) blurring into Discovery/Trust's identity/reliability scope (a different axis entirely, established in this investigation's own earlier architecture review).

## Constraints

### Constitutional

Per `docs/PROJECT_CONSTITUTION.md`: Rule 2 (Evidence Authority — "unknown information remains unknown... missing information remains missing... conflicting information remains conflicting until resolved") directly governs this capability's core purpose: a classification must never convert an unclassifiable source into a false positive or false negative. Rule 3 (Deterministic Intelligence) requires that classification, wherever implemented, be reproducible from the same evidence.

### Governance and accepted ADRs

- ADR 0009: any classification authority must be service-owned, with providers observing and repositories persisting mechanically only; must not be folded into `CanonicalValuationService`.
- ADR 0020: if any classification record is ever consumed as a canonical input anywhere, it must be strict-known, immutable, versioned, and correction-lineage-bearing like every other canonical record — no exception for being "just a classification."
- ADR 0022: the Valuation Inputs list is declared exhaustive (`docs/ADR/0022-canonical-valuation-methodology.md`, "Valuation inputs" section: "This list is exhaustive, not illustrative"). A classification record must not be treated as an implicit new valuation input without a future, separate, explicit amendment. **Correction (Version 1.2, per second audit finding):** Version 1.1 attributed this specific section and language to ADR 0021; independently re-verified by direct text search, ADR 0021 contains no "Valuation Inputs" section and no "exhaustive" language anywhere — that section and language exist only in ADR 0022. ADR 0021 separately and correctly governs the five-layer evidence boundary and the closed "Required new record families" table (see Problem Validation above), which is a related but distinct concept from ADR 0022's specific exhaustive-input-list gate.
- ADR 0022: must remain entirely unmodified; this record and its option set change nothing about the existing methodology, horizon, or entity-class criteria.
- ADR 0024: `valuation` remains a structured, non-scalar, confidence-bearing assessment; no option explored here may introduce a scalar, score, or probability of any kind.
- ADR 0005: any entity/representation-scoped classification must respect existing entity/representation boundaries, not introduce a new identity model.

### Technical

No new provider class; no new network-calling adapter; no automated prose interpretation of disclosure content (explicitly out of scope, consistent with PR #130's own documented scope limit).

### Operational

Any option must remain compatible with this repository's existing test/CI/quality-gate conventions (`ruff`, `black`, `mypy`, `pytest`) and must not require live network access to be *designed* (though some options may require it to *operate*).

### Persistence and migration

If a persisted record type is selected by a future decision, it must use the existing generic analytical persistence envelope (`data/data_ops.sqlite`) per ADR 0021 — no new database or schema-migration mechanism.

### Replay and historical reconstruction

Any persisted classification record must be replay-safe under ADR 0020's strict-known policy if it is ever consumed by a strict-known-replayed process.

### Compatibility

Must not weaken, bypass, or reinterpret ADR 0022's existing fail-closed exact-365-day check.

### Security and privacy

Not materially implicated; disclosure sources are public by definition (`official_disclosure`, `onchain_observation`).

### Performance and scalability

Explicitly named as a driving concern in the broader investigation (Hunter's mission to evaluate "thousands of protocols"); addressed under Architectural Dimensions and Comparative Analysis below.

### Evidence and provenance

Any classification, wherever it lives, must itself carry provenance (what was inspected, when, by what method) — a classification without provenance would itself violate Constitutional Rule 2.

## Evidence Inventory

| ID | Evidence | Authority/source | Finding | Quality and limitations | Supports or challenges |
|---|---|---|---|---|---|
| E-001 | Sky's persisted `FundamentalEvidenceRecord`/`ValueCaptureRuleSnapshot` | `data/data_ops.sqlite`, verified via `SupplyAndValueCaptureRepository`, sha256 `1f3b59ac...` (re-verified multiple times across this investigation, unchanged) | 30-day accounting period (not 365), `amount: None`, `rate_or_proportion: None` | High confidence — direct authorized repository read of real production data | Supports problem existence |
| E-002 | 13-protocol survey (Sky + Aave, Uniswap, GMX, Lido, EigenLayer, Ethereum, Chainlink, Helium, LayerZero, Pendle, Frax, Ethena), conducted earlier in this investigation | Mixture of directly-verified primary sources (GitHub-hosted contracts/governance repos for Aave, Uniswap, GMX, Lido, EigenLayer, Helium, Ethereum) and lower-confidence indexed-search-snippet sources (Chainlink, LayerZero, Pendle, Frax, Ethena numeric specifics) | 13/13 subjects fail an identical hypothetical strict ADR 0022-style test; the failure recurs via a small number of genuinely distinguishable structural shapes, falsification-tested to a minimal two-category structural distinction (source-native declared-period-bound records vs. unattributed continuous/live flow) | Mixed confidence, explicitly documented per-source in the original survey; structural conclusions (verified via primary contract/governance-repo sources for most subjects) carry higher confidence than specific numeric claims | Supports both problem existence and non-triviality (this is not a Sky-specific anomaly) |
| E-003 | `configs/value_capture_sources.yaml`, current `main` | Direct file read | Only two enabled sources exist; neither's registered capability set, even if fully reachable, would by itself satisfy ADR 0022 for any currently-registered entity | High confidence — direct read | Supports problem existence; supports that no "just pick a different registered source" escape currently exists |
| E-004 | `src/hunter/valuation/service.py:203-215` | Direct file read | The exact-365-day-equality check is the sole, correctly-functioning rejection point; it is a fail-closed correctness feature, not a defect | High confidence | Establishes that the gap is disclosure-architecture-shaped, not a software defect |
| E-005 | `docs/ADR/0004-trust-layer.md` | Accepted ADR | Establishes classification-before-intelligence precedent for a different axis (identity/trust) | High confidence | Supports Option 2A/1A-style designs having architectural precedent |
| E-006 | `docs/ADR/0022-canonical-valuation-methodology.md`, "Valuation inputs" section (corrected in Version 1.2 from a misattribution to ADR 0021, per second audit finding — independently re-verified: ADR 0021 has no "Valuation Inputs" section) | Accepted ADR | Input list is declared exhaustive ("This list is exhaustive, not illustrative"); a new record type cannot become an implicit input without further authorization | High confidence | Constrains all "first-class record" options (Decision Axis 2) |

## Assumptions

| ID | Assumption | Rationale | Confidence | Falsification condition | Consequence if false |
|---|---|---|---|---|---|
| A-001 | The 13-protocol survey's structural findings generalize beyond the specific 13 subjects sampled | The sample spans nearly every category this investigation's own scope named (lending, DEX, derivatives, liquid staking, restaking, native asset, oracle, DePIN, interoperability, yield, stablecoin/app-business) and the recurring shape was independently verified via primary contract/governance sources for most subjects, not merely inferred | Medium | A materially different sample (e.g., smaller/less mature protocols, or protocols outside the sampled categories) shows a majority satisfying ADR 0022's exact shape natively | The motivation for building this capability weakens proportionally; the capability might still be justified for the observed minority pattern (e.g., EigenLayer's declared-period-record shape) |
| A-002 | A classification capability can be designed that adds no new provider/network dependency beyond what already exists | Classification of *structure* (schema/timing/binding properties) does not inherently require new network access beyond what acquisition already performs, for most option variants | Medium-High | An option requiring classification strictly *before* any acquisition attempt (Decision Axis 1, Option 1A) may need its own lightweight reachability/metadata probe, which could be a new (if minimal) network touchpoint | Some options in Decision Axis 1 would need re-evaluation against the "no new provider class" constraint |
| A-003 | ADR 0009's Provider→Service→Repository→Persistence pattern can be extended to a new classification authority without modification to the pattern itself | This pattern has already been reused, unmodified, across Discovery, `market_facts`, `value_capture`, `valuation_methodology`, and `valuation` | High | A classification authority is found to require a genuinely new authority pattern (e.g., needing to *revise* other authorities' outputs, which none of the enumerated options propose) | Would itself require ADR 0009 amendment, a materially larger and out-of-scope decision |
| A-004 | Post-acquisition classification (Options 1B, 3E, and the post-acquisition half of 1C) yields an independently-derived structural signal, not merely a restatement of a human operator's prior manifest-authoring decision | Added by revision, per audit finding. Acquisition for `official_disclosure`/`onchain_observation` sources is manifest-verbatim (`src/hunter/valuation_evidence/command.py`); a human operator already decided what to put in the period/amount/rate fields before any classification could run against the resulting record | Low-Medium | The specific fields a post-acquisition classifier inspects (e.g., "is a period field populated") are shown to be fully determined by whether the operator succeeded in transcribing the source, not by any property the classifier itself discovers | 1B/3E's "highest accuracy" advantage over pre-acquisition options (1A, 1E) would need to be understood as "most accurate re-statement of operator intent," not independent structural discovery; does not invalidate the options, but qualifies their comparative advantage |
| A-005 | A subject's disclosure structure, once classified, remains stable enough for that classification to stay valid without re-classification | Added by revision, per audit finding. Implicit throughout Decision Axis 2 (persistence options assume a persisted classification retains value over time) | Low | This investigation's own cited evidence (GMX's 2026 pause/resume policy, EigenLayer's ELIP-012 emission-rate change, Uniswap's UNIfication fee-switch activation) shows real subjects changing disclosure mechanics over time | Persisted classification options (2A, 2E) would need an explicit re-classification trigger or staleness policy; this record does not resolve which, consistent with its enumeration-only scope. See new Option 4D (temporal/versioned multiplicity) for one architectural response to this assumption's falsification |
| A-006 | One registered source corresponds to exactly one disclosure structure | Implicit throughout Decision Axis 3's per-source option (3D); partially tested and held for the two sources actually registered today (Sky's `official_disclosure` vs. the generic `onchain_observation` source are correctly distinguished at source granularity) | Medium | A future source is registered that itself exposes more than one disclosure structure within a single registration (e.g., one endpoint that sometimes returns period-bound records and sometimes does not) | Source-level granularity (3D) would need to be paired with a multiplicity-handling option (Decision Axis 4) even for a single source, not only across multiple sources describing one entity |

## Architectural Dimensions

The following material dimensions were identified as affecting this decision, following `docs/ARCHITECTURE_DECISION_PREPARATION_GUIDE.md` Section 6:

- **Authority and ownership**: whether classification is a new, distinct, service-owned authority (per ADR 0009) or an attribute owned by an existing authority.
- **Timing**: where in the existing pipeline (Discovery → Trust → acquisition → methodology selection → valuation) classification would occur.
- **Persistence and identity**: whether classification produces a durable, versioned, identifiable record or a transient computation.
- **Granularity/scope**: the level (entity, representation, provider, source, or evidence-chain) at which one classification applies.
- **Multiplicity**: whether one subject can hold more than one simultaneous classification.
- **Replay and strict-known semantics**: if persisted, whether and how classification participates in ADR 0020's replay discipline.
- **Provenance**: what evidence a classification decision itself must cite, and how that citation is preserved.
- **Correction/versioning**: how a classification is revised if later evidence contradicts an earlier one.
- **Confidence and missingness**: whether classification itself can be partial/uncertain, and how that is represented without inventing a probability/score (prohibited).
- **Scalability**: whether the design scales to "thousands of protocols" without protocol-specific logic (a design goal established earlier in this investigation, reused here as an evaluation criterion, not a decision).
- **Compatibility boundary**: exhaustiveness of ADR 0022's Valuation Inputs list (corrected from ADR 0021 in Version 1.2, per second audit finding); non-scalar boundary of ADR 0024; entity/representation boundary of ADR 0005.

### Q1 — Disclosure architecture types observed (evidence, not decision)

Enumerated from real evidence gathered during this investigation (the 13-protocol survey), described in plain terms without assigning a canonical taxonomy label, since Phase 2 of that survey explicitly falsification-tested naive category labels and found most collapse into two genuinely irreducible structural shapes:

1. **Unattributed continuous/live flow** — amount exists only as a continuously-accruing or live/mutable balance with no source-native declared attribution period; a separately-versioned, externally-mutable rate/proportion. Observed in Sky, Aave, Uniswap, GMX, Lido, Ethereum's burn/issuance mechanics, Chainlink's narrow disclosed fragment, Helium's epoch-level data, Pendle, Frax, Ethena — 11 of 13 subjects.
2. **Attributed, source-declared period-bound flow** — the source's own record structure inherently declares an explicit attribution period (start/end or start+duration) as part of the same record carrying the amount, contract-enforced, regardless of whether that period is annual. Observed clearly in one directly-verified case: EigenLayer's AVS reward-submission mechanism.
3. **Governance-versioned scheduled/self-narrating disclosure** — a richly-documented, git-versioned governance record set (multi-year calendar-dated schedules, explicit self-narrated correction history). Observed in Helium; on falsification, this was found to be a documentation/governance-*rigor* attribute of shape 1 above, not a structurally distinct third shape.
4. **Periodic narrative-report overlay** — continuous on-chain accrual with a separate, irregular or periodically-reviewed narrative disclosure layered on top (dated "Update" posts, quarterly summaries). Observed in Ethena, Frax, and Sky's own quarterly Insights reporting; on falsification, also found to collapse into shape 1 (an additional disclosure *channel*, not a different binding/temporal contract).
5. **Absent or immature disclosure** — no usable value-capture claim exists at all, or only a narrow, recent, partial fragment exists. Observed in Chainlink (general case) and LayerZero (contested, partially unverifiable). This is explicitly **not** a fifth architecture type — the same falsification exercise proved this is an evidence *state* (absence/immaturity/unverifiability/conflict), never a structural classification category (see Q4 below).
6. **Fully bound flow (hypothetical; not observed in any of the 13 surveyed subjects)** — added by revision, per audit finding, as an explicit reference point rather than a gap in the taxonomy's conceptual range: a record in which amount, an exact-matching accounting period, and rate/proportion are all bound together in one immutable disclosure — the shape ADR 0022 actually requires. Neither observed shape reaches this: shape 2 (EigenLayer) binds amount to a declared period but keeps rate in a separately-stored, independently-versioned parameter ("regardless of whether a rate is included in the same record" — this is a limitation of the real evidence, not of the classification test itself). This entry is explicitly hypothetical and does not assert that any real source exhibits it; it exists so the taxonomy states its own conceptual boundary rather than only documenting failure modes.

**Edge cases not fully resolved by the binary test above, noted by revision per audit finding, and left open rather than silently assumed away:**

- **Within-record mixed/partial attribution**: a hypothetical record where the amount field carries a declared period but the rate field does not (or the reverse), as distinct from EigenLayer's already-observed case (period bound to amount, rate separately stored in a *different* record). The binary "does this record's schema declare a period field" test resolves this at the amount/period boundary but does not, by itself, specify how a classifier should treat rate-completeness within the same evaluation.
- **Schema-capable-but-inconsistently-populated period fields**: a source whose record schema *could* carry a period (structural capability) but does not always populate it across every instance (instance-level variability), as distinct from a source whose schema has no period field at all. Whether classification should apply at the schema level (capability) or the instance level (actual population) for such a source is not resolved here — it is a genuine open question for whichever future decision selects a granularity and timing option.

Mutual exclusivity of the two named observed shapes was independently re-checked at the single-record level and holds: the binary test ("does this record's own schema declare a period field bound to the amount") is well-defined and admits no self-contradictory classification for any one record considered alone.

This enumeration is evidence feeding the option analysis below, not itself a decision — no taxonomy is being adopted by this record.

### Q2 — Can disclosure architecture be classified independently from valuation?

Yes, with a specific, evidenced boundary. The properties that would need to be classified — whether a source's record schema declares an attribution period as an inherent field, whether amount and rate are bound in one record, whether a source distinguishes effective/recorded/known time, whether the source's mechanism/rate is policy-mutable, whether the evidence is self-attesting or externally asserted, whether records are individually addressable, and how corrections are represented — are all properties of the *source's* structure, established (in this investigation's own prior architectural-dimension-discovery exercise) to survive implementation-independence and economic-invariance tests without reference to any specific valuation methodology, discount rate, horizon, or output. None of these properties require knowing what `discounted-value-capture-flow-v1` or any future methodology would do with the evidence. This supports (without deciding) that a classification capability's inputs and outputs can be fully specified without depending on `CanonicalValuationService` or any methodology snapshot.

### Q4 — Can Hunter distinguish unsupported architecture vs. incomplete vs. unavailable vs. conflicting, without changing ADR 0022?

Yes, in principle, and this investigation already produced the relevant analytical groundwork (reused here as evidence, not re-derived): "absent," "immature," "unverifiable," and "conflicting" disclosure were each tested against the proof-of-category standard and each failed — they are evidence *states*, orthogonal to structural classification, exactly mirroring how ADR 0004/ADR 0020 already treat missingness, staleness, and conflict as states applied to entities/records generally. A classification capability could therefore report, for a given source: (a) a structural classification (or "unclassified" if genuinely ambiguous) as one axis, and (b) an evidence state (absent/incomplete/unavailable/conflicting) as an orthogonal axis — without ADR 0022 itself needing to change, since ADR 0022's own missingness/conflict fail-closed language already anticipates exactly this kind of state without prescribing how it is discovered.

## Interaction Diagram

*(Answers Q8 of the governing task, together with the "Existing Architecture" section above: how would this capability interact with Discovery, Validation, Market Facts, Value Capture, and Valuation without changing authority boundaries?)*

Textual representation of where a Disclosure Architecture Classification capability could interface with existing authorities, without altering any existing authority boundary. This diagram reflects the common shape across most enumerated options; Decision Axis 1 options vary the exact position of the dashed box — including, per the revision below, whether it sits inside Discovery itself (Option 1E) rather than strictly after it, as every other Decision Axis 1 option assumes. **Correction (Version 1.2, per second audit finding):** the diagram previously terminated at `ValuationAssessmentRecord` without naming its eventual consumer. Market Validation (ADR 0016, the sole canonical production analytical runtime per ADR 0016/0020) is now shown explicitly as a downstream boundary. This is a labeling correction only — no authority boundary changes: Market Validation was already, and remains, the eventual, unchanged consumer of `ValuationAssessmentRecord`; nothing in this record routes classification output there, and ADR 0024's no-scalar guarantee (checked per option throughout this record) is exactly what prevents any option from leaking into Market Validation's own composition authority.

```text
Discovery (ADR 0001)
   |
   v
Identity Resolution / Trust Layer (ADR 0004, ADR 0005)
   |
   v
+ - - - - - - - - - - - - - - - - - - - - - - - - - - - - +
:  Disclosure Architecture Classification (proposed;        :
:  classifies structure only; no acquisition, no fusion,    :
:  no valuation; timing/persistence/granularity/multiplicity:
:  are open per Decision Axes 1-4 below)                    :
+ - - - - - - - - - - - - - - - - - - - - - - - - - - - - +
   |                                  |
   v                                  v
hunter.market_facts            hunter.value_capture
(Provider -> Service ->        (Provider -> Service ->
 Repository, unchanged)         Repository, unchanged;
   |                             manifest-verbatim
   |                             acquisition unchanged)
   |                                  |
   +----------------+-----------------+
                     v
      hunter.valuation_methodology
      (CanonicalValuationMethodologyAuthority,
       sole write authority, unchanged)
                     |
                     v
      hunter.valuation
      (CanonicalValuationService, sole write
       authority, unchanged; existing exact-365-day
       fail-closed check unchanged)
                     |
                     v
      ValuationAssessmentRecord (ADR 0024:
      structured, non-scalar; unchanged)
                     |
                     v
      Market Validation (ADR 0016: sole canonical
      production analytical runtime; ADR 0020:
      strict-known input authority -- unchanged,
      not gated by, and not fed by, any Disclosure
      Architecture Classification option in this
      record; shown here only to make the existing,
      already-unchanged boundary explicit, per
      revision Version 1.2)
```

The dashed box is deliberately unowned by any existing authority in this diagram — every enumerated option in Decision Axis 2 places its actual authority ownership differently (a new authority, an attribute of an existing authority, or no persisted authority at all). No option in this record routes classification output through `CanonicalValuationService` as a required gate; all options preserve `CanonicalValuationService` as the sole, unchanged decision-maker for whether a given evidence chain is actually valid, consistent with ADR 0009 and this investigation's own prior finding that classification must never become a shadow authority over valuation decisions. Market Validation, now shown explicitly, is likewise unchanged and ungated by any option: it consumes only the already-defined, ADR 0024-governed `ValuationAssessmentRecord`, never a classification record directly, under any option enumerated here.

## Candidate Options

Four decision axes are enumerated (Decision Axis 4 is further decomposed into two nested sub-questions below, per the revision audit). A future decision could select one option per axis, or a composite spanning axes; this record does not evaluate composites *across axes* (e.g., a specific pairing of a timing option with a persistence option), per its enumeration-only scope, though the Comparative Analysis table and each option's "Open dependencies" field note where axes interact. **Revision note (audit finding):** Option 1C is retained despite being a composite *within* one axis (of 1A and 1B), because it has a genuinely distinct property neither 1A nor 1B has alone — a two-stage lifecycle requiring its own correction/supersession lineage design — and is therefore not "truly redundant" under this revision's own removal criterion (see Requirements). It is now explicitly labeled as a composite/hybrid variant rather than presented as if structurally equivalent to the three non-composite options in its axis.

### Decision Axis 1 — Classification Timing

*(Answers Q3 of the governing task: should Hunter determine disclosure eligibility before or after acquisition? Enumerated below across five options — four from the initial version, one added by this revision.)*

#### Option 1A — Pre-acquisition classification (source/registry metadata only)

- Description: Classify a registered source's disclosure architecture using only its registry configuration (`source_type`, `authority_tier`, declared `capabilities`) and any static metadata already known, before any acquisition attempt.
- Authority and ownership: Would need a new, distinct, service-owned authority operating over `configs/value_capture_sources.yaml`/`configs/market_fact_sources.yaml`-derived data, or equivalent persisted registrations; does not touch `CanonicalValuationService`.
- Boundaries: Operates strictly upstream of `hunter.value_capture`'s acquisition path; produces a classification before any `FundamentalEvidenceRecord` exists.
- Persistence and replay: Classification precedes any evidence record's existence, so strict-known replay of classification itself would need its own effective/recorded/known clock, independent of any evidence chain's clock.
- Evidence and provenance: Provenance is limited to configuration metadata, not actual disclosed content — a genuine limitation, since a source's *registered* type may not fully predict its *actual* disclosed structure (e.g., Sky is registered `official_disclosure` but its real content's shape was only knowable after acquisition).
- Implementation Impact (added by revision, per second audit finding): Low — a lightweight classification function/service reading already-existing YAML registry files; no new persistence schema required unless paired with a Decision Axis 2 option that persists results.
- Compatibility: Does not touch ADR 0022's exhaustive input list (classification is not an input); compatible with ADR 0022 (unmodified); compatible with ADR 0024 (produces no scalar).
- Advantages: Cheapest, fastest, requires no network access beyond what's already needed to register a source; can immediately flag sources whose *registered* capability set structurally cannot satisfy ADR 0022 (e.g., today's `verified-onchain-value-capture`, which lacks any `rule:*` capability).
- Disadvantages: Cannot detect the specific content-level mismatch this investigation actually found for Sky (a registered `official_disclosure` source with a correctly-declared `rule:buyback_and_burn` capability whose *actual* disclosed period is still only 30 days) — that gap is only visible after real content is inspected.
- Failure modes: False negatives (a source that looks capable on paper but isn't in practice) and false positives (a source that looks incapable on paper but could be manually supplemented) are both possible.
- Migration implications: Minimal — operates on already-existing configuration files, no schema for existing records changes.
- Reversibility: High — a pre-acquisition-only classifier can be added or removed without touching any persisted evidence record.
- Open dependencies: Depends on Decision Axis 2 (is this classification persisted) and Decision Axis 3 (source-level granularity is the natural fit here).

#### Option 1B — Post-acquisition classification (acquired-record structure only)

- Description: Classify disclosure architecture only after a `FundamentalEvidenceRecord`/`ValueCaptureRuleSnapshot` pair has actually been acquired, based on the acquired record's own real, structural properties (does it carry a source-declared period field, is amount populated, is rate populated, etc.).
- Authority and ownership: Could plausibly be an attribute computed by (but not owned by) the existing `SupplyAndValueCaptureService`, or a new authority consuming already-persisted records read-only.
- Boundaries: Strictly downstream of acquisition; upstream of, or parallel to, methodology selection.
- Persistence and replay: If persisted, naturally inherits the same effective/recorded/known clocks as the evidence record it classifies, simplifying strict-known replay compared to Option 1A.
- Evidence and provenance: Highest-fidelity provenance of all Decision Axis 1 options — the classification cites the actual record, not a prediction about it.
- Implementation Impact (added by revision, per second audit finding): Low-Medium — a function/service reading already-persisted `FundamentalEvidenceRecord`/`ValueCaptureRuleSnapshot` fields; cost is concentrated in cleanly distinguishing structural classification from evidence-state handling (Q4), not in the classification logic itself.
- Compatibility: Same as 1A for ADR 0021/0022/0024 — not a new input, not a scalar, ADR 0022 unmodified.
- Advantages: Exactly matches what this investigation actually needed for Sky (the gap was only visible once the real 30-day, `amount: None` record existed); highest accuracy.
- Disadvantages: Requires the (potentially costly, or currently network-blocked, as demonstrated repeatedly in this investigation) acquisition step to have already succeeded before any classification value is delivered — does not shorten the "discover the gap only after a full cycle" problem this ADPR's Motivation section identifies as the reason this capability is wanted.
- Failure modes: A source that is reachable but yields an incomplete/ambiguous record (an evidence *state*, per Q4) could be mistaken for an architecture-level rejection if the distinction between structural classification and evidence state (Q4) is not implemented cleanly.
- Migration implications: None to existing schemas if implemented as a read-only computation over existing records; some if implemented as a new persisted record type (see Decision Axis 2).
- Reversibility: High.
- Open dependencies: Depends on Decision Axis 2 and 3; most naturally pairs with evidence-chain-level granularity (Decision Axis 3, Option 3E).

#### Option 1C — Two-stage classification (composite of 1A and 1B; provisional pre-acquisition, confirmed post-acquisition)

**Composite label (revision note, per audit finding):** this option is explicitly a composite of Option 1A (provides the provisional stage's inputs) and Option 1B (provides the confirmation stage's inputs), retained as its own entry only because the two-stage *lifecycle* — and the correction/supersession lineage question it introduces — is a genuinely distinct architectural property neither 1A nor 1B has in isolation. It is not evaluated as a fully independent peer to 1A/1B/1D/1E in the sense the Preparation Guide's Option Normalization step intends; the Comparative Analysis table below labels it accordingly.

- Description: A cheap, low-confidence provisional classification from Option 1A's inputs, explicitly marked provisional, later confirmed or revised once Option 1B's real-record inputs become available.
- Authority and ownership: Would require two related but distinct classification outputs (provisional and confirmed), raising the question of whether these are the same record type with a status field or genuinely different record types — an open question this record does not resolve.
- Boundaries: Spans both upstream and downstream of acquisition.
- Persistence and replay: More complex — two classification events with two different clocks would need explicit correction/supersession lineage from provisional to confirmed, mirroring ADR 0020's correction pattern.
- Evidence and provenance: Best of both — early signal plus eventual high-fidelity confirmation — at the cost of needing to clearly label which is which at all times, to avoid the provisional classification being mistaken for a confirmed one (a genuine Constitutional Rule 2 risk if mishandled).
- Implementation Impact (added by revision, per second audit finding): Medium-High — combines 1A's and 1B's own implementation costs and additionally requires new correction/supersession lineage logic for the provisional-to-confirmed transition; the most implementation-complex option in Decision Axis 1.
- Compatibility: Same ADR 0021/0022/0024 compatibility as 1A/1B individually.
- Advantages: Captures the benefit of early signal (1A) without sacrificing eventual accuracy (1B).
- Disadvantages: Most implementation-complex of the Decision Axis 1 options; introduces a genuine new correction-lineage design question not present in 1A or 1B alone.
- Failure modes: Provisional-vs-confirmed confusion; stale provisional classifications never getting confirmed (a form of missingness needing its own explicit handling).
- Migration implications: Higher than 1A or 1B individually if implemented as two persisted record states.
- Reversibility: Medium — reversing this design after adoption means untangling which downstream consumers, if any, came to depend on the provisional signal.
- Open dependencies: Depends most heavily on Decision Axis 2's resolution, since it requires deciding whether provisional and confirmed classifications share one persisted record family or two.

#### Option 1D — On-demand classification (no fixed pipeline position)

- Description: Classification is not wired into any fixed pipeline stage at all; it is invoked whenever a caller (a human operator, a future methodology-selection step, or an audit process) needs an answer, using whatever evidence happens to be available at invocation time.
- Authority and ownership: Could be a stateless service/function with no fixed position in Discovery → Trust → acquisition → valuation, callable from any of those stages.
- Boundaries: Deliberately unbounded — the most flexible, and the least architecturally prescriptive, of the Decision Axis 1 options.
- Persistence and replay: If never persisted, replay is not applicable; if persisted per-invocation, would need the same clock-handling questions as 1A/1B/1C depending on what evidence was available at invocation time.
- Evidence and provenance: Provenance must explicitly record *what was available* at invocation time, since the same source could yield different classification results depending on whether it's invoked pre- or post-acquisition — a genuine consistency risk if not carefully specified.
- Implementation Impact (added by revision, per second audit finding): Low if never persisted (a pure function callable from any pipeline stage); rises sharply if ad hoc persistence patterns emerge informally, since those would need retrofitting into a proper authority later.
- Compatibility: Same ADR 0021/0022/0024 compatibility as the others.
- Advantages: Maximum flexibility; no premature commitment to a fixed pipeline position; could be prototyped cheaply.
- Disadvantages: Without a fixed position, classification could be invoked inconsistently across different callers, undermining the "deterministic reasoning" goal named in this record's Motivation section; risks becoming a convenience utility rather than a canonical authority, which would itself be an ADR 0009 violation if it silently began being treated as authoritative without service-owned validation.
- Failure modes: Inconsistent results across callers who invoke it at different pipeline stages; erosion of the "one clear owner" principle if adopted informally rather than through a proper authority.
- Migration implications: Minimal if never persisted.
- Reversibility: High if never persisted; lower if ad hoc persistence patterns emerge organically and later need consolidation.
- Open dependencies: Most dependent of the four on Decision Axis 2 being resolved carefully, precisely because its flexibility could otherwise erode authority clarity.

#### Option 1E — Discovery-integrated classification (added by revision, per audit finding)

- Description: Classification happens as an inherent step of candidate discovery/registration itself (ADR 0001), rather than as a separate stage positioned after Trust/Identity Resolution as every other Decision Axis 1 option assumes. A candidate would carry a disclosure-architecture classification (or explicit "not yet classified" state) from the moment it enters the Discovery pipeline.
- Authority and ownership: Would require Discovery itself, or a component invoked directly within Discovery's pipeline, to own or trigger classification — a materially different authority placement than 1A–1D, all of which sit downstream of Discovery.
- Boundaries: Directly tests the Discovery/Disclosure-Classification boundary the other four options never touch. ADR 0001 (Discovery-First) scopes Discovery narrowly to "continuously discover assets, protocols, networks, and ecosystems" — evidence-shape judgment is a different concern than candidate discovery, established earlier in this same investigation's architecture review of a related capability (CEAF/CVEA). This option would need to either (a) have Discovery merely *trigger* a separately-owned classification authority (functionally similar to 1A but invoked earlier in the pipeline) or (b) have Discovery itself gain classification responsibility (which would blur Discovery's own scoped authority under ADR 0001, mirroring the same entanglement risk already identified for Option 2B against `SupplyAndValueCaptureService`).
- Persistence and replay: Same clock-independence question as 1A (classification would precede any evidence record's existence), compounded by needing to also be compatible with Discovery's own checkpoint/replay conventions (ADR 0001's "discovery sources must be independent, typed, retried, checkpointed, and evidence-preserving").
- Evidence and provenance: Same content-blindness limitation as 1A (registry/candidate metadata only, no acquired disclosure content) — likely the coarsest-provenance option in the axis, since even less may be known about a freshly-discovered candidate than about an already-registered source.
- Implementation Impact: Medium — touches Discovery's own pipeline structure (ADR 0001), a larger and more sensitive surface than any other Decision Axis 1 option, none of which modify Discovery; cost is concentrated in correctly bounding Discovery's authority per the ADR 0001-boundary check this record flags as unresolved.
- Compatibility: No ADR 0021/0022/0024 conflict. Compatibility with ADR 0001 depends entirely on which of the two authority placements above (trigger-only vs. Discovery-owned) is chosen by a future decision; a future decision selecting Discovery-owned classification would need to check that placement against ADR 0001's own scope boundary explicitly, which this record flags rather than resolves.
- Advantages: Earliest possible signal of any Decision Axis 1 option — potentially before a source is even registered in `configs/value_capture_sources.yaml`/`configs/market_fact_sources.yaml`; could inform whether registering a source for a given candidate is worthwhile at all.
- Disadvantages: Weakest content fidelity of any timing option (same limitation as 1A, at an even earlier and less-informed pipeline point); highest risk of blurring Discovery's own scoped authority if implemented as Discovery-owned rather than Discovery-triggered.
- Failure modes: Same false-negative/false-positive risk as 1A, amplified by less available metadata; risk of silently expanding Discovery's authority contrary to ADR 0001 if boundaries are not enforced with the same discipline this record's other options apply to `CanonicalValuationService`.
- Migration implications: Would touch Discovery's own pipeline structure (ADR 0001), a different and potentially larger surface than any other Decision Axis 1 option, none of which modify Discovery.
- Reversibility: Medium — lower than 1A's, since integrating with Discovery's pipeline structure is more disruptive to unwind than adding or removing a standalone post-Discovery stage.
- Open dependencies: Depends on Decision Axis 2 and 3, same as the other timing options; additionally depends on a future, explicit check against ADR 0001's scope boundary that this record does not perform.

### Decision Axis 2 — Record First-Class Status

*(Answers Q5 of the governing task: should disclosure architecture become a first-class canonical record? Enumerated below across five options — four from the initial version, one added by this revision — with no option selected.)*

#### Option 2A — First-class, persisted, immutable, versioned canonical record

- Description: A new record family (structurally analogous to `FundamentalEvidenceRecord`), owned by a new, distinct, service-owned authority, persisted through the existing generic analytical envelope in `data/data_ops.sqlite`.
- Authority and ownership: New, distinct authority; must not be `CanonicalValuationService` (would blur classification and valuation authority) and must not be a provider or repository (would violate ADR 0009).
- Boundaries: Cleanly separable — this authority classifies; it does not acquire, validate valuation inputs, select methodology, or perform valuation, mirroring the explicit boundary this investigation's own prior architecture reviews (CEAF/CVEA) already established as necessary.
- Persistence and replay: Full ADR 0020 strict-known/immutable/versioned/correction-lineage discipline applies, exactly like every other canonical record family.
- Evidence and provenance: Strongest provenance of all Decision Axis 2 options — an auditable, permanent, queryable record of every classification decision ever made.
- Implementation Impact (added by revision, per second audit finding): Highest of the Decision Axis 2 options — a full new record family, new service-owned authority, and new persistence plan, mirroring the implementation scope of existing canonical record families like `FundamentalEvidenceRecord`.
- Compatibility: Does not become an ADR 0022 input by default — would require a separate, future, explicit authorization before any methodology variant could consume it, exactly as this investigation's prior CVEA architecture reviews concluded; ADR 0022 unmodified; ADR 0024 unaffected (no scalar).
- Advantages: Auditable, replayable, consistent with this repository's dominant persistence pattern; supports the "thousands of protocols" scalability goal by making classification a queryable, reusable fact rather than a recomputation.
- Disadvantages: Highest implementation cost of the Decision Axis 2 options; requires its own full authority-boundary design (a future ADR's work, not this record's); risks becoming a "shadow authority" if boundaries are not enforced with the same rigor as this investigation's prior reviews already specified.
- Failure modes: Authority creep (classification silently becoming binding on downstream methodology decisions) if the classify/decide boundary is not enforced.
- Migration implications: New record family, new persistence plan, no change to any existing record family's schema.
- Reversibility: Medium — once real classification history accumulates, removing the record family loses that history (though it could be archived/superseded rather than deleted, per this repository's existing ADPR/ADR immutability conventions).
- Open dependencies: Requires its own future ADR before being authorized; this option itself does not authorize anything.

#### Option 2B — Derived/computed attribute on existing records

- Description: Classification is expressed as an additional field or annotation on `FundamentalEvidenceRecord` (or an equivalent existing record), computed at persistence time by the existing `SupplyAndValueCaptureService`, rather than as a separate record family.
- Authority and ownership: No new authority; the existing `SupplyAndValueCaptureService` would gain a new responsibility, which is itself a change to that service's scope, not a purely additive one.
- Boundaries: Weaker separation than 2A — classification and evidence-content validation become entangled in the same service and record, which this investigation's prior architecture reviews specifically flagged as a risk to avoid ("classification must never become a shadow authority... valuation decisions remain owned by [the domain service]" — the same principle applies symmetrically to evidence-acquisition services).
- Persistence and replay: Inherits `FundamentalEvidenceRecord`'s existing replay discipline automatically, but any future correction to classification logic alone (without a change to the underlying evidence) would awkwardly require a new evidence-record version even though the evidence itself did not change — a genuine correction-lineage mismatch.
- Evidence and provenance: Provenance is implicit in the evidence record itself; no separate classification-decision audit trail exists.
- Implementation Impact (added by revision, per second audit finding): High — requires modifying `hunter.value_capture`'s existing, already-audited `FundamentalEvidenceRecord` schema and `SupplyAndValueCaptureService`'s logic, with a correspondingly larger regression-testing surface than any additive Decision Axis 2 option.
- Compatibility: Would require modifying `hunter.value_capture`'s existing, already-audited `FundamentalEvidenceRecord` schema — a materially larger and riskier change than 2A's additive new record family; ADR 0021/0022/0024 compatibility otherwise unaffected in principle, but the schema modification itself is a bigger surface for unintended regression.
- Advantages: No new record family or authority to design; potentially faster to prototype.
- Disadvantages: Schema modification risk to an already-implemented, already-audited record family; entangles two responsibilities in one service, contrary to this repository's demonstrated preference (ADR 0009) for narrow, single-purpose authorities; correction-lineage mismatch noted above.
- Failure modes: Future classification-logic changes forcing spurious evidence-record version bumps; service-boundary erosion.
- Migration implications: Requires an actual schema/field addition to an existing, live record family — the highest migration risk among Decision Axis 2 options.
- Reversibility: Low — removing a field from an already-persisted record family is harder to do cleanly than retiring a separate record family.
- Open dependencies: Would need its own careful compatibility analysis against every existing consumer of `FundamentalEvidenceRecord` before being viable at all.

#### Option 2C — Non-persisted, ephemeral, computed-on-demand classification

- Description: A pure function (or stateless service call) that computes a classification result at the moment it's needed, without ever writing it to `data/data_ops.sqlite`.
- Authority and ownership: Could still be a distinct, service-owned function/authority in the ADR 0009 sense (a "service" need not persist to have a clear owner), but produces no durable record.
- Boundaries: Cleanly separable from `CanonicalValuationService`, same as 2A.
- Persistence and replay: Not applicable in the conventional sense — nothing is persisted to replay. Strict-known replay of a historical valuation decision that depended on a classification would need to either (a) not depend on classification at all (classification is advisory-only, never part of the replayed decision chain) or (b) recompute classification identically at replay time, which requires the same evidence to still be available and the classification logic to be perfectly deterministic and version-pinned — a nontrivial guarantee to make without persistence.
- Evidence and provenance: Weakest of the Decision Axis 2 options — no permanent record of what was classified, when, or why, unless logged out-of-band (which would itself need its own design).
- Implementation Impact (added by revision, per second audit finding): Lowest of the Decision Axis 2 options — a pure function with no persistence, migration, or schema design required.
- Compatibility: Never becomes an ADR 0022 input by construction (nothing persists to be consumed); ADR 0021/0024 unaffected.
- Advantages: Cheapest to implement; zero persistence/migration footprint; trivially reversible.
- Disadvantages: Directly undermines this investigation's own Constitutional Rule 2 concern (traceable, reproducible, verifiable evidence) if classification ever informs a real decision without being recorded; weakest support for the "thousands of protocols" scalability goal, since every future query recomputes rather than reuses.
- Failure modes: Silent drift if the underlying classification logic changes between two invocations for the same source with no record of which logic version produced which historical answer.
- Migration implications: None.
- Reversibility: Highest of all options.
- Open dependencies: Least likely of the four Decision Axis 2 options to satisfy this repository's own Quality Standard's "Persistence and Replay Quality" and "Evidence and Provenance Quality" dimensions if classification is ever treated as more than a disposable convenience.

#### Option 2D — External/out-of-band, human-maintained documentation only

- Description: Classification exists only as human-maintained documentation or configuration (e.g., an additional field in `configs/value_capture_sources.yaml`, or a standalone markdown registry) — never computed or persisted by any runtime code.
- Authority and ownership: No software authority at all; ownership is editorial/operational (whoever maintains the config file).
- Boundaries: Trivially separable from every existing authority, since it isn't runtime-integrated.
- Persistence and replay: Not applicable — this is configuration, not a canonical record; replay of a historical decision would need to consult the configuration file's own git history, which has no formal strict-known guarantees.
- Evidence and provenance: Provenance is whatever commit-message discipline the maintainer applies — weaker and less structured than any runtime-computed option.
- Implementation Impact (added by revision, per second audit finding): Lowest of any option in this record — no code required at all; entirely a documentation/configuration convention.
- Compatibility: Cannot become an ADR 0022 input by construction; ADR 0021/0024 unaffected.
- Advantages: Zero implementation cost; immediately actionable (could be started today without any code change); matches this investigation's already-demonstrated practice of manually documenting exactly this kind of finding (e.g., the CVEA epic's own survey findings, PR #130's own extensive inline documentation).
- Disadvantages: Does not scale to "thousands of protocols" without becoming an unmaintainable manual burden — the same objection this investigation's prior architecture reviews raised against purely protocol-specific handling generally; provides no machine-checkable guarantee, and drifts silently from actual source behavior with no automated detection.
- Failure modes: Staleness (a config entry says one thing while the real source has since changed) with no mechanism to detect the drift.
- Migration implications: None to runtime code; only affects configuration file conventions.
- Reversibility: Trivially high.
- Open dependencies: None technical; would still need its own lightweight process convention if adopted (where does it live, who updates it, how is it referenced from an evidence-acquisition manifest).

#### Option 2E — Operational/diagnostic, non-canonical persisted record (added by revision, per audit finding)

- Description: Classification is persisted, structured, and queryable, but explicitly outside ADR 0020's strict-known/replay-governed canonical record system — an operational or diagnostic artifact rather than a domain record. Directly precedented in this repository today: `.github/workflows/acquire-sky-valuation-evidence.yml`'s uploaded `sky-valuation-evidence-diagnostic` artifact (raw content, response headers, hashes, and a non-interpretive field scan) is persisted and structured without being a canonical, strict-known-replayed record of any kind.
- Authority and ownership: No canonical authority in the ADR 0009 sense; ownership is whatever process produces the diagnostic artifact (e.g., a workflow, a CLI command, an audit script), analogous to how the existing diagnostic workflow is owned by CI/operational tooling, not by a domain service.
- Boundaries: Cleanly separable from `CanonicalValuationService` and every other domain authority, by construction — it is explicitly not a domain record.
- Persistence and replay: Persisted (unlike 2C) but explicitly not strict-known-replayable in the ADR 0020 sense (unlike 2A) — occupies real middle ground between 2A and 2C/2D that the initial version of this record did not enumerate. Historical reconstruction would rely on whatever the artifact-storage mechanism itself preserves (e.g., CI artifact retention, as the existing diagnostic workflow already demonstrates with a 14-day retention window), not on `data/data_ops.sqlite`'s bitemporal envelope.
- Evidence and provenance: Stronger than 2C (something is actually recorded) and comparable to or stronger than 2D (structured rather than free-form documentation), but explicitly weaker than 2A on long-term auditability, since operational/diagnostic storage is not designed for permanent, canonical retention the way `data/data_ops.sqlite` is.
- Implementation Impact: Low-Medium — directly reuses the existing diagnostic-workflow artifact pattern (already implemented and precedented); cost is concentrated in defining a retention/lifecycle policy, not in building new persistence machinery.
- Compatibility: Cannot become an ADR 0022 input by construction (it is explicitly non-canonical); ADR 0021/0024 unaffected.
- Advantages: Directly precedented and already proven low-risk in this repository (the existing diagnostic workflow); avoids both 2A's full authority-design burden and 2C/2D's provenance/scalability weaknesses; a natural fit for early, exploratory classification before a future decision commits to a canonical record design.
- Disadvantages: Not a permanent, queryable, canonical fact the way 2A is — classification produced this way could not, without further work, ever become an authorized ADR 0022 input, even after a future explicit authorization decision, unless it is first migrated into a canonical record family; retention/lifecycle policy (e.g., artifact expiry) becomes a real, if lower-stakes, provenance question of its own.
- Failure modes: Silent loss of classification history if the operational storage mechanism's retention policy expires it (e.g., the existing diagnostic workflow's 14-day artifact retention) before anyone consumes it; risk of informal over-reliance on an explicitly-non-canonical artifact as if it were authoritative, the same authority-creep risk already identified for Option 1D.
- Migration implications: None to existing canonical schemas; a later move from 2E to 2A would require an explicit, one-time migration of historically-useful classifications into the canonical system.
- Reversibility: High — operational/diagnostic artifacts can be discarded, regenerated, or superseded without touching any canonical record.
- Open dependencies: Requires its own lightweight process convention (where artifacts live, retention policy) if adopted, similar in kind to 2D's open dependency but with a structured-storage mechanism rather than free-form documentation.

### Decision Axis 3 — Classification Granularity Level

*(Answers Q7 of the governing task: should classification be entity-level, representation-level, provider-level, source-level, or evidence-chain-level? Enumerated below across seven options — five from the initial version, one added by this revision, and one relocated to this axis from a mislabeled position in the initial version's Decision Axis 4.)*

#### Option 3A — Entity-level

- Description: One classification per canonical economic entity (ADR 0005), regardless of how many representations, providers, or sources describe it.
- Authority and ownership: Would need to resolve conflicts when an entity's different representations/sources disagree on structure.
- Boundaries: Aligns with ADR 0005's entity concept, the highest existing identity level.
- Persistence and replay: Simplest replay story of the granularity options (fewest records).
- Evidence and provenance: Coarsest — loses information when a single entity's various sources genuinely differ in disclosure architecture (a real, observed case: Sky itself has both an `official_disclosure` source and, separately, a registered `onchain_observation` capability set — entity-level classification would need to either merge or arbitrarily pick between these).
- Implementation Impact (added by revision, per second audit finding): Low — fewest records of any granularity option; classification logic keyed only on entity ID.
- Compatibility: No ADR 0021/0022/0024 conflict inherent to this granularity choice by itself.
- Advantages: Fewest records to maintain; simplest mental model.
- Disadvantages: Loses real structural distinctions this investigation directly observed (multiple simultaneous disclosure structures per entity — see Q6/Decision Axis 4); most likely of the granularity options to require an arbitrary tie-break rule.
- Failure modes: Silent information loss when one entity's sources genuinely differ.
- Migration implications: None beyond whatever Decision Axis 2 requires.
- Reversibility: Medium — later splitting an entity-level classification into finer granularity would require re-deriving history.
- Open dependencies: Interacts directly with Decision Axis 4 (multiplicity) — entity-level granularity combined with single-classification-per-unit (Option 4A) would be the most information-lossy combination in the entire option space; entity-level combined with multi-classification (Option 4B) recovers much of the lost information.

#### Option 3B — Representation-level

- Description: One classification per ADR 0005 representation (e.g., Sky's Ethereum-native representation specifically, distinct from any wrapped/bridged representation that might exist).
- Authority and ownership: Same authority-design questions as 3A, one level finer.
- Boundaries: Matches ADR 0022's own Scope condition 1 (single-chain, non-wrapped, non-bridged native representation), which already operates at representation granularity.
- Persistence and replay: More records than entity-level, fewer than provider/source-level typically.
- Evidence and provenance: Better fidelity than entity-level for genuinely representation-specific disclosure differences (e.g., a wrapped asset's disclosure architecture could differ from its native counterpart's).
- Implementation Impact (added by revision, per second audit finding): Low — one level finer than 3A but comparably simple; classification logic keyed on representation ID.
- Compatibility: Aligns naturally with ADR 0022's existing representation-scoped eligibility criteria; no ADR 0021/0024 conflict.
- Advantages: Matches the granularity ADR 0022 itself already reasons at, minimizing translation friction for a future methodology-selection consumer.
- Disadvantages: Still does not resolve the case observed in this investigation where one representation (Sky's single Ethereum-native representation) is described by two structurally different sources (`official_disclosure` and `onchain_observation`).
- Failure modes: Same category of information loss as 3A, one level finer, so proportionally smaller but not eliminated.
- Migration implications: None beyond Decision Axis 2.
- Reversibility: Medium, same reasoning as 3A.
- Open dependencies: Same Decision Axis 4 interaction as 3A.

#### Option 3C — Provider-level

- Description: One classification per registered provider (e.g., `CoinGeckoObservedMarketFactProvider`, `RegisteredValueCaptureProvider`), describing the structural properties that provider's acquisition mechanism generally exhibits.
- Authority and ownership: Simplest of all granularity options in one sense — Hunter today has very few distinct provider classes.
- Boundaries: Diverges from ADR 0005's entity/representation model entirely; aligns instead with ADR 0009's Provider layer.
- Persistence and replay: Fewest records of any granularity option (currently, effectively two: the market-facts provider and the value-capture provider), but also the least useful signal, since `RegisteredValueCaptureProvider` is a single generic class servicing both `official_disclosure` and `onchain_observation` source types with structurally different implications (as this investigation directly found) — provider-level classification would conflate these.
- Evidence and provenance: Weakest differentiation of any granularity option given Hunter's current provider count.
- Implementation Impact (added by revision, per second audit finding): Low — trivial given Hunter's current small provider count, though this itself is the source of 3C's disadvantaged content-relevance.
- Compatibility: No direct ADR 0021/0022/0024 conflict, but the granularity mismatch against ADR 0022's representation-scoped criteria is a real friction point for any future consumer.
- Advantages: Trivial to enumerate today (very few providers exist); conceptually simple.
- Disadvantages: Given today's actual provider design (one generic manifest-verbatim provider class servicing multiple source types), this granularity would currently classify almost nothing usefully differently — it conflates exactly the distinction (Sky's disclosure vs. the generic on-chain-observation source) this investigation found most decision-relevant.
- Failure modes: Under-differentiation; two structurally different sources sharing one provider class would receive the same classification despite being genuinely different.
- Migration implications: None beyond Decision Axis 2.
- Reversibility: High, given how little would need to be recorded.
- Open dependencies: Least compelling of the granularity options against the evidence this record cites, though still enumerated per the task's explicit requirement to explore every possibility.

#### Option 3D — Source-level

- Description: One classification per registered source entry (`sky-protocol-tokenomics-disclosure`, `verified-onchain-value-capture`, etc., i.e., each row in `configs/value_capture_sources.yaml`/`configs/market_fact_sources.yaml`).
- Authority and ownership: Naturally aligned with the existing source-registry concept already implemented (`ValueCaptureSourceRegistry`, `MarketFactSourceRegistry`).
- Boundaries: Matches Option 1A's pre-acquisition timing option particularly well, since source-registry metadata is available before any acquisition.
- Persistence and replay: A moderate, manageable number of records (bounded by the number of registered sources, which grows deliberately and slowly per ADR 0009/0021's registration discipline, not per-entity).
- Evidence and provenance: Directly resolves the specific conflation Option 3C could not — Sky's `official_disclosure` source and the generic `onchain_observation` source would receive independent classifications, matching exactly what this investigation's own Phase 5/6 exhaustive-search work already needed to do manually.
- Implementation Impact (added by revision, per second audit finding): Low-to-medium — extends the existing source-registry pattern (`ValueCaptureSourceRegistry`/`MarketFactSourceRegistry`) rather than inventing a new mechanism, but scales with the number of registered sources.
- Compatibility: No ADR 0021/0022/0024 conflict; aligns with ADR 0009's existing registration-time authority pattern.
- Advantages: Directly matches how this investigation already had to reason about the problem (per-source, not per-entity or per-provider); scales with deliberate source registrations, not with entity count, supporting the "thousands of protocols" goal without thousands of classification records if many entities eventually share a small number of source *types*.
- Disadvantages: Does not, by itself, resolve the case where one *source's* actual disclosed content (as opposed to its registration) varies unexpectedly — that gap is the same one Option 1A (pre-acquisition timing) already has relative to 1B.
- Failure modes: A source could be registered once but change its real disclosure behavior over time without triggering a reclassification, unless paired with a versioning/correction mechanism (Decision Axis 2, Option 2A most directly supports this).
- Migration implications: None beyond Decision Axis 2; naturally extends the existing source-registry files or a parallel record.
- Reversibility: Medium.
- Open dependencies: Pairs most naturally with Option 1A/1C and Option 2A, per this analysis, without this record selecting that pairing.

#### Option 3E — Evidence-chain-level

- Description: One classification per specific, already-acquired `FundamentalEvidenceRecord` + `ValueCaptureRuleSnapshot` pairing (i.e., the finest possible granularity, tied to actual persisted evidence rather than any upstream registration).
- Authority and ownership: Pairs most naturally with Option 1B (post-acquisition timing).
- Boundaries: Finest-grained, most tightly coupled to actual evidence content.
- Persistence and replay: Most records of any granularity option (potentially one classification per correction/version of an evidence chain); inherits the underlying evidence records' own clocks most directly.
- Evidence and provenance: Highest-fidelity provenance of all granularity options — the classification is about the exact real record, not a generalization from source/provider/entity metadata.
- Implementation Impact (added by revision, per second audit finding): Highest of 3A-3G's non-hierarchical options — finest granularity means the most classification records to create and maintain, tied one-to-one with evidence-chain versions/corrections.
- Compatibility: No ADR 0021/0022/0024 conflict; most naturally auditable against ADR 0020's per-record strict-known discipline.
- Advantages: Most accurate; directly answers "is this specific evidence chain the kind of thing ADR 0022 could ever accept" with no generalization error.
- Disadvantages: Cannot exist before acquisition (inherits Option 1B's core disadvantage: does not shorten the discover-by-failure cycle this record's Motivation section names as the reason to want this capability at all); most records to maintain of any granularity option, with the least amount of reuse across entities/sources that share a common underlying disclosure architecture.
- Failure modes: Same as 1B — most useful for confirming what's already known, least useful for the "know before you try" goal.
- Migration implications: None beyond Decision Axis 2.
- Reversibility: High.
- Open dependencies: Depends most heavily on Decision Axis 1 (only compatible with 1B or the post-acquisition side of 1C).

#### Option 3F — Evidence-type-level (added by revision, per audit finding)

- Description: One classification per value of `hunter.value_capture.models.EvidenceType` — a fixed, closed `Literal` with five values (`official_disclosure`, `onchain_observation`, `governance_record`, `audited_financial_disclosure`, `market_fact_reference`), independently re-verified directly against current `main`. Only two of the five currently have any registered source; this option classifies at the schema-enum level, independent of whether a specific source of a given type is registered yet.
- Authority and ownership: Same authority-design questions as 3C/3D, one level more abstract — this granularity is bound to a fixed code-level enum, not to any specific registration.
- Boundaries: Diverges from both ADR 0005 (entity/representation) and ADR 0009 (provider) framings; aligns instead with `hunter.value_capture`'s own evidence-schema taxonomy.
- Persistence and replay: Fewest possible records of any granularity option (bounded at five, the size of the enum itself) — even coarser than 3C's current provider count, but for a different reason (a closed schema enum, not an accident of today's implementation).
- Evidence and provenance: Deliberately forward-looking rather than content-derived — a classification at this level says "sources of this *type* generically exhibit structural property X," which is a claim about the schema category, not about any specific source's actual disclosed content, so it inherits a limitation similar to 3C's (may not usefully differentiate two structurally different sources sharing one evidence type) while also, unlike 3C, remaining meaningful for evidence types that have no registered source yet (`governance_record`, `audited_financial_disclosure`, `market_fact_reference`).
- Implementation Impact (added by revision, per second audit finding): Low — bounded at five records (the size of the fixed `EvidenceType` enum), independent of how many sources are registered.
- Compatibility: No ADR 0021/0022/0024 conflict.
- Advantages: The only granularity option that can produce a classification for an evidence type before any source of that type is ever registered — directly useful for anticipating whether a not-yet-built source type could ever satisfy ADR 0022, without waiting for a specific source to exist.
- Disadvantages: Coarsest content-relevant signal of any granularity option for evidence types that *do* have multiple, structurally different registered sources (the same conflation risk 3C has, for a related but distinct reason); today, `official_disclosure` alone already contains at least one enabled and one disabled/template source, so even this level is not free of the conflation 3D was designed to resolve.
- Failure modes: Under-differentiation for evidence types with multiple, structurally different sources, symmetric to 3C's failure mode.
- Migration implications: None beyond Decision Axis 2.
- Reversibility: High.
- Open dependencies: Most useful in combination with a future-facing timing option (1A/1E) evaluating whether to register a not-yet-existing source of a given evidence type at all, a pairing this record notes but does not select.

#### Option 3G — Economic-claim-level (relocated by revision from the initial version's mislabeled Option 4D; see Decision Axis 4 below for the multiplicity-handling cross-reference)

- Description: One classification per specific economic claim (mirroring ADR 0021's own "economic entity, valued asset claim" concept), rather than per entity/representation/provider/source. A subject with multiple distinct economic claims (e.g., a hypothetical future entity with both a buyback-and-burn claim and a separate staking-distribution claim) would receive independent classifications per claim, without needing an explicit one-to-many mechanism bolted onto entity/source records.
- Authority and ownership: Reuses ADR 0021's existing "economic claim" concept as the unit of classification, rather than inventing a new unit — the closest alignment with `hunter.value_capture`'s existing model of any granularity option.
- Boundaries: `FundamentalEvidenceRecord`/`ValueCaptureRuleSnapshot` are already scoped to an economic claim, not to an entity directly, per ADR 0021 — this option matches that existing scoping exactly.
- Persistence and replay: Comparable to 3D/3E in record count; inherits the underlying claim's own strict-known chronology most naturally of any granularity option.
- Evidence and provenance: As complete as evidence-chain-level (3E) for genuinely distinct economic claims; does **not**, by itself, resolve the case where the *same* economic claim is described by multiple disclosure channels (e.g., Sky's buyback-and-burn claim described both on-chain and via a quarterly narrative report) — that case still needs a Decision Axis 4 answer (see below) nested within one claim's classification, exactly the limitation the audit found this option's original framing obscured by presenting it as if it resolved multiplicity on its own.
- Implementation Impact (added by revision, per second audit finding): Medium — comparable to 3D/3E in record count; reuses ADR 0021's existing economic-claim concept rather than introducing a new unit, which lowers implementation novelty relative to its record-count cost.
- Compatibility: Closest alignment with ADR 0021's existing terminology of any granularity option; no ADR 0022/0024 conflict.
- Advantages: Avoids inventing a new unit of classification by reusing one ADR 0021 already establishes; likely the least surprising design to someone already familiar with `hunter.value_capture`'s existing model.
- Disadvantages: Does not, alone, solve same-claim multi-channel disclosure — Ethena's four distinct disclosure streams (Reserve Fund updates, custodian attestations, PoR, governance fee-switch threads), cited in this investigation's underlying survey, describe overlapping, not cleanly separable, economic claims, so claim-level granularity narrows but does not eliminate the need for a Decision Axis 4 answer.
- Failure modes: Could create a false impression of having "solved" multiplicity when only claim-level differentiation is addressed, not channel-level differentiation within one claim — this is precisely the mislabeling the audit identified when this content was presented as a Decision Axis 4 (multiplicity) option rather than a Decision Axis 3 (granularity) option.
- Migration implications: Minimal if built directly atop the existing economic-claim concept.
- Reversibility: Medium.
- Open dependencies: Still requires a Decision Axis 4 answer (4A/4B/4C, or the new 4D below) for same-claim, multi-channel disclosure — this option narrows Decision Axis 4's scope but does not replace it, which is the corrected relationship between the two axes this revision establishes.

#### Option 3H — Hierarchical/multi-level classification (added by revision, per second audit finding)

**Independence evaluation (revision note, per second audit finding — this is the explicit answer the audit required, not left implicit):** hierarchical/multi-level classification is genuinely independent of Options 3A–3G and is not fully representable by combining existing options. Options 3A–3G each fix classification to exactly *one* level (entity, representation, provider, source, evidence-chain, evidence-type, or economic-claim); Decision Axis 4 (4A–4D) governs multiplicity *within* one chosen level for one unit (simultaneous or temporal). Neither axis, individually or combined, specifies classifying at *more than one level at once* with an explicit roll-up/aggregation relationship between levels (e.g., classifying each registered source, and separately maintaining an entity-level summary derived from its sources' classifications, with a defined rule for how the summary is computed and kept consistent as source-level classifications change). This is a structurally distinct design question — the relationship is *between levels*, not between multiple classifications of the *same* unit — so it is added here as its own option rather than folded into Decision Axis 3 or Decision Axis 4's existing framing.

- Description: Classification is produced at more than one granularity level simultaneously (e.g., source-level per Option 3D and entity-level per Option 3A), with an explicit, defined roll-up or aggregation rule relating the levels — as opposed to picking exactly one level (3A–3G) or handling multiplicity only within one level (4A–4D).
- Authority and ownership: Would require a single classification authority to own multiple, related record shapes (or multiple authorities with a defined precedence/aggregation contract between them) — a materially more complex authority design than any single-level option.
- Boundaries: Cleanly separable from `CanonicalValuationService`, same as the other Decision Axis 3 options; the added complexity is entirely about relationships *among* classification levels, not about touching any other existing authority.
- Persistence and replay: Most complex of any Decision Axis 3 option — replay at a cutoff would need to reproduce not just each level's classification but the roll-up relationship and computation as it stood at that cutoff, an aggregation-replay contract with no existing precedent elsewhere in this repository's canonical record families (unlike 4B/4C's one-to-many shape, which does have ADR 0021 precedent via multiple `ObservedMarketFactRecord`s per entity).
- Evidence and provenance: Potentially the most complete of any granularity option (preserves both fine-grained source-level detail and coarse-grained entity-level summary), but only if the aggregation rule itself is provenance-tracked as rigorously as the underlying classifications — an aggregation computed silently or non-deterministically would violate Constitutional Rule 2 even if each input classification is individually well-provenanced.
- Implementation Impact: Highest of any Decision Axis 3 option. Requires designing and implementing not only multiple classification record shapes (or reusing 3A–3G's shapes at multiple levels concurrently) but also a new, explicit aggregation/roll-up computation, its own versioning story, and its own correction-lineage behavior when an underlying (e.g., source-level) classification changes and the derived (e.g., entity-level) summary must be recomputed — a materially larger implementation surface than any single-level option, and larger than 4B/4C's simultaneous-multiplicity contract, which does not require a computed aggregation step.
- Compatibility: No ADR 0021/0022/0024 conflict in principle; the aggregation mechanism itself would need its own explicit strict-known replay design to remain compatible with ADR 0020 if ever persisted, which this record flags as an open dependency rather than resolving.
- Advantages: Could serve consumers needing either fine-grained (source-level) or coarse-grained (entity-level) answers without forcing a single project-wide granularity choice, avoiding the tie-break/information-loss trade-off any single-level option (3A–3G) makes alone.
- Disadvantages: By far the most implementation-complex option in Decision Axis 3, with no existing precedent for the aggregation-replay contract it would require; risks becoming exactly the kind of "protocol-specific special-casing" or premature architectural complexity this record's own Risks table already warns against for other options, at a larger scale.
- Failure modes: Aggregation logic drifting out of sync with underlying classifications; a silently non-deterministic or unversioned roll-up rule undermining Constitutional Rule 3 (Deterministic Intelligence) even if every individual classification is itself deterministic.
- Migration implications: Would depend on which underlying levels (3A–3G) and which Decision Axis 2 persistence option are combined; inherently larger than any single-level, single-persistence-option combination.
- Reversibility: Low — unwinding an aggregation relationship after downstream consumers begin relying on the rolled-up view is more disruptive than adding or removing a single-level classification.
- Open dependencies: Requires a future decision on which specific levels to relate hierarchically, the exact aggregation rule, and its own replay/correction design — the largest set of unresolved dependencies of any option in this record, consistent with it being flagged as the highest-implementation-cost option rather than a lightweight addition.

### Decision Axis 4 — Multiplicity Handling

*(Answers Q6 of the governing task: can one project belong to multiple disclosure classes?)*

**Revision note (audit finding — axis decomposition):** this axis is not four flat, mutually exclusive peers. It decomposes into two nested questions: (i) can more than one classification apply to the same unit *at the same time* (simultaneous multiplicity) — 4A answers no, 4B/4C answer yes; and, only if the answer to (i) is yes, (ii) is exactly one of those simultaneous classifications designated primary for consumers that want a single answer — 4B answers no, 4C answers yes. Option 4C is therefore conditional on 4B's "yes" branch, not an independent fourth point. A separate, orthogonal question — same-claim, multi-*channel* disclosure — is addressed by Option 3G above (economic-claim-level granularity), which narrows but does not replace this axis: even at claim-level granularity, one claim can still be described by more than one disclosure channel, which is exactly what 4A/4B/4C (simultaneous) and the new 4D below (temporal) govern. The initial version's Option 4D conflated a granularity choice with a multiplicity choice and has been relocated to Decision Axis 3 as Option 3G; the content below is new, added by this revision to cover a distinct multiplicity question the initial version never separated from simultaneous multiplicity.

#### Option 4A — Single mandatory classification per unit

- Description: Whatever unit Decision Axis 3 selects, exactly one classification applies at a time; if a subject's disclosure architecture appears to fit more than one shape, a tie-break rule forces a single answer.
- Authority and ownership: Simplest data model; requires a deterministic tie-break rule to be specified (out of scope for this record, but a clear future prerequisite).
- Boundaries: Cleanest, most conservative option.
- Persistence and replay: Simplest replay story (no multi-valued fields to reconcile across a cutoff).
- Evidence and provenance: Loses information in exactly the case this investigation directly observed (multiple simultaneous evidence structures per subject — Sky's own quarterly narrative report existing alongside its continuous on-chain mechanism; Ethena's four distinct disclosure streams; Lido's two structurally distinct fee systems running in parallel).
- Implementation Impact (added by revision, per second audit finding): Lowest of the Decision Axis 4 options — single-valued field, no multi-valued schema or aggregation logic required; the only added cost is specifying the tie-break rule itself.
- Compatibility: No inherent ADR 0021/0022/0024 conflict.
- Advantages: Simplest to reason about and implement; avoids any multi-valued-field replay complexity.
- Disadvantages: Directly contradicts the evidence this record's own Q1/Q6 analysis surfaces — real subjects were repeatedly and directly observed to exhibit more than one simultaneous evidence structure; forcing a single classification would require an arbitrary, evidence-destroying tie-break.
- Failure modes: Silent loss of a genuinely valid alternate disclosure path that could have satisfied ADR 0022 even if the "primary" classification did not.
- Migration implications: None beyond Decision Axis 2.
- Reversibility: Low in effect — once a tie-break rule is baked into behavior, downstream consumers may implicitly depend on "there is always exactly one answer," making a later move to multiplicity (Option 4B) a breaking change for those consumers.
- Open dependencies: Requires a tie-break rule to be specified by whatever future decision selects this option — an explicit open question this record does not resolve.

#### Option 4B — Multiple simultaneous classifications per unit

- Description: A given unit (entity/representation/provider/source/evidence-chain, per Decision Axis 3) may hold more than one classification at once, each independently tracked, with no forced primary.
- Authority and ownership: More complex data model; a "classification" becomes a one-to-many relationship from the classified unit.
- Boundaries: Most faithful to the evidence this record cites.
- Persistence and replay: Each classification instance needs its own identity and lineage; replay at a cutoff must return the full set valid at that cutoff, not a single value — a genuinely more complex, but well-precedented (ADR 0021 already handles multiple simultaneous evidence records per entity), replay contract.
- Evidence and provenance: Highest-fidelity of the Decision Axis 4 options — no information is discarded.
- Implementation Impact (added by revision, per second audit finding): Medium-to-high — requires a one-to-many schema and replay contract, though ADR 0021 already precedents multiple simultaneous records per entity, lowering novelty relative to its structural complexity.
- Compatibility: No inherent ADR 0021/0022/0024 conflict; if classification records ever become an ADR 0022 input (a separate future decision), a one-to-many relationship is no different in kind from how ADR 0021 already handles multiple `ObservedMarketFactRecord`s per entity.
- Advantages: Matches reality most closely; directly supports the observed cases (Sky, Ethena, Lido) where a subject genuinely exhibits more than one simultaneous disclosure structure; each classification could independently be evaluated against a future methodology variant, maximizing the chance that at least one of a subject's several disclosure paths is usable.
- Disadvantages: Most implementation-complex of the Decision Axis 4 options; downstream consumers must handle a set rather than a scalar answer, which is a more demanding (though not novel, given ADR 0021 precedent) contract.
- Failure modes: A consumer naively assuming single-valued classification (if this option is adopted inconsistently) could silently pick an arbitrary element of the set, recreating Option 4A's information-loss risk by implementation accident rather than design.
- Migration implications: None beyond Decision Axis 2, though the record schema (whatever Decision Axis 2 selects) must be designed multi-valued-aware from the start if this option is later chosen — a retrofit would be more disruptive than designing for it upfront.
- Reversibility: Medium — collapsing multi-valued classifications down to single-valued later (moving toward 4A) would require an explicit, and lossy, migration decision.
- Open dependencies: Interacts with every other Decision Axis; most naturally pairs with Option 2A (first-class persisted record, since a one-to-many relationship most cleanly maps to a proper record family with foreign-key-style references) and Option 3A/3D (entity or source level, where the multiplicity was actually observed).

#### Option 4C — Multiple classifications with one designated primary

- Description: A hybrid — a unit may hold multiple classifications (as in 4B), but exactly one is marked primary/authoritative for any consumer that needs a single answer, while the full set remains available to any consumer that wants it.
- Authority and ownership: Same base data model as 4B, plus an additional "primary" designation requiring its own rule/authority.
- Boundaries: Attempts to combine 4A's simplicity-for-simple-consumers with 4B's information preservation.
- Persistence and replay: As complex as 4B, plus the primary-designation itself needs its own versioning (a "primary" designation could change over time even if the underlying set of classifications does not).
- Evidence and provenance: As complete as 4B for consumers who want the full picture; as simple as 4A for consumers who only want one answer, at the cost of needing a documented, deterministic primary-selection rule.
- Implementation Impact (added by revision, per second audit finding): Highest of the Decision Axis 4 options — inherits 4B's full implementation cost and adds a new primary-selection rule and its own versioning on top.
- Compatibility: No inherent ADR 0021/0022/0024 conflict.
- Advantages: Serves both simple and sophisticated consumers without forcing a single, project-wide choice between 4A's simplicity and 4B's completeness.
- Disadvantages: Requires designing and maintaining a primary-selection rule, which is itself a new, nontrivial decision (out of scope here) and a new source of potential disagreement/staleness if the rule's assumptions stop matching reality.
- Failure modes: The primary-selection rule itself becoming a hidden, undocumented point of architectural authority if not designed with the same rigor as the classification capability itself.
- Migration implications: Same as 4B, plus the primary-designation mechanism.
- Reversibility: Medium, similar to 4B.
- Open dependencies: Requires its own future decision on the primary-selection rule; otherwise inherits 4B's dependencies.

#### Option 4D — Temporal/versioned multiplicity (added by revision, per audit finding; distinct from simultaneous multiplicity)

- Description: A unit's classification is single-valued *at any given moment* (no simultaneous multiplicity, unlike 4B/4C) but is explicitly versioned across time, with an append-only correction/successor chain, as the same unit's real disclosure structure changes. Directly motivated by evidence this record already cites: GMX's 2026 pause/resume policy change, EigenLayer's ELIP-012 emission-rate change, and Uniswap's UNIfication fee-switch activation all show a real subject's disclosure mechanics changing over time for the *same* channel, which is a different phenomenon from 4B's simultaneous coexistence of *different* channels.
- Authority and ownership: Same authority-design question as 4A/4B/4C — whichever authority owns classification (Decision Axis 2) must also own the correction/supersession rule for this axis.
- Boundaries: Orthogonal to the simultaneous-multiplicity question above — a design could combine "no simultaneous multiplicity" (4A-like) with "yes, versioned over time" (4D), or "yes simultaneous" (4B/4C) with "yes, each also independently versioned over time" (4D applied per-branch); this record does not evaluate that combination, consistent with its no-cross-axis-composites scope, but notes the combination is coherent and not excluded by anything else enumerated here.
- Persistence and replay: Requires exactly the append-only correction/supersession lineage pattern ADR 0020 already mandates for every other canonical record family — directly precedented, not a new replay contract, unlike 4B/4C's genuinely new one-to-many replay shape.
- Evidence and provenance: Addresses Assumption A-005 (disclosure-structure temporal stability) directly — without this option or an equivalent mechanism, any persisted classification (2A/2E) risks silently going stale as a real source's mechanics evolve, with no architected way to detect or correct it.
- Implementation Impact (added by revision, per second audit finding): Low — reuses ADR 0020's existing append-only correction/supersession pattern already implemented for other canonical record families, rather than inventing a new replay contract.
- Compatibility: No ADR 0021/0022/0024 conflict; directly reuses ADR 0020's existing correction-lineage discipline rather than inventing a new one.
- Advantages: Directly answers the temporal-stability gap Assumption A-005 identifies; lowest implementation novelty of the Decision Axis 4 options, since it reuses an already-precedented pattern rather than inventing a new multi-valued contract.
- Disadvantages: Does not address simultaneous multiplicity at all — a design needing both (a subject with several disclosure channels at once, each of which can also individually evolve over time) would need to combine this option with 4B or 4C, a composite this record notes but does not evaluate.
- Failure modes: A classification could become silently stale if no trigger exists to detect that a source's real disclosure has changed and a new version is needed — this option specifies the versioning *mechanism* but, consistent with this record's enumeration-only scope, does not specify the trigger or detection policy.
- Migration implications: None beyond Decision Axis 2, though whichever persistence option (2A/2E) is eventually selected must be designed correction-lineage-aware from the start for this option to be available later without a disruptive retrofit — the same caution already noted for 4B's multi-valued design.
- Reversibility: High for the versioning mechanism itself (append-only correction chains are additive, not destructive); the underlying question of when to trigger a new version is a separate, unresolved dependency.
- Open dependencies: Requires a future decision on staleness/re-classification triggers, not addressed by this record; can be combined with 4B/4C (each simultaneous branch independently versioned) or with 4A (a single classification, versioned over time), a combination this record notes without evaluating.

## Comparative Analysis

**Revision note (audit finding):** Option 1C (composite of 1A/1B) and Option 4C (conditional on 4B, per the axis decomposition noted above) remain in this table for completeness, but should be read as composite/conditional entries, not as fully independent peers to the other options in their respective axes — see the composite-label notes on each. **Revision note (Version 1.2, per second audit finding):** Option 3H (hierarchical/multi-level classification) is added as a new column; see Option 3H's own Independence evaluation for why it is not treated as a composite or conditional entry despite building on 3A–3G. 22 options total: five in Decision Axis 1 (1A–1E), five in Decision Axis 2 (2A–2E), eight in Decision Axis 3 (3A–3H), four in Decision Axis 4 (4A–4D).

| Criterion | 1A | 1B | 1C* | 1D | 1E | 2A | 2B | 2C | 2D | 2E | 3A | 3B | 3C | 3D | 3E | 3F | 3G | 3H | 4A | 4B | 4C** | 4D |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| Correctness/accuracy | Lower | Highest | High | Variable | Lowest | High | High | Medium | Lowest | Medium | Lower | Medium | Lowest | Medium | Highest | Lowest | Good | Highest (if aggregation is correct) | Lower | Highest | Highest | High |
| Constitutional compliance (Rule 2 traceability) | Yes | Yes | Yes | Depends on invocation discipline | Depends on Discovery boundary | Strongest | Strong | Weakest | Weak | Strong (non-canonical) | Yes | Yes | Yes | Yes | Yes | Yes | Yes | Depends on aggregation being provenance-tracked | Yes | Yes | Yes | Yes |
| Governance/ADR 0009 authority clarity | Clear if new authority | Clear if new authority | Needs care | Weakest | Needs care (ADR 0001 boundary) | Strongest | Weakened (entangled) | Clear but ephemeral | N/A (no runtime authority) | Clear, non-domain | N/A (orthogonal) | N/A | N/A | N/A | N/A | N/A | N/A | Needs care (multi-authority or precedence contract) | N/A | N/A | N/A | N/A |
| Replayability (ADR 0020) | Needs its own clock | Inherits evidence clock | Needs correction lineage | Depends on persistence choice | Needs its own clock | Full discipline | Awkward version-coupling | Not applicable | Not applicable | Not canonical (artifact-retention-bound) | Simplest | Simple | Simplest | Moderate | Most granular | Simplest (fewest possible) | Good | Most complex (no existing precedent for aggregation-replay) | Simplest | More complex, precedented | Most complex | Full discipline, most precedented in axis |
| Evidence/provenance integrity | Metadata-only | Highest | Highest eventually | Implicit in evidence | Coarsest | Highest | Implicit | Weakest | Weakest (informal) | Good, non-canonical | Coarsest | Coarser | Coarsest | Good | Highest | Coarsest (schema-only) | Good, claim-scoped | Potentially most complete, contingent on aggregation provenance | Lossy | Lossless | Lossless for consumers wanting one answer | Addresses A-005 directly |
| Maintainability/scalability to "thousands of protocols" | High | Medium | Medium | Depends | High (earliest signal) | High | Medium | Low reuse | Low (manual) | Medium (retention-bound) | Highest reuse | High reuse | Highest reuse (too coarse) | High, matches source count | Lowest reuse | Highest reuse (bounded at 5) | Reuses existing scaling model | Lowest of Decision Axis 3 (largest implementation surface) | Simple but lossy | Scales with real complexity | Scales, adds rule maintenance | Scales, reuses existing pattern |
| Migration risk | Low | Low | Medium | Low | Medium (touches Discovery) | Low-medium (new family) | High (existing schema change) | None | None | None | Low | Low | Low | Low | Low | Low | Low | High (depends on which levels/persistence option are combined) | Low | Medium | Medium | Low |
| Reversibility | High | High | Medium | High | Medium | Medium | Low | Highest | Highest | High | Medium | Medium | High | Medium | High | High | Medium | Low | Low (in effect) | Medium | Medium | High (mechanism); trigger design unresolved |
| Compatibility with ADR 0022 (input-list exhaustivity; corrected in Version 1.2 from a misattribution to ADR 0021) | Compatible, no auto-input | Compatible | Compatible | Compatible | Compatible, no auto-input | Compatible, requires future authorization to become an input | Compatible, same caveat | Compatible | Compatible | Compatible, cannot become an input by construction | Compatible | Compatible | Compatible | Compatible | Compatible | Compatible | Most naturally compatible (reuses claim concept) | Compatible in principle; aggregation mechanism's own replay design is an open dependency | Compatible | Compatible | Compatible | Compatible |
| Compatibility with ADR 0022 (unmodified) | Yes | Yes | Yes | Yes | Yes | Yes | Yes | Yes | Yes | Yes | Yes | Yes | Yes | Yes | Yes | Yes | Yes | Yes | Yes | Yes | Yes | Yes |
| Compatibility with ADR 0024 (no scalar) | Yes | Yes | Yes | Yes | Yes | Yes | Yes | Yes | Yes | Yes | Yes | Yes | Yes | Yes | Yes | Yes | Yes | Yes | Yes | Yes | Yes | Yes |

\* Composite of 1A and 1B, retained for its distinct two-stage lifecycle property; not a fully independent peer.
\** Conditional on 4B's simultaneous-multiplicity answer being "yes"; not an independent fourth point in the axis.

All options across the four axes are compatible with ADR 0022 and ADR 0024 without exception — no enumerated option was found to require weakening either. ADR 0022's exhaustive-input-list compatibility is universal in the narrow sense (no option, by itself, changes the exhaustive input list) but every option that could eventually feed valuation decisions would still require its own future, separate authorization before doing so, consistent with this investigation's prior findings. **Correction (Version 1.2):** this paragraph and the table row above previously attributed the exhaustive input list to ADR 0021; corrected to ADR 0022, per the second independent audit's finding.

## Falsification Results

For each option, the following invalidating conditions were tested; none were found to invalidate any option outright (each option remains viable under this record's stated constraints), but each option's material weaknesses are recorded as boundary conditions a future decision must weigh:

- **1A** would be invalidated if it turned out registry metadata alone reliably predicted actual disclosed content for the sources this investigation examined — tested directly: Sky's `official_disclosure` registration correctly predicted the *category* of source but not the *specific* 30-day-vs-365-day mismatch, so 1A survives as a viable-but-limited option, not a sufficient one alone.
- **1B** would be invalidated if the primary motivation for this capability (shortening the discover-by-failure cycle) were not real — tested against this investigation's own Motivation section evidence (E-002 required a full multi-stage investigation before the structural gap was visible); 1B does not resolve that motivation by itself, but is not falsified as inaccurate — it remains the most accurate option, just not the earliest-warning one.
- **1C** would be invalidated if provisional/confirmed classifications could not be kept unambiguously distinguishable — no counterexample was found that makes this impossible, only that it requires careful design (an open question, not a falsification).
- **1D** would be invalidated if ad hoc invocation could be shown to always erode authority clarity — not proven; it depends entirely on implementation discipline, so 1D survives as viable but carries the highest execution-dependent risk of the four.
- **2A** would be invalidated if a new record family were shown to require ADR 0009 amendment — tested against ADR 0009's actual text (Provider → Service → Repository → Persistence, reusable across "current and future subsystems"); no amendment is required, so 2A survives.
- **2B** would be invalidated if modifying `FundamentalEvidenceRecord`'s schema could be shown to break existing, already-audited consumers — this was not exhaustively tested (would require enumerating every current consumer of that record family, out of scope for this record), so 2B's viability remains conditionally open pending that future check, which this record flags as a genuine gap rather than resolving.
- **2C** would be invalidated if any option requiring persisted replay depended on it — since Decision Axis 1/3/4 options can combine with 2C only if replay is explicitly deemed unnecessary for classification, 2C survives only within that explicit condition, which is recorded here rather than assumed away.
- **2D** would be invalidated if it could be shown incompatible with the "thousands of protocols" scalability goal — this was directly tested against that goal and 2D's own stated disadvantage stands: it does not scale without becoming a manual burden, so 2D survives as viable only for a small, deliberately bounded set of sources, not as a general answer.
- **3A/3B** would be invalidated if entity/representation-level classification could be shown to never lose real information — falsified directly: Sky's own dual source registration (official_disclosure and onchain_observation) is a direct counterexample, so 3A/3B survive only when paired with a Decision Axis 4 option that preserves multiplicity (4B/4C/4D), not with 4A.
- **3C** would be invalidated if Hunter's provider model were more differentiated than it currently is — tested directly against `RegisteredValueCaptureProvider`'s actual single-class-servicing-multiple-source-types design; 3C's core objection is confirmed, so it survives as enumerated but with the weakest evidentiary support of the granularity options today (this could change if Hunter's provider model changes in the future, which is itself an open dependency).
- **3D** would be invalidated if source registration were shown not to correspond to a meaningful boundary — tested directly against ADR 0009's own registration-time authority pattern and this investigation's own Phase 5/6 exhaustive-search methodology, which was itself conducted at source granularity; not falsified.
- **3E** would be invalidated if it could never exist before acquisition — true by definition, which is recorded as a limitation, not a falsification (the option remains internally coherent, it simply cannot serve Option 1A/1C's early-warning goal).
- **3F** (added by revision) would be invalidated if `EvidenceType` were shown not to be a fixed, closed enum independent of source registration — independently re-verified directly against `src/hunter/value_capture/models.py`: it is a five-value `Literal`, with three of five values currently unregistered by any source; not falsified, though its content-relevance disadvantage (coarsest signal for evidence types with multiple structurally different sources, e.g., `official_disclosure` today) is confirmed, mirroring 3C's.
- **3G** (relocated by revision from the initial version's mislabeled Option 4D) would be invalidated if ADR 0021's economic-claim concept could be shown not to generalize to genuinely claim-external disclosure differences (e.g., a purely reporting-channel difference unrelated to any distinct claim) — tested against the evidence: Ethena's four distinct disclosure streams (Reserve Fund updates, custodian attestations, PoR, governance fee-switch threads) describe overlapping, not cleanly separable, economic claims, so 3G's claim-scoping does not fully resolve this case by itself, confirming the disadvantage already recorded above (and now correctly cross-referenced to Decision Axis 4, per the revision) rather than falsifying the option outright.
- **3H** (added by revision, per second audit finding) would be invalidated if hierarchical/multi-level classification could be shown to be fully representable by combining existing Decision Axis 3/4 options without an explicit inter-level aggregation rule — tested directly against 3A–3G (each fixes exactly one level) and 4A–4D (which govern multiplicity within one level for one unit, not a relationship between levels): neither, alone or combined, specifies how a source-level classification and an entity-level summary derived from it stay consistent as the source-level classification changes; not falsified, so 3H is retained as a genuinely independent option, consistent with its own Independence evaluation.
- **4A** would be invalidated if real subjects never exhibited multiple simultaneous disclosure structures — falsified directly: Sky, Ethena, and Lido were all directly observed exhibiting this in the evidence this record cites (E-002), so 4A survives only as a deliberately lossy simplification, not as a complete model.
- **4B/4C** would be invalidated if multi-valued replay were shown to be architecturally impossible under ADR 0020 — tested against ADR 0021's own existing precedent of multiple simultaneous `ObservedMarketFactRecord`s per entity, which already establishes that one-to-many, strict-known-replayable relationships are architecturally supported; not falsified. Per the revised axis decomposition, 4C's falsification condition is additionally conditional on 4B surviving, since 4C is 4B plus a primary-designation rule.
- **4D** (new content, added by revision; distinct from the relocated former content now at 3G) would be invalidated if real subjects were shown never to change disclosure mechanics for the same channel over time — falsified directly by the same evidence already cited for this record's Assumption A-005: GMX's 2026 pause/resume policy, EigenLayer's ELIP-012 emission-rate change, and Uniswap's UNIfication activation each show real, single-channel disclosure mechanics changing over time; 4D survives as the architecturally cheapest response to this evidence, since it reuses ADR 0020's existing correction-lineage pattern rather than inventing a new one.
- **1E** (added by revision) would be invalidated if Discovery-integrated classification were shown to be structurally impossible under ADR 0001 — not tested to that depth by this record; ADR 0001's own scope language ("continuously discover... before deciding what deserves analysis") does not explicitly foreclose a Discovery-triggered (as opposed to Discovery-owned) classification call, so 1E survives as viable but is explicitly flagged as requiring a future, dedicated ADR 0001-boundary check this record does not perform.
- **2E** (added by revision) would be invalidated if the cited precedent (the diagnostic-workflow artifact pattern) were shown not to actually exist in this repository — independently re-verified directly against `.github/workflows/acquire-sky-valuation-evidence.yml`, confirmed to exist and to persist structured, non-canonical diagnostic output exactly as described; not falsified.

## Rejected Options

None. Per explicit task scope ("Do NOT recommend. Do NOT rank. Do NOT choose."), no option in this record has been rejected. Every enumerated option remains open for a future decision session. This section is intentionally empty of rejections; it exists in the template to record rejections if and when a future decision-making session performs Comparative Evaluation and Falsification with the intent to select — which this record explicitly does not do.

**Revision note:** the initial version's Option 4D (claim-scoped classification) was not rejected — it was relocated to Decision Axis 3 as Option 3G, because independent audit found it was a mislabeled granularity option, not a multiplicity option. All of its substantive content is preserved at its corrected location; nothing was deleted.

## Risks

| Risk | Category | Likelihood | Impact | Mitigation | Residual uncertainty |
|---|---|---|---|---|---|
| Classification capability becomes a shadow authority over valuation decisions | Architecture | Medium | High | Enforce classify-only boundary explicitly in whichever option is selected, per this investigation's own prior CVEA-epic findings on exactly this risk | Depends entirely on future implementation discipline, not resolvable by this record alone |
| Premature commitment to entity/representation/provider/source granularity before enough real multi-source cases are observed | Architecture | Medium | Medium | Decision Axis 3's evidence base (13-protocol survey) is real but not exhaustive; a future decision should treat granularity choice as informed-but-not-final | Sample-size limitation acknowledged in Assumption A-001 |
| A persisted classification record (Option 2A) becomes a de facto ADR 0022 input without explicit authorization | Governance | Low-Medium | High | Any future implementation must treat this as a separate, explicit authorization decision, not an automatic consequence of building the classifier | Requires future ADR discipline to prevent |
| Classification logic silently encodes protocol-specific special-casing rather than genuine structural properties | Architecture | Medium | High | This investigation's own falsification exercise (referenced in Q1) already demonstrates the discipline needed (merge-first, falsify-before-split); a future implementation must apply the same discipline | Cannot be fully mitigated by an enumeration-only record; depends on implementation-time rigor |
| Scope creep into evidence fusion, scoring, probability, or heuristics during eventual implementation | Governance | Medium | High | Explicitly prohibited by this record's own scope and by the task that produced it; must be re-affirmed in any future ADR | Standing risk for any future implementation phase |
| Sample used for Q1's taxonomy (13 protocols) is a convenience sample, not exhaustive or random | Evidence | High (acknowledged) | Medium | Already flagged in the original survey's own limitations section (§8 of that survey) and carried forward here as Assumption A-001 | Genuinely unresolved; would require a broader, possibly random, sample to fully address |
| Option 3C (provider-level granularity), if selected while Hunter's provider model stays coarse, permanently forecloses the finer-grained distinction (Sky vs. the generic on-chain source) this investigation found most decision-relevant, without a disruptive re-architecture to recover it later | Architecture | Medium | Medium-High | A future decision selecting 3C should explicitly weigh this foreclosure risk, not just 3C's stated "least compelling" evidentiary weakness | Added by revision, per audit finding; not resolvable by this enumeration-only record |
| Option 2D (documentation-only classification), if adopted as a permanent design choice, produces nothing machine-readable, so any future automated consumer (e.g., a methodology-selection step) would require full replacement rather than incremental extension | Architecture | Medium | Medium | A future decision selecting 2D should treat it as explicitly provisional/bounded rather than a long-term answer, consistent with 2D's own disadvantages already noting it does not scale | Added by revision, per audit finding; not resolvable by this enumeration-only record |

## Open Questions

| Question | Blocking? | Owner | Required evidence or action | Status |
|---|---|---|---|---|
| Which option (or composite) across the four decision axes should be selected? | Yes, for ADR readiness | Future architecture-review/decision session | A dedicated selection exercise, explicitly out of this record's scope | Open |
| What deterministic tie-break rule would Option 4A use, if selected? | Yes, if 4A is ever selected | Future decision session | Not yet defined anywhere in this investigation | Open |
| What primary-selection rule would Option 4C use, if selected? | Yes, if 4C is ever selected | Future decision session | Not yet defined | Open |
| Does modifying `FundamentalEvidenceRecord`'s schema (Option 2B) break any existing consumer? | Yes, if 2B is ever selected | Future implementation session | A full consumer audit of `FundamentalEvidenceRecord`, not performed by this record | Open |
| Should a future classification record ever become an authorized ADR 0022 valuation input, and under what conditions? | Yes, before any methodology variant could consume classification output | A separate, future, explicitly-scoped ADR | Not addressed by this record; explicitly deferred | Open |
| Is the 13-protocol survey sample representative enough of Hunter's realistic future candidate universe to finalize a taxonomy (Q1), or is further survey work needed first? | Not blocking for this record (enumeration only), but blocking for any future taxonomy finalization | Future survey/decision work | A broader or differently-sampled survey | Open, explicitly acknowledged as Assumption A-001 |
| Should a governing Epic or Issue be created to track this ADPR's eventual decision session? | Not blocking this record | Repository owner | A new Issue/Epic, not created by this record per its explicit no-issue-creation scope discipline established across this investigation unless separately requested | Open |
| Does Discovery-integrated classification (Option 1E) violate ADR 0001's scope boundary if implemented as Discovery-owned rather than Discovery-triggered? | Yes, if 1E is ever selected with Discovery-owned authority placement | Future decision session | A dedicated ADR 0001-boundary check, not performed by this record | Open, added by revision |
| What staleness/re-classification trigger would Option 4D (temporal/versioned multiplicity) use to detect that a source's real disclosure has changed? | Yes, if 4D is ever selected | Future decision session | Not yet defined anywhere in this investigation | Open, added by revision |
| What retention/lifecycle policy would Option 2E (operational/diagnostic record) use, and would it differ from the existing diagnostic-workflow precedent's 14-day artifact retention? | Yes, if 2E is ever selected | Future decision session | Not yet defined beyond the existing precedent | Open, added by revision |

## Constitution Review

- Constitutional Rule 1 (Purpose — investment decision support, not complexity for its own sake): all four decision axes were evaluated in part against whether they serve or merely add complexity to the underlying goal of correctly classifying real disclosure structure; no option was found to conflict with this rule, though Options 2B, 1D, and (added by revision) 1E carry the highest risk of adding complexity or authority ambiguity without proportional benefit if implemented carelessly.
- Constitutional Rule 2 (Evidence Authority — traceable, reproducible, verifiable; unknown/missing/conflicting must remain visible): directly shaped this record's treatment of Q4 (evidence states must remain distinct from structural classification) and is the primary basis for Option 2C/2D's recorded disadvantages (weak provenance).
- Constitutional Rule 3 (Deterministic Intelligence): directly shaped the rejection of any implicit heuristic/scoring approach and the explicit exclusion of machine learning, probability, and fuzzy matching from every enumerated option, consistent with this record's own prohibitions section.

Outcome: `PASS` — no enumerated option conflicts with the Constitution; several options carry risks that a future implementation must actively manage to remain compliant, but none is constitutionally foreclosed at the enumeration stage.

## Governance Review

- `docs/DEVELOPMENT_GOVERNANCE.md` Stage 1 (this preparation process itself) has been followed: problem definition, evidence collection, dimension discovery, option enumeration, comparative evaluation, and falsification are all present; option selection is deliberately withheld per explicit task scope, which the Preparation Guide's own "Option Enumeration" step (Section 7) explicitly anticipates ("Do not prematurely rank or recommend options during enumeration").
- ADR 0009, ADR 0020, ADR 0021, ADR 0022, ADR 0024, ADR 0005, ADR 0004 were each checked against every enumerated option (see Constraints and per-option Compatibility fields above); no option was found to require modifying any of them. **Correction (Version 1.2):** a second independent audit found that Version 1.1's specific check against ADR 0021's "exhaustive Valuation Inputs list" was in fact checking text and a section that exist only in ADR 0022 (ADR 0021 has no such section) — every occurrence has been corrected throughout this record (Constraints, Evidence Inventory E-006, Comparative Analysis, Risks, Open Questions, ADR Readiness). ADR 0021's own, separately correct content (the five-layer evidence boundary and the closed "Required new record families" table) was not affected by the error and remains accurately cited.
- `docs/architecture-records/README.md`'s "Prohibited Practices" were checked: no ADR was created first with this record invented afterward (no ADR exists); no assumption is labeled as evidence (Assumptions are in their own section, explicitly confidence-rated); no rejected option was removed after a recommendation (none was made; the initial version's Option 4D was relocated, not removed, per the Rejected Options section's revision note); no Issue/PR/commit/release link is fabricated (all marked `not yet created` or `not applicable` where absent); the ADPR number (`ADPR-0002`) is newly allocated, not reused. This revision (Version 1.1) was made by direct amendment rather than a new superseding ADPR, correctly per the README's own rule that immutability applies only after `APPROVED` — this record has never been `APPROVED`.

Outcome: `PASS`.

## Quality Assessment

Applying `docs/ARCHITECTURE_DECISION_QUALITY_STANDARD.md`. **Revision note:** the independent audit of Version 1.0 found the self-graded ratings for Option completeness and Comparative fairness did not hold up under independent re-derivation (concrete missing options; two options presented as independent peers despite being composites; one option mislabeled under the wrong axis). This table reflects Version 1.1 after those specific corrections; the "Evidence and rationale" column for the two affected dimensions states both the original finding and what changed. **Revision note (Version 1.2):** a second, fresh independent audit of Version 1.1 found further, distinct weaknesses: no option carried a distinctly labeled "Implementation Impact" field (Option completeness — options were not normalized to comparable depth); a systematic ADR 0021/ADR 0022 misattribution ran through the Constraints, Evidence Inventory, Comparative Analysis, Risks, Open Questions, and ADR Readiness sections (Canonical consistency, Evidence integrity); and hierarchical/multi-level classification's independence was left unevaluated (Option completeness). This table reflects Version 1.2 after those corrections.

| Dimension | Rating | Evidence and rationale | Blocking limitation |
|---|---|---|---|
| Problem correctness | GOOD | Problem is grounded in directly-verified repository evidence (E-001, E-003, E-004), not a disguised implementation preference | None |
| Scope completeness | EXCELLENT | In/out of scope explicitly enumerated; task's own prohibitions directly incorporated | None |
| Canonical consistency | GOOD (a Version 1.1 misattribution corrected in Version 1.2) | Checked against ADR 0004/0005/0009/0020/0021/0022/0024, Constitution, and Principles. **Correction (Version 1.2):** the second audit found Version 1.1's specific ADR 0021 citations for the exhaustive Valuation Inputs list were misattributed (that content exists only in ADR 0022); corrected throughout | None remaining against the specific audit finding |
| Evidence integrity | ACCEPTABLE | E-001/E-003/E-004 are high-confidence direct reads; E-002 (the 13-protocol survey) carries documented, mixed per-source confidence, explicitly carried forward rather than concealed. **Correction (Version 1.2):** Evidence Inventory item E-006's citation was corrected from ADR 0021 to ADR 0022, per the second audit finding | Sample representativeness (A-001) remains open |
| Assumption discipline | GOOD | Six assumptions recorded with confidence, falsification condition, and consequence-if-false (three added by revision: A-004 operator-interpretation restatement, A-005 temporal stability, A-006 one-source-one-structure) | None |
| Option completeness | GOOD (was ACCEPTABLE under independent audit of Version 1.0; a second independent audit of Version 1.1 found further gaps, now corrected in Version 1.2) | Version 1.0's independent audit found four concrete missing options (Discovery-integrated timing; non-canonical operational/diagnostic persistence, precedented by the existing diagnostic workflow; evidence-type-level granularity, verified against `EvidenceType`'s five-value enum; temporal/versioned multiplicity distinct from simultaneous multiplicity). Version 1.1 added all four (as Options 1E, 2E, 3F, and 4D respectively), for 21 options across 4 axes. **Correction (Version 1.2):** a second, fresh independent audit found (a) no option carried a distinctly labeled "Implementation Impact" field, so options were not normalized to comparable depth as the Quality Standard requires, and (b) hierarchical/multi-level classification's independence from existing options was left unevaluated. Version 1.2 adds an explicit Implementation Impact field to every option and adds hierarchical/multi-level classification as new Option 3H, following an explicit independence evaluation concluding it is genuinely independent — for 22 options across 4 axes | None remaining against the specific audit findings; a broader future search cannot be ruled out in principle, consistent with any enumeration-only record |
| Comparative fairness | GOOD (was ACCEPTABLE under independent audit of Version 1.0) | Version 1.0's audit found Option 1C (composite of 1A/1B) and Option 4C (conditional on 4B) scored in the Comparative Analysis table as if fully independent peers, and Option 4D mislabeled as a multiplicity option when it was actually a granularity option. Version 1.1 explicitly labels 1C and 4C as composite/conditional in both their own sections and the table, and relocates the mislabeled content to Decision Axis 3 as Option 3G. **Correction (Version 1.2):** the Comparative Analysis table's ADR 0022 compatibility row header and cross-option ADR citations were corrected from ADR 0021, and the table gained a 3H column so every option is scored against the same criteria | None remaining against the specific audit findings |
| Falsifiability | GOOD | Each option's invalidating condition was explicitly tested, including the six new options; none required inventing a counterexample not groundable in already-gathered evidence | None |
| Authority and ownership clarity | GOOD | Every option's authority impact is explicit; the classify-vs-decide boundary is reaffirmed throughout, including for the newly-added Option 1E's ADR 0001 boundary question | None |
| Persistence and replay quality | ACCEPTABLE | Addressed per option; several options (1C, 4B/4C, and the new 4D's trigger design) leave open questions explicitly flagged rather than resolved, appropriately for an enumeration-only record | Tie-break/primary-selection/staleness-trigger rules remain undefined (see Open Questions) |
| Evidence and provenance quality | GOOD | Provenance impact explicit per option, including the newly-added options; Constitutional Rule 2 applied consistently | None |
| Operational quality | ACCEPTABLE | Failure modes recorded per option; full operational runbooks are appropriately deferred to a future implementation ADR | None |
| Testability and validation | ACCEPTABLE | Not directly applicable at enumeration stage; each option's determinism/replay implications are addressed, which bounds future testability | Full acceptance criteria require a selected option |
| Maintainability and extensibility | GOOD | Scalability to "thousands of protocols" applied as an explicit comparative criterion throughout, including two dead-end risks (Options 3C, 2D) surfaced by revision that were previously only implicit in per-option disadvantages | None |
| Risk quality | GOOD | Seven material risks recorded with likelihood, impact, mitigation, and residual uncertainty (two dead-end risks added by revision) | None |
| Traceability | ACCEPTABLE | Issue/Epic/ADR are honestly marked `not yet created`/`none produced`, per the repository's own "never fabricate links" rule | Governing Issue/Epic does not yet exist |

No mandatory dimension is below `ACCEPTABLE`; Constitution and Governance dimensions (folded into Canonical Consistency) are `GOOD`. Per the Quality Standard's own gate, this record could support `READY_FOR_ADR` on quality grounds alone — but ADR readiness additionally requires a selected option, which this record deliberately withholds (see ADR Readiness below).

## Architecture Readiness

- Outcome: `READY`
- Rationale: the problem, evidence, constraints, architectural dimensions, and a materially complete option set (22 options across 4 axes, following the two independent audits' corrections) are all present and internally consistent; no unresolved Constitution or Governance conflict exists. Version 1.0 was already rated `READY` here; the independent audits' `REQUIRES ADPR REVISION` conclusions targeted option completeness and internal consistency specifically (see Quality Assessment), both now corrected, not this section's underlying rationale.
- Missing evidence: broader/less-convenience-sampled protocol survey data (Assumption A-001), a `FundamentalEvidenceRecord` consumer audit (relevant only if Option 2B is later considered), tie-break/primary-selection/staleness-trigger rule definitions (relevant only if Option 4A/4C/4D is later selected), an ADR 0001-boundary check (relevant only if Option 1E is later selected with Discovery-owned authority placement), and a retention/lifecycle policy (relevant only if Option 2E is later selected) — none of these block architecture readiness itself, since they are conditional on options not yet selected.
- Unresolved conflicts: none.

## ADR Readiness

- Outcome: `BLOCKED`
- Proposed ADR title: not proposed — no option has been selected.
- Proposed ADR scope: not proposed.
- Decisions the ADR must fix (once a future session selects among this record's options): the classification timing (Decision Axis 1, now including whether classification is Discovery-integrated per Option 1E); whether classification is a first-class persisted record and, if so, its exact authority boundary relative to `CanonicalValuationService` and `SupplyAndValueCaptureService` (Decision Axis 2, now including the non-canonical operational/diagnostic middle ground of Option 2E); the classification granularity level (Decision Axis 3, now including evidence-type-level and economic-claim-level per Options 3F/3G, and hierarchical/multi-level classification per Option 3H); and the multiplicity model, decomposed per the revision into (i) simultaneous multiplicity (Decision Axis 4, 4A/4B/4C) and (ii) temporal/versioned multiplicity (4D) — including, if 4A or 4C is selected, the specific tie-break or primary-selection rule, and if 4D is selected, the specific staleness/re-classification trigger.
- Matters the ADR must leave open regardless of which option is selected: whether and when a classification record ever becomes an authorized ADR 0022 valuation input (explicitly named in this record's Open Questions as requiring its own separate, later ADR); the exact taxonomy category names for disclosure architecture (Q1's enumeration here is evidence, not a finalized taxonomy, and now includes an explicitly hypothetical fully-bound reference shape plus two named edge cases); Milestone 5 of Issue #107 remains untouched and unaffected by any option in this record.

This record is `BLOCKED` for ADR creation not because evidence is insufficient (Architecture Readiness is `READY`) but because, per explicit task instruction, no option has been selected, and ADR creation requires a decision this record was scoped not to make.

## Final Recommendation

No recommendation is made, per explicit task scope ("Do NOT recommend. Do NOT rank. Do NOT choose."). All 22 options across the four decision axes remain open. A future decision session, informed by this record, must perform its own Comparative Evaluation with the explicit intent to select — a step this record deliberately stops short of — before an ADR can be written. The missing element blocking ADR creation is not evidence; it is a decision, and this record is the evidence base a future decision-making session would use to make one.

## Decision History

| Date | State | Change | Author or reviewer |
|---|---|---|---|
| 2026-07-28 | `IN_RESEARCH` | Record created; full option enumeration completed per explicit task scope; no option selected | AI preparation session (Claude Code) |
| 2026-07-28 | `IN_RESEARCH` | Version 1.1: revised in response to an independent architecture audit (`REQUIRES ADPR REVISION`). Added Options 1E, 2E, 3F, 3G (relocated from a mislabeled Option 4D), and 4D (new content); explicitly labeled Options 1C and 4C as composite/conditional rather than independent peers; added Assumptions A-004–A-006; added a hypothetical fully-bound taxonomy shape and two named edge cases to Q1; qualified the Motivation section's overclaim; added Q3/Q5/Q6/Q7/Q8/Q9 cross-reference labels; added two dead-end risks (Options 3C, 2D); corrected the Option completeness and Comparative fairness Quality Assessment ratings. No option selected, no ADR written, no scope change | AI preparation session (Claude Code), correcting findings from an independent audit conducted by a prior session |
| 2026-07-28 | `IN_RESEARCH` | Version 1.2: revised in response to a second, fresh independent architecture audit of Version 1.1 (`REQUIRES ADPR REVISION`). Added a distinctly labeled "Implementation Impact" field to every one of the 21 pre-existing options; corrected a systematic misattribution of ADR 0022's exhaustive Valuation Inputs list concept to ADR 0021 across the Constraints, Evidence Inventory, Comparative Analysis, Risks, Open Questions, Governance Review, and ADR Readiness sections; added an explicit Market Validation (ADR 0016)/ADR 0020 boundary label to the Interaction Diagram, labeling-only and not changing any authority boundary; and added Option 3H (hierarchical/multi-level classification), with an explicit Independence evaluation concluding it is genuinely independent of Options 3A–3G and Decision Axis 4, for 22 options total. Updated the Comparative Analysis table (3H column), Falsification Results (3H entry), and Quality Assessment ratings accordingly. No option selected, no ADR written, no scope change, no ADR modified, Issue #107 not modified | AI preparation session (Claude Code), correcting findings from a second independent audit conducted by a prior session |

## Traceability

- Epic: not yet created
- Issue: not yet created (this record is a candidate follow-on to Issue #107, which it does not modify)
- Preparation working document: this record was produced directly against the template and guide; no separate intermediate working document exists
- Checklist review: not yet performed (would require an independent reviewer per `docs/checklists/ARCHITECTURE_DECISION_PREPARATION_CHECKLIST.md`)
- ADPR: `ADPR-0002` (this record)
- ADR: none produced
- Implementation plan: not applicable — no implementation is authorized or proposed by this record
- PR: this record itself is delivered via a documentation-only pull request; no production code is touched
- Merge commit: not yet recorded
- Release: not yet assigned

## Immutability and Supersession

Not yet `APPROVED`. Once approved as an accurate record of the enumerated option space, substantive changes (e.g., adding a materially new option, or changing an option's recorded impact) require either amending this record prior to approval or, after approval, a new ADPR that explicitly supersedes it, per `docs/architecture-records/README.md`.
