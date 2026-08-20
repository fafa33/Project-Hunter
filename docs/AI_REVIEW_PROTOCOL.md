# Project Hunter AI Review Protocol

## Purpose

This document owns independent contribution-review roles, finding classification, review reporting, and approval semantics. It extends `docs/DEVELOPMENT_GOVERNANCE.md`; it does not define product architecture.

## Roles

- **Implementer** — produces and self-verifies the change. The implementer does not independently approve its own substantive work.
- **Reviewer** — evaluates the actual implementation against applicable architecture, contracts, evidence, tests, and repository boundaries.
- **Verifier** — confirms that a blocking finding was actually resolved.

## When independent review is required

Independent review is required for substantive production-code, architecture, security, authority, persistence/replay, evidence-integrity, migration, and similarly material changes. Review is proportional to risk and scope.

A trivial documentation, metadata, or mechanical cleanup does not require a hostile full-repository audit merely because it is a permanent contribution. A reviewer may still be used when useful.

## Review method

Reviewers should actively try to falsify the change and inspect the relevant diff and governing repository facts. Review should be evidence-based, reproducible, and focused on the behavior actually affected.

The old rule requiring a fresh exact-head/exact-base hostile-review attestation after every source or target movement is retired. After a substantive code change, re-review the affected scope. Repeat a broad review only when the substantive scope materially changes. Metadata-only edits and non-blocking dispositions do not invalidate review.

## Findings

### Blocking

A finding blocks merge only when it demonstrates a real risk such as:

- correctness or deterministic-behavior failure;
- security or credential exposure;
- architecture or ownership violation;
- persistence, replay, lineage, migration, or evidence-integrity failure;
- an unsatisfied required operational behavior;
- another defect that makes the contribution unsafe to merge.

A confirmed blocking finding is classified as:

- **isolated** — specific to this contribution; or
- **systemic** — exposes a reusable boundary that could reasonably permit recurrence.

A systemic blocking finding should identify the earliest practical reusable boundary for durable hardening.

### Non-blocking

Maintainability improvements, optional refactors, readability changes, style preferences, extra-test suggestions, defensive hardening without a demonstrated failure, and future improvements are non-blocking unless evidence shows they create an actual merge risk.

Non-blocking recommendations must not delay merge after blocking findings are resolved. They may be documented for later work without another commit or full review cycle.

## Resolution and verification

Fix blocking findings in scope and verify the affected behavior. For systemic blocking findings, verify an appropriate durable guard when required by `docs/HUNTER_IMPLEMENTATION_CONTRACT.md`.

Do not turn a non-blocking recommendation into a mandatory change merely to clear a comment counter. A resolved or explicitly non-blocking thread is not a merge blocker.

## Review report

A review report records:

- reviewed scope;
- blocking findings, if any;
- non-blocking recommendations, if useful;
- classification/evidence for each blocking finding;
- verification of blocker resolution;
- final review outcome.

Valid outcomes are `APPROVED`, `CHANGES REQUIRED`, or `BLOCKED`. Approval means no unresolved blocking finding remains; it does not override required CI/security checks or human merge approval.

## Independence

Independent review requires separation between implementation and approval for substantive changes. Reviewers do not implement their own blocking fixes and then treat that self-review as independent approval.

## Long-term objective

Review exists to catch consequential defects while preserving development throughput. Review quality is measured by the material risk it detects, not by the number of comments it produces.
