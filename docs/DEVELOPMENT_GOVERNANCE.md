# Development Governance

## Purpose

This document defines the mandatory development lifecycle for permanent changes made to Project Hunter.

Its purpose is to ensure that every accepted change is planned, implemented, verified, reviewed, documented, and validated before becoming part of the repository.

This document governs process only.

It does not define constitutional authority, engineering principles, architecture, runtime behavior, implementation contracts, or Sprint scope.

---

# Scope

This governance applies to every permanent repository change, including:

- source code;
- architecture documentation;
- repository documentation;
- configuration;
- database schema;
- persistence definitions;
- tests;
- automation;
- tooling;
- governance documents.

It applies equally to human contributors and AI contributors.

Exploration, brainstorming, research, prototypes, and conversations remain outside this process until they become repository changes.

---

# Development Lifecycle

Every permanent contribution follows the same lifecycle.

```text
Planning
    ↓
Implementation
    ↓
Local Verification
    ↓
Draft Pull Request
    ↓
CI Verification
    ↓
Architecture Review
    ↓
Review Report
    ↓
Final Validation
    ↓
Ready for Review
    ↓
Merge
```

No lifecycle stage may be skipped.

The amount of documentation scales with the size and risk of the change, but every stage always exists.

---

# Stage 1 — Planning

Planning defines the intended change before implementation begins.

Planning should identify, where applicable:

- purpose;
- scope;
- affected documents;
- affected architectural areas;
- expected implementation impact;
- expected compatibility impact;
- expected migration requirements;
- identified risks.

Implementation begins only after the intended scope is understood.

For architecturally significant changes, Stage 1 planning must follow `docs/ARCHITECTURE_DECISION_PREPARATION_GUIDE.md`. The guide standardizes problem validation, evidence assessment, option enumeration, falsification, readiness review, and the creation of Architecture Decision Preparation Records before an ADR or implementation proceeds. It elaborates this stage and does not create an independent governance or architectural authority.

---

# Stage 2 — Implementation

Implementation follows the approved plan.

Implementation must not:

- expand scope without approval;
- introduce unrelated changes;
- leave incomplete work;
- introduce temporary placeholders into permanent code or documentation;
- weaken existing guarantees without explicit approval.

If implementation requires additional scope, planning must be updated before work continues.

---

# Stage 3 — Local Verification

Local Verification confirms that the implemented change is internally coherent enough to open a Draft Pull Request and request repository CI.

Local Verification includes, where applicable:

- successful implementation;
- completed documentation;
- locally completed tests and static checks that are available in the contributor environment;
- consistent references;
- consistent naming;
- absence of placeholder content;
- consistency between implementation and documentation.

Known environment limitations must be recorded before the Draft Pull Request is opened.

A Draft Pull Request may be opened only after Local Verification is complete.

---

# Stage 4 — Draft Pull Request and CI Verification

The Draft Pull Request is the governed integration surface for repository checks, review evidence, and discussion.

While the Pull Request remains Draft:

- repository CI and required automated checks run against the exact proposed commit;
- failures return the change to implementation or verification;
- evidence and acceptance-criteria status are updated as results become available;
- the contribution must not be represented as merge-ready.

CI Verification confirms that all required automated checks pass on the exact Pull Request head. Green CI is necessary but not sufficient for later approval or merge.

---

# Stage 5 — Architecture Review

After successful Local Verification and required CI Verification, the change undergoes architectural review.

Architecture Review evaluates whether the contribution remains consistent with the project's accepted architectural decisions and canonical documents.

Any architectural issue returns the change to the appropriate earlier lifecycle stage.

Architecture Review evaluates consistency.

It does not redefine architecture.

---

# Stage 6 — Review Report

Every Architecture Review produces a review report.

The report records:

- review outcome;
- identified issues;
- resolutions;
- remaining follow-up actions, if any.

If no issues are identified, the report explicitly records:

> No issues were identified during independent review.

---

# Stage 7 — Final Validation

Final Validation confirms that the contribution has successfully completed every required lifecycle stage.

Validation records, where applicable:

- files changed;
- Local Verification completed;
- CI Verification completed;
- Architecture Review completed;
- Review Report completed;
- outstanding issues;
- overall readiness.

Only after successful Final Validation may the contribution be declared:

**READY FOR REVIEW**

---

# Pull Request Governance

A Draft Pull Request may be opened only after implementation and Local Verification are complete.

A Pull Request may leave Draft status only after:

- required CI Verification has completed successfully;
- Architecture Review has completed;
- Review Report has been recorded;
- Final Validation has completed.

A Pull Request marked **Ready for Review** must not contain unresolved blocking findings.

Opening a Draft Pull Request does not approve implementation, satisfy Architecture Review, establish merge readiness, or authorize merge.

---

# Merge Readiness

Green automated checks and the absence of unresolved blocking findings are necessary conditions for merge. Neither is sufficient on its own.

A Pull Request must not be merged while any required acceptance criterion or required operational validation is missing, `FAIL`, or `BLOCKED`. This applies regardless of automated check status and regardless of how much review discussion has occurred.

The implementer declares one of the following states on a Pull Request:

- **Ready for Review** — required verification and self-assessment are complete, per Stage 7.
- **Changes Required** — implementation, evidence, or documentation remains incomplete.
- **Blocked** — completion depends on an unavailable environment, provider, credential, or external condition.

The implementer does not declare a Pull Request **Approved**. Approval is an outcome of independent review, governed by `docs/AI_REVIEW_PROTOCOL.md`, and is recorded only by the reviewer after required review and verification have completed.

A Pull Request must not be merged while any unresolved blocking finding remains open, consistent with Stage 7 and with `docs/AI_REVIEW_PROTOCOL.md`'s Blocking Findings section. Non-blocking recommendations, and findings that have already been resolved, do not prevent merge.

`docs/MERGE_READINESS_GATE.md` is the implementation guide for this rule: it defines the required review dimensions, the acceptance-criteria matrix format, the evidence package a Pull Request must include, and the pull request template that operationalizes the rule stated here. It does not define an independent governance authority and must not be read as one.

---

# Proportionality

Every lifecycle stage is mandatory.

Only the depth of documentation scales with the complexity and risk of the change.

Smaller changes require less documentation.

Architecturally significant changes require more comprehensive documentation.

The lifecycle itself never changes.

---

# Ambiguity

If implementation requires decisions outside the approved scope, work pauses until clarification is obtained.

Architectural uncertainty is resolved through the project's governance process rather than individual assumption.

---

# Amendment

Changes to this document follow the same lifecycle defined within this document.

No amendment may conflict with:

- `PROJECT_CONSTITUTION.md`
- `PROJECT_PRINCIPLES.md`

---

# Relationship to Other Canonical Documents

| Document | Responsibility |
|----------|----------------|
| PROJECT_CONSTITUTION | Constitutional governance |
| PROJECT_PRINCIPLES | Engineering principles |
| CANONICAL_ARCHITECTURE_MAP | Document authority hierarchy |
| Architecture documents | System architecture |
| ADRs | Architectural decisions |
| ARCHITECTURE_DECISION_PREPARATION_GUIDE | Stage 1 preparation standard for architecturally significant changes |
| HUNTER_IMPLEMENTATION_CONTRACT | Implementation obligations |
| This document | Development lifecycle |
| MERGE_READINESS_GATE | Implementation guide for the Merge Readiness rule owned by this document |

---

# Ownership Boundary

This document owns:

- development lifecycle;
- process stages;
- review workflow;
- validation workflow;
- pull request readiness;
- process governance.

This document does not own:

- constitutional rules;
- engineering principles;
- architecture;
- runtime behavior;
- implementation requirements;
- Sprint planning;
- operational procedures.

Those responsibilities remain with their respective canonical documents.
