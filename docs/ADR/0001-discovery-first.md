# ADR 0001: Discovery-First Architecture

## Decision

Hunter is a market discovery engine before it is a project analysis engine.

## Context

Hunter began with a configured project universe and evidence-backed deep analysis. That foundation is valuable, but it cannot identify the best long-term opportunities across crypto if the investable universe is manually bounded.

The architecture must continuously discover assets, protocols, networks, and ecosystems before deciding what deserves analysis.

## Alternatives

- Keep Hunter centered on a static configured project list.
- Build additional deep-analysis engines before expanding market discovery.
- Treat discovery as a peripheral data import rather than the system entry point.

## Reasoning

The highest investment value comes from seeing more of the market, filtering it safely, and prioritizing research. Discovery expands the opportunity set while allowing the existing deep-analysis runtime to remain stable.

Discovery-first architecture also prevents premature optimization around a narrow project set and creates a durable foundation for identity resolution, screening, queueing, competition intelligence, and future intrinsic value.

## Consequences

- The configured project list becomes a compatibility seed, not the complete universe.
- Discovery sources must be independent, typed, retried, checkpointed, and evidence-preserving.
- Coverage metrics must distinguish discovery coverage from analysis readiness.
- Future releases should improve market visibility before adding speculative intelligence.
