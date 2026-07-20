# Project Hunter AI Review Protocol

## Purpose

This document defines the mandatory protocol for independent review of Project Hunter contributions.

Its purpose is to ensure that every contribution receives an objective architectural review before merge.

This document governs review responsibilities and review reporting only.

It does not define constitutional authority, engineering principles, architecture, implementation obligations, or the development lifecycle.

---

# Scope

This protocol applies to every permanent repository contribution, including:

- source code;
- documentation;
- configuration;
- database migrations;
- automation;
- tests;
- architecture changes;
- operational tooling.

The protocol applies equally to human contributors and AI contributors.

---

# Relationship To The Development Lifecycle

Every contribution follows the lifecycle defined by:

`docs/DEVELOPMENT_GOVERNANCE.md`

This document extends only the review-related stages of that lifecycle.

It does not create an alternative lifecycle or approval process.

---

# Review Roles

A contribution may involve three independent roles.

## Implementer

The implementer produces the change.

The implementer is responsible for:

- implementing the approved scope;
- performing self-verification;
- documenting implementation changes;
- requesting review.

The implementer does not approve the implementation.

---

## Reviewer

The reviewer performs an independent technical review.

The reviewer evaluates whether the contribution remains consistent with:

- accepted architecture;
- implementation obligations;
- documentation;
- repository boundaries;
- project governance.

The reviewer evaluates the implementation directly.

The reviewer does not rely solely on implementation summaries.

---

## Verifier

The verifier confirms that required review findings have been resolved.

Verification confirms the implemented state rather than the intended state.

The verifier may be a human or an AI.

---

# Independence

Independent review requires separation between implementation and approval.

The reviewer shall evaluate the actual implementation rather than the implementer's explanation.

Independent review exists to detect:

- architectural inconsistencies;
- undocumented behavior;
- implementation mistakes;
- documentation inconsistencies;
- hidden side effects;
- incomplete verification.

---

# Review Principles

Every review shall be:

- evidence-based;
- objective;
- reproducible;
- architecture-aware;
- implementation-aware;
- proportional to the scope of the change.

Personal preference must never replace architectural requirements.

---

# Review Responsibilities

Review shall evaluate, where applicable:

- consistency with accepted architecture;
- implementation boundary compliance;
- documentation consistency;
- migration safety;
- replay safety;
- persistence safety;
- deterministic behavior;
- compatibility;
- evidence integrity;
- test adequacy.

Review evaluates implementation.

It does not redefine architecture or implementation contracts.

---

# Blocking Findings

A blocking finding is an issue that makes the contribution unsafe to merge.

Examples include:

- architectural violations;
- implementation boundary violations;
- undocumented behavior changes;
- migration risks;
- replay failures;
- evidence integrity failures;
- documentation contradictions;
- security issues;
- deterministic behavior failures.

Blocking findings must be resolved before approval.

---

# Non-blocking Findings

Recommendations improve quality without making the contribution unsafe.

Examples include:

- maintainability improvements;
- documentation enhancements;
- additional testing;
- readability improvements;
- future refactoring opportunities.

Recommendations must not delay merge after all blocking findings are resolved.

---

# Review Reports

Every review produces a review report.

The report records:

- review outcome;
- blocking findings;
- recommendations;
- required follow-up actions.

If no blocking findings exist, the report explicitly records:

> No blocking findings were identified.

---

# Approval

Approval is permitted only after:

- required review has completed;
- blocking findings have been resolved;
- required verification has completed.

Approval confirms review completion.

It does not replace repository governance or merge policy.

---

# AI Collaboration

AI systems may participate as:

- implementers;
- reviewers;
- verifiers.

Roles must remain independent whenever independent review is required.

No AI system should approve its own implementation without an independent reviewer unless the work is explicitly identified as a local draft outside the normal repository lifecycle.

---

# Long-Term Objective

Independent review exists to preserve Project Hunter's architectural integrity over long-term development.

Every accepted contribution should remain understandable, reviewable, and maintainable years after it is merged.

---

# Relationship to Other Canonical Documents

| Document | Responsibility |
|----------|----------------|
| PROJECT_CONSTITUTION | Constitutional governance |
| PROJECT_PRINCIPLES | Engineering principles |
| CANONICAL_ARCHITECTURE_MAP | Canonical document hierarchy |
| Architecture documents | System architecture |
| DEVELOPMENT_GOVERNANCE | Development lifecycle |
| HUNTER_IMPLEMENTATION_CONTRACT | Implementation obligations |
| This document | Independent review protocol |

---

# Ownership Boundary

This document owns:

- review roles;
- review responsibilities;
- independent review;
- review reporting;
- approval protocol.

This document does not own:

- constitutional governance;
- engineering principles;
- architecture;
- implementation contracts;
- development lifecycle;
- Sprint planning;
- operational procedures.

Those responsibilities remain with their respective canonical documents.