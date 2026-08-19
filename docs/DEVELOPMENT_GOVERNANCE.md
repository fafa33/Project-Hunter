# Development Governance

## Purpose

This document owns Project Hunter's development lifecycle and pull-request readiness process. It governs process, not product architecture or runtime behavior.

## Core principle

Governance exists to reduce real merge risk, not to create process state. A rule that repeatedly blocks healthy work without detecting a correctness, security, architecture, evidence, persistence/replay, migration, or equivalent defect must be simplified or removed.

## Lifecycle

Every permanent contribution follows the same logical stages, with depth proportional to risk:

```text
Plan scope
  -> Implement
  -> Local verification
  -> Pull request
  -> CI / security verification
  -> Independent review when required by risk
  -> Resolve blocking findings
  -> Final current-state validation
  -> Merge
```

A Draft PR is optional for incomplete work. It is not mandatory when local verification is already complete.

## Planning and implementation

Understand the requested scope before implementation. Do not silently expand scope, invent architecture, leave placeholders, or weaken existing guarantees. Architecturally significant work must follow the applicable architecture preparation and ADR process; ordinary local work should not be forced through repository-wide architecture ceremony.

## Verification

For code-changing contributions, run the repository quality preflight when the environment permits:

```text
python scripts/hunter_pr_preflight.py
```

Hosted checks on the final code head remain authoritative. Required checks are:

- `Quality Gates`;
- `dependency-review`;
- `CodeQL`;
- `Hunter Governance Review`.

Real failures return the change to implementation. Metadata formatting does not.

## Review

Independent review is required for substantive production-code, architecture, security, authority, persistence/replay, evidence-integrity, migration, or similarly risky changes. Review depth is proportional to the changed behavior.

Review findings are blocking only when they demonstrate a real merge risk. Non-blocking recommendations — including optional refactoring, readability improvements, extra tests, style preferences, and future hardening — do not delay merge.

After a blocking finding is fixed, verify the affected behavior. A new broad review is required only when the substantive scope materially changes. Metadata-only edits and disposition of non-blocking recommendations do not invalidate completed review or checks.

## Traceability

Issues, ADRs, PR descriptions, acceptance notes, and operational evidence remain useful traceability. They must be accurate when present. Their formatting, branch/commit naming, checkbox state, top-level comments, reactions, and timestamps are not automated merge authority.

If a governing Issue contains real acceptance criteria or operational requirements, those requirements must actually be satisfied before merge. They do not need to be encoded in a particular PR-body table to be valid.

## Merge readiness

A PR must not merge while any of the following is true:

- it is Draft;
- GitHub reports a merge conflict or cannot yet resolve mergeability;
- a substantive inline review thread is unresolved;
- a current review is `CHANGES_REQUESTED`;
- a required code/security check is missing, pending, cancelled, or failed;
- `Hunter Governance Review` reports failure;
- the current head cannot be safely attributed to the PR;
- a real governing acceptance requirement is known to remain unsatisfied.

The automated controller derives readiness from current GitHub state. Historical superseded workflow runs, metadata edits, Issue/branch naming, PR prose, comments, reactions, owner acknowledgements, Draft Promotion state, and hostile-review attestation markers do not determine readiness.

## Merge authority

Automation may report readiness but does not merge on its own. Human merge approval remains required. No merge occurs until the final required gates are green.

## Proportionality

The lifecycle stages remain conceptually present for every permanent change, but the evidence and review depth scale with risk. Governance must not force a small cleanup through the same review volume as a new authority, persistence model, or product architecture.

## Ownership boundary

This document owns development lifecycle, review workflow integration, validation workflow, PR readiness, and process governance. Product architecture, implementation contracts, and architecture-audit semantics remain with their canonical owners.
