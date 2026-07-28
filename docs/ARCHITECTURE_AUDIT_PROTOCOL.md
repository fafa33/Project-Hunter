# Architecture Audit Protocol

## Status

- Status: Accepted
- Version: 2.0
- Authority: `docs/DEVELOPMENT_GOVERNANCE.md`
- Scope: Independent review of Architecture Decision Preparation Records and architecture-decision readiness

## Purpose

This document defines the canonical protocol for independent architecture audits in Project Hunter.

The objective of an architecture audit is to determine whether an architecture preparation record is sufficiently complete, internally consistent, evidenced, and materially reliable for architectural decision-making.

An architecture audit is not an unrestricted defect-hunting exercise. The presence of a defect does not by itself block ADR readiness. A finding blocks progression only when the auditor demonstrates that the defect can materially distort, invalidate, or prevent the architectural decision.

This protocol governs audit method, finding classification, materiality assessment, verdicts, re-audit scope, and audit reporting. It does not select an architectural option, approve an ADR, authorize implementation, or replace canonical architecture authority.

## Applicability

This protocol is mandatory for independent review of:

- Architecture Decision Preparation Records (ADPRs);
- architecture-decision readiness assessments;
- substantive revisions to architecture preparation records;
- architecture option-set completeness reviews;
- architecture audits required by the Development Governance lifecycle.

Implementation reviews remain governed by `docs/AI_REVIEW_PROTOCOL.md`. Architecture preparation quality remains governed by `docs/ARCHITECTURE_DECISION_QUALITY_STANDARD.md`.

## Audit Objective

Every audit shall answer one primary question:

> Is the preparation record reliable enough to support an architectural decision without inventing missing evidence, hiding a material uncertainty, misrepresenting authority, or excluding a materially distinct viable option?

The auditor shall distinguish:

- defects that affect only presentation;
- defects that reduce documentation quality but do not alter the decision basis;
- defects that can cause an unreliable architectural decision;
- defects showing that the architecture problem itself is not ready for decision preparation.

## Audit Principles

Every architecture audit shall be:

- evidence-based;
- independent;
- reproducible;
- proportional to decision risk;
- architecture-aware;
- neutral among candidate options;
- explicit about uncertainty;
- focused on decision materiality rather than defect count.

Auditors must not:

- treat every defect as blocking;
- use document length as a proxy for completeness;
- require cosmetic normalization that cannot affect the decision;
- introduce new requirements not grounded in canonical documents or the declared audit scope;
- rank or recommend architectural options unless recommendation is explicitly authorized;
- use personal preference as a substitute for evidence or governance.

## Audit Workflow

Every audit follows four ordered stages.

### Stage 1 — Evidence Collection

The auditor identifies the exact reviewed revision and examines the applicable preparation record, canonical documents, accepted ADRs, referenced evidence, and relevant repository state.

Claims about a missing section, contradictory authority, unsupported citation, absent option, replay weakness, provenance weakness, or governance conflict must be verified against the actual source material.

### Stage 2 — Finding Detection

The auditor records all substantiated findings without assigning a final verdict prematurely.

Each finding must identify:

- a stable finding ID;
- exact evidence;
- exact location;
- affected audit dimension;
- concise description of the defect.

### Stage 3 — Materiality Assessment

Each finding must be assessed for its effect on the architectural decision.

The auditor must answer:

1. Is the finding factually substantiated?
2. Which decision dimension is affected?
3. What incorrect, incomplete, or unreliable architectural decision could result if the finding is ignored?
4. Is the effect editorial, quality-affecting, decision-blocking, or fundamental?
5. Does the finding block ADR readiness?

A finding that cannot identify a plausible decision consequence cannot be classified as decision-blocking.

### Stage 4 — Final Verdict

The verdict is derived from the classified findings according to the mandatory verdict rules in this document. The verdict must not be chosen before materiality assessment is complete.

## Required Finding Record

Every finding must use the following fields:

| Field | Requirement |
|---|---|
| Finding ID | Stable identifier such as `F-001` |
| Evidence | Direct, verifiable evidence supporting the finding |
| Location | File, section, line, table, option, or diagram affected |
| Category | Affected decision or documentation dimension |
| Severity | Class A, B, C, or D |
| Decision impact | Specific effect on architectural decision quality |
| Consequence if ignored | Plausible incorrect or unreliable decision outcome |
| Required action | Minimum action needed to remove the material defect |
| Blocks ADR | `YES` or `NO` |

A finding is incomplete when any mandatory field is absent.

## Severity Classes

### Class A — Editorial

A Class A finding affects presentation, readability, citation formatting, naming, labels, or diagram clarity without altering the represented architecture or decision basis.

Typical examples:

- typographical errors;
- a mislabeled heading where the underlying analysis is correct;
- a wrong cross-reference that does not misstate substantive authority;
- diagram omissions already made explicit in controlling prose;
- inconsistent formatting.

Class A findings do not block ADR readiness.

### Class B — Documentation Quality

A Class B finding reduces clarity, comparability, completeness of explanation, or auditability, but the preparation still contains enough correct information to support the decision reliably.

Typical examples:

- implementation impact discussed but not normalized under a separate label;
- an assumption insufficiently highlighted but already represented in the analysis;
- a non-material edge case omitted from examples;
- uneven explanatory depth that does not favor or conceal an option;
- a correct boundary expressed indirectly rather than explicitly.

Class B findings do not normally block ADR readiness. They may require correction before ADR approval when their cumulative effect makes the decision basis difficult to audit.

### Class C — Decision Blocking

A Class C finding can materially distort, invalidate, or prevent the architectural decision.

Typical examples:

- omission of a materially distinct viable option;
- incorrect or ambiguous canonical authority;
- hidden responsibility transfer;
- analysis that falsely claims deterministic replay;
- provenance or evidence gaps that change option viability;
- inconsistent evaluation criteria that favor an option;
- unresolved contradiction affecting constraints or option eligibility;
- absent migration or implementation consequences that could make an option non-viable;
- a taxonomy gap that causes materially different cases to be treated as equivalent.

A Class C finding blocks ADR readiness. The auditor must explicitly identify the wrong or unreliable decision that could result if the finding remains unresolved.

### Class D — Fundamental Architecture Gap

A Class D finding shows that the architecture problem is not ready for reliable option evaluation.

Typical examples:

- the problem statement is materially false or undefined;
- the decision scope cannot be bounded;
- controlling authority is unresolved;
- required evidence does not exist and cannot yet be obtained;
- the option space cannot be meaningfully enumerated;
- the preparation attempts to decide multiple incompatible architecture problems as one decision;
- the proposed decision conflicts with constitutional authority and no valid resolution path exists.

A Class D finding means architecture preparation cannot proceed to ADR until the underlying problem or evidence gap is resolved.

## Materiality Rules

### Decision-Consequence Requirement

A Class C or D finding is valid only when the auditor states:

- the specific decision dimension affected;
- the architectural conclusion that could become wrong, incomplete, or unsupported;
- why existing correct content does not already neutralize the defect.

General statements such as "the document is incomplete," "quality is insufficient," or "this requirement is missing" are not sufficient to establish materiality.

### Existing-Substance Rule

Missing labels, headings, duplicated wording, table rows, or diagrams do not become Class C merely because a checklist names them. The auditor must evaluate whether the required substance is actually absent or only expressed elsewhere.

### Cumulative-Quality Rule

Multiple Class B findings may produce `CONDITIONAL_ADR_READY` only when their cumulative effect makes the preparation materially difficult to interpret, compare, or audit. The auditor must explain the cumulative effect. Defect count alone is insufficient.

### No Severity Inflation

A citation or attribution defect is:

- Class A when it is only a cross-reference error and the substantive authority is correctly represented elsewhere;
- Class B when it weakens auditability or creates local ambiguity without changing the decision basis;
- Class C only when it materially misstates binding authority, constraints, eligibility, or compatibility and could therefore alter the decision.

## Audit Dimensions

Audits shall examine, where applicable:

- problem correctness;
- scope completeness;
- canonical consistency;
- evidence integrity;
- assumption discipline;
- option completeness;
- option normalization;
- comparative fairness;
- falsifiability;
- authority and ownership;
- persistence and replay;
- evidence and provenance;
- implementation impact;
- migration impact;
- operational impact;
- testability and validation;
- maintainability and extensibility;
- governance compatibility;
- traceability;
- unresolved risk and uncertainty.

Not every dimension must have an independent heading in the preparation record. The audit evaluates substantive coverage unless a governing template explicitly requires a field.

## Verdicts

Only the following verdicts are permitted.

### `READY_FOR_ADR`

Use when no material deficiencies remain. Class A findings may exist if they are trivial and recorded.

### `READY_FOR_ADR_WITH_MINOR_FINDINGS`

Use when only Class A and non-cumulative Class B findings exist. These findings must be tracked but do not prevent ADR drafting.

### `CONDITIONAL_ADR_READY`

Use when no Class C or D finding exists, but cumulative Class B findings must be resolved before the ADR is approved or merged. ADR drafting may proceed, but the listed conditions remain mandatory.

### `ADPR_REVISION_REQUIRED`

Use when at least one unresolved Class C finding exists.

The report must identify every Class C finding that blocks readiness and state the decision consequence of leaving it unresolved.

### `ARCHITECTURE_NOT_READY`

Use when at least one unresolved Class D finding exists.

The report must identify the underlying problem, evidence, authority, or scope gap that prevents reliable architecture preparation.

## Verdict Derivation

| Highest unresolved severity | Additional condition | Verdict |
|---|---|---|
| None or trivial A | No material limitation | `READY_FOR_ADR` |
| A or B | B findings are not cumulatively material | `READY_FOR_ADR_WITH_MINOR_FINDINGS` |
| B | Cumulative quality limitations require correction before approval | `CONDITIONAL_ADR_READY` |
| C | At least one decision-blocking finding | `ADPR_REVISION_REQUIRED` |
| D | At least one fundamental gap | `ARCHITECTURE_NOT_READY` |

An auditor must not issue `ADPR_REVISION_REQUIRED` or `ARCHITECTURE_NOT_READY` from a raw PASS/FAIL count.

## Mandatory Blocking Rule

No audit may issue `ADPR_REVISION_REQUIRED` unless each blocking finding includes:

1. direct evidence;
2. Class C classification;
3. affected decision dimension;
4. the specific unreliable decision that could result;
5. the minimum correction required.

No audit may issue `ARCHITECTURE_NOT_READY` unless each fundamental finding includes the same information and explains why ADPR revision alone is insufficient.

A purported blocking finding missing any of these elements is downgraded to non-blocking until materiality is demonstrated.

## Re-Audit Protocol

### Targeted Re-Audit

After a revision made solely to address prior findings, the next audit shall normally be targeted to:

- the previously blocking findings;
- the changed sections;
- regressions or contradictions directly introduced by those changes;
- unchanged content only where necessary to verify the correction.

The auditor must not restart an unlimited full-document defect search merely because a revision occurred.

### Full Re-Audit

A new full audit is required when:

- decision scope changes;
- a new decision axis is introduced;
- canonical authority changes;
- materially new evidence changes option viability;
- options are added, removed, merged, or redefined materially;
- the prior audit is shown to have used an invalid scope or incorrect canonical baseline.

### New Findings During Targeted Re-Audit

A new finding outside the targeted scope may be raised only when it is:

- directly caused by the revision;
- a previously hidden Class C or D defect discovered while validating the correction;
- supported by evidence and full materiality analysis.

New Class A or B observations outside the targeted scope are recorded as follow-up notes and do not restart the revision cycle.

## Audit Completion Requirements

Every completed audit must contain:

1. reviewed artifact and exact revision;
2. audit scope;
3. evidence sources examined;
4. dimension-by-dimension result;
5. complete finding records;
6. final findings matrix;
7. verdict derivation;
8. final verdict;
9. conditions or required corrections, if any.

The final findings matrix is mandatory:

| Finding | Class | Decision impact | Consequence if ignored | Blocks ADR | Evidence |
|---|---|---|---|---|---|
| F-001 | A | None | None | NO | Exact source |
| F-002 | B | Quality only | Reduced auditability | NO | Exact source |
| F-003 | C | Option viability | Could select a non-viable option | YES | Exact source |

An audit without this matrix is incomplete and cannot establish readiness or block readiness.

## Standard Audit Template

Auditors shall use `docs/ARCHITECTURE_AUDIT_TEMPLATE.md`.

The template operationalizes this protocol but does not create independent authority. When the template and this protocol conflict, this protocol controls.

## Document Precedence

For architecture preparation audits, the applicable precedence is:

1. `docs/PROJECT_CONSTITUTION.md`;
2. `docs/CANONICAL_ARCHITECTURE_MAP.md` and other controlling canonical authority;
3. accepted ADRs;
4. `docs/DEVELOPMENT_GOVERNANCE.md`;
5. this protocol for audit classification, materiality, and verdicts;
6. `docs/ARCHITECTURE_DECISION_PREPARATION_GUIDE.md` for preparation lifecycle and required outputs;
7. `docs/ARCHITECTURE_DECISION_QUALITY_STANDARD.md` for quality dimensions and ratings;
8. `docs/ARCHITECTURE_AUDIT_TEMPLATE.md` for report structure;
9. local issue, PR, or prompt instructions that do not conflict with higher authority.

`docs/AI_REVIEW_PROTOCOL.md` governs implementation review and does not override this protocol's architecture-readiness verdict rules.

## Relationship to ADPR Self-Assessment

An ADPR self-assessment is preparation evidence, not an independent verdict. The auditor must evaluate the actual record and may confirm or reject the self-assessment.

A difference between self-assessment and audit does not by itself establish a blocking finding. The underlying substantive defect and materiality must be shown.

## Reference Reclassification Example

Applying this protocol to an audit that identifies:

- no separately labeled implementation-impact field while implementation effects are already substantively discussed;
- a locally incorrect ADR attribution while the controlling constraint is correctly represented elsewhere;
- an omitted boundary in a diagram that is correctly enforced in prose;
- an unexamined hierarchical variant that is not shown to be materially distinct;

would not automatically justify `ADPR_REVISION_REQUIRED`.

The auditor must separately establish whether any item can alter option eligibility, authority, comparative evaluation, or the architectural decision. Without that materiality showing, the appropriate verdict is normally `READY_FOR_ADR_WITH_MINOR_FINDINGS` or, when cumulative clarity problems require correction before approval, `CONDITIONAL_ADR_READY`.

This example does not predetermine the classification of any specific ADPR. Actual classification remains evidence-dependent.

## Governance Integration

This protocol elaborates the Architecture Review and Review Report stages of `docs/DEVELOPMENT_GOVERNANCE.md` for architecture-preparation artifacts.

It does not:

- replace the development lifecycle;
- define constitutional authority;
- define architecture;
- accept an ADR;
- authorize implementation;
- determine pull-request merge readiness for implementation changes;
- permit unresolved Class C or D findings to be bypassed.

## Ownership Boundary

This document owns:

- architecture-audit workflow;
- finding classification;
- materiality assessment;
- architecture-readiness verdicts;
- architecture re-audit scope;
- architecture-audit reporting requirements.

This document does not own:

- constitutional governance;
- architectural decisions;
- preparation authorship;
- implementation review;
- final ADR acceptance;
- implementation authorization;
- pull-request merge authority.

## Amendment Policy

Changes to this protocol follow `docs/DEVELOPMENT_GOVERNANCE.md`.

Substantive amendments that alter architecture governance authority, readiness semantics, or canonical document precedence require the architecture decision preparation process defined by `docs/ARCHITECTURE_DECISION_PREPARATION_GUIDE.md`.