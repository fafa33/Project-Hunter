# Experimental Derived-Reasoning Persistence

## Authority and isolation

Probability assessments, Pattern assessments, Technology Necessity assessments, and standalone Committee assessments remain experimental under ADR 0016 and `ANALYTICAL_AUTHORITY_REGISTRY.md`. Persistence makes these outputs durable for research and replay; it does not promote them to production authority or establish a production score, ranking, recommendation, or decision.

The dedicated store defaults to `data/experimental/derived_reasoning.sqlite` in `configs/experimental_persistence.yaml`. It is disabled by default and physically separate from operational, Dashboard, Market Validation, and other production analytical stores. Opening it requires both explicit enablement and prior structural bootstrap. No unrelated CLI, runtime, status, automation, scheduler, or Dashboard command initializes it.

Bootstrap is opt-in through `bootstrap_experimental_store(path)`. It creates only the existing generic persistence schema at the explicitly supplied path. It inserts no analytical rows and performs no acquisition or network work.

## Record semantics

All records use the Phase 2.1 `AnalyticalRecord` and `AuthorizedAnalyticalWrite` contract. Domain services—not repositories—supply effective and recorded times, known-time policy, schema/model/configuration/methodology versions, confidence, missing evidence, source IDs and versions, evidence references, correction lineage, and the native domain payload.

| Native output | Experimental semantic type | Logical identity |
|---|---|---|
| Probability assessment | `experimental.probability-assessment` | semantic type plus target ID |
| Pattern assessment | `experimental.pattern-assessment` | semantic type plus target ID |
| Technology Necessity assessment | `experimental.technology-necessity-assessment` | semantic type plus technology ID |
| Standalone Committee assessment | `experimental.standalone-committee-assessment` | semantic type plus project ID |

Physical record IDs use the `experimental-derived` namespace and deterministically include semantic type, logical identity, native assessment identity, recorded time, payload, and predecessor. This prevents cross-type collisions. Native fields are preserved under `payload.native_assessment`; `payload.authority_classification` is always `experimental`.

Standalone Committee records are not Market Validation project results. The experimental repository rejects canonical Market Validation field claims such as `committee_decision` and `hunter_score`; Market Validation continues to own and persist its canonical committee fields independently.

## Writes, correction, and replay

The experimental repository accepts only service-authorized writes for the four allow-listed semantic types. Identical writes are idempotent, while reuse of an identity with different content fails. Corrections require an explicit predecessor with the same semantic type and logical identity plus a non-empty reason. Predecessors remain immutable and queryable in ordered lineage history.

Reads support exact identity, logical lineage, semantic-type retrieval, and strict-known as-of selection. Strict-known replay requires `effective_at` at or before the effective cutoff, `recorded_at` at or before the known-by cutoff, and an explicit `known_at` at or before that cutoff. Records whose known time is unavailable retain the stated limitation and are excluded from strict-known replay.

## Permitted and prohibited consumers

Permitted consumers are explicitly opt-in experimental analysis, research validation, replay, and audit tools that open the isolated store. These records must not feed Opportunity assembly, Market Validation, canonical Timing, production ranking, Dashboard API, desktop OperationalConsole, Operational Corpus authority claims, automation, or scheduler jobs. Any future production promotion requires a new ADR and compatible cutover contract; the existence of a persisted record is not promotion evidence.
