# Merge Readiness Gate

## Status

This document is a reference and implementation guide. It does not define an independent governance authority.

The mandatory Merge Readiness rule is owned by `docs/DEVELOPMENT_GOVERNANCE.md` (Pull Request Governance / Merge Readiness section), consistent with that document's Ownership Boundary over pull request readiness, review workflow, validation workflow, and process governance. This document elaborates that rule with required review dimensions, the acceptance-criteria matrix format, the evidence package a Pull Request must include, and the pull request template that operationalizes it. Where anything below appears to restate the rule, `docs/DEVELOPMENT_GOVERNANCE.md` is authoritative and this document must be read as consistent with it, not as a second source of the rule.

## Purpose

Project Hunter pull requests must not be merged solely because automated checks are green or because no unresolved blocking review comment remains. Those signals establish code health only; they do not prove that the governing Issue, ADR, evidence, replay, persistence, or operational requirements are complete.

## Mandatory rule

A pull request is merge-ready only when all required acceptance criteria and operational validations are explicitly evidenced as passing.

Two distinct declarations exist, and they are never made by the same role:

- The **implementer** declares one of `READY FOR REVIEW`, `CHANGES REQUIRED`, or `BLOCKED` on the Pull Request, per `docs/DEVELOPMENT_GOVERNANCE.md`'s Merge Readiness section. The implementer never declares `APPROVED`.
- The **independent reviewer** alone declares the final verdict of `APPROVED`, `CHANGES REQUIRED`, or `BLOCKED`, per `docs/AI_REVIEW_PROTOCOL.md`'s Approval section, only after required review and verification have completed.

A `FAIL` or `BLOCKED` required criterion prohibits merge regardless of which role is declaring readiness.

## Required review dimensions

### Code quality

The repository-approved Ruff, Black, MyPy, Pytest, migration, and configuration checks must pass. Exact commands and results must be recorded in the PR.

### Architecture compliance

The PR must identify governing Issues and ADRs and record both the Architecture Impact Check and Evidence Impact. Review must verify authority boundaries, provenance, correction semantics, missingness, strict-known replay, persistence, entity scope, and any applicable anti-correlation rules.

### Acceptance-criteria matrix

Every acceptance criterion must appear in the PR description with exactly one status:

- `PASS`
- `FAIL`
- `BLOCKED`
- `NOT APPLICABLE`

No criterion may be silently omitted or treated as satisfied merely because tests pass.

### Operational validation

When the Issue requires real acquisition, provider access, persistence, replay, query, correction, or runbook execution, those actions must actually be performed in a suitable environment. Evidence must include the relevant commands, environment, persisted identifiers, query or replay output, and disclosed limitations.

Mocked, fixture-backed, fabricated, or current-state-substituted evidence does not satisfy a requirement for live or point-in-time validation unless the governing Issue explicitly permits it.

### Evidence package

A merge-ready PR must include:

- final commit SHA;
- exact quality-gate results;
- acceptance-criteria matrix;
- runtime and runbook evidence;
- persisted record identifiers where applicable;
- independent query or replay confirmation;
- known limitations and residual risks;
- final verdict.

## Reviewer responsibility

Reviewers must distinguish code correctness from completion correctness. An approval must not be inferred from silence. The final review must explicitly state one allowed verdict and justify it against the complete acceptance-criteria matrix and operational evidence.

## Author responsibility

Authors declare `READY FOR REVIEW` only when required verification and self-assessment are complete; otherwise the PR remains in draft status or is clearly marked `CHANGES REQUIRED` or `BLOCKED`. Authors never declare `APPROVED`. Environmental limitations must be reported immediately and must not be converted into fabricated evidence, narrowed acceptance criteria, or an undocumented scope change.

## Maintainer responsibility

Maintainers must not merge when any required criterion is `FAIL`, `BLOCKED`, missing, or unsupported by evidence. Green CI is necessary but never sufficient.

## Origin

This gate was introduced after PR #110 demonstrated that all automated checks could pass while a required live supply-basis acquisition and complete persisted evidence chain remained outstanding due to an environment-specific CoinGecko network block.
