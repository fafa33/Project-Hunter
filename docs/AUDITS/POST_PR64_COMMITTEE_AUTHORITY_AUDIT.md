# Post-PR64 Committee Authority Audit

## Audit target

Canonical branch: `main`

Audited merge commit: `23a29eeae2a7e8a2a9c2254b1f9db0dbd5464582`

Relevant remediation: PR #64.

## Final result

**BLOCKED**

PR #64 materially strengthens the service boundary, but the repository still does not demonstrate a complete production-authoritative input-resolution path.

## Verified improvements

- `AuthoritativeInvestmentCommitteeService` now requires an injected `CommitteeInputResolver`.
- Every scored raw record and derived assessment is resolved by ID and family at the cycle cutoff before scoring.
- Missing resolution rejects before evaluation.
- Caller-supplied values must equal the resolver-returned value.
- Authority class is explicit and missing or non-production authority rejects fail-closed.
- Candidate identity is typed and compared exactly across project, entity, representation, and optional chain scope.
- Future-known, future-effective, freshness, superseded revision, superseded timestamp, and invalidated timestamp checks are enforced against the resolved envelope.
- Ranking, champion consistency, and atomic committee-output persistence remain service-owned.
- Valuation, comparative valuation, mispricing, and asymmetry remain unavailable and are not synthesized.

## Blocking defects

### 1. No concrete production resolver is implemented or wired

Files inspected from PR #64:

- `src/hunter/committee/authority.py`
- `src/hunter/committee/models.py`
- `src/hunter/committee/service.py`
- `tests/test_committee_authority_resolution.py`

`CommitteeInputResolver` is only a `Protocol`. PR #64 does not add a concrete resolver that reads the authoritative persistence repositories, reconstructs known-at state, resolves canonical-current correction lineage, and returns immutable production envelopes.

An injected caller-controlled resolver can therefore manufacture `ResolvedCommitteeInput` objects. The service validates the envelope, but it does not establish that the resolver itself is an approved production authority.

Required remediation:

- Implement a concrete production resolver owned by the persistence/service architecture.
- Resolve each supported family through its authoritative repository.
- Reconstruct records known at `known_at` rather than trusting caller-provided metadata.
- Resolve canonical-current lineage from persistence, not caller-supplied `current_revision_id`.
- Prevent arbitrary external resolver implementations from being used by the production composition root.

### 2. Resolver output authenticity is not cryptographically or repository bound

`validate_authoritative_input()` compares `supplied` with `resolved.value`, but both can originate from the same untrusted resolver/caller path. Equality proves consistency between two objects; it does not prove persistence origin.

Required remediation:

- Bind the resolved envelope to repository-owned immutable records and source identifiers.
- Include repository namespace/type and authoritative record fingerprint or equivalent immutable binding.
- Reject envelopes whose value, identity, timestamps, authority, or lineage metadata do not match the repository record.

### 3. Production composition and migration compatibility are not demonstrated

The service constructor now requires `input_resolver`, but PR #64 does not show the production composition root, orchestrator, CLI, automation path, or runtime wiring being updated to supply an approved resolver.

Required remediation:

- Wire the concrete resolver into every authoritative committee execution path.
- Add a regression test proving no production path can instantiate the service without the approved resolver.
- Verify existing runtime and automation paths remain operational.

### 4. Test coverage is policy-focused, not complete end-to-end authority coverage

The added tests validate envelope policy behavior, but do not prove:

- persistence write -> cutoff-aware repository reconstruction -> resolver -> committee evaluation;
- canonical correction-lineage selection from stored revisions;
- rejection of a forged custom resolver in production wiring;
- complete persisted-input -> ranking -> champion -> dashboard projection flow;
- source-record traceability from dashboard output back to the exact resolved records.

Required remediation:

Add deterministic integration tests using real repositories and the production resolver. The tests must cover current, stale, future-known, future-effective, superseded, invalidated, cross-project, cross-chain, cross-representation, unknown ID, forged resolver, and corrected-record cases.

## Scope conclusion

PR #64 closes the local validation-envelope defects from PR #63, but it does not yet establish that authoritative committee inputs are actually resolved from approved production persistence.

Issue #61 must remain open until a concrete repository-backed resolver, production wiring, and end-to-end regression coverage are merged and a fresh audit of canonical `main` is approved.
