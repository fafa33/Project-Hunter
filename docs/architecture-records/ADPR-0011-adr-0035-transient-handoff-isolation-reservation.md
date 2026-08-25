# ADPR-0011 — ADR 0035 transient handoff isolation and event reservation

Status: Draft architecture correction

Governing issue: #336

Exact baseline: `40b4bed1cee8568840feea6e8401da49d4fe6d67`

Blocked implementation: Issue #334 / PR #335

## Scope

This preparation is intentionally narrow. It exists only to close two architecture gaps discovered during ADR 0035 Phase B exact-head review:

1. the mechanically enforceable isolation topology for exact provider response bytes when Source Handling forbids durable retention; and
2. the atomic reservation rule for one single-use transient capture across canonical validation events.

No Phase B implementation is authorized by this document itself.

## Owner-selected direction to formalize

### Process isolation for non-retained exact response bytes

Same-process Python privacy conventions are insufficient as an authority boundary. When exact provider response bytes are required for semantic validation while durable retention is forbidden, the canonical consumer must execute across a real process boundary. The caller-facing process must not hold a readable copy, readable descriptor, or equivalent capability for the exact transient body.

The correction must preserve ADR 0033 Source Handling authority, ADR 0034 Model Adapter / response-capture authority, and ADR 0035 ResponseValidator authority.

### One transient capture, one canonical validation event

A single-use transient capture must be atomically reserved to exactly one canonical `validation_event_id` at authorization time. The first successful canonical reservation owns the one-shot capture. Another event, including explicit re-validation, must not reuse the same transient capture.

If explicit re-validation still requires exact response bytes after the original capture has been reserved/consumed and those bytes were not durably retained, the re-validation path requires a fresh upstream capture/observation under the governing upstream contracts.

Retry/join of the same canonical event must preserve the same reservation/result semantics and must not mint a sibling reservation.

## Explicit non-goals

- terminal `ResponseValidationRecord` persistence
- `validation_recorded_at`
- correction allocator/CAS/replay
- provider routing/fallback redesign
- downstream extraction/promotion
- Issue #315
- DefiLlama integration
- production activation
- implementation of PR #335

## Implementation-adjacent findings outside this architecture correction

Two open PR #335 findings remain ordinary implementation defects and do not require architecture selection:

- JSON Schema `integer` must accept mathematically integral finite decimals such as `1.0`;
- syntactically valid very-large JSON integers must map to resource/rule unavailability rather than `INVALID_SYNTAX`.

## Required review outcome

The architecture review must determine whether this owner-selected direction is internally consistent with ADRs 0031, 0033, 0034, and 0035 and whether any additional authority conflict is exposed. If no substantive conflict is found, the correction should be transposed narrowly into ADR 0035 and accepted through the repository's normal architecture lifecycle.
