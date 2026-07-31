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
| [ADPR-0003](architecture-records/ADPR-0003-canonical-comparative-valuation.md) | Canonical Comparative Valuation | READY_FOR_REVIEW | #135 | #156 | not yet created | not yet created | not yet recorded | not yet assigned | not applicable | not applicable |

## Active Preparation Records

Records in `PROPOSED`, `IN_RESEARCH`, or `READY_FOR_REVIEW` state are listed here for navigation.

| ADPR | Title | Status | Blocking questions | Owner |
|---|---|---|---|---|
| [ADPR-0002](architecture-records/ADPR-0002-disclosure-architecture-classification.md) | Disclosure Architecture Classification | IN_RESEARCH | Which option across the four decision axes should be selected (see record's Open Questions); no ADR can be created until a future session selects | not yet assigned |
| [ADPR-0003](architecture-records/ADPR-0003-canonical-comparative-valuation.md) | Canonical Comparative Valuation | READY_FOR_REVIEW | Independent architecture-audit verdict on PR #157; real cohort and calibration evidence remain later production-activation gates | Issue #156 |

## Approved and Implemented Records

| ADPR | ADR | Status | Implementation | Validation |
|---|---|---|---|---|
| [ADPR-0001](architecture-records/ADPR-0001-architecture-decision-preparation-framework.md) | not applicable | IMPLEMENTED | PR #132 | Post-merge coherence remediation tracked by Issue #133 |

## Superseded and Archived Records

| ADPR | Final status | Successor | Reason |
|---|---|---|---|

## ADR Mapping

| ADR | Governing ADPR | Decision scope | Current status |
|---|---|---|---|
| not applicable | [ADPR-0001](architecture-records/ADPR-0001-architecture-decision-preparation-framework.md) | Governance Stage 1 preparation framework; no architectural decision created | not applicable |

## Epic and Issue Mapping

| Epic or Issue | ADPRs | ADRs | Implementation PRs | Status |
|---|---|---|---|---|
| Issue #133 | [ADPR-0001](architecture-records/ADPR-0001-architecture-decision-preparation-framework.md) | not applicable | PR #132; remediation PR not yet created | Open remediation |
| Issue #135 / Issue #156 | [ADPR-0003](architecture-records/ADPR-0003-canonical-comparative-valuation.md) | not yet created | PR #157 (architecture preparation only) | Independent review pending |

## Component Mapping

| Component or architectural area | Active ADPRs | Accepted ADRs | Notes |
|---|---|---|---|
| Discovery | | | |
| Validation | | | |
| Evidence | [ADPR-0002](architecture-records/ADPR-0002-disclosure-architecture-classification.md) | not applicable | Enumerates options for classifying disclosure structure prior to or independent of acquisition; no ADR produced yet |
| Valuation | [ADPR-0002](architecture-records/ADPR-0002-disclosure-architecture-classification.md); [ADPR-0003](architecture-records/ADPR-0003-canonical-comparative-valuation.md) | ADR 0021, ADR 0022, ADR 0024, ADR 0025 (unmodified, checked for compatibility only) | ADPR-0003 prepares the methodology decision for ADR 0021's already-owned but unavailable Comparative Valuation authority; it does not authorize implementation or amend accepted ADRs |
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
