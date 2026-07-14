# ADR 0005: Entity Model Separation

## Decision

Hunter must distinguish economic entities from their representations, including native assets, tokens, protocols, networks, wrapped assets, bridged assets, contracts, and provider listings.

## Context

Crypto identity is complex. One project may have a protocol, governance token, native asset, wrapped representations, bridge contracts, multiple deployments, and provider-specific listings. Treating all records as a single flat asset creates incorrect merges and misleading analysis.

## Alternatives

- Treat ticker symbol as the primary identity.
- Treat each provider listing as a separate canonical entity forever.
- Treat token contracts as proof of the full project identity.
- Delay entity modeling until valuation.

## Reasoning

Identity must be modeled before valuation. Hunter needs explicit structures that allow future phases to attach tokenomics, revenue, competition, network effects, liquidity, and intrinsic value to the correct level of analysis.

## Consequences

- Candidate records may represent projects, protocols, tokens, networks, infrastructure, or unknown entities.
- Identity Resolution must handle native-versus-wrapped, token-versus-protocol, migrations, forks, and chain-specific deployments.
- Future Entity Layer work should extend the registry without replacing it.
- Screening and reporting must avoid implying that a token movement, listing, or contract automatically represents full project value.
