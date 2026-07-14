# ADR 0003: Dynamic Candidate Registry

## Decision

Hunter uses a SQL-backed dynamic Candidate Registry as the durable canonical map of the discovered investable market.

## Context

Market-wide discovery can produce tens of thousands of assets and protocols. JSONL files and static configuration are not appropriate as the authoritative indexed registry for identities, aliases, source identifiers, lifecycle states, screening results, conflicts, and queue entries.

## Alternatives

- Continue using the configured 50-project universe as the primary registry.
- Store discovered candidates only in raw append-only evidence files.
- Create separate registries per provider.
- Push candidates directly into deep analysis without registry lifecycle control.

## Reasoning

The Candidate Registry provides indexed lookup, idempotent writes, lifecycle tracking, source references, screening state, queue state, and future attachment points for identity resolution, competition intelligence, network effects, and intrinsic value.

## Consequences

- Configured projects are imported as seed candidates.
- Provider-specific identifiers are preserved separately from canonical candidate identity.
- Ticker equality is never sufficient for merge.
- Registry writes must be idempotent and scalable.
- The registry becomes the entry point for market-wide triage, not a replacement for deep analysis.
