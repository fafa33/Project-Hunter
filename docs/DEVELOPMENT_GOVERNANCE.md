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
Verification
    ↓
Architecture Review
    ↓
Review Report
    ↓
Final Validation
    ↓
Ready for Review
    ↓
Pull Request
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

# Stage 3 — Verification

Verification confirms that the implemented change is internally complete.

Verification includes, where applicable:

- successful implementation;
- completed documentation;
- completed tests;
- consistent references;
- consistent naming;
- absence of placeholder content;
- consistency between implementation and documentation.

Verification failures prevent further progression through the lifecycle.

---

# Stage 4 — Architecture Review

After successful verification, the change undergoes architectural review.

Architecture Review evaluates whether the contribution remains consistent with the project's accepted architectural decisions and canonical documents.

Any architectural issue returns the change to the appropriate earlier lifecycle stage.

Architecture Review evaluates consistency.

It does not redefine architecture.

---

# Stage 5 — Review Report

Every Architecture Review produces a review report.

The report records:

- review outcome;
- identified issues;
- resolutions;
- remaining follow-up actions, if any.

If no issues are identified, the report explicitly records:

> No issues were identified during independent review.

---

# Stage 6 — Final Validation

Final Validation confirms that the contribution has successfully completed every required lifecycle stage.

Validation records, where applicable:

- files changed;
- verification completed;
- architecture review completed;
- review report completed;
- outstanding issues;
- overall readiness.

Only after successful Final Validation may the contribution be declared:

**READY FOR REVIEW**

---

# Pull Request Governance

A Pull Request may be opened only after Final Validation.

A Pull Request may leave Draft status only after:

- Verification has completed;
- Architecture Review has completed;
- Review Report has been recorded;
- Final Validation has completed.

A Pull Request marked **Ready for Review** must not contain unresolved blocking findings.

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
| HUNTER_IMPLEMENTATION_CONTRACT | Implementation obligations |
| This document | Development lifecycle |

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