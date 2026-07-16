# Global Discovery and Candidate Registry

Hunter now treats the configured 50-project universe as a seeded segment of a broader
market discovery registry. The registry is additive and does not change the canonical
analysis runtime, scoring, weighting, committee logic, timing algorithms, or replay
semantics.

Related ADRs:

- `docs/ADR/0001-discovery-first.md`
- `docs/ADR/0002-evidence-first.md`
- `docs/ADR/0003-candidate-registry.md`
- `docs/ADR/0004-trust-layer.md`
- `docs/ADR/0005-entity-model.md`

## Purpose

The candidate registry is the durable entry point for market-wide discovery. It stores
candidate assets and protocols before they are eligible for full project analysis, while
preserving deterministic identifiers and source references that support identity
resolution, candidate queues, screening, and future intrinsic value, competition
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

The registry stores the structures used by identity resolution: external identifiers,
aliases, public source references, and status fields for identity, queue, screening,
intrinsic value, competition, and network-effect phases. Candidate identity remains
deterministic and evidence-scoped unless a configured slug, trusted provider identifier,
or chain-aware contract identifier supports a stronger outcome.

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

## Identity Resolution

The Candidate Registry includes a deterministic Identity Resolution layer. Identity
resolution evaluates existing registry evidence and persists one outcome per candidate:

- `exact`
- `probable`
- `ambiguous`
- `conflict`
- `rejected`
- `unresolved`

Resolution uses only persisted registry evidence. Chain-aware contract identifiers,
configured Hunter project ids, trusted provider ids, official repositories, and official
links may improve confidence. Ticker equality is never sufficient for a merge. Ticker
collisions remain ambiguous unless stronger evidence exists. Provider metadata
disagreements are persisted as conflicts instead of being silently overwritten.

Identity outcomes update `identity_resolution_status` and may move discovered,
screenable, or evidence-pending candidates into `identified` when evidence supports an
`exact` or `probable` outcome. Identity Resolution does not implement intrinsic value,
competition intelligence, network effects, or investment scoring.

## Public Sources

Committed defaults support public CoinGecko, DefiLlama, GeckoTerminal, and DexScreener
discovery endpoints. CoinGecko contributes broad market-cap-ranked token coverage.
DefiLlama contributes protocol and TVL coverage. GeckoTerminal contributes decentralized
liquidity-pool token observations with chain and contract identifiers. DexScreener
contributes decentralized-market token profiles and boosted-token observations.

GeckoTerminal and DexScreener records are keyed by chain plus contract address when that
evidence is available. This allows cross-provider overlap without relying on ticker
symbols. Hunter records provider disagreements and source uniqueness through source
references and does not treat a single decentralized-market listing as proof of project
quality, official identity, or long-term investment merit.

Provider failures are reported as unavailable discovery runs and are not treated as
absence of market candidates. Adapters use bounded deterministic retry/backoff for
timeout, rate-limit, and temporary server failures; provider failure records the run as
unavailable and does not write partial registry state. Endpoint configuration can be
overridden with `HUNTER_DISCOVERY_<PROVIDER>_BASE_URL`, `HUNTER_DISCOVERY_<PROVIDER>_LIMIT`,
`HUNTER_DISCOVERY_<PROVIDER>_TIMEOUT_SECONDS`, `HUNTER_DISCOVERY_<PROVIDER>_MAX_ATTEMPTS`,
and `HUNTER_DISCOVERY_<PROVIDER>_BACKOFF_SECONDS`.

## Coverage

`hunter discovery coverage` reports registry coverage, source-provider coverage,
screening coverage, identity-resolution coverage, candidate lifecycle distribution,
automation job coverage, and missing evidence counts separately. These values are
intentionally not collapsed into a single completion score.

Discovery reporting also includes assets by provider, ecosystem, chain, category, provider
overlap, provider uniqueness, new candidates, and unique canonical candidate counts. These
metrics are designed for market visibility, not investment scoring.

## CLI

- `hunter discovery sync`
- `hunter discovery sync --provider coingecko --limit 250`
- `hunter discovery sync --provider defillama --limit 250`
- `hunter discovery sync --provider geckoterminal --limit 250`
- `hunter discovery sync --provider dexscreener --limit 250`
- `hunter discovery sync --provider all --limit 250`
- `hunter discovery run --limit 250`
- `hunter discovery status`
- `hunter discovery coverage`
- `hunter discovery report`
- `hunter discovery screen`
- `hunter discovery identity resolve`
- `hunter discovery identity coverage`
- `hunter discovery identity report`
- `hunter discovery identity conflicts`
- `hunter discovery queue refresh`
- `hunter discovery automation install`
- `hunter discovery automation status`
- `hunter discovery automation run-now`
- `hunter discovery candidate <slug-or-id>`
