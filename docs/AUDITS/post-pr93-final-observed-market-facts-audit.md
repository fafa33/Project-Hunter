# Post-PR93 Final Observed Market Facts Authority Audit

## Verdict

**APPROVED**

No remaining blocker was identified against Issue #88, ADR 0021, or the observed-market-facts
completion criteria.

## Audited repository state

- Canonical `main` merge commit: `c3b9c5aba9ae1bce59860eccda7f7651f48f3da3`
- Merged final remediation PR: #93
- Audited remediation HEAD: `507ed2226a9456d3249dcd41d7edc57f1dadf722`
- Final-head CI run: `30054132516`
- Market-facts schema contract: `market-facts-v3.4.2`

## Final authority verification

### 1. Domain contract

Verified.

Observed facts carry deterministic record and logical IDs, schema and semantic versions, canonical
entity/asset/representation identity, chain/contract scope, provider listing and exact provider
source-record identity/version, typed fact kind, decimal value, unit and quote currency, venue
scope, effective/observed/recorded/known times, raw payload hash, bounded decimal confidence,
quality/conflict state, and correction lineage.

Unknown facts, invalid decimal values, invalid confidence, naive times, impossible temporal order,
incomplete identity, identity/listing mismatches, invalid units, and missing provenance fail closed.

### 2. Service-owned ingestion

Verified.

The service validates source registration, capability, endpoint, parser, registry fingerprint,
canonical identity binding, fact-specific units and quote requirements, provenance, temporal order,
quality, duplicates, corrections, and conflicts before issuing an authorized repository write plan.

Providers cannot directly persist records or assert `conflict_state="resolved"`.

### 3. Canonical persistence and replay

Verified.

Observed facts, availability evidence, and conflict-resolution decisions persist through Hunter's
generic SQL snapshot repository in canonical `data/data_ops.sqlite`. No standalone market-facts or
valuation database is created.

Persistence is immutable insert-or-identical, divergent identity reuse is rejected, corrections
append successors, and strict-known reads enforce effective, recorded, and known cutoffs with
deterministic ordering.

Legacy snapshots missing the canonical confidence and source-record provenance contract reconstruct
fail-closed as unavailable.

### 4. Multiple-provider conflict authority

Verified.

Divergent same-window observations from independently registered providers are preserved
individually. The service marks the disagreement open, `unresolved_conflicts` exposes it, and
strict-known replay returns unavailable instead of averaging or silently selecting a winner.

Resolution is service-owned and immutable. `MarketFactConflictResolution` records:

- the complete candidate record set;
- the selected record;
- policy ID, version, and deterministic fingerprint;
- rationale;
- candidate effective time;
- decision effective, recorded, and known times.

Only `highest-confidence-then-record-id@1.0.0` is authorized. The service independently evaluates the
policy and rejects an unknown policy, wrong selected record, incomplete candidate set, missing
candidate, mixed logical lineage/effective window, or non-divergent set.

Before the resolution is effective, recorded, and known by the requested cutoffs, strict-known
selection remains unavailable. After all cutoffs, only the exact policy-selected record is eligible.
The resolved group leaves the current unresolved-conflict read while unrelated open groups remain.

### 5. Canonical identity and provenance

Verified.

The fingerprinted source registry binds provider listing IDs to exact canonical
entity/asset/representation/chain/contract tuples and declares the bounded observation-confidence
policy. Exact provider source-record ID/version, parser version, endpoint, raw hash, and registry
fingerprint survive deterministic identity, persistence, and typed reconstruction.

### 6. Valuation-family non-activation

Verified.

`valuation`, `comparative_valuation`, `mispricing`, and `asymmetry` remain explicitly unavailable in
Market Validation regardless of source label. The observed-market-facts foundation does not create
fair value, comparative valuation, mispricing, scenario probability, asymmetry, ranking, or
recommendation authority.

### 7. Read boundaries and missingness

Verified.

Typed reads cover exact record ID, logical lineage, exact fact window, strict-known selection,
unresolved conflicts, and immutable conflict-resolution history. Unavailable provider outcomes
create operational evidence only and never synthesize zero, previous, median, current/latest, or
neutral fallback facts.

### 8. Deterministic tests and final-head quality gates

Verified.

The focused tests cover valid observed facts, invalid types/units/values/times/identity/provenance,
idempotency, divergent duplicates, append-only corrections, strict-known cutoffs, stale facts,
multiple-provider disagreement, unauthorized repository writes, confidence, canonical persistence,
valuation-family non-activation, and versioned conflict resolution.

GitHub Actions passed on exact remediation HEAD `507ed2226a9456d3249dcd41d7edc57f1dadf722`:

- `ruff check .` — passed
- `black --check .` — passed
- `mypy` — passed
- `pytest` — passed

## Issue #88 disposition

Issue #88 satisfies its completion criterion after this audit is merged into `main`.

The issue must remain open until this audit PR is merged. After that merge, Issue #88 may be closed.
