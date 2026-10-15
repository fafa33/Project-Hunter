# HMA-1 Execution Record — Phase 2 Security Remediation

## Metadata
- **Branch:** `hma1-minimal-trusted-gate-17597437651104276792`
- **Base Branch:** `main`
- **HEAD SHA:** `82d480dc05c66707fe880e11c8ac0e2da7115d01`
- **Timestamp:** 2026-10-15T08:00:00Z

---

## Adversarial Review Remediation Summary

### Finding 1 Remediation — Candidate-Controlled Security Validation Separation
- **Architectural Shift:** Candidate-side checks are non-authoritative; trusted default-branch verification controls admission. Candidate-executed CI (`ci.yml`) is strictly designated as non-authoritative candidate hygiene ("Quality Gates").
- **Default-Branch Owned Adjudication:** Authoritative security validation and candidate admission adjudication execute exclusively in default-branch-owned controllers (`scripts/hunter_governance_review_v2.py` / `scripts/hunter_merge_readiness_v2.py` / `scripts/hunter_preflight_reuse_decision.py`).
- **Exact PR HEAD SHA Binding & Revalidation:** Default-branch controller queries `GET /repos/{repo}/pulls/{pr}` to bind validation strictly to `head.sha`. Before issuing authoritative success, `candidate_admission` revalidates `head.sha` to detect concurrent candidate pushes.
- **Fail-Closed API Errors:** Network failures, API timeouts, or malformed PR payloads during SHA verification fail closed with immediate `failure` status.
- **Backed Status Verification:** `read_trusted_upgrade_status` verifies that status checks carry backing GitHub Actions workflow runs (`actions/runs/{run_id}`) matching `head_sha` and workflow path before trusting them.

### Finding 2 Remediation — Inadequate Privilege Verification
- **Least-Privilege Enforcement:** Verified `.github/workflows/ci.yml` enforces least privilege via top-level `permissions: {}` and job `permissions: actions: read, contents: read`. Candidate jobs carry no write-capable tokens or secret environment variables.
- **Removal of Environment-Variable Name Checks:** Candidate-side environment-variable checks do not establish the trusted boundary; GitHub workflow permissions and trusted controllers are the authority. Security boundaries are enforced via workflow permissions and default-branch API verification rather than candidate-controlled script assertions.
- **Trusted Controller Privilege Separation:** Trusted validation (`hunter-trusted-preflight-upgrade.yml`) checks out default-branch scripts first and executes candidate code as an untrusted payload (`./candidate`) without write permissions or secret injection.

---

## Changed Files

1. `.github/workflows/ci.yml`: Restored `Verify CI wiring` hygiene step and added inline architectural documentation clarifying non-authoritative candidate hygiene status.
2. `scripts/hunter_governance_review_v2.py`: Added exact PR HEAD SHA initial verification, fail-closed API error handling, and pre-success PR HEAD SHA revalidation in `candidate_admission`.
3. `scripts/hunter_pr_preflight.py`: Candidate-side hygiene only; no authority to publish readiness.
4. `scripts/hunter_preflight_reuse_decision.py`: Included `.github/workflows/` and `tests/` in `TRUSTED_DEFINITION_PREFIXES` to detect candidate workflow/test tampering and force default-branch trusted gate execution.
5. `tests/test_hma1_trusted_gate.py`: Implemented 9 mandatory adversarial tests verifying real execution and consumption paths.
6. `docs/architecture/HMA-1.md`: HMA-1 architecture specification.
7. `docs/architecture/HMA-1-EXECUTION.md`: Execution tracking document.

---

## Test Commands and Exact Results

### Command: Combined HMA-1 Adversarial Security and Merge Readiness Suite
```bash
PYTHONPATH=scripts:src /home/jules/.local/bin/pytest tests/test_hma1_trusted_gate.py tests/test_hunter_merge_readiness_v2.py
```
**Output:**
```
============================= test session starts ==============================
platform linux -- Python 3.12.3, pytest-9.0.2, pluggy-1.6.0
rootdir: /app
configfile: pyproject.toml
collected 57 items

tests/test_hma1_trusted_gate.py .........                                [ 15%]
tests/test_hunter_merge_readiness_v2.py ................................ [ 71%]
................                                                         [100%]

============================== 57 passed in 0.61s ==============================
```

---

## Implemented Protections vs. Deferred Phase 3 Items

### Implemented Protections (Phase 2):
1. **Unprivileged Candidate CI Context:** Candidate PR workflows (`ci.yml`) run with `permissions: {}` and no secret access.
2. **Default-Branch Owned Admission Authority:** `candidate_admission` in `scripts/hunter_governance_review_v2.py` controls candidate progression using default-branch code.
3. **Exact HEAD SHA Binding & Recheck:** PR current HEAD SHA is verified before evaluation and rechecked before publishing success.
4. **Workflow & Test Tampering Prevention:** Any candidate PR editing `.github/workflows/` or `tests/` triggers `reusable=False`, forcing full trusted controller execution under `hunter-trusted-preflight-upgrade.yml`.
5. **Backed Status Validation:** Status checks without verified backing GitHub Actions workflow runs are rejected.
6. **Canonical Authority Preservation:** `scripts/hunter_merge_readiness_v2.py` remains the sole readiness authority.

### Deferred Items (Phase 3):
1. **Fork PR Proof Propagation:** Seamless cross-repository status proof propagation for untrusted external fork PRs without write permissions (Phase 3).
2. **Legacy Push-Event Workflow Retirement:** Complete retirement of legacy push-event preflight trigger paths once default-branch proof channels are fully deployed (Phase 3).

---

## Unresolved Security Risks

- **Current Status:** Security findings are tracked in live PR review threads; no claim of full closure is made before GitHub checks and review resolution.
- **Risk Mitigation:** All candidate admission and status adjudication logic executes strictly in default-branch-owned code with exact PR HEAD SHA binding and fail-closed API error handling.
