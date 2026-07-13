# Discovery Runtime Storage

The candidate registry stores operational discovery state under `data/discovery/runtime`.
Runtime SQLite files are intentionally ignored by Git because the registry is incremental
operational state, not a source fixture.

Committed configuration and tests remain small and deterministic. Re-running discovery
syncs is idempotent: candidates, identifiers, aliases, sources, and runs are keyed by
stable identities and indexed for lookup by slug, lifecycle status, source, and external
identifier.
