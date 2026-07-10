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
- fusion configuration fingerprint
- weighting/contribution-model fingerprint
- identity schema version
- effective analytical window

Operational timestamps do not affect source `PipelineRun` identity.
Operational `created_at` timestamps are also excluded from fused record conflict semantics.

## Provenance

Fusion preserves:

- source Intelligence IDs
- engine IDs and versions
- plugin IDs and versions where available
- evidence IDs and references
- lineage-aware canonical evidence groups
- run IDs
- effective timestamps
- uncertainty and confidence breakdown
- corroboration, contradiction, dependency, and missing-evidence assessments
- unified signals, observations, insights, narrative, and graph structures

## Persistence

`FusedIntelligence` is converted to immutable `FusedIntelligenceRecord` and persisted through the existing repository and UnitOfWork boundaries. SQLAlchemy remains inside `src/hunter/persistence/`.

Persisted fused records include the full explainability payload required by persisted-only downstream consumers:

- contributions
- engine and plugin provenance
- corroboration assessment
- contradiction assessment
- dependency assessment
- missing evidence assessment
- unified signals
- unified observations
- unified insights
- unified narrative
- graph nodes and edges
- confidence breakdown
- configuration fingerprint
- contribution-model fingerprint
- source Intelligence IDs
- source run IDs
- effective analytical window

Pipeline integration is optional. If no fusion engine and target are supplied, pipeline behavior is unchanged.

## Alignment

Fusion inputs contribute only when explicitly aligned to the requested target. Supported target references are:

- project
- asset
- protocol
- chain
- sector
- narrative
- ecosystem

Project alignment uses the Intelligence project. Other target types use standardized target reference metadata or persisted `target_refs`.

## Evidence Deduplication

Canonical evidence grouping collapses equivalent evidence when:

- evidence IDs match
- source references match
- lineage keys indicate the same underlying evidence

The canonical group preserves all contributing evidence IDs, references, lineage keys, and source Intelligence IDs.

## Corroboration and Contradiction

Corroboration is deterministic and accounts for:

- signal direction
- signal confidence
- evidence reliability and freshness
- effective-time proximity
- source independence after dependency detection

Contradiction detection is deterministic and accounts for:

- opposing signal direction
- strength spread
- signal confidence
- evidence reliability and freshness
- effective-time proximity

## Configuration

Default configuration is stored in `configs/intelligence_fusion.yaml`.
