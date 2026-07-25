# Merge Readiness Gate

## Purpose

Project Hunter pull requests must not be merged solely because automated checks are green or because no reviewer has left a blocking comment. Those signals establish code health only; they do not prove that the governing Issue, ADR, evidence, replay, persistence, or operational requirements are complete.

## Mandatory rule

A pull request is merge-ready only when all required acceptance criteria and operational validations are explicitly evidenced as passing.

The allowed final verdicts are:

- `APPROVED`: every required acceptance criterion and operational validation passes.
- `CHANGES REQUIRED`: implementation, tests, evidence, or documentation remains incomplete.
- `BLOCKED`: completion depends on an unavailable environment, provider, credential, network route, or external condition.

A `FAIL` or `BLOCKED` required criterion prohibits merge.

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

Authors must keep incomplete PRs in draft status or clearly mark them `CHANGES REQUIRED` or `BLOCKED`. Environmental limitations must be reported immediately and must not be converted into fabricated evidence, narrowed acceptance criteria, or an undocumented scope change.

## Maintainer responsibility

Maintainers must not merge when any required criterion is `FAIL`, `BLOCKED`, missing, or unsupported by evidence. Green CI is necessary but never sufficient.

## Origin

This gate was introduced after PR #110 demonstrated that all automated checks could pass while a required live supply-basis acquisition and complete persisted evidence chain remained outstanding due to an environment-specific CoinGecko network block.
