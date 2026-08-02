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
| [ADPR-0003](architecture-records/ADPR-0003-canonical-comparative-valuation.md) | Canonical Comparative Valuation | IMPLEMENTED | #135 | #156 / #159 | [ADR 0026](ADR/0026-canonical-comparative-valuation-methodology.md) (Accepted) | PR #161 | `5df7ff4` | not yet assigned | not applicable | not applicable |

## Active Preparation Records

Records in `PROPOSED`, `IN_RESEARCH`, or `READY_FOR_REVIEW` state are listed here for navigation.

| ADPR | Title | Status | Blocking questions | Owner |
|---|---|---|---|---|
| [ADPR-0002](architecture-records/ADPR-0002-disclosure-architecture-classification.md) | Disclosure Architecture Classification | IN_RESEARCH | Which option across the four decision axes should be selected (see record's Open Questions); no ADR can be created until a future session selects | not yet assigned |

## Approved and Implemented Records

| ADPR | ADR | Status | Implementation | Validation |
|---|---|---|---|---|
| [ADPR-0001](architecture-records/ADPR-0001-architecture-decision-preparation-framework.md) | not applicable | IMPLEMENTED | PR #132 | Post-merge coherence remediation tracked by Issue #133 |
| [ADPR-0003](architecture-records/ADPR-0003-canonical-comparative-valuation.md) | [ADR 0026](ADR/0026-canonical-comparative-valuation-methodology.md) (Accepted) | IMPLEMENTED | Preparation PR #157; ADR PR #158; acceptance PR #160; implementation PR #161 | PR #161 merged at `5df7ff4`; post-merge chain stabilization tracked by Issue #166 |

## Superseded and Archived Records

| ADPR | Final status | Successor | Reason |
|---|---|---|---|

## ADR Mapping

| ADR | Governing ADPR | Decision scope | Current status |
|---|---|---|---|
| not applicable | [ADPR-0001](architecture-records/ADPR-0001-architecture-decision-preparation-framework.md) | Governance Stage 1 preparation framework; no architectural decision created | not applicable |
| [ADR 0026](ADR/0026-canonical-comparative-valuation-methodology.md) | [ADPR-0003](architecture-records/ADPR-0003-canonical-comparative-valuation.md) | First methodology and authority contract for Canonical Comparative Valuation | Accepted and implemented through PR #161 |

## Epic and Issue Mapping

| Epic or Issue | ADPRs | ADRs | Implementation PRs | Status |
|---|---|---|---|---|
| Issue #133 | [ADPR-0001](architecture-records/ADPR-0001-architecture-decision-preparation-framework.md) | not applicable | PR #132; remediation PR not yet created | Open remediation |
| Issue #135 / Issue #156 / Issue #159 | [ADPR-0003](architecture-records/ADPR-0003-canonical-comparative-valuation.md) | [ADR 0026](ADR/0026-canonical-comparative-valuation-methodology.md) (Accepted) | PR #157 (preparation), PR #158 (ADR), PR #160 (acceptance status), PR #161 (implementation) | Comparative Valuation foundation implemented |
| Issue #162 | not applicable | ADR 0021 | PR #163 | Canonical Mispricing foundation implemented; merge `57c6fca` |
| Issue #164 | not applicable | ADR 0021 | PR #165 | Canonical Asymmetry foundation implemented; merge `99c95f0` |
| Issue #166 | not applicable | ADR 0021 / ADR 0026 | pending Draft PR | Post-merge valuation-family stabilization audit and documentation coherence |

## Component Mapping

| Component or architectural area | Active ADPRs | Accepted ADRs | Notes |
|---|---|---|---|
| Discovery | | | |
| Validation | | | |
| Evidence | [ADPR-0002](architecture-records/ADPR-0002-disclosure-architecture-classification.md) | not applicable | Enumerates options for classifying disclosure structure prior to or independent of acquisition; no ADR produced yet |
| Valuation | [ADPR-0002](architecture-records/ADPR-0002-disclosure-architecture-classification.md) | ADR 0021, ADR 0022, ADR 0024, ADR 0025, ADR 0026 | Valuation, Comparative Valuation, Mispricing, and Asymmetry foundations are implemented as separate authorities. Runtime normalization and downstream Market Validation composition remain separately governed and non-activated unless expressly authorized. |
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
