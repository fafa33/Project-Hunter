# Global Discovery and Candidate Registry

Hunter now treats the configured 50-project universe as a seeded segment of a broader
market discovery registry. The registry is additive and does not change the canonical
analysis runtime, scoring, weighting, committee logic, timing algorithms, or replay
semantics.

## Purpose

The candidate registry is the durable entry point for market-wide discovery. It stores
candidate assets and protocols before they are eligible for full project analysis, while
preserving deterministic identifiers and source references that future phases can attach
to identity resolution, candidate queues, screening, intrinsic value, competition
intelligence, and network-effect analysis.

## Storage

Operational state is stored in SQLite at `data/discovery/runtime/candidates.sqlite` by
default. This location is ignored by Git. The schema uses indexed lookups for candidate
slug, lifecycle status, discovery source, and external identifiers so discovery can scale
incrementally without full registry scans.

Registry writes are idempotent. Candidate ids are immutable once assigned; repeated
discovery refreshes update aliases, identifiers, source references, and freshness without
creating duplicate candidates. Batch imports use one transaction per batch instead of one
connection per candidate, which keeps large source imports practical as the registry grows.

## Lifecycle

Supported candidate lifecycle states are:

- `discovered`
- `identified`
- `evidence_pending`
- `screenable`
- `analyzable`
- `ranked`
- `deep_research`
- `rejected`
- `archived`

Configured Hunter projects are seeded as `analyzable` because the existing runtime can
already evaluate them. Public provider listings are inserted as `screenable` when they
have at least one deterministic external identifier. No investment ranking is inferred
from discovery alone.

## Identity Compatibility

This phase does not implement identity resolution. It stores the structures that future
identity resolution needs: external identifiers, aliases, public source references, and
placeholder status fields for identity, queue, screening, intrinsic value, competition,
and network-effect phases. Candidate identity remains deterministic and provider-scoped
unless an existing slug or identifier proves the candidate already exists.

Ticker symbols are retained as aliases, not canonical identifiers. Hunter does not merge
candidates from symbol equality alone, which keeps ticker collisions, wrapped assets, and
ambiguous identities unresolved until stronger evidence exists.

## Screening and Queue

Discovery includes an inexpensive screening pass and persistent candidate queue. Screening
uses only available registry evidence: deterministic identifiers, source provenance,
sector/category evidence, live adapter observation, and compatibility with the current
deep-analysis path.

Screening now applies deterministic minimum-quality gates:

- `advanced`: sufficient identifiers and source provenance, plus analyzable seed status
  or market/protocol measurement evidence.
- `deferred`: candidate exists but lacks enough evidence for prioritization.
- `rejected`: candidate fails deterministic quality checks such as blocked, spam, scam, or
  impersonation markers.

The queue ranks what Hunter should investigate next without treating popularity as
investment quality or excluding already known assets. Queue entries are keyed by candidate
id, so refreshes update priorities without duplicating entries.

## Public Sources

Committed defaults support public CoinGecko and DefiLlama discovery endpoints. Provider
failures are reported as unavailable discovery runs and are not treated as absence of
market candidates. Adapters use bounded deterministic retry/backoff for timeout,
rate-limit, and temporary server failures; provider failure records the run as unavailable
and does not write partial registry state.

## Coverage

`hunter discovery coverage` reports registry coverage, source-provider coverage,
screening coverage, candidate lifecycle distribution, automation job coverage, and missing
evidence counts separately. These values are intentionally not collapsed into a single
completion score.

## CLI

- `hunter discovery sync`
- `hunter discovery sync --provider coingecko --limit 250`
- `hunter discovery sync --provider defillama --limit 250`
- `hunter discovery sync --provider all --limit 250`
- `hunter discovery run --limit 250`
- `hunter discovery status`
- `hunter discovery coverage`
- `hunter discovery report`
- `hunter discovery screen`
- `hunter discovery queue refresh`
- `hunter discovery automation install`
- `hunter discovery automation status`
- `hunter discovery automation run-now`
- `hunter discovery candidate <slug-or-id>`
