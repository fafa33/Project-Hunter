# Architecture Decision Index

## Purpose

This index is the central navigation and traceability registry for Architecture Decision Preparation Records (ADPRs), Architecture Decision Records (ADRs), governing Issues or Epics, implementation Pull Requests, merge commits, validation state, and releases.

It creates no independent architecture or governance authority. Canonical authority remains with the documents and ADRs linked here.

## Status Vocabulary

Use only the lifecycle states defined by `docs/ARCHITECTURE_DECISION_PREPARATION_GUIDE.md`:

- `PROPOSED`
- `IN_RESEARCH`
- `READY_FOR_REVIEW`
- `APPROVED`
- `IMPLEMENTED`
- `VALIDATED`
- `SUPERSEDED`
- `ARCHIVED`

## Decision Registry

| ADPR | Title | Status | Epic | Issue | ADR | Implementation PR | Merge commit | Release | Supersedes | Superseded by |
|---|---|---|---|---|---|---|---|---|---|---|---|
| [ADPR-0001](architecture-records/ADPR-0001-architecture-decision-preparation-framework.md) | Architecture Decision Preparation Framework | IMPLEMENTED | not applicable | #133 | not applicable | #132 | not yet recorded | not yet assigned | not applicable | not applicable |
| [ADPR-0002](architecture-records/ADPR-0002-disclosure-architecture-classification.md) | Disclosure Architecture Classification | IN_RESEARCH | not yet created | not yet created | none produced | not yet created | not yet recorded | not yet assigned | not applicable | not applicable |
| [ADPR-0003](architecture-records/ADPR-0003-canonical-comparative-valuation.md) | Canonical Comparative Valuation | APPROVED | #135 | #156 / #159 | [ADR 0026](ADR/0026-canonical-comparative-valuation-methodology.md) (Accepted) | PR #161 | `5df7ff4` | not yet assigned | not applicable | not applicable |
| [ADPR-0004](architecture-records/ADPR-0004-canonical-market-validation-composition-authority.md) | Canonical Market Validation Composition Authority (Phase 1) | APPROVED | not yet created | #173 | [ADR 0027](ADR/0027-canonical-market-validation-composition-authority.md) (Proposed) | not yet created | `5626a7b` | not yet assigned | not applicable | not applicable |
| [ADPR-0005](architecture-records/ADPR-0005-evidence-assembly-supporting-authorities.md) | Canonical Evidence Assembly Supporting Authorities (revision 3 — two independent-review rounds' findings resolved) | READY_FOR_REVIEW | not yet created | #191 (prerequisite for blocked #190) | [ADR 0028](ADR/0028-evidence-assembly-supporting-authorities.md) (Proposed) | not yet created | not yet recorded | not yet assigned | not applicable | not applicable |

## Active Preparation Records

Records in `PROPOSED`, `IN_RESEARCH`, or `READY_FOR_REVIEW` state are listed here for navigation.

| ADPR | Title | Status | Blocking questions | Owner |
|---|---|---|---|---|
| [ADPR-0002](architecture-records/ADPR-0002-disclosure-architecture-classification.md) | Disclosure Architecture Classification | IN_RESEARCH | Which option across the four decision axes should be selected (see record's Open Questions); no ADR can be created until a future session selects | not yet assigned |
| [ADPR-0005](architecture-records/ADPR-0005-evidence-assembly-supporting-authorities.md) | Canonical Evidence Assembly Supporting Authorities | READY_FOR_REVIEW | None material — revision 2 resolves all four open questions from revision 1 (Methodology Contract scope/ownership, Evidence Semantics authoring model, ADR 0025/0022 compatibility) in response to independent review of Draft PR #192; a second independent review round is pending | not yet assigned |

## Approved and Implemented Records

| ADPR | ADR | Status | Implementation | Validation |
|---|---|---|---|---|
| [ADPR-0001](architecture-records/ADPR-0001-architecture-decision-preparation-framework.md) | not applicable | IMPLEMENTED | PR #132 | Post-merge coherence remediation tracked by Issue #133 |
| [ADPR-0003](architecture-records/ADPR-0003-canonical-comparative-valuation.md) | [ADR 0026](ADR/0026-canonical-comparative-valuation-methodology.md) (Accepted) | APPROVED | Preparation PR #157; ADR PR #158; acceptance PR #160; implementation PR #161 | ADR 0026 and Comparative Valuation were implemented separately through PR #161; the approved preparation record remains immutable; post-merge chain stabilization is tracked by Issue #166 |

## Superseded and Archived Records

| ADPR | Final status | Successor | Reason |
|---|---|---|---|

## ADR Mapping

| ADR | Governing ADPR | Decision scope | Current status |
|---|---|---|---|
| not applicable | [ADPR-0001](architecture-records/ADPR-0001-architecture-decision-preparation-framework.md) | Governance Stage 1 preparation framework; no architectural decision created | not applicable |
| [ADR 0026](ADR/0026-canonical-comparative-valuation-methodology.md) | [ADPR-0003](architecture-records/ADPR-0003-canonical-comparative-valuation.md) | First methodology and authority contract for Canonical Comparative Valuation | Accepted and implemented through PR #161 |
| [ADR 0027](ADR/0027-canonical-market-validation-composition-authority.md) | [ADPR-0004](architecture-records/ADPR-0004-canonical-market-validation-composition-authority.md) | Composition ownership, exact-version adapters, family normalization, `WeightEngine` boundary, correlation/caps, residual independence, replay, and activation gates for the valuation family | Proposed; drafted under Issue #178; pending independent review and acceptance; no implementation or activation authorized |
| [ADR 0025](ADR/0025-canonical-valuation-evidence-assembly-authority.md) | not applicable (predates the ADPR framework; no governing ADPR was produced) | Canonical Evidence Assembly Authority: `CanonicalEvidenceAssemblyService`, lossless-composition invariants, `AssembledFundamentalEvidenceRecord` family, Evidence Shape Registry, methodology-contract input eligibility | Accepted; `CanonicalEvidenceAssemblyService` implemented, but not production-constructible pending ADR 0028 (see Issue #191) |
| [ADR 0028](ADR/0028-evidence-assembly-supporting-authorities.md) | [ADPR-0005](architecture-records/ADPR-0005-evidence-assembly-supporting-authorities.md) | Ownership, persistence, versioning, correction, provenance, strict-known replay, conflict, and amendment-governance semantics for Methodology Contract (owned by `hunter.valuation_methodology`; identity is `(contract_id, contract_version)`, its actual pre-existing fields), Evidence Shape Registry, Evidence Semantics, and `EvidenceSemanticsClassificationRuleset` (all owned by `hunter.evidence_assembly`); narrow amendments to ADR 0025's ownership silence and to ADR 0021's field-list table (additive `accepts_assembled_evidence` field on `ValuationMethodologySnapshot`); ADR 0022 reaffirmed, unmodified | Proposed, revision 3; drafted under Issue #191; resolved five round-1 and four round-2 independent-review `CHANGES REQUIRED` findings; pending a third independent review round and acceptance; no implementation authorized |

## Epic and Issue Mapping

| Epic or Issue | ADPRs | ADRs | Implementation PRs | Status |
|---|---|---|---|---|
| Issue #133 | [ADPR-0001](architecture-records/ADPR-0001-architecture-decision-preparation-framework.md) | not applicable | PR #132; remediation PR not yet created | Open remediation |
| Issue #135 / Issue #156 / Issue #159 | [ADPR-0003](architecture-records/ADPR-0003-canonical-comparative-valuation.md) | [ADR 0026](ADR/0026-canonical-comparative-valuation-methodology.md) (Accepted) | PR #157 (preparation), PR #158 (ADR), PR #160 (acceptance status), PR #161 (implementation) | ADPR remains APPROVED; Comparative Valuation foundation implemented separately |
| Issue #162 | not applicable | ADR 0021 | PR #163 | Canonical Mispricing foundation implemented; merge `a9f46f1` |
| Issue #164 | not applicable | ADR 0021 | PR #165 | Canonical Asymmetry foundation implemented; merge `99c95f0` |
| Issue #166 | not applicable | ADR 0021 / ADR 0026 | PR #167 merged; technical audit still pending | Post-merge valuation-family stabilization audit and documentation coherence |
| Issue #173 | [ADPR-0004](architecture-records/ADPR-0004-canonical-market-validation-composition-authority.md) | [ADR 0027](ADR/0027-canonical-market-validation-composition-authority.md) (Proposed) | Preparation PR #174; approved correction PR #175; independent review PR #177 (merged); implementation not yet created | ADPR-0004 `APPROVED` through independent architecture audit (PR #177); ADR 0027 drafted under Issue #178; no implementation or activation authorized until ADR 0027 is independently reviewed and accepted |
| Issue #178 | [ADPR-0004](architecture-records/ADPR-0004-canonical-market-validation-composition-authority.md) | [ADR 0027](ADR/0027-canonical-market-validation-composition-authority.md) (Proposed) | ADR draft PR #179; implementation not yet created | ADR drafted from approved ADPR-0004 per Issue #178; awaiting independent review, acceptance, and merge before any implementation may begin |
| Issue #190 | not applicable | ADR 0025 | not yet created | `BLOCKED`: `CanonicalEvidenceAssemblyService` cannot be constructed with production collaborators until Issue #191 / ADPR-0005 / ADR 0028 are accepted and implemented; see `docs/ARCHITECTURE_AUDITS/issue-190-evidence-assembly-authority-gap.md` |
| Issue #191 | [ADPR-0005](architecture-records/ADPR-0005-evidence-assembly-supporting-authorities.md) | [ADR 0028](ADR/0028-evidence-assembly-supporting-authorities.md) (Proposed) | [PR #192](https://github.com/fafa33/Project-Hunter/pull/192) (Draft); implementation not yet created | Architecture preparation only; prerequisite for unblocking Issue #190; revision 3 resolves five round-1 and four round-2 independent-review `CHANGES REQUIRED` findings; awaiting a third independent review round and acceptance before any implementation may begin |

## Component Mapping

| Component or architectural area | Active ADPRs | Accepted ADRs | Notes |
|---|---|---|---|
| Discovery | | | |
| Validation | [ADPR-0004](architecture-records/ADPR-0004-canonical-market-validation-composition-authority.md) | ADR 0016, ADR 0020, ADR 0021, ADR 0024, ADR 0026, [ADR 0027](ADR/0027-canonical-market-validation-composition-authority.md) (Proposed) | Binding normalization, correlation, anti-double-counting, `WeightEngine` boundary, replay, persistence, activation, rollback, canary, and ownership decisions for Canonical Market Validation composition are drafted in ADR 0027, pending independent review and acceptance; no runtime activation authorized |
| Evidence | [ADPR-0002](architecture-records/ADPR-0002-disclosure-architecture-classification.md), [ADPR-0005](architecture-records/ADPR-0005-evidence-assembly-supporting-authorities.md) | ADR 0025, [ADR 0028](ADR/0028-evidence-assembly-supporting-authorities.md) (Proposed) | ADPR-0002 enumerates disclosure-classification options; no ADR produced yet. ADR 0025 authorizes Canonical Evidence Assembly; ADR 0028 (Proposed) owns Evidence Shape Registry, Evidence Semantics, and the new `EvidenceSemanticsClassificationRuleset` (a fully specified, replay-deterministic governed reference-data family with an explicit `acceptance_sequence` ordering field) entirely within `hunter.evidence_assembly` — see Issue #191. **Dependency direction**: `hunter.evidence_assembly` depends on `hunter.valuation_methodology`'s public `MethodologyContractAuthority` contract to consume Methodology Contract records; it owns no part of that authority itself. `assemble()` remains production-unreachable pending ADR 0028 acceptance and implementation. |
| Valuation | [ADPR-0002](architecture-records/ADPR-0002-disclosure-architecture-classification.md), [ADPR-0005](architecture-records/ADPR-0005-evidence-assembly-supporting-authorities.md) | ADR 0021, ADR 0022, ADR 0024, ADR 0025, ADR 0026, [ADR 0028](ADR/0028-evidence-assembly-supporting-authorities.md) (Proposed) | Valuation, Comparative Valuation, Mispricing, and Asymmetry foundations are implemented as separate authorities. Runtime normalization and downstream Market Validation composition remain separately governed and non-activated unless expressly authorized. ADR 0028 (Proposed) gives `CanonicalValuationMethodologyAuthority`/`ValuationMethodologyRepository` sole ownership of the Methodology Contract Authority — a new `accepts_assembled_evidence` field on `ValuationMethodologySnapshot`, plus a new per-target `MethodologyEvidenceInputContract` record family whose identity is its own actual, pre-existing `(contract_id, contract_version)` fields, not invented ones — additively amending **ADR 0021**'s field-list table (ADR 0022's Milestone-2 permitted values are reaffirmed, unmodified). **Dependency direction**: `hunter.valuation_methodology`'s service/repository logic never imports from, or depends on, `hunter.evidence_assembly.service` — no cycle exists. One narrow, unmodifiable-code-forced exception: `hunter.valuation_methodology.service` imports the leaf `MethodologyEvidenceInputContract` dataclass from `hunter.evidence_assembly.models` (a dependency-free data type, not a service or business-logic dependency) to satisfy ADR 0025's already-fixed Protocol return type — see ADR 0028 §"Package ownership and dependency direction" for the full precision on why this does not reintroduce split ownership or a cycle. |
| Opportunity assessment and ranking | | | |
| Historical validation and replay | | | |
| Persistence and correction | | | |
| Automation and operations | | | |
| Governance | none | not applicable | [ADPR-0001](architecture-records/ADPR-0001-architecture-decision-preparation-framework.md) records the implemented preparation framework |

## Maintenance Rules

- Add an entry when an ADPR number is allocated.
- Update status when an ADPR changes lifecycle state.
- Add ADR, PR, commit, validation, and release links only after those artifacts exist.
- Mark absent artifacts as `not applicable` or `not yet created`; never fabricate links.
- Preserve superseded and archived records.
- Keep ADPR and ADR numbers independent.
- Treat this file as a navigation registry, not as a substitute for the linked records.
- Validate links and status consistency during architecture review and final validation.
