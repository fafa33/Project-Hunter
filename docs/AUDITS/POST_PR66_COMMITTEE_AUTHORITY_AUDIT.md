# Post-PR66 Committee Authority Audit

## Scope

Canonical production state audited: `main` at merge commit `fe25bf07f8950f93610df400e3786bbcb566e5d6`.

Audited path:

`persisted input -> repository resolver -> authoritative service -> committee engine -> ranking -> champion persistence -> dashboard consumption`

## Result

**BLOCKED**

PR #66 materially closes the caller-controlled input gap for the repository families it supports, but the full production authority path required by Issue #61 is not yet demonstrated end to end.

## Verified

1. `AuthoritativeInvestmentCommitteeService` requires the concrete repository-backed resolver and validates every supported record before engine evaluation.
2. Supported families are resolved by persisted ID and family from Hunter SQL repositories.
3. Resolution rejects records unknown at the cycle cutoff and excludes future-effective revisions from canonical-current lineage selection.
4. Historical invalidation is evaluated relative to the cycle cutoff.
5. Explicit production authority, complete candidate identity, supplied-versus-persisted equality, chronology, lineage and freshness are enforced before scoring.
6. Repository record type and immutable serialized fingerprint are checked before scoring.
7. Raw critical-alert strings are blocked from authoritative evaluation.
8. Snapshot payload keys for valuation, comparative valuation, mispricing and asymmetry are blocked.
9. Opportunity, probability, pattern and necessity inputs fail closed because no approved repository mapping is exposed.
10. Committee ranking, champion consistency checking and atomic committee-cycle persistence remain in the authoritative service path.

## Remaining blockers

### B1 — Production runtime wiring is not demonstrated

`build_authoritative_committee_service()` exists as a composition root, but PR #66 provides no installed runtime, CLI, scheduler or orchestrator call path that constructs it and executes real committee cycles. The production system can therefore contain a correct authority component without actually using it.

Required closure evidence:

- a concrete production entry point uses the approved composition root;
- no alternative direct `InvestmentCommitteeEngine.evaluate()` or `select_champion()` path is used for authoritative outputs;
- a deterministic test exercises that production entry point.

### B2 — No persistence-to-dashboard end-to-end authority test

The new tests stop at committee service output and composition construction. They do not prove the required full path:

`authoritative SQL input -> resolver -> service -> persisted assessments/ranking/champion -> dashboard provider/API`

Required closure evidence:

- persisted authoritative inputs are inserted;
- the production entry point runs the cycle;
- ranking and champion are loaded from the authoritative committee repository;
- the dashboard reads those persisted records without recomputation or alternate inputs.

### B3 — Persistence origin binding is descriptive, not independently anchored

`repository_namespace` is a constant written by the resolver, and the fingerprint is calculated from the record returned by the injected repository object. This proves envelope/value consistency, but it does not independently prove that the repository object is the approved production database/session rather than a caller-supplied repository-compatible object.

Required closure evidence:

- the production composition root owns construction of the SQL engine/session/factory from approved runtime configuration;
- external callers cannot inject an arbitrary `RepositoryFactory`-compatible object into the authoritative runtime path;
- tests reject unapproved database/session origins.

### B4 — Generic inactive lifecycle state is not rejected

Resolution excludes records whose `invalidated_at` is effective at the cutoff, but it does not inspect a generic lifecycle/status field. A persisted record marked `inactive`, `withdrawn`, `deprecated` or equivalent can still pass when no `invalidated_at` metadata is present.

Required closure evidence:

- define the canonical active lifecycle states for supported persistence records;
- fail closed on any non-active or unknown lifecycle state;
- add deterministic tests for inactive, withdrawn, deprecated and unknown states.

### B5 — Generic snapshots remain a metric-level authority surface

The service blocks four unavailable valuation-family keys, but the engine still consumes decision-driving values such as `risk` and `backtesting_reliability` from generic `SnapshotRecord.payload`. Record-level production authority does not establish that each metric in the payload was produced by its canonical analytical owner.

Required closure evidence:

- define an allowlisted snapshot type/schema and metric-owner contract;
- reject unknown decision-driving payload keys and mismatched snapshot types;
- prove risk and backtesting metrics originate from their canonical owners, or keep them unavailable.

## Conclusion

PR #66 is a significant hardening step and closes the direct forged-input path for the currently mapped repository families. Issue #61 should remain open until B1 through B5 are closed and the complete production persistence-to-dashboard path is proven by deterministic tests.