# Architecture Decision Quality Standard

## Purpose

This standard defines the minimum quality expected of Architecture Decision Preparation Records (ADPRs) and the architectural decisions they support.

It evaluates decision quality, not merely whether a template was completed.

## Applicability

Apply this standard to architecturally significant changes governed by `docs/ARCHITECTURE_DECISION_PREPARATION_GUIDE.md`.

## Rating Scale

Use one rating for every quality dimension:

- `EXCELLENT` — complete, precise, strongly evidenced, and resilient to challenge.
- `GOOD` — materially complete with only minor non-blocking limitations.
- `ACCEPTABLE` — sufficient for the decision, but with documented limitations.
- `NEEDS_IMPROVEMENT` — material weakness prevents reliable decision-making.
- `UNACCEPTABLE` — missing, contradictory, misleading, or incompatible with canonical authority.

An ADPR is not `READY_FOR_ADR` when any mandatory dimension is `NEEDS_IMPROVEMENT` or `UNACCEPTABLE`.

## Quality Dimensions

### 1. Problem Correctness

The problem reflects the actual architecture and is not a disguised implementation preference.

### 2. Scope Completeness

In-scope, out-of-scope, dependencies, and decision boundaries are explicit.

### 3. Canonical Consistency

The preparation accurately represents the Constitution, Project Principles, canonical architecture, accepted ADRs, and Development Governance.

### 4. Evidence Integrity

Material claims are traceable to authoritative evidence. Limitations, conflicts, staleness, and missing evidence are visible.

### 5. Assumption Discipline

Assumptions are separated from evidence, confidence is recorded, and falsification conditions are defined.

### 6. Option Completeness

All materially distinct viable options are represented. Options are normalized to comparable depth and no option is excluded because it is inconvenient or unfavored.

### 7. Comparative Fairness

The same criteria are applied consistently across options. Benefits, costs, uncertainty, and trade-offs are represented without advocacy bias.

### 8. Falsifiability

The analysis states what would invalidate each viable option and records counterexamples, boundary cases, and failed tests.

### 9. Authority and Ownership Clarity

The decision identifies canonical owners, authority boundaries, prohibited responsibility overlap, and downstream consumers.

### 10. Persistence and Replay Quality

Where applicable, identity, versioning, correction, effective time, recorded time, strict-known replay, deterministic ordering, and migration are explicitly resolved.

### 11. Evidence and Provenance Quality

Where applicable, evidence lineage, source identity, collection method, confidence, missingness, conflicts, sufficiency, and calibration are explicit.

### 12. Operational Quality

Failure behavior, observability, availability, recoverability, deployment, migration, rollback, and operational cost are addressed.

### 13. Testability and Validation

The proposed architecture can be verified deterministically. Acceptance criteria and required operational validation are derivable from the decision.

### 14. Maintainability and Extensibility

The decision minimizes hidden coupling, duplicated authority, accidental complexity, and foreseeable redesign while avoiding speculative abstraction.

### 15. Risk Quality

Material technical, governance, operational, migration, and long-term risks include likelihood, impact, mitigation, and residual uncertainty.

### 16. Traceability

The relationship among Issue or Epic, ADPR, ADR, implementation, PR, commit, validation, supersession, and release is accurate and auditable.

## Mandatory Decision Gate

A preparation may be declared `READY_FOR_ADR` only when:

- every dimension has a recorded rating and rationale;
- no mandatory dimension is below `ACCEPTABLE`;
- Constitution and Governance dimensions are at least `GOOD`;
- evidence integrity, option completeness, comparative fairness, and falsifiability are at least `ACCEPTABLE`;
- all blocking questions are resolved;
- residual limitations are explicitly carried into the proposed ADR scope.

## Prohibited Scoring Practices

Do not:

- average ratings to conceal a blocking failure;
- convert missing evidence into a neutral score;
- treat document length as quality;
- reward complexity or number of options by itself;
- allow strong implementation detail to compensate for unresolved architecture;
- allow reviewer preference to replace stated criteria;
- declare readiness from a total score while a mandatory dimension fails.

## Assessment Record

Use this table in each ADPR:

| Dimension | Rating | Evidence and rationale | Blocking limitation |
|---|---|---|---|
| Problem correctness | | | |
| Scope completeness | | | |
| Canonical consistency | | | |
| Evidence integrity | | | |
| Assumption discipline | | | |
| Option completeness | | | |
| Comparative fairness | | | |
| Falsifiability | | | |
| Authority and ownership clarity | | | |
| Persistence and replay quality | | | |
| Evidence and provenance quality | | | |
| Operational quality | | | |
| Testability and validation | | | |
| Maintainability and extensibility | | | |
| Risk quality | | | |
| Traceability | | | |

## Relationship to Review

This standard supports preparation review and later independent architecture review. It does not replace `docs/AI_REVIEW_PROTOCOL.md`, Final Validation, or `docs/MERGE_READINESS_GATE.md`.
