# Post-PR91 Observed Market Facts Authority Audit

## Verdict

**NOT APPROVED**

PR #91 resolves five of the six blockers recorded by the post-PR89 audit and implements the
detection half of the multiple-provider conflict contract. One mandatory conflict-resolution
control remains absent.

Issue #88 must remain open.

## Audited repository state

- Canonical `main` merge commit: `5fcdbf354a16a61c7bd1c4f217ecc943d2afbb50`
- Merged remediation PR: #91
- Audited remediation HEAD: `56db7a4e56938e0e96a9baf76730b20ba5eed331`
- Final-head CI run: `30023702606`

## Verified remediation

### 1. Bounded confidence

Verified.

The source registry declares decimal observation confidence, validates the inclusive `[0,1]`
boundary, incorporates the policy into its fingerprint, and passes it through normalized facts,
deterministic content identity, generic SQL snapshots, and typed reconstruction. Tests cover
round-trip persistence and out-of-range rejection.

### 2. Exact provider source-record identity and version

Verified.

Successful observations require and preserve `provider_source_record_id` and
`provider_source_record_version`. CoinGecko binds these to the provider listing ID and
`last_updated` version, the service rejects a source-record/listing mismatch, and both fields
participate in content identity and persistence. Legacy snapshots without the new provenance
contract are reconstructed fail-closed as unavailable.

### 3. Temporal ordering

Verified.

The service rejects `known_at < observed_at` and `known_at < effective_at` in addition to the
existing request, acquisition, and recorded-time constraints. Deterministic tests cover both
invalid orders.

### 4. Canonical identity binding

Verified.

The fingerprinted source registry binds each provider listing ID to one exact canonical
entity/asset/representation/chain/contract tuple. The service authorizes that complete tuple before
persistence, and the tests reject a semantically mismatched canonical entity.

### 5. Valuation-family non-activation

Verified.

Market Validation now converts `valuation`, `comparative_valuation`, `mispricing`, and `asymmetry`
to explicit unavailable inputs regardless of the supplied source label. Tests prove that a
non-CoinGecko source label cannot activate any of the four engines.

### 6. Multiple-provider conflict detection and fail-closed replay

Partially verified.

The service compares same-window observations, preserves divergent records independently, marks the
new divergent record as an open conflict, and makes strict-known selection return unavailable.
The test uses two independently registered providers and proves that no silent winner is selected.

## Remaining blocker

### Versioned conflict resolution is absent

Issue #88 requires an explicit policy and version before any downstream canonical selection can
resume after a source disagreement.

The current models have only `conflict_state`; they do not carry:

- a conflict-resolution policy ID;
- a policy version or fingerprint;
- the set of candidate record IDs evaluated by the policy;
- a selected record ID and resolution rationale;
- resolution effective, recorded, and known times.

There is also no service-owned conflict-resolution command. A provider-supplied fact may enter with
`conflict_state="resolved"` because the service accepts that state without requiring or validating
any resolution policy. The repository then treats `resolved` as strict-known eligible.

This leaves an authority bypass: conflict resolution can be asserted as input data rather than
created by a versioned service-owned decision.

Required remediation:

1. Reject provider-supplied `conflict_state="resolved"`.
2. Add an immutable conflict-resolution decision record containing policy ID/version or fingerprint,
   candidate record IDs, selected record ID, rationale, effective time, recorded time, and
   `known_at`.
3. Add a service-owned resolution command that validates the full candidate set and appends the
   decision without rewriting observations.
4. Make strict-known selection accept a winner only when an eligible resolution decision explicitly
   selects that exact record under the requested cutoff.
5. Add tests for unauthorized resolved-state rejection, valid versioned resolution, pre-resolution
   cutoff unavailability, post-resolution selection, incomplete candidate-set rejection, and
   deterministic replay.

## Documentation

The stale implementation report was corrected to name canonical `data/data_ops.sqlite`, Hunter's
generic SQL snapshot types, and the current migration identifier. No standalone market-facts
authority is documented or created.

## Quality gates

Verified on the final PR #91 HEAD in GitHub Actions:

- `ruff check .` — passed
- `black --check .` — passed
- `mypy` — passed
- `pytest` — passed

## Required disposition

1. Merge this audit without closing Issue #88.
2. Implement the remaining versioned conflict-resolution authority in a focused remediation PR.
3. Run all four quality gates on one exact final remediation HEAD.
4. Merge the remediation only after its final HEAD is green.
5. Run one further independent post-merge audit.
6. Close Issue #88 only if that audit returns `APPROVED` and is merged.
