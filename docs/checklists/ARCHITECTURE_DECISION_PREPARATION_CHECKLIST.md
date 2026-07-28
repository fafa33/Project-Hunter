# Architecture Decision Preparation Checklist

## Metadata

- ADPR ID:
- Title:
- Reviewer:
- Review date:
- Reviewed revision or commit:

## A. Applicability

- [ ] The change is architecturally significant under `ARCHITECTURE_DECISION_PREPARATION_GUIDE.md`.
- [ ] The preparation does not duplicate a decision already fixed by canonical authority.
- [ ] The required decision and its boundaries are explicit.

## B. Problem and Scope

- [ ] The current condition is accurately described.
- [ ] The desired condition is explicit.
- [ ] In-scope and out-of-scope matters are separated.
- [ ] The motivation and consequences of inaction are documented.
- [ ] The problem has been validated as architectural rather than merely implementational.

## C. Existing Authority and Architecture

- [ ] Relevant constitutional documents were inspected.
- [ ] Relevant canonical architecture documents were inspected.
- [ ] Relevant accepted ADRs were inspected.
- [ ] Existing authority, ownership, persistence, replay, and evidence contracts are recorded.
- [ ] No current guarantee is silently weakened or redefined.

## D. Constraints

- [ ] Constitutional and governance constraints are complete.
- [ ] Technical and operational constraints are complete.
- [ ] Persistence, migration, and compatibility constraints are complete.
- [ ] Replay and historical reconstruction constraints are complete.
- [ ] Security, performance, and scalability constraints are addressed where applicable.
- [ ] Evidence and provenance constraints are explicit.

## E. Evidence

- [ ] Evidence is distinguished from assumptions and recommendations.
- [ ] Evidence authority, relevance, quality, and limitations are recorded.
- [ ] Conflicting evidence remains visible.
- [ ] Missing or unavailable evidence is explicit.
- [ ] Material claims can be traced to sources, implementation, tests, or observed results.

## F. Assumptions

- [ ] Every material assumption is listed separately.
- [ ] Confidence and falsification conditions are recorded.
- [ ] Consequences of false assumptions are documented.
- [ ] No assumption substitutes for a required canonical fact or unavailable evidence.

## G. Architectural Dimensions

- [ ] Authority and ownership are addressed.
- [ ] Component and responsibility boundaries are addressed.
- [ ] Identity, representation, versioning, and correction are addressed where applicable.
- [ ] Persistence and strict-known replay are addressed where applicable.
- [ ] Provenance, missingness, confidence, sufficiency, and calibration are addressed where applicable.
- [ ] Testability, extensibility, migration, rollback, observability, and operability are addressed where applicable.

## H. Options

- [ ] All materially distinct viable options are enumerated.
- [ ] Options are described at comparable depth.
- [ ] Composite or hybrid variants are explicit.
- [ ] No option was excluded merely because it is not preferred.
- [ ] Option descriptions do not contain hidden recommendations.

## I. Comparative Evaluation

- [ ] The same criteria are applied to every option.
- [ ] Correctness and constitutional compliance are evaluated.
- [ ] Governance, authority, replay, and evidence integrity are evaluated.
- [ ] Complexity, maintainability, scalability, migration, and operational cost are evaluated.
- [ ] Trade-offs and uncertainty are explicit.
- [ ] Ties or unresolved conflicts are not concealed.

## J. Falsification

- [ ] Every viable option has documented invalidation conditions.
- [ ] Boundary, adversarial, and failure cases were considered.
- [ ] The leading option was tested against counterexamples.
- [ ] Failed falsification tests are recorded rather than omitted.

## K. Rejected Options

- [ ] Every rejected option has a specific reason.
- [ ] Rejection is supported by evidence or an identified constraint.
- [ ] Reconsideration conditions are documented.
- [ ] Rejected options remain traceable in the permanent record.

## L. Risks and Open Questions

- [ ] Technical, operational, governance, migration, and long-term risks are recorded.
- [ ] Likelihood, impact, mitigation, and residual uncertainty are stated for material risks.
- [ ] Open questions are listed.
- [ ] Readiness-blocking questions are distinguished from non-blocking questions.

## M. Compliance

- [ ] No unresolved Constitution conflict exists.
- [ ] No unresolved Development Governance conflict exists.
- [ ] No unresolved canonical architecture or accepted ADR conflict exists.
- [ ] Ownership boundaries of this preparation framework are preserved.

## N. Readiness and Traceability

- [ ] Architecture readiness outcome is justified.
- [ ] ADR readiness outcome is one of the allowed states.
- [ ] Proposed ADR scope is precise.
- [ ] Decisions the ADR must fix are explicit.
- [ ] Matters that remain open are explicit.
- [ ] Issue, Epic, ADPR, ADR, PR, commit, and release links are recorded where they exist.
- [ ] Missing links are marked not applicable or not yet created; none are fabricated.

## Final Review Outcome

Select exactly one:

- [ ] `READY_FOR_ADR`
- [ ] `NEEDS_REVISION`
- [ ] `BLOCKED`
- [ ] `NOT_AN_ARCHITECTURE_DECISION`

### Blocking findings

### Non-blocking observations

### Required revisions

### Reviewer rationale
