# Architecture Decision Preparation

## Metadata

- ADPR ID:
- Title:
- Status: `PROPOSED | IN_RESEARCH | READY_FOR_REVIEW | APPROVED | IMPLEMENTED | VALIDATED | SUPERSEDED | ARCHIVED`
- Author:
- Reviewers:
- Created:
- Last updated:
- Related Epic:
- Related Issue:
- Planned ADR:

## 1. Problem Statement

Describe the architectural problem precisely.

### Current condition

### Desired condition

### Decision required

### In scope

### Out of scope

## 2. Problem Validation

Explain why this is a real architectural problem and why existing canonical documents or implementation contracts do not already resolve it.

## 3. Motivation

Explain why the problem matters and the consequences of not resolving it.

## 4. Existing Architecture

Describe relevant existing authority, ownership, boundaries, persistence, replay, identity, evidence, correction, and operational behavior.

## 5. Constraints

### Constitutional

### Governance

### Technical

### Operational

### Persistence and migration

### Replay and historical reconstruction

### Compatibility

### Security and privacy

### Performance and scalability

### Evidence and provenance

## 6. Evidence Inventory

| Evidence | Authority/source | Relevance | Quality and limitations | Supports or challenges |
|---|---|---|---|---|
| | | | | |

Record conflicting, missing, stale, or non-reproducible evidence explicitly.

## 7. Assumptions

| Assumption | Why required | Confidence | Falsification condition | Consequence if false |
|---|---|---|---|---|
| | | | | |

## 8. Architectural Dimensions

Identify every material dimension, including where applicable:

- authority;
- ownership;
- component boundaries;
- identity and representation;
- persistence;
- effective and recorded time;
- versioning and correction;
- strict-known replay;
- provenance;
- missingness and confidence;
- sufficiency and calibration;
- security;
- scalability;
- performance;
- testability;
- extensibility;
- migration and rollback;
- operability and observability.

## 9. Candidate Options

Do not rank options during initial enumeration. Describe each materially distinct option at comparable depth.

### Option 1 — [Name]

- Description:
- Authority and ownership:
- Persistence and replay:
- Evidence and provenance:
- Compatibility:
- Advantages:
- Disadvantages:
- Failure modes:
- Migration implications:
- Reversibility:
- Open dependencies:

### Option 2 — [Name]

Repeat the same fields.

## 10. Comparative Evaluation

| Criterion | Option 1 | Option 2 | Additional options |
|---|---|---|---|
| Correctness | | | |
| Constitutional compliance | | | |
| Governance compliance | | | |
| Authority clarity | | | |
| Replayability | | | |
| Evidence integrity | | | |
| Maintainability | | | |
| Scalability | | | |
| Operational complexity | | | |
| Migration risk | | | |
| Implementation effort | | | |
| Reversibility | | | |
| Long-term extensibility | | | |

State evaluation evidence and uncertainty; do not hide ties or unresolved conflicts.

## 11. Falsification

For every viable option, document:

- what evidence would invalidate it;
- boundary and adversarial cases tested;
- counterexamples considered;
- unresolved failure conditions;
- whether the option survived falsification.

## 12. Rejected Options

For each rejected option record:

- reason for rejection;
- supporting evidence;
- violated constraint or inferior trade-off;
- conditions under which it should be reconsidered.

## 13. Risks

### Technical

### Operational

### Governance

### Migration

### Long-term

For each material risk, include likelihood, impact, mitigation, and residual uncertainty.

## 14. Open Questions

List unresolved questions and identify which ones block readiness.

## 15. Constitution Check

Record exact relevant constitutional clauses and explain compliance or conflict.

## 16. Governance Check

Record relevant Development Governance, canonical architecture, ADR, and implementation-contract requirements and explain compliance or conflict.

## 17. Architecture Readiness

- Outcome: `READY | NEEDS_REVISION | BLOCKED`
- Rationale:
- Missing evidence:
- Unresolved conflicts:

## 18. ADR Readiness

- Outcome: `READY_FOR_ADR | NEEDS_REVISION | BLOCKED | NOT_AN_ARCHITECTURE_DECISION`
- Proposed ADR title:
- Proposed ADR scope:
- Decisions the ADR must fix:
- Matters the ADR must leave open:

## 19. Recommendation

State a recommendation only after option enumeration, comparison, and falsification are complete. Explain the evidence and trade-offs supporting it.

## 20. Traceability

- Epic:
- Issue:
- Preparation record:
- Checklist review:
- ADR:
- Implementation plan:
- PR:
- Commit:
- Release:
- Supersedes:
- Superseded by:
