# ADR 0002: Evidence-First Outputs

## Decision

Hunter must preserve evidence provenance for every discovery record, analysis input, and meaningful output.

## Context

Investment intelligence is dangerous when it hides uncertainty. Crypto data sources are incomplete, inconsistent, stale, and sometimes wrong. Hunter must not turn weak evidence into confident conclusions.

## Alternatives

- Trust provider records as authoritative.
- Store only normalized outputs and discard source provenance.
- Fill missing fields with inferred values.
- Treat stale evidence as live evidence when current data is unavailable.

## Reasoning

Evidence preservation is required for explainability, historical replay, conflict detection, calibration, and trust. Without provenance, Hunter cannot distinguish observation from assumption or reproduce why a decision was made.

## Consequences

- Every source observation needs source identity, observation time, confidence, freshness, and reference data.
- Missing data remains unavailable.
- Conflicts are persisted and reported.
- Historical replay must consume only evidence that existed at the replay cutoff.
- Hunter must prefer explicit unavailable states over fabricated completeness.
