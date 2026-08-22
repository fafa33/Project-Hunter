# ADR 0034 Phase A — Model Adapter Durable Attempt Foundation

## Traceability

- Governing Issue: #303
- Governing architecture: [ADR 0034](../ADR/0034-evidence-intelligence-model-adapter-provider-attempt-boundary.md) (Accepted)
- Starting `main` SHA: `b5719abac0142430b9431b4002a2f04172d6d399` (PR #302 merge commit; nothing landed after it)
- Phase: A of the Model Adapter implementation lifecycle. Provider transport and response capture are a separate later lifecycle.

## Architecture authorities applied

| Authority | How this contribution conforms |
|---|---|
| ADR 0034 | Implements the pre-dispatch half of the accepted boundary: execution profile, attempt-time Source Handling, request-evidence decision, durable-before-send attempt, single-use handoff. Stops before dispatch. |
| ADR 0033 | The Model Adapter is a consumer. Authority is resolved strict-known at the attempt cutoff, never created or overridden, and never trusted from a caller. Persistence independently rederives and verifies rather than trusting supplied state. |
| ADR 0031 | `EvidencePromptArtifact` and `EvidencePreModelBuildRecord` are consumed by identity and never mutated or re-canonicalized. Capability incompatibility fails closed. |
| ADR 0032 | Nothing is promoted to a project-neutral core. The contract stays Hunter-owned in `hunter.evidence_intelligence`. |
| ADR 0020 | Historical reads are strict-known at a recorded cutoff. Current or later authority is never substituted. |
| ADR 0009 | Authority lives in `ModelAdapterService`; `ModelAdapterPersistenceRepository` stores, reads, and verifies, but decides nothing. |
| ADR 0016 | Nothing here promotes anything to canonical or analytical authority. |

## Implementation scope

```text
EvidencePreModelBuildRecord + EvidencePromptArtifact
  -> ModelExecutionProfile              (immutable, versioned, secret-free)
  -> capability compatibility           (fail closed)
  -> attempt-time Source Handling       (strict-known at the attempt cutoff)
  -> ProviderRequestEvidence            (per-category, or governed unavailability)
  -> ModelAttemptRecord                 (durable before send, immutable)
  -> ModelHandoffRecord                 (single-use, bound to that exact snapshot)
  -> NoNetworkDispatchSeam              (STOP — zero network)
```

### New record families

| Family | Module | Mutability |
|---|---|---|
| `ModelExecutionProfile` | `model_adapter.py` | Frozen; identity derived from the full body, so any change is a new version |
| `ProviderRequestEvidence` | `model_adapter.py` | Frozen; either authorized durable evidence or explicit governed unavailability |
| `ModelAttemptRecord` | `model_adapter.py` | Frozen; never mutated into a terminal state |
| `ModelHandoffRecord` | `model_adapter.py` | Frozen; consumable exactly once |

### New services and seams

- `ModelAdapterService` — sole owner of Phase A authority.
- `NoNetworkDispatchSeam` — the Phase A terminus. Takes no endpoint, client, or credential and cannot be pointed at a network.

## Persistence mechanism

SQLite through the existing `EvidenceIntelligenceRepository` path, following the ADR 0031 `pre_model_persistence` pattern already established in this repository: append-only tables keyed by deterministic identity, storing `payload_hash` plus canonical `payload_json`, with hash verification on read.

Two tables are added: `model_attempt_records` and `model_handoff_records`. No existing table, column, or record is modified, so no data migration is required. The schema is created with `CREATE TABLE IF NOT EXISTS`, and the one-dispatch-per-attempt invariant is declared as `CREATE UNIQUE INDEX IF NOT EXISTS` so it also applies to a database created before that invariant existed.

## Transaction and atomicity model

Attempt and handoff are written inside one `BEGIN IMMEDIATE` transaction, attempt first. A committed handoff therefore always has a committed attempt behind it, and a failure to persist the attempt leaves no dispatch-capable handoff.

The Source Handling snapshot is taken once, by a single `resolve_pre_model_source_handling` call that resolves the fact, policy, field-category registry, and authorization rule together at the attempt cutoff and derives one decision. The handoff is built from that same resolved snapshot in the same call path, so there is no "resolve, wait, then independently authorize" window.

## Single-use consumption mechanism

Compare-and-set: `UPDATE model_handoff_records SET consumed_at = ? WHERE handoff_id = ? AND consumed_at IS NULL` inside `BEGIN IMMEDIATE`, with `rowcount != 1` treated as already consumed. This is a conditional write rather than a read-then-write, so concurrent consumers cannot both win. Proven under eight concurrent threads.

The invariant enforced is **one dispatch opportunity per attempt**, not merely one use per handoff — see the defect record below.

## Retry semantics

A retry is always a new attempt. It requires a new attempt cutoff, a fresh strict-known Source Handling resolution, a new `ModelAttemptRecord`, a new `ModelHandoffRecord`, and explicit `predecessor_attempt_id` lineage. Attempt ordinal and predecessor linkage are validated structurally: ordinal > 1 without a predecessor is rejected, and ordinal 1 with a predecessor is rejected. No prior attempt, handoff, cutoff, decision, or dispatch capability is ever reused. There is no blind-retry path in Phase A.

## Source Handling integration

Every attempt re-resolves authority at its own cutoff. Build-time authority is lineage and reconstruction evidence only and is never consulted for permission. A caller may pass a decision, but it is compared against independently rederived authority and rejected on any disagreement; it is never used in place of resolution. Unknown, missing, ambiguous, conflicting, or unresolved authority fails closed as a pre-dispatch refusal.

Durability is decided per category against the exact historical field-category registry. `processing_decision == ALLOW` grants no durability at all: exact bytes, `SOURCE_DERIVED_TEXT`, and `CONTENT_DERIVED_ID` are each checked independently, and the governing top-level retention decision is checked as well. When content categories are denied, the adapter records `REQUEST_EVIDENCE_UNAVAILABLE_BY_POLICY` with a governed reason code and persists no bytes, hash, size, or content-derived identity. The opaque dispatch capability is governed as `OPERATIONAL_METADATA` and is omitted entirely when that category is denied — no substitute identifier is fabricated.

## Replay semantics

Historical reads filter on `recorded_at <= cutoff`, so a record is invisible before it existed. `strict_known_request_evidence` returns exactly the evidence state that was durably authorized at the historical cutoff. When retention was prohibited, it returns the recorded unavailability; it never re-derives a hash or size from the still-available prompt artifact, and a later permissive authority does not change what a historical read returns.

## Secret-exclusion mechanism

Structural, not conventional. `ModelExecutionProfile.__post_init__` rejects, at construction:

- credential-bearing field and parameter names (`api_key`, `authorization`, `bearer`, `client_secret`, `session_token`, `cookie`, `private_key`, and related forms);
- credential-shaped values (`Bearer …`, `Basic …`, `sk-…`, `ghp_…`, `xox…`, `AKIA…`, PEM private-key headers);
- nested mappings, which are the usual route for a raw credential dictionary;
- raw `bytes`.

A `credential_slot_identity` may name a slot but may not carry a credential value. Because rejection happens at construction, secret material cannot reach the attempt, handoff, or durable request evidence at all — there is no redaction step to bypass.

## Zero-network statement

**This contribution makes zero live model or provider calls.** No provider SDK, HTTP client, endpoint, base URL, credential, or socket is introduced. The pipeline terminates at `NoNetworkDispatchSeam`, which records that a dispatch opportunity was consumed and reports `transmitted_bytes == 0`.

This is proven, not asserted: one test monkeypatches `socket.socket`, `socket.create_connection`, and `socket.getaddrinfo` to raise, and runs the entire prepare-and-consume pipeline. Further tests assert that neither new module references `openai`, `anthropic`, `google.genai`, `groq`, `ollama`, `httpx`, `requests`, or `urllib`, and that the seam exposes no provider surface.

## Test coverage

68 tests in `tests/test_model_adapter_phase_a.py`, written as paired negative/positive fixtures so a removed guard fails a test rather than silently widening authority.

Every reusable guard was verified non-vacuous by deliberately weakening it in source and confirming the corresponding regression fails. All ten guards are detected:

| Weakened guard | Detected |
|---|---|
| Capability compatibility check | yes |
| Attempt-time processing gate | yes |
| Caller-decision re-verification | yes |
| Content-category durability gate | yes |
| Secret field-name rejection | yes |
| Secret value-shape rejection | yes |
| Single-use compare-and-set | yes |
| One-handoff-per-attempt | yes |
| Rederived-authority verification at persistence | yes |
| Strict-known cutoff filter | yes |
| Build-bound capability identity | yes |
| Allocation belongs to the governing build | yes |
| Prohibited-capability secret screening | yes |
| Persistence request-evidence re-derivation | yes |

Two rounds of mutation testing were required. The first exposed one vacuously
covered guard (top-level retention denial); the second, run after review fixes,
exposed two more where an overlapping check masked the guard under test. Each was
isolated with a case only that guard can reject.

## Defects found by hostile self-review

Both were found by adversarial probing *after* the suite was green, and both are now fixed with regressions. They are recorded in `docs/DEFECT_REGISTRY.json`.

### `MA-001` — one attempt could authorize two dispatches

Single-use was enforced per handoff. Two handoffs differing only in expiry share one attempt identity, so each was independently consumable and a single attempt dispatched twice — exactly the duplicate-execution risk ADR 0034 exists to prevent. Fixed by enforcing at most one dispatch authorization per attempt, at both the service and storage layers.

### `MA-002` — the repository was a public authority bypass

`persist_attempt_and_handoff` validated internal lineage but not authority, so a caller who never went through `ModelAdapterService` could fabricate an attempt and handoff with entirely forged Source Handling identities and dispatch them. Fixed by applying ADR 0033's persistence invariant: persistence independently re-resolves the attempt authority and rejects any handoff whose bound fact, policy, registry, rule, disposition, or cutoff identities disagree. This is verification, not decision-making — the layer recomputes the outcome the authority already produced and refuses disagreement.

## Defects found by independent review

Independent review of the first head found six further real defects, each verified
against the code before fixing and each now carrying its own regression:

| Finding | Severity | Defect | Fix |
|---|---|---|---|
| `credential_slot_identity` unusable | Critical | The generic field-name scan matched the substring `credential`, so the modelled slot field could never be set and its value-shape check was unreachable — the documented contract was not implementable. | The one legitimately named field is exempt from name-based rejection and value-checked instead. |
| Secrets via `prohibited_capabilities` | P1 | That tuple was exempt from secret screening, yet it is hashed into `profile_identity`, which is persisted on the attempt and handoff — a durable credential-derived representation. | Every entry is screened before normalization. |
| Capability not bound to the build | P1 | The check compared two caller-supplied objects, so a profile and capability agreeing with each other but not with the build's own allocation could authorize a dispatch. | The governing allocation is now required and verified against the build, and the capability against the allocation. |
| Request evidence not re-derived at persistence | P1 | Persistence verified identity consistency but not permission, so a direct caller with content-denying authority could persist forged durable content. | Persistence independently derives the permitted state and rejects disagreement, including any prohibited content-derived material. |
| Authority verified outside the write transaction | P1 | Re-resolution happened before `BEGIN IMMEDIATE`, leaving an unbounded window. | Re-resolution moved inside the write transaction; the residual cross-substrate window is documented rather than papered over — see below. |
| Connections never closed | Major | `sqlite3.Connection.__exit__` ends the transaction but does not close, leaking handles and holding locks until garbage collection. | A context manager closes deterministically. |

### Residual limitation, stated plainly

The Source Handling authority store and the evidence database are separate
substrates, so `BEGIN IMMEDIATE` cannot also lock the authority store. Verifying
inside the write transaction narrows the exposure to that transaction, but a
restrictive successor backdated to at or before the attempt cutoff and published
within that window would not be observed. ADR 0034 makes an equivalent atomic
snapshot-to-handoff guarantee a precondition for provider **activation**, which
Phase A does not perform. Closing it fully requires a governed cross-substrate
commit primitive that does not exist yet, and inventing one inside an
implementation PR is exactly what Issue #303 forbids. It is recorded here as a
hard gate for the phase that introduces transport.

## Verification

Repository-canonical normal Pre-PR contract on the exact final head, using the pinned CI toolchain from `requirements/ci-constraints.txt`:

```text
python scripts/hunter_pr_preflight.py --mode normal
```

Results are recorded in the pull request against its exact final head SHA.

## Deferred boundaries

Phase A deliberately does **not** implement, and this contribution does not authorize:

- provider transport, provider SDK, endpoint, or credential;
- live model or provider invocation;
- `ProviderResponseArtifact` or any response capture;
- `ModelAttemptOutcomeRecord` terminal outcome families beyond the minimum pre-dispatch refusal semantics;
- uncertain-delivery reconciliation, which belongs with the transport that can produce it;
- `ResponseValidator`, semantic validation, extraction promotion, or canonical knowledge promotion;
- provider or model routing, fallback, ranking, or dynamic selection.

The legacy `AIExtractionProvider` / `SecureAIProviderRunner` path is untouched, is not reused, and is not relabeled as ADR 0034-conformant. Conformance obligations CO-01 through CO-23 remain the governing gates for the later phases; Phase A satisfies the pre-dispatch subset only and discharges none of the transport or response obligations.
