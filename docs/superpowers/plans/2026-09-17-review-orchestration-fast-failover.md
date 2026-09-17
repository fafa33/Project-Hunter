# Review Orchestration Fast-Failover Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make trusted exact-head review orchestration fail over quickly and accept authenticated native Codex outcomes without duplicate JSON review requests.

**Architecture:** Keep the default-branch collector as the sole trusted invocation controller. Extend response classification so native authenticated outcomes are correlated to the collector trigger, and change policy to one bounded invocation per enabled reviewer with immediate failover on explicit unavailability.

**Tech Stack:** Python 3, pytest, GitHub Actions, repository JSON policy.

**Spec:** `docs/superpowers/specs/2026-09-17-review-orchestration-fast-failover-design.md`

## Global Constraints
- Exact-head, claims-bound, fail-closed review authority.
- Never execute candidate code as trusted controller.
- No generic GitHub Actions comment may become review authority.
- One invocation per enabled reviewer per exact HEAD.
- Codex timeout 300 seconds; explicit unavailability fails over immediately.

---

### Task 1: Collector response classification

**Files:**
- Modify: `scripts/hunter_reviewer_collector.py`
- Test: `tests/test_hunter_reviewer_collector.py`

- [ ] Add failing tests for native clear correlation, explicit unavailable fast-failover, stale/pre-trigger/wrong-author/wrong-head rejection, and one invocation.
- [ ] Run focused tests and verify RED.
- [ ] Implement minimal response classification and collection behavior.
- [ ] Run focused tests and verify GREEN.

### Task 2: Governance adoption and policy

**Files:**
- Modify: `scripts/hunter_governance_review_v2.py`
- Modify: `docs/CODE_WRITE_POLICY.json`
- Test: `tests/test_exact_head_review_authority.py`

- [ ] Add failing tests proving only trigger-correlated native Codex clear adopts current claims.
- [ ] Verify RED.
- [ ] Normalize trusted collector-correlated native clear into authority while preserving structured ack support.
- [ ] Set Codex timeout to 300 seconds, retries to zero, and policy wording to one invocation/fast failover.
- [ ] Verify focused suites GREEN.

### Task 3: Whole-family verification and final HEAD

**Files:**
- Modify last: `.hunter/pre-ready-hostile-review.json`

- [ ] Run collector, exact-head authority, bootstrap, governance and merge-readiness regression suites.
- [ ] Run Ruff, Black, diff check, and repository pre-push gates.
- [ ] Regenerate the hostile-review artifact last and commit it separately.
- [ ] Push final HEAD once, then invoke the trusted default-branch collector exactly once for that HEAD.
- [ ] Verify published Candidate Admission/Governance/Merge Readiness statuses and unresolved threads from GitHub.
### Task 4: Automatic exact-head orchestration
- [x] Add a failing workflow regression proving synchronize events trigger the trusted collector.
- [x] Enable pull_request_target opened/reopened/synchronize on the existing collector workflow.
- [x] Derive PR/head from the event while preserving workflow_dispatch fallback.
- [x] Verify collector tests remain green.
