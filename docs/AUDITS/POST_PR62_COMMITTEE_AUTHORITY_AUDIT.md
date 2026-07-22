# Post-PR62 Committee Authority Audit

## Audit target

Canonical branch: `main`

Audited merge commit: `76f1f8b8aa2cfd27204d3af98157f4e1833bf5dd`

Relevant remediation: PR #62, implementing the first authority-policy slice from Issue #61.

## Final result

**BLOCKED**

PR #62 materially strengthens the committee boundary, but the full Issue #61 completion criterion is not yet satisfied.

## Verified improvements

- `src/hunter/committee/authority.py` introduces explicit freshness policies for each currently scored input family.
- Non-production authority classes are rejected before scoring when an authority class is explicitly present.
- Future-known and future-effective checks from PR #59 remain in the service boundary.
- Project-ID mismatches are rejected when the input exposes `project_id`.
- Inputs marked by `is_superseded` or `is_invalidated`, and lifecycle states exactly equal to `superseded`, `invalidated`, or `retracted`, are rejected.
- Existing deterministic ranking, champion consistency, and atomic persistence flow remains in `AuthoritativeInvestmentCommitteeService`.
- Valuation, comparative valuation, mispricing, and asymmetry remain unactivated and no neutral defaults are introduced.

## Blocking defects

### 1. Missing authority metadata defaults to production authority

File: `src/hunter/committee/authority.py`

Symbol: `_validate_authority_class`

Current behavior uses:

```python
getattr(value, "authority_class", PRODUCTION_AUTHORITY)
```

An input that exposes no authority metadata is therefore treated as production-authoritative. This is fail-open and permits legacy, descriptive, or experimental objects without an explicit authority declaration to affect authoritative scoring.

Required remediation:

- Require an explicit, typed authority classification on every scored input.
- Missing authority metadata must reject, not default to production.
- Preserve descriptive or experimental records only as non-scoring context.

### 2. Identity enforcement is incomplete and fail-open

File: `src/hunter/committee/authority.py`

Symbol: `_validate_identity`

Current enforcement checks only `project_id`, and absence of `project_id` is accepted. It does not enforce the required entity, asset, chain, contract, token representation, or other representation scope.

Required remediation:

- Introduce a typed committee identity contract.
- Require the identity dimensions applicable to each family.
- Reject missing required identity fields.
- Reject cross-project, cross-entity, cross-chain, and cross-representation inputs before scoring.

### 3. Correction-lineage enforcement does not establish canonical-current authority

File: `src/hunter/committee/authority.py`

Symbol: `_validate_correction_state`

The implementation rejects local flags such as `is_superseded` and `is_invalidated`, plus lifecycle values exactly equal to `superseded`, `invalidated`, or `retracted`. It does **not** reject a generic lifecycle value such as `inactive`, and it does not verify that the record is the authoritative current member of its correction lineage as known by Hunter at the cycle cutoff. A stale predecessor without the recognized flags or lifecycle strings can still pass.

Required remediation:

- Reject every non-active lifecycle state according to a typed lifecycle policy; specifically, `lifecycle_state="inactive"` must not pass.
- Validate correction lineage through authoritative persisted lineage identifiers or repository resolution.
- Resolve the record valid and known at the replay cutoff.
- Reject superseded predecessors even when caller-visible convenience flags are absent or forged.

### 4. Known-by-Hunter replay cutoff is not fully enforced

Files:

- `src/hunter/committee/service.py`
- `src/hunter/committee/authority.py`

The boundary rejects future timestamps carried by the object, but it does not verify through the authoritative repository that the referenced persisted record was actually known by Hunter at the requested cutoff and was the record selected by point-in-time reconstruction.

Required remediation:

- Resolve each input ID through its authoritative repository at the cycle cutoff.
- Compare the supplied object with the repository-resolved immutable record.
- Reject caller-constructed or altered objects that merely carry plausible IDs and timestamps.

### 5. No complete deterministic regression suite for the new policy boundary

PR #62 changes production code but does not establish the complete test coverage required by Issue #61 for:

- missing authority metadata;
- descriptive, experimental, unavailable, and unknown authority classes;
- stale raw and derived inputs;
- missing and mismatched identity dimensions;
- generic inactive lifecycle state;
- superseded correction lineage resolved from persistence;
- known-by-Hunter cutoff reconstruction;
- unavailable valuation families producing no neutral eligibility;
- complete persisted-input to committee, ranking, champion, and dashboard flow;
- source-record traceability and dashboard read-only behavior.

Required remediation:

Add focused deterministic tests before claiming Issue #61 complete.

## Scope conclusion

PR #62 closes only the first policy layer. It does not yet close Issue #61 or establish a fully authoritative end-to-end committee input boundary.

The next production PR must close the five blockers above. After that PR is merged with Ruff, Black, mypy, pytest, CI, and independent review green, canonical `main` must be audited again.
