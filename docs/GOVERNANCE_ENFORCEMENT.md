# Governance Enforcement

## Purpose

This document describes the active repository automation that enforces current merge risk. Canonical lifecycle semantics are owned by `docs/DEVELOPMENT_GOVERNANCE.md`; review semantics are owned by `docs/AI_REVIEW_PROTOCOL.md`.

## Active path

The trusted default branch provides three lightweight surfaces:

1. `Hunter Governance Agent Preflight` — structural PR sanity only; it does not enforce Issue/branch/title/body/hostile-review ceremony.
2. `Hunter Governance Review` — current mergeability sanity through `scripts/hunter_governance_review_v2.py`.
3. `Hunter Merge Readiness` — final current-state controller through `scripts/hunter_merge_readiness_v2.py`.

Required code/security checks are `Quality Gates`, `dependency-review`, and `CodeQL`.

Alongside those, `scripts/hunter_workflow_state.py` lets an agent check its own reported progress against current GitHub state (`docs/AGENT_WORKFLOW_STATE_ENFORCEMENT.md`). It publishes no status and is not a merge gate; it consumes the merge-readiness definition above rather than restating it.

## What is not enforced

The active path does not make these authoritative:

- Issue identity or sequence;
- branch or commit naming;
- PR-body templates, matrices, or readiness declarations;
- top-level comments or reactions;
- owner `+1` acknowledgement timestamps;
- metadata edits;
- exact-pair hostile-review attestation markers;
- historical/superseded workflow results.

## Review feedback

Unresolved substantive inline review findings and current `CHANGES_REQUESTED` are read live by Merge Readiness. Non-blocking recommendations are not merge blockers and must not generate an endless fix/re-review loop.

## Fail-closed boundaries

Real risk remains fail-closed: conflicts, unresolved mergeability, required-check failures, substantive review blockers, and unsafe shared-head attribution cannot publish merge-ready success.

Transient GitHub transport failures use the shared bounded-retry boundary in `scripts/hunter_github_transport.py`; infrastructure unavailability is never converted into a semantic approval.

## Retired migration machinery

The pre-v2 PR-body/Issue preflight, Draft Promotion state machine, owner-reaction acknowledgement gate, CodeRabbit hostile-review adapter, semantic revision controller, and legacy Governance Review package are retired from active merge authority. They must not be reintroduced as prerequisites without a new demonstrated risk that current-state gates cannot express.

## Human control

Automation does not merge. Human merge approval remains required after all required final-head gates are green.
