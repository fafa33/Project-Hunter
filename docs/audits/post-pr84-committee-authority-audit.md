# Post-PR84 Committee Authority Audit

## Verdict

**APPROVED**

No remaining blocker was identified against the final committee-authority contract or Issue #61 completion criteria.

## Audited repository state

- Canonical `main` merge commit: `3db1052a1daf7aec320b30f6f48658c2cf61c45c`
- Merged implementation PR: #84
- Audited implementation HEAD: `f969924b4a55ea333caa820be1ddacc57ed7e981`
- Final-head CI run: #285 (`29996266163`)

## Independent findings

### 1. Installed CLI and canonical persistence

Verified.

`tests/test_committee_authority_final_e2e.py` resolves the installed `hunter` console script from `PATH`, executes `hunter committee-authority` through `subprocess.run` from an unrelated working directory, requires exit code zero, and verifies the reported canonical database path. The test also proves that neither a standalone committee database nor a wrong-working-directory `data_ops.sqlite` is created.

### 2. Persisted ranking and exact Dashboard projection

Verified.

The installed-CLI test executes a two-candidate cycle, loads the canonical SQL assessments, requires contiguous ranks `(1, 2)`, and verifies every Dashboard row against the exact persisted assessment identity, project ID, decision, confidence, rank, and source-record IDs. `src/hunter/dashboard/data.py` directly exposes `record.rank` and persisted source IDs. Record counts before and after Dashboard construction are identical, proving read-only projection.

### 3. Post-evaluation persistence failure and atomic rollback

Verified.

The failure test wraps `GenericSQLCommitteeOutput.persist_cycle`, invokes the real persistence method first so votes, assessments, and champion are staged after evaluation, and then raises a forced error. Two identical executions prove:

- no committee votes persist;
- no assessments persist;
- no champion persists;
- exactly one deterministic durable failed run remains;
- no successful run is substituted;
- parent attempted-run identity is preserved;
- manifest and input fingerprints are exact;
- the original forced persistence error remains visible.

This closes the rollback blocker recorded by the post-PR82 audit.

### 4. Cross-candidate vote identity

Verified.

Committee vote identity includes `project_id`, preventing identical engine scores and references from colliding across candidates while retaining deterministic identity and divergent-duplicate protection.

### 5. Final-head quality gates

Verified on the exact implementation HEAD `f969924b4a55ea333caa820be1ddacc57ed7e981` in CI run #285:

- `ruff check .` — passed
- `black --check .` — passed
- `mypy` — passed
- `pytest` — passed

## Issue #61 disposition

The remaining blockers from the prior audit are closed. Issue #61 satisfies its completion criterion after this audit is merged into `main`.
