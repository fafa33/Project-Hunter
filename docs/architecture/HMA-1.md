# HMA-1 Architecture Specification & Inventory

## Architecture Overview

HMA-1 (Hunter Minimal Automation - 1) defines the approved minimal automation architecture for Project Hunter. It establishes strict separation between unprivileged candidate validation environments and the independent, default-branch-owned Trusted Gate.

HMA-1 consists of two core architectural components:

1. **Restricted, Unprivileged CI:** Candidate pull requests execute in a strictly sandboxed, unprivileged context. Candidate workflows and code operate without repository secrets, elevated tokens, or `statuses: write` permissions.
2. **Independent Trusted Gate:** Evaluates candidate mergeability and readiness using the existing canonical readiness authority (`scripts/hunter_merge_readiness_v2.py`). No redundant decision engines, nested graders, receipt signers, or status publishers are introduced.

---

## Phase 1 — Targeted Inventory

### 1. Existing CI and Replay Workflows
- **`ci.yml` (CI / Quality Gates & Main Validation):**
  - Triggered on `pull_request` (branches: `main`) and `push` (branches: `main`).
  - Top-level `permissions: {}`.
  - `quality` job runs unprivileged (`actions: read, contents: read`) on PRs, compiling scripts/src hygiene without secrets or status write authority.
  - `main-validation` job executes full deterministic preflight on merged `main`.
- **`hunter-pre-pr-preflight.yml` (Hunter / Pre-PR Preflight):**
  - Triggered on `push` to candidate feature branches (`branches-ignore: main`).
  - Runs in candidate context with `contents: read` permission.
- **`hunter-trusted-preflight-upgrade.yml` (Hunter / Trusted Preflight Upgrade):**
  - Triggered on `pull_request_target` (`opened`, `reopened`, `synchronize`).
  - Controller checked out from trusted `main` branch.
  - Validates candidate code in `./candidate` directory using trusted default-branch scripts (`hunter_defect_prevention_preflight.py`).
  - `publish-proof` job is isolated and holds `statuses: write` permission to publish exact-head status proof without executing candidate code.
- **`hunter-merge-readiness.yml` (Hunter / Merge Readiness):**
  - Triggered on `pull_request_target`, `workflow_run`, `schedule`, `workflow_dispatch`.
  - Executes trusted controller (`scripts/hunter_merge_readiness_v2.py`) to publish the `Hunter Merge Readiness` commit status.
- **`hunter-governance-preflight.yml` & `hunter-governance-reconcile.yml`:**
  - Structural sanity and governance reconciliation.

### 2. Required Status Checks and Publishers
- **`Quality Gates`:** Published by GitHub Actions via `ci.yml` (`quality` job). Required by `hunter_merge_readiness_v2.py`.
- **`dependency-review`:** Published by GitHub Actions via `dependency-review.yml`. Required by `hunter_merge_readiness_v2.py`.
- **`CodeQL`:** Published by GitHub Actions via CodeQL workflows / bridge. Required by `hunter_merge_readiness_v2.py`.
- **`Hunter Trusted Preflight Upgrade / PR #<pr>`:** Published by `hunter-trusted-preflight-upgrade.yml` (`publish-proof` job) for candidate PRs modifying protected preflight definition paths.
- **`Hunter Merge Readiness`:** Published exclusively by `hunter-merge-readiness.yml` calling `scripts/hunter_merge_readiness_v2.py`.

### 3. Consumers of Readiness Statuses
- **GitHub Ruleset (`refs/heads/main` Branch Protection):** Enforces `Hunter Merge Readiness` status check as a required merge gate for `main`.
- **Governance Controllers & Admission Controllers:** `scripts/hunter_workflow_state.py` and `scripts/hunter_candidate_admission.py` consume `hunter_merge_readiness_v2.evaluate()`.

### 4. Existing Trust Boundaries and Security Risks
- **Candidate Code Isolation:** Candidate code must never execute in a job possessing repository secrets or status write access.
- **Exact HEAD SHA Binding:** Readiness evaluation and preflight adjudication must bind strictly to the exact commit SHA reported by `github.event.pull_request.head.sha`, preventing race conditions with rapid pushes.
- **Forge Prevention:** Candidate PRs modifying preflight scripts, test configurations (`pyproject.toml`, `requirements/ci-constraints.txt`), or workflow definitions must not be allowed to self-authorize or reuse hosted push proofs. `hunter_preflight_reuse_decision.py` detects trusted definition modifications and forces full trusted controller gate execution.

### 5. Safe Migration and Rollback Requirements
- **Zero Disruption:** Migration must preserve existing `ci.yml` "Quality Gates" and `hunter-merge-readiness.yml` checks without status flickering or red checks on valid PRs.
- **Fail-Closed Defaults:** Any evidence unavailability, SHA mismatch, or unexpected exception must result in `pending` or `failure`, never unexpected `success`.
- **Reversibility:** Changes rely strictly on repo-native Python scripts and standard GitHub Actions workflow structure, allowing instant rollback if required.

---

## Phase 2 — First Implementation Slice Design

The first implementation slice verifies and enforces:
1. Candidate code execution occurs in an unprivileged environment (`permissions: actions: read, contents: read` or `{}`).
2. Exact PR HEAD SHA binding across candidate admission, preflight reuse decision, and readiness evaluation.
3. Anti-forgery protections ensuring candidate-controlled workflow or test modifications force full execution under the trusted default-branch controller.
4. Single canonical readiness authority preservation via `scripts/hunter_merge_readiness_v2.py`.
5. Focused adversarial regression testing (`tests/test_hma1_trusted_gate.py`).
