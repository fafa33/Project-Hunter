# Issue #340 — ADR 0035 Phase C terminal persistence implementation checkpoint

Exact baseline: `698fff713f68cf3391aa4f20b286016442cf54ee`.

This checkpoint starts the narrow Phase C implementation after PR #335. It is intentionally implementation-scoped and records the mechanical contract that the code/tests on this branch must satisfy before the Draft PR can leave Draft.

## Canonical write boundary

- Persist only already-decided Phase B `ResponseValidationExecutionResult` / refusal-equivalent canonical results.
- `ResponseValidator` remains semantic authority; persistence must not derive, alter, rank, upgrade, downgrade, or reinterpret validation state/reason/findings.
- Persistence atomically assigns `validation_recorded_at`; callers and workers cannot supply it.
- One immutable terminal generation-0 record exists per canonical `validation_event_id`; identical retry joins the same record and preserves the original recorded-at coordinate.
- For a repeated `validation_event_id`, idempotency requires exact equality of the complete canonical non-content tuple: decision kind/id/state, authorization id, attestation id, profile resolution/publication/version, validator contract identity/version, requested-output contract identity/version/hash, Source Handling resolution/fact/policy/registry/authorization-rule ids, response-capture/evidence-state, provider attempt/handoff/outcome/execution-profile/request-evidence lineage, pre-model build/prompt/intent/ledger/allocation/package ids, evidence-input identity, input mode, revalidation generation/predecessor, and refusal `available_authority` when applicable. Any mismatch is a hard conflict and must not mutate the existing record.
- Enforce `validation_cutoff <= validation_recorded_at`; incomparable or inverted chronology fails closed before append.

## Non-retention invariant

`TRANSIENT_NOT_RETAINED` response bytes must never be persisted, hashed, serialized, logged, reconstructed, or included in terminal-record diagnostics. Phase C stores only canonical non-content decision/lineage metadata already produced by Phase B.

The terminal payload hash is versioned as `sha256-canonical-noncontent-json-v1`: SHA-256 over UTF-8 bytes of canonical JSON containing only the immutable non-content record fields plus the persistence-owned timestamp and record identity. Canonical JSON uses sorted keys, no insignificant whitespace (`separators=(",", ":")`), and UTF-8 text without ASCII escaping. Response bytes are never an input to this hash.

## Read / replay boundary

- Reload must verify indexed SQL columns, payload identity/hash, event identity, attestation compatibility, and chronology.
- Strict-known terminal replay is bounded by the persisted `validation_recorded_at`; it must never substitute current/latest profile, Source Handling, requested-output, Model Adapter, or evidence state.
- Correction allocation/CAS/replay is explicitly deferred to the next slice.

## Required implementation order

1. Add immutable terminal record contract and identity.
2. Add persistence-owned write capability bound to the canonical `ResponseValidator`/terminal persistence service.
3. Add insert-only terminal table/indexes and atomic append-or-join.
4. Re-verify Phase B result + attestation lineage mechanically before append.
5. Add strict reload/corruption/chronology checks.
6. Add targeted success/refusal/idempotency/concurrency/tamper/non-retention tests.
7. Run neighboring Phase A/B tests and full `hunter_pr_preflight.py --mode normal`.

No correction, downstream promotion, provider redesign, Smart Prompt Machine, scheduler, Dashboard, or production activation belongs in this PR.
