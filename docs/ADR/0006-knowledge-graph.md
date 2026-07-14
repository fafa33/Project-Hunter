# ADR 0006: Future Knowledge Graph

## Decision

Hunter should eventually use a knowledge graph for relationships between entities, technologies, dependencies, competitors, ecosystems, evidence, and investment theses, but it must not be introduced before identity and evidence foundations are stable.

## Context

Hunter already tracks technology dependencies, economic dependencies, on-chain surfaces, developer evidence, narrative evidence, and candidate relationships. Future competition intelligence, network effects, intrinsic value, and historical analogues will require relationship-aware modeling.

## Alternatives

- Keep all relationships embedded in isolated engine outputs.
- Introduce a graph database immediately.
- Replace existing repositories with a new graph runtime.
- Model relationships only in reports.

## Reasoning

A knowledge graph can improve long-term extensibility, but premature graph infrastructure would add complexity before entity identity and evidence quality are reliable. The correct path is to design current models so graph relationships can attach later.

## Consequences

- Current work should preserve stable ids, evidence ids, source ids, entity types, and relationship-ready metadata.
- The Candidate Registry and evidence repositories remain authoritative until an approved future ADR changes that boundary.
- Future graph work must reuse existing evidence and registry records instead of creating a competing runtime.
- Knowledge graph implementation remains out of scope until identity resolution and trust layers are production-stable.
