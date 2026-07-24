# Post-PR59 Committee Authority Audit

## Audit target

Canonical repository branch: `main`

Audited merge commit: `dcfc8b1acc220f540394fd483adbead7883971f2`

Relevant merged changes:

- PR #58 — authoritative committee persistence, ranking, champion snapshot, and read-only dashboard projection.
- PR #59 — future-known and future-effective input validation remediation.

## Audit result

**BLOCKED**

PRs #58 and #59 establish a useful first authoritative committee-output slice and close the two reviewed future-data defects, but the implementation does not yet satisfy the full authoritative input boundary and end-to-end completion criteria from Issue #57.

## Verified findings

### Service-owned evaluation boundary

`src/hunter/committee/service.py` owns evaluation, ranking, champion construction, and the call into persistence through `AuthoritativeInvestmentCommitteeService.evaluate_cycle()`.

### Atomic persistence and replay behavior

`src/hunter/committee/repository.py` persists the cycle and ranked assessments in one SQLite transaction. Existing cycle IDs use insert-or-identical verification, while divergent cycle or assessment content is rejected.

### Point-in-time checks added by PR #59

`src/hunter/committee/service.py::_validate_sources()` now validates both raw records and optional derived assessments.

Verified checks include:

- non-empty persisted IDs for raw inputs;
- non-empty assessment IDs for `opportunity`, `probability`, `pattern`, and `necessity`;
- rejection of raw and derived inputs known after the cycle cutoff;
- rejection of raw and derived inputs effective after the cycle cutoff.

### Ranking and dashboard projection

The service assigns deterministic contiguous one-based ranks and verifies that the selected champion matches rank 1. `CommitteeDashboardProjection` reads persisted committee outputs and does not invoke the analytical engine.

## Blocking findings

### BLOCKER 1 — No stale-beyond-policy enforcement

**File:** `src/hunter/committee/service.py`

**Symbols:** `_validate_sources()`, `_validate_persisted_input()`, `_validate_assessment_input()`

The service checks whether inputs are from the future, but it does not apply any maximum-age or domain freshness policy. A record or assessment can therefore be arbitrarily old and still enter authoritative scoring.

**Required remediation:**

- define authoritative freshness policy by input family;
- evaluate the timestamp actually consumed by scoring;
- reject stale inputs or preserve them as explicit stale abstentions according to policy;
- add deterministic boundary tests.

### BLOCKER 2 — No project/entity/representation identity binding

**File:** `src/hunter/committee/service.py`

**Symbols:** `_validate_sources()`, `_validate_persisted_input()`, `_validate_assessment_input()`

The current validation does not prove that each source record or derived assessment belongs to `CommitteeInputSet.project_id`, the same economic entity, and the correct token/network representation. A persisted input from another project can pass ID and chronology checks and affect the authoritative result.

**Required remediation:**

- define canonical identity fields required for every accepted input family;
- validate project/entity/representation equality before scoring;
- reject ambiguous or mismatched identity;
- add raw-record and derived-assessment mismatch tests.

### BLOCKER 3 — No correction-lineage or superseded-record rejection

**File:** `src/hunter/committee/service.py`

**Symbols:** `_validate_persisted_input()`, `_validate_assessment_input()`

The committee boundary does not inspect correction lineage, supersession state, or whether the supplied record is the valid point-in-time version. A corrected or superseded record can still be supplied directly when its timestamps otherwise pass validation.

**Required remediation:**

- require correction/version metadata for authoritative input families;
- resolve the valid record as known at the cycle cutoff;
- reject superseded, revoked, or lineage-inconsistent inputs;
- add correction and replay tests.

### BLOCKER 4 — No explicit authority-class enforcement

**Files:** `src/hunter/committee/models.py`, `src/hunter/committee/service.py`, canonical authority registry/documentation

`CommitteeInputSet` accepts `opportunity`, `probability`, `pattern`, `necessity`, intelligence, fused intelligence, evidence, and snapshots by type, but the service does not verify that the supplied output is classified as production-authoritative rather than descriptive, experimental, research-only, or unavailable.

Current repository authority classification explicitly keeps several of these capabilities experimental or unavailable. Type membership alone is therefore insufficient for authoritative committee admission.

**Required remediation:**

- introduce a canonical machine-readable authority registry or equivalent accepted contract;
- require service-owned authoritative record provenance for every admitted input;
- reject experimental, descriptive-only, or unavailable outputs from authoritative scoring;
- keep missing inputs explicit rather than substituting neutral values.

### BLOCKER 5 — Valuation-family authority remains unavailable

The following required decision inputs remain unavailable under the accepted architecture:

- canonical valuation;
- comparative valuation;
- mispricing;
- asymmetry.

The committee slice therefore cannot yet claim the complete evidence-backed opportunity decision boundary described by Issue #57.

**Required remediation:**

Implement and accept the valuation-family records, methodologies, normalization, calibration, service-owned persistence, chronology, confidence, and missingness contracts before admitting them into authoritative committee evaluation.

### BLOCKER 6 — Required end-to-end regression coverage is incomplete

The merged slice contains focused persistence and chronology coverage, but the full completion set is not demonstrated for:

- stale-beyond-policy rejection or explicit stale abstention;
- project/entity/representation mismatch;
- correction lineage and superseded-record rejection;
- experimental-output rejection;
- known-by-Hunter replay resolution across all input families;
- a complete persisted evidence-to-ranking-to-dashboard cycle using only production-authoritative inputs.

**Required remediation:**

Add deterministic tests covering each blocker and one complete end-to-end authoritative cycle. The dashboard test must prove source traceability and absence of score recomputation.

## Quality-gate status

The audit branch CI was triggered for the documentation-only change. CI success can verify repository formatting, typing, and test stability, but it cannot convert the blocking architectural and runtime findings above into approval.

Required gates for every remediation PR remain:

```bash
ruff check .
black --check src tests config alembic
mypy
pytest
```

## Decision

**BLOCKED**

Do not treat Issue #57 or the complete authoritative Opportunity input boundary as closed on the basis of PRs #58 and #59 alone.

The next production work must be a separate remediation scope that completes:

1. stale policy enforcement;
2. identity binding;
3. correction-lineage and replay-cutoff enforcement;
4. authority-class admission control;
5. deterministic end-to-end coverage.

After those changes are merged and independently audited, proceed to Canonical Valuation, Comparative Valuation, Mispricing, and Asymmetry in roadmap order.
