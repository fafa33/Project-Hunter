# Intelligence Layer

## Architecture

The Intelligence Layer is the canonical foundation for analytical output in Project Hunter.

Every analytical engine must produce standardized `Intelligence` objects rather than emitting unrelated score, report, ranking, or ad hoc structures. Downstream systems may transform intelligence into scores, reports, rankings, alerts, or opportunity timing, but engines must first express their findings through the Intelligence Layer.

The layer is intentionally independent from scoring, reporting, ranking, automation, scheduling, dashboards, and opportunity timing.

## Intelligence Flow

The canonical flow is:

1. Engine performs analysis.
2. Engine produces signals, evidence, observations, and insights.
3. Engine emits an `Intelligence` object through `PipelineContext`.
4. Intelligence is validated.
5. Intelligence is registered or aggregated.
6. Downstream systems consume validated intelligence.

No engine may bypass this layer when producing knowledge for the platform.

## Data Model

The core immutable objects are:

- `Signal`: a categorized analytical signal with strength, severity, confidence, timestamp, source, and metadata.
- `Evidence`: collected source material with reliability, freshness, reference, raw data, collection time, and source.
- `Observation`: an evidence-backed description produced by an engine for a project.
- `Insight`: a higher-level explanation supported by observations.
- `Intelligence`: the complete engine output containing signals, evidence, observations, insights, confidence, generation time, project, engine, and metadata.

Objects are immutable by construction. Collections are normalized to tuples.

## Confidence Model

Every intelligence object includes a standardized confidence model.

Confidence contains:

- `score`
- `completeness`
- `evidence_quality`
- `freshness`
- `uncertainty`

The confidence score is deterministic and bounded between `0.0` and `1.0`.

Confidence measures implementation and evidence quality. It is not a price forecast, ranking signal, or opinion score.

## Registry

The `IntelligenceRegistry` maintains canonical access to registered intelligence types and engine outputs.

It supports:

- Registering intelligence types.
- Registering engine output.
- Lookup by engine.
- Lookup by project.
- Lookup by category.

The registry validates intelligence before storing it and rejects duplicate intelligence ids.

## Aggregation

The `IntelligenceAggregator` combines validated intelligence from multiple engines into an `IntelligenceCollection`.

Aggregation does not score, rank, recommend, decide, or forecast.

Aggregation only creates a unified collection and deterministic aggregate confidence from the included intelligence objects.

## Integration

Plugins may emit intelligence through `PipelineContext.emit_intelligence`.

The pipeline consumes intelligence from `PipelineContext.intelligence`.

Future reports, rankings, scoring systems, and opportunity timing systems must consume validated intelligence objects rather than engine-specific structures.

## Future Extensions

The Intelligence Layer supports future modules including:

- Macro Intelligence.
- Whale Intelligence.
- Developer Intelligence.
- Protocol Intelligence.
- News Intelligence.
- Social Intelligence.
- On-chain Intelligence.
- Governance Intelligence.
- AI Intelligence.
- Portfolio Intelligence.
- Opportunity Timing.

Future modules must preserve the same intelligence contract and validation rules.

