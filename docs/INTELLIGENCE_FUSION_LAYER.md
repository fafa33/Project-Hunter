# Intelligence Fusion Layer

The Intelligence Fusion Layer deterministically combines standardized Intelligence outputs into immutable FusedIntelligence artifacts.

## Scope

Fusion consumes only standardized `Intelligence` objects or persisted `IntelligenceRecord` objects. It does not call external providers, rank opportunities, issue trading recommendations, or mutate source intelligence.

Supported fusion targets:

- project
- asset
- protocol
- chain
- sector
- narrative
- ecosystem

## Architecture

The layer lives under `src/hunter/intelligence/fusion/` and is organized as small deterministic assessment modules:

- normalization converts standardized intelligence into `FusionInput`.
- deduplication removes repeated evidence identities and references.
- dependencies detects shared-evidence dependency edges.
- corroboration identifies independently corroborated signal categories.
- contradiction detects material signal-strength divergence.
- alignment scopes inputs to the requested fusion target.
- weighting produces engine contribution weights without hardcoded engine names.
- confidence combines contribution quality, corroboration, dependency, contradiction, and missing-evidence factors.
- narrative emits a deterministic unified narrative.
- graph emits serializable nodes and edges for provenance inspection.

## Identity

`FusedIntelligence.id` is a stable analytical identity derived from:

- fusion target
- source intelligence IDs
- fusion strategy
- effective timestamp
- fused confidence breakdown

Operational timestamps do not affect source `PipelineRun` identity.

## Provenance

Fusion preserves:

- source Intelligence IDs
- engine IDs and versions
- plugin IDs and versions where available
- evidence IDs and references
- run IDs
- effective timestamps
- uncertainty and confidence breakdown

## Persistence

`FusedIntelligence` is converted to immutable `FusedIntelligenceRecord` and persisted through the existing repository and UnitOfWork boundaries. SQLAlchemy remains inside `src/hunter/persistence/`.

Pipeline integration is optional. If no fusion engine and target are supplied, pipeline behavior is unchanged.

## Configuration

Default configuration is stored in `configs/intelligence_fusion.yaml`.
