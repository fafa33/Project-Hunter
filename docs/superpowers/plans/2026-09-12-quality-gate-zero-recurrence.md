# Quality Gate and Zero-Recurrence Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add immediate readiness reconciliation, mandatory review-thread closure, and evidence-backed enforcement of known defect families.

**Architecture:** Extend the existing readiness and defect-prevention boundaries. Events only trigger live-state reconciliation; registry applicability selects focused regression evidence; the hosted full suite remains unchanged.

**Tech Stack:** Python 3.12, pytest, GitHub Actions YAML, GitHub REST/GraphQL APIs.

**Spec:** `docs/superpowers/specs/2026-09-12-quality-gate-zero-recurrence-design.md`

## Global Constraints

- No full local repository suite; focused tests only during implementation.
- No automatic review-thread resolution.
- No lifecycle promotion without matching machine enforcement.
- No merge without explicit owner approval.

---

### Task 1: Immediate Merge Readiness Reconciliation

**Files:**
- Modify: `.github/workflows/hunter-merge-readiness.yml`
- Modify: `scripts/hunter_merge_readiness_v2.py`
- Modify: `tests/test_hunter_merge_readiness_v2.py`

**Interfaces:**
- Consumes: GitHub `workflow_run` event payload.
- Produces: `candidate_prs() -> tuple[int, ...]` that sweeps open PRs after default-branch governance reconcile completion.

- [ ] Add tests proving a governance-reconcile completion with default-branch SHA selects all open PRs, while ordinary dependency completion retains exact-head attribution.
- [ ] Run those tests and confirm the expected selection failure.
- [ ] Add the workflow trigger and minimal event classification in `candidate_prs()`.
- [ ] Run readiness unit and convergence tests until green.
- [ ] Run Ruff, Black check, and `git diff --check`; commit the isolated change.

### Task 2: Review Thread Closure Guard

**Files:**
- Modify: the existing pre-Ready/pre-push entrypoint selected after tracing callers.
- Reuse: `scripts/hunter_merge_readiness_v2.py::unresolved_review_threads`
- Modify/Create: focused tests adjacent to the selected entrypoint.

**Interfaces:**
- Consumes: live paginated GitHub review threads and evidence-bearing replies.
- Produces: fail-closed zero-unresolved decision before Ready; no mutation of threads.

- [ ] Trace the actual Ready-authoring entrypoint and write a failing test for one unresolved fixed finding.
- [ ] Add missing-API and paginated-thread adversarial tests and confirm RED.
- [ ] Implement the smallest shared observation/validation interface without duplicating readiness rules.
- [ ] Run focused tests and ensure valid resolved threads pass and unknown API state fails closed.
- [ ] Run relevant lint/type checks and `git diff --check`; commit separately.

### Task 3: Historical Defect Backfill and Applicable Focused Enforcement

**Files:**
- Modify: `docs/DEFECT_REGISTRY.json`
- Modify: `scripts/hunter_defect_prevention_preflight.py`
- Modify: `.githooks/pre-push` or `scripts/hunter_pre_push.py` only at the existing safety-boundary extension point.
- Modify: `tests/test_hunter_defect_prevention_preflight.py`
- Modify/Create: regression tests referenced by verified historical families.

**Interfaces:**
- Consumes: changed repository paths and registry `applicability.changed_paths` plus `regression_evidence` pytest selectors.
- Produces: deterministic deduplicated focused pytest selector tuple and nonzero exit on an applicable known-defect regression.

- [ ] Classify exported review history by PR, thread, severity, correction evidence, and existing family; exclude unverified findings from enforcement claims.
- [ ] Write failing tests for exact applicability, prefix-boundary traps, duplicate selectors, missing selectors, and a failing selected regression.
- [ ] Implement deterministic selector resolution and execution at pre-push without changing the hosted full-suite owner.
- [ ] Add only evidence-backed registry families and tests; keep partially covered families at `regression-tested`.
- [ ] Run focused registry/pre-push tests, adversarial bypass cases, Ruff, Black check, Mypy for changed Python, and `git diff --check`.
- [ ] Use the normal pre-push hook for publication, verify hosted exact-head proof, create a Draft PR, and stop before merge.

## Self-Review

All spec requirements map to a task. The interfaces preserve the validation-stage ownership and current-state readiness authority. The plan contains no placeholder implementation or automatic thread mutation.
