# ADPR-0001: Architecture Decision Preparation Framework

## Status

- Lifecycle status: `IMPLEMENTED`
- Readiness outcome: `READY_FOR_ADR`
- Record type: Retrospective bootstrap record
- Governing issue: [Issue #133](https://github.com/fafa33/Project-Hunter/issues/133)
- Original implementation PR: [PR #132](https://github.com/fafa33/Project-Hunter/pull/132)
- ADR: `not applicable`
- Release: `not yet assigned`

## Bootstrap Statement

This record is retrospective because the Architecture Decision Preparation framework did not exist before PR #132 created it. The framework therefore could not govern its own initial creation prospectively.

This is a one-time bootstrap condition, not a general exception. All substantive future amendments to the framework must follow the accepted preparation lifecycle and create or update the applicable ADPR before implementation proceeds.

## Problem Statement

Project Hunter had binding architecture documents, ADRs, implementation governance, review protocols, and merge-readiness rules, but no repository-wide standard for preparing architecturally significant decisions before ADR creation or implementation.

Major decisions could therefore reach implementation without a consistent permanent record of:

- validated problem definition;
- evidence quality and limitations;
- fixed constraints;
- architectural dimensions;
- materially distinct options;
- rejected alternatives;
- falsification attempts;
- Constitution and Governance checks;
- readiness for ADR creation.

## Motivation

The repository required a repeatable, auditable, AI-compatible process that preserves the reasoning behind major decisions and reduces preventable architecture defects without creating a second architecture authority or replacing Development Governance.

## Current Architecture Before the Decision

Before PR #132:

- the Constitution and Principles governed project-level rules;
- the Canonical Architecture Map owned document precedence;
- accepted ADRs owned binding architectural decisions;
- Development Governance owned the permanent-change lifecycle;
- the Implementation Contract owned implementation obligations;
- the AI Review Protocol owned independent review behavior;
- no canonical preparation process existed before ADR creation.

## Scope

Included:

- architecturally significant pre-implementation decision preparation;
- evidence and assumption separation;
- option enumeration and normalization;
- comparative evaluation and falsification;
- readiness gates;
- permanent ADPR records;
- an architecture traceability index;
- a quality standard, glossary, template, and checklist.

Excluded:

- defining architecture;
- accepting ADRs;
- authorizing implementation;
- performing architecture review;
- approving Pull Requests;
- determining merge readiness;
- changing runtime, persistence, replay, evidence, scoring, or analytical authority.

## Constraints

- The Constitution must remain the highest authority.
- The Canonical Architecture Map must retain ownership of document precedence.
- Accepted ADRs must remain the only binding architecture decisions at the ADR layer.
- Development Governance must remain the process authority.
- The preparation framework must elaborate Stage 1 only.
- ADPRs must remain non-authoritative reasoning records.
- Documentation burden must remain proportional to risk.
- No runtime or analytical behavior may change.

## Evidence Inventory

| Evidence | Authority and relevance | Limitation |
|---|---|---|
| `docs/PROJECT_CONSTITUTION.md` | Highest project authority | Does not define preparation mechanics |
| `docs/PROJECT_PRINCIPLES.md` | Binding engineering and architecture principles | Does not define preparation workflow |
| `docs/CANONICAL_ARCHITECTURE_MAP.md` | Owns document hierarchy and navigation | Initially did not map the new framework |
| `docs/DEVELOPMENT_GOVERNANCE.md` | Owns development lifecycle | Stage 1 was previously high-level only |
| `docs/ADR/README.md` and accepted ADRs | Define durable decisions and ADR lifecycle | ADRs preserve decisions, not full preparation research |
| `docs/AI_REVIEW_PROTOCOL.md` | Independent post-implementation review | Review occurs after implementation evidence exists |
| PR #132 | Actual framework implementation and ownership-boundary declaration | Initial framework predated its own ADPR requirement |
| Issue #133 | Independent post-merge coherence audit | Created after the framework merged |

## Assumptions

- A permanent reasoning record improves future auditability when it is explicitly non-authoritative.
- Humans and AI contributors can follow the same preparation lifecycle.
- A proportional process can remain mandatory for significant decisions without overburdening ordinary maintenance.
- Retrospective reconstruction is acceptable only for this bootstrap record because the original PR, issue history, and merged documents preserve sufficient evidence.

## Architectural Dimensions

- authority and document precedence;
- governance ownership;
- decision-record permanence;
- ADR relationship;
- lifecycle states;
- evidence quality;
- assumption visibility;
- option completeness;
- falsifiability;
- traceability;
- proportionality;
- review separation;
- merge-authority separation;
- bootstrap behavior.

## Candidate Options

### Option A — Keep the existing process without a preparation framework

Major decisions would continue to rely on issue text, conversations, ADR context, and reviewer discipline.

### Option B — Expand ADRs to contain all research and preparation material

The ADR would become both the research workspace and the final binding decision record.

### Option C — Create an independent Architecture Review Governance authority

A separate governance layer would own preparation and architecture review independently of Development Governance.

### Option D — Create a Stage 1 preparation standard under Development Governance

A dedicated guide, templates, checklist, ADPR records, quality standard, glossary, and traceability index would standardize preparation while preserving all existing authority boundaries.

## Comparative Evaluation

| Criterion | A | B | C | D |
|---|---|---|---|---|
| Preserves existing authority boundaries | Yes | Mostly | No | Yes |
| Preserves full rejected-option history | Weak | Possible but burdens ADRs | Yes | Yes |
| Separates research from binding decision | No | No | Yes | Yes |
| Compatible with Development Governance | Weak | Partial | No | Yes |
| Supports AI and human repeatability | Weak | Moderate | Moderate | Strong |
| Proportional documentation | Uncontrolled | Poor | Poor | Explicit |
| Traceability before ADR | Weak | Partial | Strong | Strong |

## Falsification

Option D would be unacceptable if it:

- claimed independent architecture authority;
- allowed ADPR approval to substitute for ADR acceptance;
- authorized implementation or merge;
- made every trivial change require a full research packet;
- permitted assumptions to replace missing evidence;
- obscured rejected options or unresolved uncertainty;
- reassigned review authority from the AI Review Protocol or Development Governance.

The merged framework explicitly prohibits those outcomes and defines proportional scope and ownership boundaries.

## Rejected Options

### Option A — Rejected

It leaves the original traceability and consistency problem unresolved.

Reconsider only if the project abandons formal architecture governance entirely.

### Option B — Rejected

It overloads ADRs with mutable research material and weakens the distinction between decision preparation and the final binding decision.

Reconsider only if the ADR format is intentionally redesigned repository-wide.

### Option C — Rejected

It creates duplicate process authority and conflicts with Development Governance ownership.

Reconsider only through a higher-authority governance amendment that explicitly reassigns ownership.

### Option D — Selected

It solves the preparation gap while preserving existing authority boundaries.

## Risks

- The framework may become performative paperwork rather than useful reasoning.
- Contributors may confuse `APPROVED` ADPR status with implementation approval.
- The architecture index may drift from actual artifact status.
- The framework may be applied too broadly to low-risk maintenance.
- Future amendments may bypass the process unless bootstrap limits are explicit.

## Risk Controls

- explicit ownership boundaries;
- proportionality rules;
- fixed readiness outcomes;
- permanent rejected-option recording;
- maintenance checks during architecture review and final validation;
- this one-time retrospective bootstrap statement;
- explicit mapping in the Canonical Architecture Map.

## Open Questions at Original Decision Time

- Whether historical ADRs should receive retrospective ADPRs.
- Whether automation should later validate index links and lifecycle-state consistency.
- Whether ADPR numbering should remain globally sequential as the repository grows.

These questions were not required to establish the initial framework and remain available for future governed decisions.

## Constitution Check

No constitutional authority was reassigned. The Constitution remains highest authority, and the framework requires explicit Constitution review before ADR readiness.

Outcome: `PASS`.

## Governance Check

The framework derives its authority from Development Governance Stage 1 and does not own architecture, ADR acceptance, implementation, review, validation, PR readiness, or merge authorization.

The post-merge audit identified mapping, index-status, bootstrap, and Draft PR lifecycle coherence gaps. Issue #133 and its remediation PR correct those gaps without changing the selected authority model.

Outcome: `PASS_WITH_REMEDIATION_RECORDED`.

## Readiness Determination

The original decision basis was sufficient to create and accept the preparation framework without inventing architectural authority or runtime behavior.

Outcome: `READY_FOR_ADR`.

No ADR was required because the framework elaborates an existing Development Governance stage and does not define runtime or analytical architecture. Its canonical placement must nevertheless be mapped by the Canonical Architecture Map.

## Recommendation

Retain the Architecture Decision Preparation framework as a mandatory Stage 1 standard for architecturally significant changes under Development Governance.

Maintain ADPRs as permanent, non-authoritative reasoning records and require all substantive future framework amendments to follow the framework prospectively.

## Traceability

- Framework implementation: PR #132
- Post-merge audit: Issue #133
- Bootstrap record: `ADPR-0001`
- ADR: `not applicable`
- Runtime impact: none
- Analytical authority impact: none
- Persistence or replay impact: none
