# Post-PR89 Observed Market Facts Authority Audit

## Verdict

**NOT APPROVED**

The merged implementation establishes canonical generic-SQL persistence and preserves strict-known
cutoffs, but it does not yet satisfy all mandatory Issue #88 domain, provenance, temporal, identity,
and conflict-handling requirements.

Issue #88 must remain open. A focused remediation PR and a subsequent independent post-merge audit
are required before closure.

## Audited repository state

- Canonical `main` merge commit: `bd1f58a5132616c7df611639b5af9b5c87cc46e9`
- Merged implementation PR: #89
- Audited implementation HEAD: `a17f758e9568c1531e9a18fc7e56703b9a8ffb5b`
- Final-head CI run: `30018618894`

## Verified controls

The audit verified that the merged implementation:

- uses canonical `data/data_ops.sqlite` through Hunter's generic SQL repositories;
- enforces service-owned repository write authorization;
- preserves immutable insert-or-identical behavior and rejects divergent identity reuse;
- appends correction successors without overwriting predecessors;
- applies `effective_at`, `recorded_at`, and `known_at` cutoffs during strict-known replay;
- excludes stale and explicitly unresolved-conflict records from strict-known selection;
- exposes exact-record, lineage, fact-window, and unresolved-conflict reads;
- records unavailable acquisition outcomes without synthesizing fact values;
- rejects the existing CoinGecko aliases for `valuation`, `comparative_valuation`, `mispricing`,
  and `asymmetry`;
- passed Ruff, Black, mypy, and pytest on the final PR #89 HEAD.

## Blocking findings

### 1. Required confidence is absent

Issue #88 requires every immutable observed-market-fact record to carry confidence bounded to
`[0,1]` and to reject invalid confidence.

`NormalizedMarketFact` and `ObservedMarketFactRecord` have no confidence field. The service,
persistence payload, and tests therefore cannot preserve, validate, or replay fact confidence.

Required remediation:

- add a decimal, non-binary-float confidence field to the normalized and persisted contracts;
- validate the inclusive `[0,1]` range;
- include confidence in deterministic content identity and SQL snapshot payloads;
- add rejection, round-trip, correction, and strict-known tests.

### 2. Exact provider source-record identity and version are absent

Issue #88 requires exact provider source record ID/version preservation. The current contract stores
provider ID, endpoint, parser version, and raw payload hash, but no provider source-record ID or
source-record version.

A payload hash is not a substitute for the upstream record identity/version because multiple
provider revisions or records may legitimately share a retrieval endpoint and parser.

Required remediation:

- add required `provider_source_record_id` and `provider_source_record_version` fields;
- validate nonblank provenance at the acquisition boundary;
- include both fields in record identity, persistence, and read reconstruction;
- prove exact round-trip and divergent-version behavior in tests.

### 3. Impossible `known_at` ordering is accepted

Issue #88 explicitly requires rejection when `known_at` precedes observation/effective time.

The service currently checks:

- `known_at >= requested_at`;
- `acquired_at >= requested_at`;
- `recorded_at >= known_at`;
- observation/effective time does not exceed acquisition time.

It does not require `known_at >= observed_at` and `known_at >= effective_at`. A result can therefore
claim that a fact was known before it was observed or became effective and still be persisted.

Required remediation:

- reject facts where `known_at < observed_at` or `known_at < effective_at`;
- add deterministic tests for both invalid orders.

### 4. Multiple-provider conflicts are not detected by the authority

Issue #88 requires preservation of independent provider observations, explicit source disagreement,
no silent winner selection, and an explicit versioned policy before authoritative selection.

The service trusts the provider-supplied `conflict_state` and does not compare a new accepted fact
with already-persisted eligible facts for the same logical window. Two providers can both persist
different values as `conflict_state="none"`. `strict_known_fact` then sorts eligible records and
returns one record, which silently selects a winner without a declared conflict-resolution policy.

The existing conflict test only injects `conflict_state="open"` manually; it does not prove automatic
multi-provider disagreement detection.

Required remediation:

- detect divergent eligible observations for the same logical fact window;
- preserve all observations and mark or derive the unresolved conflict through service-owned policy;
- make strict-known selection return unavailable while a material conflict is unresolved;
- require a versioned resolution policy before a resolved winner can become authoritative;
- add two-provider disagreement and no-silent-selection tests.

### 5. Canonical identity resolution is not proven

Issue #88 requires canonical entity/asset/representation resolution and rejection of provider-listing
or representation/chain mismatches before persistence.

The current checks ensure that identity fields are nonblank, chain and contract are jointly present,
and provider listing ID is not textually equal to the canonical IDs. The source registry authorizes
only the generic scope `canonical_asset_representation`; it does not bind a provider listing to one
specific canonical entity, asset claim, representation, chain, and contract tuple.

Consequently, a structurally valid but semantically mismatched canonical identity can be paired with
an otherwise valid provider listing and endpoint.

Required remediation:

- resolve the complete identity tuple through an authoritative, versioned mapping;
- bind provider listing IDs to exact canonical entity/asset/representation and chain/contract scope;
- reject cross-entity, cross-representation, and chain/contract mismatches;
- add deterministic mismatch and successful-resolution tests.

### 6. Valuation-family inputs are not unconditionally disabled

Issue #88 is foundation-only and prohibits activation of `valuation`, `comparative_valuation`,
`mispricing`, or `asymmetry`.

`_enforce_canonical_input_authority` rejects these engines only when
`source.source == "coingecko"`. An `AVAILABLE` source with another source label can pass through and
activate a valuation-family input even though no canonical valuation-family authority has been
authorized.

Required remediation:

- force all four engines to explicit `UNAVAILABLE` at this foundation stage, independent of source
  label;
- enable them only through a later separately authorized canonical contract;
- add tests proving that arbitrary non-CoinGecko source labels cannot activate any of the four
  inputs.

## Non-blocking documentation finding

`docs/IMPLEMENTATION_REPORTS/v3.4.0-observed-market-facts.md` still describes the old standalone
`data/market_facts/runtime/market_facts.sqlite` path. The runtime now correctly defaults to canonical
`data/data_ops.sqlite`, so the report should be updated to avoid documenting a prohibited authority.

## Required disposition

1. Merge this audit without closing Issue #88.
2. Open a focused remediation issue or PR covering all six blockers and the stale documentation.
3. Run Ruff, Black, mypy, and pytest on one exact remediation HEAD.
4. Merge the remediation only after its final HEAD is green.
5. Run another independent post-merge audit.
6. Close Issue #88 only if that later audit returns `APPROVED` and is merged.
