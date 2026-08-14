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
| [ADPR-0005](architecture-records/ADPR-0005-evidence-assembly-supporting-authorities.md) | Canonical Evidence Assembly Supporting Authorities (revision 8 preparation; ADR 0028 acceptance revision 9) | READY_FOR_REVIEW | not yet created | #191 completed preparation; #193 acceptance; prerequisite for blocked #190 | [ADR 0028](ADR/0028-evidence-assembly-supporting-authorities.md) (Accepted, revision 9) | [PR #192](https://github.com/fafa33/Project-Hunter/pull/192) (Merged preparation) | `89a2c2c9c2714aa2391c1fb3f26de5ab3e36eb32` | not yet assigned | not applicable | not applicable |
| [ADPR-0006](architecture-records/ADPR-0006-ai-context-prompt-intelligence-foundation.md) | AI Context and Prompt Intelligence Foundation | APPROVED | not yet created | #206 | [ADR 0031](ADR/0031-ai-context-prompt-intelligence-foundation.md) (Accepted) | [Draft PR #207](https://github.com/fafa33/Project-Hunter/pull/207) | not yet recorded | not yet assigned | not applicable | not applicable |
| [ADPR-0007](architecture-records/ADPR-0007-project-agnostic-prompt-intelligence-core.md) | Project-Agnostic Prompt Intelligence Core | APPROVED | not yet created | #237 / #247 | [ADR 0032](ADR/0032-project-agnostic-prompt-intelligence-core.md) (Accepted) | architecture PR #239; independent review PR #248 | `4938d2d` / `c201300` | not yet assigned | not applicable | not applicable |

## Active Preparation Records

Records in `PROPOSED`, `IN_RESEARCH`, or `READY_FOR_REVIEW` state are listed here for navigation.

| ADPR | Title | Status | Blocking questions | Owner |
|---|---|---|---|---|
| [ADPR-0002](architecture-records/ADPR-0002-disclosure-architecture-classification.md) | Disclosure Architecture Classification | IN_RESEARCH | Which option across the four decision axes should be selected (see record's Open Questions); no ADR can be created until a future session selects | not yet assigned |
| [ADPR-0005](architecture-records/ADPR-0005-evidence-assembly-supporting-authorities.md) | Canonical Evidence Assembly Supporting Authorities | READY_FOR_REVIEW | None material — revision 8 preparation is complete and accepted ADR 0028 revision 9 makes the prepared ADR 0021/0025 amendments effective; production implementation remains incomplete under Issues #194–#197; Issue #190 remains blocked until #197 concludes `IMPLEMENTABLE` | not yet assigned |

## Approved and Implemented Records

| ADPR | ADR | Status | Implementation | Validation |
|---|---|---|---|---|
| [ADPR-0001](architecture-records/ADPR-0001-architecture-decision-preparation-framework.md) | not applicable | IMPLEMENTED | PR #132 | Post-merge coherence remediation tracked by Issue #133 |
| [ADPR-0003](architecture-records/ADPR-0003-canonical-comparative-valuation.md) | [ADR 0026](ADR/0026-canonical-comparative-valuation-methodology.md) (Accepted) | APPROVED | Preparation PR #157; ADR PR #158; acceptance PR #160; implementation PR #161 | ADR 0026 and Comparative Valuation were implemented separately through PR #161; the approved preparation record remains immutable; post-merge chain stabilization is tracked by Issue #166 |
| [ADPR-0006](architecture-records/ADPR-0006-ai-context-prompt-intelligence-foundation.md) | [ADR 0031](ADR/0031-ai-context-prompt-intelligence-foundation.md) (Accepted) | APPROVED | Not started or authorized | Second independent preparation audit passed for `7a64f54bf51fd9f81ee859f9ca63389e5938bdac`; ADR 0031 aligned to Option 7 at `12ac95d3582bf569fe76beb29c06a274133cf080`; exact-pair closure review passed for `d94632a368b54ce1a9dc3a3a0e6c0ed40b0637c1` against `8dfd663ddf1db7a7b54bdd46eedca8aac0d36ff0` |
| [ADPR-0007](architecture-records/ADPR-0007-project-agnostic-prompt-intelligence-core.md) | [ADR 0032](ADR/0032-project-agnostic-prompt-intelligence-core.md) (Accepted) | APPROVED | Not started or authorized | Independent architecture review PR #248 passed `READY_FOR_ADR`; ADR acceptance is lifecycle-only and does not admit any concrete shared contract or authorize runtime implementation |

## Superseded and Archived Records

| ADPR | Final status | Successor | Reason |
|---|---|---|---|

## ADR Mapping

| ADR | Governing ADPR | Decision scope | Current status |
|---|---|---|---|
| not applicable | [ADPR-0001](architecture-records/ADPR-0001-architecture-decision-preparation-framework.md) | Governance Stage 1 preparation framework; no architectural decision created | not applicable |
| [ADR 0026](ADR/0026-canonical-comparative-valuation-methodology.md) | [ADPR-0003](architecture-records/ADPR-0003-canonical-comparative-valuation.md) | First methodology and authority contract for Canonical Comparative Valuation | Accepted and implemented through PR #161 |
| [ADR 0027](ADR/0027-canonical-market-validation-composition-authority.md) | [ADPR-0004](architecture-records/ADPR-0004-canonical-market-validation-composition-authority.md) | Composition ownership, exact-version adapters, family normalization, `WeightEngine` boundary, correlation/caps, residual independence, replay, and activation gates for the valuation family | Proposed; drafted under Issue #178; pending independent review and acceptance; no implementation or activation authorized |
| [ADR 0025](ADR/0025-canonical-valuation-evidence-assembly-authority.md) | not applicable (predates the ADPR framework; no governing ADPR was produced) | Canonical Evidence Assembly Authority plus accepted ADR 0028 supporting-authority and lineage amendments | Accepted; `CanonicalEvidenceAssemblyService` implementation exists but remains incomplete/non-constructible with production collaborators; #190 remains blocked pending #194–#197 and #197 `IMPLEMENTABLE` |
| [ADR 0028](ADR/0028-evidence-assembly-supporting-authorities.md) | [ADPR-0005](architecture-records/ADPR-0005-evidence-assembly-supporting-authorities.md) | Accepted Methodology Contract ownership in `hunter.valuation_methodology`; Evidence Shape Registry and Evidence Semantics ownership in `hunter.evidence_assembly`; upstream `CanonicalEvidenceSemanticInputAuthority` in `hunter.evidence_semantic_inputs`; immutable policy/output families, strict-known replay, correction/provenance, and exact semantic lineage through assembly; accepted amendments to ADR 0021 and ADR 0025 | Accepted, revision 9. Architecture: Accepted. Implementation: Incomplete. Preparation merged in PR #192 at `89a2c2c9c2714aa2391c1fb3f26de5ab3e36eb32`; historical Issue #193 acceptance contribution is PR #198 at `a71af2420ee0527ba7bf845068da675c7c3d0f82`; current substantive ADR-bearing commit is `1fdffad39e4beee0990d3680980ba31583813885`; Issues #194–#197 remain outstanding and #190 remains blocked until #197 concludes `IMPLEMENTABLE` |
| [ADR 0031](ADR/0031-ai-context-prompt-intelligence-foundation.md) | [ADPR-0006](architecture-records/ADPR-0006-ai-context-prompt-intelligence-foundation.md) | Evidence Intelligence-specific context resolution, selection, allocation, canonical prompt compilation, subordinate pre-model build identity, policy-controlled reconstruction, proposal-only authority, and deferred generic ownership | Accepted; ADPR-0006 is `APPROVED`; exact-pair closure review passed; no implementation or Phase 1 work authorized |
| [ADR 0032](ADR/0032-project-agnostic-prompt-intelligence-core.md) | [ADPR-0007](architecture-records/ADPR-0007-project-agnostic-prompt-intelligence-core.md) | Project-neutral ownership/dependency boundary and evidence-gated admission rule; no specific Hunter contract or external-project contract is declared shared | Accepted; ADPR-0007 is `APPROVED`; independent architecture review PR #248 passed `READY_FOR_ADR`; no runtime implementation or external-project integration is authorized by acceptance alone |
| [ADR 0033](ADR/0033-source-handling-classification-authority.md) | not applicable (owner-directed minimal decision scoped to the producer authority ADR 0031 left open; no governing ADPR was produced) | Canonical Evidence Intelligence Source Handling Authority (intake/document-lifecycle fact producer plus Source Handling Policy Service); strictly separated fact set, classification event, retention policy version, and derived retention decision; five orthogonal closed V1 handling dimensions with restrictive join and no `retainable` fact; admissibility and release-authorization contract; append-only streams with `BLOCKED` conflict and strict-known dual-cutoff selection; document-scoped applicability with most-restrictive mixed-content resolution; deny-by-default categories, build-status semantics, persistence rederivation, reference-safety-typed rejected-build audit, legacy-version and typed-content-state rules | Proposed; corrected after independent review of head `ac8306e` replaced the single-value handling vocabulary with the normalized fact model; drafted to unblock PR #260, which remains draft; no runtime implementation is authorized; no analytical authority is created and no `ANALYTICAL_AUTHORITY_REGISTRY` entry is required |

## Epic and Issue Mapping

| Epic or Issue | ADPRs | ADRs | Implementation PRs | Status |
|---|---|---|---|---|
| Issue #133 | [ADPR-0001](architecture-records/ADPR-0001-architecture-decision-preparation-framework.md) | not applicable | PR #132; remediation PR not yet created | Open remediation |
| Issue #135 / Issue #156 / Issue #159 | [ADPR-0003](architecture-records/ADPR-0003-canonical-comparative-valuation.md) | [ADR 0026](ADR/0026-canonical-comparative-valuation-methodology.md) (Accepted) | PR #157 (preparation), PR #158 (ADR), PR #160 (acceptance status), PR #161 (implementation) | ADPR remains APPROVED; Comparative Valuation foundation implemented separately |
| Issue #162 | not applicable | ADR 0021 | PR #163 | Canonical Mispricing foundation implemented; merge `a9f46f1` |
| Issue #164 | not applicable | ADR 0021 | PR #165 | Canonical Asymmetry foundation implemented; merge `99c95f0` |
| Issue #166 | not applicable | ADR 0021 / ADR 0026 | PR #167 merged; technical audit still pending | Post-merge valuation-family stabilization audit and documentation coherence |
| Issue #173 | [ADPR-0004](architecture-records/ADPR-0004-canonical-market-validation-composition-authority.md) | [ADR 0027](ADR/0027-canonical-market-validation-composition-authority.md) (Proposed) | Preparation PR #174; approved correction PR #175; independent review PR #177 (merged); implementation not yet created | ADPR-0004 `APPROVED`; ADR 0027 remains Proposed and no runtime activation is authorized |
| Issue #178 | [ADPR-0004](architecture-records/ADPR-0004-canonical-market-validation-composition-authority.md) | [ADR 0027](ADR/0027-canonical-market-validation-composition-authority.md) (Proposed) | ADR draft PR #179; implementation not yet created | Awaiting independent review and acceptance |
| Issue #190 | not applicable | ADR 0025 / Accepted ADR 0028 | not yet created | `BLOCKED`: Evidence Assembly implementation remains incomplete; Issues #194–#197 remain outstanding |
| Issue #191 / Issue #193 | [ADPR-0005](architecture-records/ADPR-0005-evidence-assembly-supporting-authorities.md) | [ADR 0028](ADR/0028-evidence-assembly-supporting-authorities.md) (Accepted, revision 9) | [PR #192](https://github.com/fafa33/Project-Hunter/pull/192) merged | Preparation and acceptance complete; production implementation remains incomplete under Issues #194–#197 |
| Issue #206 | [ADPR-0006](architecture-records/ADPR-0006-ai-context-prompt-intelligence-foundation.md) | [ADR 0031](ADR/0031-ai-context-prompt-intelligence-foundation.md) (Accepted) | [Draft PR #207](https://github.com/fafa33/Project-Hunter/pull/207); implementation not started | ADPR-0006 is `APPROVED`; ADR 0031 is Accepted; no Phase 1 work is authorized |
| Issue #237 / Issue #247 | [ADPR-0007](architecture-records/ADPR-0007-project-agnostic-prompt-intelligence-core.md) | [ADR 0032](ADR/0032-project-agnostic-prompt-intelligence-core.md) (Accepted) | architecture PR #239; independent review PR #248; runtime implementation not started | ADPR-0007 is `APPROVED`; ADR 0032 is Accepted as a boundary/admission policy; concrete shared-contract admission still requires independent two-consumer evidence |

## Component Mapping

| Component or architectural area | Active ADPRs | Accepted ADRs | Notes |
|---|---|---|---|
| Discovery | | | |
| Validation | [ADPR-0004](architecture-records/ADPR-0004-canonical-market-validation-composition-authority.md) | ADR 0016, ADR 0020, ADR 0021, ADR 0024, ADR 0026, [ADR 0027](ADR/0027-canonical-market-validation-composition-authority.md) (Proposed) | Canonical Market Validation composition remains governed separately |
| Evidence | [ADPR-0002](architecture-records/ADPR-0002-disclosure-architecture-classification.md), [ADPR-0005](architecture-records/ADPR-0005-evidence-assembly-supporting-authorities.md) | ADR 0025, [ADR 0028](ADR/0028-evidence-assembly-supporting-authorities.md) (Accepted, revision 9) | Evidence authority remains governed by its accepted ADR chain |
| AI Context and Prompt Intelligence | none | [ADR 0031](ADR/0031-ai-context-prompt-intelligence-foundation.md) (Accepted); [ADR 0032](ADR/0032-project-agnostic-prompt-intelligence-core.md) (Accepted) | ADR 0031 remains authoritative for Hunter's concrete pre-model contracts. ADR 0032 establishes the accepted project-neutral boundary and evidence-gated admission rule; no external project is coupled to Hunter and no current Hunter contract is declared shared. |
| Valuation | [ADPR-0002](architecture-records/ADPR-0002-disclosure-architecture-classification.md), [ADPR-0005](architecture-records/ADPR-0005-evidence-assembly-supporting-authorities.md) | ADR 0021, ADR 0022, ADR 0024, ADR 0025, ADR 0026, [ADR 0028](ADR/0028-evidence-assembly-supporting-authorities.md) (Accepted, revision 9) | Valuation architecture remains governed separately |
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