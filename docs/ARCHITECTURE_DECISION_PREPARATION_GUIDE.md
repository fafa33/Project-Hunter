# Architecture Decision Preparation Guide

## Status

- Status: Accepted
- Version: 1.1
- Authority: `docs/DEVELOPMENT_GOVERNANCE.md`
- Scope: Architecturally significant changes

## Purpose

This document standardizes preparation of major architectural decisions before implementation and before creation of an Architecture Decision Record (ADR).

It provides an evidence-based, falsifiable, repeatable, auditable, and traceable process for turning an architectural problem into ADR-ready input.

This document governs preparation only. It does not define architecture, approve decisions, replace ADRs, perform review, authorize implementation, determine merge readiness, or own validation.

Independent audit of preparation records is governed by `docs/ARCHITECTURE_AUDIT_PROTOCOL.md`.

## Scope

This process is mandatory when a proposed change creates or materially changes one or more of the following:

- canonical authority or ownership;
- persistence, correction, versioning, or migration semantics;
- strict-known replay or historical reconstruction;
- evidence, provenance, sufficiency, or calibration contracts;
- an engine, service, subsystem, or cross-component boundary;
- compatibility guarantees or irreversible architectural commitments;
- a new ADR or an amendment that changes the substance of an accepted ADR.

It is normally not required for isolated bug fixes, mechanical refactors, test-only changes, formatting, dependency maintenance, CI maintenance, or documentation corrections that do not change architectural meaning.

When significance is uncertain, the change is treated as architecturally significant until the uncertainty is resolved.

## Guiding Principles

1. Problem before solution.
2. Evidence before opinion.
3. Assumptions must never be presented as evidence.
4. Options before recommendation.
5. Rejected options remain part of the permanent reasoning record.
6. Falsification before approval.
7. Constitution and canonical governance before local preference.
8. ADR before implementation when an ADR is required.
9. Missing evidence remains explicit; it is not replaced with convenience assumptions.
10. Preparation depth is proportional to risk, but no required lifecycle stage is silently skipped.
11. Readiness depends on material decision quality, not defect count or document formatting alone.

## Decision Preparation Lifecycle

### 1. Problem Definition

Define the current condition, desired condition, affected scope, exclusions, and the exact decision that must be made.

### 2. Problem Validation

Demonstrate that the problem is real, architecturally relevant, and not already resolved by an accepted canonical document or existing implementation contract.

### 3. Constraint Discovery

Identify technical, governance, operational, compatibility, security, persistence, replay, migration, performance, and evidence constraints.

### 4. Evidence Collection

Collect relevant canonical documents, ADRs, issues, implementation evidence, tests, audits, prototypes, benchmarks, operational observations, and external primary sources where applicable.

### 5. Evidence Quality Assessment

Classify evidence by authority, relevance, completeness, recency, reproducibility, and known limitations. Conflicting evidence must remain visible.

### 6. Architectural Dimension Discovery

Identify every material dimension of the decision, including authority, ownership, boundaries, persistence, identity, versioning, correction, replay, provenance, missingness, confidence, scalability, security, testability, migration, implementation impact, operability, and maintainability.

### 7. Option Enumeration

Enumerate all materially distinct options that satisfy the fixed constraints. Do not prematurely rank or recommend options during enumeration.

### 8. Option Normalization

Describe every option at comparable depth and against the same material dimensions. Composite options and partial variants must be made explicit.

A preparation may normalize dimensions through headings, tables, or clearly comparable prose. Independent audit evaluates whether the substance is present and comparable, not merely whether each dimension has a separately labeled field, unless a controlling template explicitly requires one.

### 9. Comparative Evaluation

Compare options against documented criteria. Trade-offs, implementation costs, failure modes, long-term consequences, and reversibility must be recorded.

### 10. Falsification

For each viable option, identify evidence or conditions that would make it unacceptable. Actively test the leading option against counterexamples, boundary cases, and alternative explanations.

### 11. Constitution Check

Confirm compatibility with `docs/PROJECT_CONSTITUTION.md` and any canonical constitutional authority identified by the architecture map.

### 12. Governance Check

Confirm compatibility with `docs/DEVELOPMENT_GOVERNANCE.md`, accepted ADRs, canonical architecture documents, and applicable implementation contracts.

### 13. Architecture Readiness

Determine whether the problem, evidence, constraints, dimensions, and option set are sufficiently complete for a decision. Unresolved material uncertainty blocks readiness.

### 14. ADR Readiness

Determine whether the preparation record can be converted into an ADR without inventing missing evidence, collapsing unresolved conflicts, or silently selecting an option.

Independent readiness review must use the finding classifications, materiality rules, and verdicts in `docs/ARCHITECTURE_AUDIT_PROTOCOL.md`.

## Required Outputs

Every completed preparation must contain:

- metadata and lifecycle status;
- problem statement and motivation;
- current architecture and scope boundaries;
- constraints;
- evidence inventory with quality assessment;
- assumptions, separately identified;
- architectural dimensions;
- normalized candidate options;
- comparative evaluation;
- falsification results;
- rejected options and reconsideration conditions;
- risks and open questions;
- Constitution check;
- Governance check;
- readiness determination;
- recommendation, when recommendation is authorized;
- traceability to Issue, Epic, ADPR, ADR, implementation, PR, commit, and release where those artifacts exist.

## Readiness Gates

A preparation is not ready for ADR unless all of the following are true:

- the problem and scope are unambiguous;
- existing canonical authority has been checked;
- material constraints are identified;
- relevant evidence is available and its limitations are recorded;
- assumptions are explicit and do not substitute for evidence;
- materially distinct viable options are represented fairly;
- evaluation criteria are consistent across options;
- rejected options include reasons and reconsideration conditions;
- material risks and unresolved questions are visible;
- falsification has been attempted;
- no unresolved Constitution conflict exists;
- no unresolved Governance or ADR conflict exists;
- the proposed ADR scope is precise;
- no implementation work is required to conceal an unresolved decision.

The allowed preparation self-assessment outcomes are:

- `READY_FOR_ADR`
- `NEEDS_REVISION`
- `BLOCKED`
- `NOT_AN_ARCHITECTURE_DECISION`

These are author self-assessments only. Independent audit verdicts are defined exclusively by `docs/ARCHITECTURE_AUDIT_PROTOCOL.md` and may include `READY_FOR_ADR_WITH_MINOR_FINDINGS`, `CONDITIONAL_ADR_READY`, `ADPR_REVISION_REQUIRED`, and `ARCHITECTURE_NOT_READY`.

## Lifecycle States

Preparation artifacts use these states:

- `PROPOSED`
- `IN_RESEARCH`
- `READY_FOR_REVIEW`
- `APPROVED`
- `IMPLEMENTED`
- `VALIDATED`
- `SUPERSEDED`
- `ARCHIVED`

`APPROVED` means the preparation record is accepted as an accurate record of the decision basis. It does not by itself approve implementation. Implementation authority remains with the applicable ADR, plan, Issue, and Development Governance lifecycle.

## Ownership Boundary

This guide owns only the standard for preparing architecturally significant decisions.

It does not own:

- constitutional authority;
- project principles;
- architectural authority;
- ADR acceptance;
- implementation scope;
- runtime behavior;
- architecture audit classification or verdicts;
- AI implementation review;
- final validation;
- pull request readiness;
- merge authorization.

## Relationship Matrix

| Document or artifact | Relationship |
|---|---|
| `PROJECT_CONSTITUTION.md` | Must comply |
| `PROJECT_PRINCIPLES.md` | Must comply where applicable |
| `DEVELOPMENT_GOVERNANCE.md` | Governing process authority; this guide elaborates Stage 1 planning |
| Canonical architecture documents | Source of existing authority and constraints |
| Accepted ADRs | Binding architectural decisions and evidence inputs |
| Preparation template | Working artifact used during research |
| Preparation checklist | Author self-review aid |
| ADPR | Permanent preparation and reasoning record |
| `ARCHITECTURE_AUDIT_PROTOCOL.md` | Independent audit materiality, verdict, and re-audit authority |
| ADR | Formal architectural decision produced after readiness |
| AI Review Protocol | Independent implementation and contribution review |
| Merge Readiness Gate | Independent implementation guide for merge readiness |

## Traceability

The intended traceability chain is:

```text
Idea
  -> Issue or Epic
  -> Architecture Decision Preparation
  -> Preparation Self-Assessment
  -> Independent Architecture Audit
  -> ADPR
  -> ADR
  -> Implementation
  -> Pull Request
  -> AI Review
  -> Merge Readiness
  -> Merge
  -> Release
```

A missing artifact must be represented as not applicable or not yet created. Links must not be fabricated.

## Success Criteria

This process succeeds when:

- major architectural work begins with a validated problem rather than a preferred implementation;
- the reasoning behind accepted decisions remains recoverable;
- evidence and assumptions remain distinguishable;
- alternatives and rejected options remain auditable;
- the same process can be executed by humans or different AI systems;
- architecture review finds fewer preventable design defects after implementation;
- non-material editorial findings do not create endless architecture-revision cycles;
- the framework remains proportionate and does not burden ordinary low-risk changes.

## Amendment Policy

Changes to this guide follow `docs/DEVELOPMENT_GOVERNANCE.md`.

No amendment may conflict with the Project Constitution, Project Principles, accepted ADRs, or canonical ownership boundaries. Substantive amendments to this preparation framework require their own architecture decision preparation record and, when they alter architecture or governance authority, an ADR or other canonical amendment required by the existing authority hierarchy.