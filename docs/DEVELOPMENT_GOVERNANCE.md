# Development Governance

## Purpose

This document defines the mandatory development lifecycle for every permanent change made to Project Hunter.

Its purpose is to ensure that every contribution is planned, implemented, verified, reviewed, documented, and validated before becoming part of the project.

This document defines process only.

Engineering principles, constitutional rules, architecture, runtime behavior, and implementation contracts are defined by their respective canonical documents.

---

## Scope

This governance applies to every permanent repository change, including:

- source code;
- architecture;
- documentation;
- configuration;
- database schema;
- tests;
- automation;
- tooling;
- this document itself.

It applies equally to every contributor, whether human or AI.

Conversation, brainstorming, research, and exploratory discussions are outside the scope of this document until they become committed repository changes.

---

# Development Lifecycle

Every permanent contribution must complete the following lifecycle.

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

No stage may be skipped.

The depth of documentation may scale with the risk of the change, but every stage always exists.

---

# Stage 1 — Planning

Before implementation begins, the contributor shall define:

- purpose of the change;
- affected architectural areas;
- affected runtime behavior;
- persistence impact;
- replay impact;
- compatibility impact;
- implementation scope;
- identified risks.

Planning defines what will change before implementation begins.

---

# Stage 2 — Implementation

Implementation shall follow the approved planning.

Implementation must not:

- silently expand scope;
- introduce unrelated refactoring;
- weaken existing guarantees;
- introduce temporary placeholders;
- leave incomplete work.

If implementation requires additional scope, planning must be updated before continuing.

---

# Stage 3 — Verification

Every change must be verified before review.

Verification confirms that:

- implementation is complete;
- documentation is complete;
- references are valid;
- naming is consistent;
- architecture remains consistent;
- no placeholder content exists;
- implementation matches documentation;
- documentation matches implementation;
- no documented guarantee has been weakened.

Verification failures block further progress.

---

# Stage 4 — Architecture Review

After successful verification, the contributor performs an independent architectural review.

The review evaluates whether the change:

- preserves architectural boundaries;
- maintains deterministic behavior;
- preserves evidence integrity;
- maintains replay correctness;
- preserves explainability;
- avoids unnecessary coupling;
- remains maintainable;
- remains extensible;
- remains consistent with all canonical documents.

Any identified issue returns the change to Implementation.

---

# Stage 5 — Review Report

Every review produces a review report.

Each identified issue records:

- issue;
- root cause;
- resolution;
- architectural impact;
- runtime impact;
- persistence impact;
- replay impact;
- compatibility impact;
- risk;
- verification of the fix.

If no issues are identified, the report explicitly states:

> No issues were identified during independent review.

---

# Stage 6 — Final Validation

Before review or merge, the contributor records:

- files changed;
- architecture impact;
- runtime impact;
- persistence impact;
- replay impact;
- compatibility impact;
- risk assessment;
- verification completed;
- architecture review completed;
- review report completed.

Only after every required item is complete may the contribution state:

**READY FOR REVIEW**

---

# Pull Request Governance

A pull request may only be opened after Final Validation.

A pull request may only leave Draft status after:

- verification has passed;
- architecture review has completed;
- review report has completed;
- final validation has completed.

No unresolved review finding may remain when a pull request is marked Ready for Review.

---

# Proportionality

Every development stage is mandatory.

Only the amount of documentation scales with the size and risk of the change.

Small changes require shorter records.

Large architectural changes require more comprehensive records.

Process steps are never omitted.

---

# Ambiguity

When implementation requires architectural judgment beyond the approved scope, work pauses until clarification is obtained.

Ambiguity is resolved through clarification, not assumption.

---

# Amendment

Changes to this document follow the same development lifecycle defined within this document.

No amendment may reduce guarantees established by:

- PROJECT_CONSTITUTION.md
- PROJECT_PRINCIPLES.md

---

## Relationship to Other Canonical Documents

| Document | Responsibility |
|----------|----------------|
| PROJECT_CONSTITUTION | Constitutional governance |
| PROJECT_PRINCIPLES | Engineering principles |
| HUNTER_IMPLEMENTATION_CONTRACT | Implementation obligations |
| ADRs | Architectural decisions |
| Architecture documents | System design |
| This document | Development process |

This document governs how Project Hunter evolves.

It does not redefine architecture, implementation, engineering principles, or constitutional authority.