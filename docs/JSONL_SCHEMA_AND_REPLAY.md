# JSONL Schema And Replay Contract

## Scope And Authority

Acquisition, Macro, Whale, and canonical Timing retain their existing domain JSONL layouts and repositories. New service-authorized writes add a common metadata member without moving any domain to SQL or changing analytical authority:

```json
"_record_metadata": {
  "schema_version": "hunter-<domain>-jsonl-v1",
  "effective_at": "...",
  "recorded_at": "...",
  "known_at": "... or null",
  "known_time_limitation": "... or null"
}
```

Domain fields remain at the top level, so normalized readers continue returning the existing payload shapes. The service supplies `JsonlWritePlan`; repositories only validate, serialize, append, normalize, and filter an explicit query boundary.

## Time Semantics

- `effective_at` is the existing source/event/assessment time represented by the record. Existing domain timestamp fields remain unchanged.
- `recorded_at` is the service-authorized time Hunter durably writes the record.
- `known_at` is present only where explicit provenance establishes when Hunter had the information. It cannot exceed `recorded_at`.
- `known_time_limitation` is required when `known_at` is unavailable and is absent when `known_at` is known.

The shared reader rejects naive or malformed timestamps, invalid known-time combinations, non-object metadata, and unsupported schema versions deterministically.

## Domain Versions And Known Time

| Domain | Version | Effective time | Known-time policy for new production writes |
| --- | --- | --- | --- |
| Acquisition | `hunter-acquisition-jsonl-v1` | Raw retrieval, normalization, validation, checkpoint, or completed-run time according to record kind | Retrieval/normalization/validation/checkpoint/completion timestamps are explicit Hunter processing boundaries and are supplied by `AcquisitionPipeline` |
| Macro | `hunter-macro-jsonl-v1` | Provider metric source time, failure occurrence, or snapshot generation | Current providers expose source/event time but no complete Hunter known-time provenance; records state that limitation and remain non-strict |
| Whale | `hunter-whale-jsonl-v1` | Provider event time, failure occurrence, or snapshot generation | `retrieval_time` is the explicit known time for evidence; failure occurrence is explicit; snapshot known time is the latest included retrieval time, or unknown when empty |
| Timing | `hunter-timing-jsonl-v1` | Assessment/run generation time | Current dependency inputs do not expose a complete known-time boundary; records state that limitation and remain non-strict |

These classifications are conservative. A source timestamp is never relabeled as recording or known time merely because it exists.

## Legacy Compatibility

An object without `_record_metadata` is a legacy unversioned record. Domain readers remove metadata before constructing existing models, so legacy and versioned records share the same domain-facing shape.

Legacy records remain available to ordinary current-state readers but expose:

```text
legacy unversioned record has unknown recorded/known-time provenance
```

No missing timestamp, source version, or provenance is inferred. Legacy records are never strict-known eligible.

## Strict-Known Replay

Strict-known queries require an explicit timezone-aware `as_of` and return only records for which:

- schema version is supported;
- effective time is on or before the cutoff;
- recorded time is on or before the cutoff;
- explicit known time is on or before the cutoff; and
- no replay limitation exists.

The filter does not fall back to a latest/current record. Unknown-known-time, legacy, future-effective, late-recorded, and late-known records are excluded.

## Backward-Compatible Write Boundary

Existing repository methods retain an optional unversioned compatibility path for direct legacy callers and fixtures. Production Acquisition, Macro, Whale, and Timing services supply versioned write plans. Compatibility writes remain explicitly legacy on read and cannot claim strict-known replay eligibility. This avoids inventing service authority inside repositories while preserving existing callers.

No existing JSONL file is rewritten or upgraded in place.
