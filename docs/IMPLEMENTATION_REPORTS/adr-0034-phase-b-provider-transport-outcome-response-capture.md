# ADR 0034 Phase B — First Provider Transport, Attempt Outcome, and Governed Response Capture

## Traceability

- Governing Issue: #313
- Governing architecture: [ADR 0034](../ADR/0034-evidence-intelligence-model-adapter-provider-attempt-boundary.md) (Accepted)
- Starting `main` SHA: `0b6095074fd5e9685b7ccbb9f3300061b41b2121` (PR #190 merge commit; nothing landed after it)
- Phase: B of the Model Adapter implementation lifecycle. `ResponseValidator` is the next planned lifecycle and is not implemented here.

## Architecture gate

Every Phase B semantic was checked against accepted architecture before any code was written. **No materially new architecture decision, authority owner, persistence semantic, retry semantic, or response semantic was required.** Each requirement maps to an existing clause of ADR 0034:

| Phase B requirement | Already-authorized clause |
|---|---|
| One provider-specific transport behind the adapter | ADR 0034 § Provider transport boundary |
| Single-use handoff governing the real send | § Single-use handoff |
| Append-only `ModelAttemptOutcomeRecord` and its outcome table | § Append-only attempt outcome |
| Governed `ProviderResponseArtifact` and per-category durability | § Provider response evidence |
| Pre-construction credential exclusion at capture | § Provider response evidence, § Credentials and secrets |
| Uncertain delivery, idempotency classification, retry-as-new-attempt | § Uncertain delivery, idempotency, and retry |
| Response captured but persistence failed | § Response-capture persistence failure |
| Historical reconstruction distinguished from re-invocation | § Historical reconstruction versus re-invocation |
| Exactly one configured profile; no routing | § Routing boundary, § Execution profile |

Two deferred items ADR 0034 makes hard runtime gates were resolved rather than assumed:

- **Field-category coverage.** The governed field-category registry expresses every request and response durable category the adapter writes, using the existing category vocabulary (`AUDIT_FIELD`, `SOURCE_BYTES`, `SOURCE_DERIVED_TEXT`, `CONTENT_DERIVED_ID`, `OPERATIONAL_METADATA`). No Source Handling registry extension was needed, and none was invented.
- **Idempotency classification.** The transport's idempotency, correlation, and reconciliation semantics are classified on the execution profile. `UNAVAILABLE` and unknown are both treated as retry-blocking; neither ever defaults to retry-safe.

The third gate — the atomic snapshot-to-handoff guarantee — retains the residual cross-substrate window Phase A recorded and did not close. Phase B does not close it and does not claim to; see **Known limitations**.

## Architecture authorities applied

| Authority | How this contribution conforms |
|---|---|
| ADR 0034 | Implements the dispatch half of the accepted boundary and stops exactly where the ADR ends it: transport-level capture, outcome, and governed response persistence, with no validation. |
| ADR 0033 | The adapter remains a consumer. Response durability is resolved from the exact historical authority at the attempt cutoff; persistence independently rederives it and rejects disagreement. No handling fact or policy is created here. |
| ADR 0031 | `EvidencePromptArtifact` is read for its content and never mutated. The provider-facing representation is a distinct transient object, not a new prompt version. |
| ADR 0032 | Nothing is promoted to the project-neutral core. The transport contract stays Hunter-owned in `hunter.evidence_intelligence`. |
| ADR 0020 | Outcome and response reads are strict-known at a recorded cutoff. Current authority is never substituted, and prohibited historical response content is never regenerated. |
| ADR 0009 | Authority lives in `ModelAdapterService`; the transport does wire mechanics only; the repository stores, reads, and verifies but decides nothing. |
| ADR 0016 | A successful provider call creates no canonical or analytical authority and no registry entry. |

## Implementation scope

```text
PreparedModelAttempt (Phase A: durable attempt + single-use handoff)
  -> verify the bound profile and its exact transport      (no selection input exists)
  -> verify the supplied authority is the prepared one
  -> verify no terminal outcome already closed this attempt
  -> TransportRequest                                      (transient, non-secret, no credential)
  -> consume_handoff_once                                  (compare-and-set) -> DispatchAuthorization
  -> transport.send(...)                                   (exactly one invocation)
  -> classify_transport_result                             (certainty governs, never the failure class)
  -> capture gate + per-category durability                (two independent gates)
  -> ProviderResponseArtifact OR governed unavailable state
  -> ModelAttemptOutcomeRecord                             (append-only)
  -> [BOUNDARY ENDS] no ResponseValidator, no promotion, no routing
```

### New and changed files

| Path | Change |
|---|---|
| `src/hunter/evidence_intelligence/model_adapter_transport.py` | New. Transport contract, credential and dispatch-authorization boundaries, and the OpenAI chat-completions transport. |
| `src/hunter/evidence_intelligence/model_adapter.py` | Adds the outcome and response record families, classification, retry derivation, capture gate, and the `dispatch` path. Phase A behaviour is unchanged apart from the retry precondition below. |
| `src/hunter/evidence_intelligence/model_adapter_persistence.py` | Adds insert-only outcome and response tables, `append_outcome`, independent authority re-verification for both, and strict-known reads. |
| `tests/model_adapter_fixture.py` | Adds the Phase B field-map surfaces, the single configured profile, and a deterministic transport double. |
| `tests/test_model_adapter_phase_b.py` | New. 125 adversarial regressions. |
| `tests/test_model_adapter_phase_a.py` | Two retry tests now durably record the predecessor outcome the new retry gate requires. Strengthened, not weakened — they exercise the real retry path instead of bypassing it, and a fabricated record no longer satisfies the gate. |
| `docs/ADR/0034-...md` | `Implementation Status` only. No architectural decision changed. |
| `docs/architecture-index.md` | Truthful runtime-state rows for Issue #313. |
| `docs/DEFECT_REGISTRY.json` | Nine new guarded classes, `MA-004`–`MA-012`, plus a strengthened `MA-006`. |

### New record families

| Family | Mutability |
|---|---|
| `ModelAttemptOutcomeRecord` | Frozen; append-only in storage. A correction is a new record carrying `supersedes_outcome_id`. |
| `ProviderResponseArtifact` | Frozen; either authorized durable evidence or an explicit governed unavailable state with no content-derived material. |

## Provider choice

OpenAI Chat Completions, over the repository's existing stdlib `urllib.request` convention. No provider SDK and no third-party HTTP dependency was added, so the governed dependency set is unchanged for one endpoint. The transport is the narrowest viable first transport: one endpoint, one protocol, one profile.

## Outcome taxonomy and delivery certainty

Two axes are kept separate because collapsing them is how ambiguity becomes a duplicate billable call.

- **Delivery certainty** — `ANSWERED`, `CONFIRMED_NOT_DELIVERED`, `UNKNOWN`. A transport claims `CONFIRMED_NOT_DELIVERED` only where the failure provably precedes request acceptance (connection refused, name resolution failure). A read timeout or a mid-flight reset is `UNKNOWN` and stays uncertain.
- **Execution evidence** — `PROVIDER_RETURNED_COMPLETION`, `NO_EXECUTION_ESTABLISHED`, `UNKNOWN`. A `429` and a `5xx` are `UNKNOWN`: neither proves the model did not run, and ADR 0034 forbids assuming otherwise.

`classify_transport_result` resolves `TIMEOUT` and `CONNECTION_FAILED` from certainty rather than from the failure class, and `ModelAttemptOutcomeRecord.__post_init__` enforces a required-certainty table, so an outcome whose certainty contradicts its own semantics cannot be constructed anywhere in the codebase. Local pre-send failure, execution failure, response-captured-persistence-failure, and uncertain delivery remain four distinguishable recorded states.

## Retry safety

`derive_retry_authorization` grants `RETRY_REQUIRES_NEW_ATTEMPT` only where the transport proved non-delivery or the provider established it rejected the request instead of executing it. Everything else blocks: uncertainty yields `RETRY_BLOCKED_DELIVERY_UNCERTAIN`, and an answered error that does not establish non-execution yields `RETRY_BLOCKED_RECONCILIATION_REQUIRED`.

`prepare_attempt` now requires the predecessor's own **durable** outcome for any `attempt_ordinal > 1`, resolved from persistence via `authoritative_outcome()` strict-known at the new attempt's cutoff, checked **before** authority is resolved and before anything durable is written. A caller-supplied record is evidence only and is compared against the durable one. An attempt with no durable outcome is treated as uncertain and blocks retry. An authorized retry still gets a new attempt, a new cutoff, a fresh strict-known Source Handling resolution, a new handoff, and a new attempt-scoped idempotency key; nothing is inherited from the predecessor.

## Governed response capture

Two independent gates, in order:

1. **Per-category durability.** `permitted_response_evidence_state` resolves the exact historical dispositions. Processing permission grants nothing. When denied, the artifact records `RESPONSE_EVIDENCE_UNAVAILABLE_BY_POLICY` with a stable reason and carries no bytes, hash, measured size, or content-derived identity, and no substitute identifier is fabricated.
2. **Credential capture gate.** `response_content_credential_risk` runs *before* `ProviderResponseArtifact` is constructed, because a provider response is untrusted external content that may echo a secret regardless of which categories are authorized. On refusal the state is `RESPONSE_EVIDENCE_UNAVAILABLE_CREDENTIAL_RISK`, nothing derived from the content is written — a digest of credential-bearing material is itself a credential-derived representation — and the transport outcome is still recorded.

The gate is shape-based, not keyword-based, so an ordinary model answer that discusses authentication remains retainable. That paired positive is tested, because a guard that rejects canonically valid output is itself a defect (`PRH-009`).

`ModelDispatchOutcome` deliberately carries no raw transport result: response content leaves the adapter only through the governed artifact, so a caller cannot receive bytes whose durability was denied.

## Credential boundary

`TransportCredential` is not a `str`, has no public attribute holding its value, renders as a redaction, and refuses serialization. It exists only at the transport edge and reaches exactly one place: the outbound `Authorization` header. The durable record families reject credential-bearing field names, credential-shaped values, nested mappings, and raw bytes at construction, and provider status metadata — untrusted external content — passes the same screen.

## Test coverage

125 new Phase B regressions plus the 68 Phase A regressions, all deterministic. Every required adversarial case in Issue #313 is covered; the numbering below is the Issue's.

| # | Requirement | Regression |
|---|---|---|
| 1 | No send without a durable attempt | `test_transport_is_never_reached_without_a_durably_persisted_attempt` |
| 2 | No send without the exact valid handoff | `test_dispatch_with_an_unpersisted_handoff_sends_nothing`, `test_an_already_consumed_handoff_cannot_authorize_a_second_send`, `test_an_expired_handoff_cannot_authorize_a_send` |
| 3 | Concurrency yields one dispatch winner | `test_concurrent_dispatch_produces_exactly_one_real_send` |
| 4 | Direct transport call is not a supported bypass | `test_a_direct_transport_call_cannot_mint_its_own_authorization` |
| 5 | Repository direct write is not a bypass | `test_repository_direct_write_cannot_bypass_model_adapter_authority`, `test_repository_rejects_an_outcome_whose_attempt_was_never_recorded` |
| 6 | No credential in any durable Phase B record | `test_no_seeded_credential_reaches_any_durable_phase_b_record`, `test_a_credential_cannot_be_rendered_or_serialized`, `test_credential_material_is_structurally_rejected_by_the_response_artifact` |
| 7 | Processing allowed + response durability denied persists nothing derived | `test_processing_allowed_with_response_content_denied_persists_no_content`, `test_denied_response_durability_does_not_fabricate_a_substitute_identity` |
| 8 | Response artifact is never validated knowledge | `test_the_response_artifact_exposes_no_validity_or_promotion_surface`, `test_a_schema_violating_response_is_still_recorded_as_transport_success` |
| 9 | Attempt immutable after terminal outcome | `test_the_attempt_record_is_unchanged_after_a_terminal_outcome`, `test_an_outcome_record_is_immutable_and_appended_not_rewritten` |
| 10 | Provider exception keeps attributable lineage | `test_a_transport_exception_leaves_attributable_uncertain_lineage`, `test_a_transport_result_from_a_different_transport_still_records_lineage` |
| 11 | Ambiguity is never `NOT_DELIVERED` | `test_ambiguous_network_failure_is_never_labelled_confirmed_non_delivery`, `test_an_outcome_record_refuses_a_certainty_its_own_semantics_contradict` |
| 12 | Uncertain delivery cannot cause blind retry | `test_uncertain_delivery_blocks_a_retry_from_being_prepared_at_all` |
| 13 | Prior handoff cannot be reused for retry | `test_the_predecessor_handoff_cannot_be_reused_to_dispatch_a_retry` |
| 14 | Prior Source Handling cannot authorize retry | `test_a_prior_attempt_time_authorization_cannot_authorize_a_retry` |
| 15 | Later authority cannot substitute into replay | `test_later_authority_cannot_be_substituted_into_a_historical_response_replay` |
| 16 | Unauthorized correlation metadata not persisted | `test_correlation_metadata_is_omitted_when_its_category_is_denied`, `test_repository_rejects_correlation_metadata_its_own_authority_prohibits` |
| 17 | Persistence failure after capture is truthful and distinct | `test_a_capture_persistence_failure_is_a_distinct_truthful_outcome`, `test_an_unavailable_store_leaves_the_attempt_nonterminal_for_recovery` |
| 18 | Malformed response cannot cross into validation | `test_no_response_validator_or_promotion_path_exists_in_this_boundary`, `test_a_malformed_response_is_transport_success_lineage_without_a_validity_claim` |
| 19 | Weakening each guard fails its named regression | See **Mutation verification** |

Each negative case is paired with the positive it must not break. The whole Phase B path is asserted to open no socket, and the OpenAI transport's classification is driven through an injected opener rather than a network, so CI never depends on provider availability, quota, or billing. **No test contacts a real provider, and no real-provider test was added.**

## Mutation verification

Thirty-eight guards were deliberately weakened one at a time and the named regression re-run. Every one failed under mutation and passed clean — the guard is proven against the specific defect its name asserts, not merely against the suite being green.

Two of them only became genuine proofs after the first attempt failed to isolate them, which is the point of running the mutation rather than assuming it. The prompt content-hash check initially "passed" its mutation because the test's replacement content differed in length, so the measured-size check accounted for the refusal; it now uses a same-length replacement, which only the hash check can catch. The measured-size check was then unreachable by substitution at all, because `measured_size_bytes` feeds `artifact_id` and the identity check fires first — so its regression was rewritten onto the case where only it can fire: an artifact that self-declares an inconsistent size and is used consistently throughout.

| Guard | Named regression |
|---|---|
| Certainty-driven classifier | `test_ambiguous_network_failure_is_never_labelled_confirmed_non_delivery` |
| Required-delivery-certainty table | `test_an_outcome_record_refuses_a_certainty_its_own_semantics_contradict` |
| Uncertainty-cannot-authorize-retry invariant | `test_an_uncertain_outcome_can_never_carry_retry_permission` |
| Response credential capture gate | `test_credential_bearing_response_is_refused_before_artifact_construction` |
| Response-evidence content exclusion | `test_a_response_artifact_cannot_claim_unavailable_while_carrying_content` |
| Per-category response durability | `test_processing_allowed_with_response_content_denied_persists_no_content` |
| Retry authorization gate | `test_uncertain_delivery_blocks_a_retry_from_being_prepared_at_all` |
| Terminal-outcome re-dispatch guard | `test_an_attempt_with_a_recorded_outcome_cannot_be_dispatched_again` |
| Correlation-metadata authorization | `test_correlation_metadata_is_omitted_when_its_category_is_denied` |
| Profile/transport binding | `test_a_transport_that_is_not_the_profiles_transport_is_refused` |
| Prepared-profile binding | `test_a_profile_other_than_the_prepared_one_is_refused` |
| Dispatch-time authority substitution | `test_dispatch_refuses_an_authority_the_attempt_was_not_prepared_under` |
| Durable payload registry validation | `test_a_durable_surface_the_registry_does_not_govern_fails_closed` |
| Append-only outcome conflict check | `test_dropping_the_attempt_outcome_conflict_check_would_permit_a_silent_rewrite` |
| Persistence correlation re-verification | `test_repository_rejects_correlation_metadata_its_own_authority_prohibits` |
| Persistence durable-response re-verification | `test_persistence_rejects_durable_response_evidence_under_denying_authority` |
| Outcome references a durable attempt | `test_repository_rejects_an_outcome_whose_attempt_was_never_recorded` |
| Single-use compare-and-set | `test_concurrent_dispatch_produces_exactly_one_real_send` |
| Dispatch-authorization mint | `test_a_direct_transport_call_cannot_mint_its_own_authorization` |
| HTTPS-only transient request | `test_the_transient_request_refuses_a_cleartext_endpoint` |
| Pre-send failure proof | `test_the_openai_transport_reports_a_mid_flight_reset_as_uncertain` |
| Transport-result self-consistency | `test_the_transport_result_refuses_a_self_contradictory_observation` |
| Reserved provider body keys | `test_a_provider_parameter_cannot_override_the_model_or_the_prompt` |
| Post-send lineage preservation | `test_a_transport_result_from_a_different_transport_still_records_lineage` |
| Retry from the durable predecessor outcome | `test_a_fabricated_predecessor_outcome_cannot_authorize_a_retry` |
| Prompt identity binding at dispatch | `test_a_substituted_prompt_artifact_cannot_be_transmitted` |
| Prompt content vs. declared hash | `test_a_prompt_artifact_keeping_its_identity_but_swapping_content_is_refused` |
| Prompt declared size vs. bytes | `test_a_prompt_artifact_whose_declared_size_contradicts_its_bytes_is_refused` |
| Transport version binding | `test_a_transport_reporting_a_different_version_is_refused` |
| Supersession-aware historical replay | `test_a_superseding_correction_is_what_a_later_replay_reads` |
| Outcome/response-artifact identity match | `test_persistence_rejects_an_outcome_naming_a_different_response_artifact` |
| Outcome claiming absent evidence | `test_persistence_rejects_an_outcome_claiming_evidence_that_is_absent` |
| Contradictory classification recorded, not raised | `test_a_contradictory_transport_classification_is_recorded_not_raised` |
| Observed execution evidence on persistence failure | `test_a_persistence_failure_after_a_malformed_response_does_not_assert_a_completion` |
| Body-construction failure is proven non-delivery | `test_a_malformed_numeric_provider_parameter_is_proven_non_delivery` |
| Request/transport endpoint agreement | `test_a_request_endpoint_disagreeing_with_the_transport_is_refused` |
| Case-insensitive correlation lookup | `test_the_correlation_identity_is_found_regardless_of_header_casing` |
| Routing-deferral structural check | `test_an_unavailable_provider_never_triggers_an_alternate_attempt` |

## Defects found by hostile self-review

Four were found and fixed before review, and registered as guarded classes.

### `MA-004` — ambiguous transport failure classified as known non-delivery

A transport-class lookup table mapped `TIMEOUT` straight to `TIMEOUT_CONFIRMED_NO_DELIVERY`. A read timeout after the request was already on the wire would have been recorded as proven non-delivery and would then have authorized a retry capable of duplicating a billable provider execution. Fixed at two independent boundaries: certainty-driven classification, and a required-certainty table enforced at record construction.

### `MA-005` — response content persisted without a pre-construction credential gate

Per-category authorization alone would have licensed persisting credential-bearing response bytes and a hash derived from them. Fixed by running the capture gate as a second, independent gate before the canonical record is constructed — a structural boundary, not a scan of an already-built record.

### `MA-006` — retry authorized by caller intent rather than predecessor evidence

`prepare_attempt` accepted `attempt_ordinal > 1` on a bare predecessor identity, so a caller could prepare and dispatch a second attempt after an uncertain first whose request the provider may already have accepted. Fixed by requiring the predecessor's recorded outcome, evaluated before any authority resolution or durable write.

### `MA-007` — transport acting as its own dispatch authority

A `send()` taking only a request could be called directly, producing a real invocation with no durable attempt, no handoff consumption, and no attributable outcome. Fixed by requiring a `DispatchAuthorization` that cannot be minted through any public API and is created only after the compare-and-set has claimed the handoff.

Three further holes were closed in the same pass without needing a new defect class, since each is an instance of an already-guarded boundary: dispatch-time authority substitution, a post-send raise that would have lost the lineage the send created, and a provider parameter able to override the request body's `model` or `messages`.

## Defects found by independent review

Codex reviewed commit `5a5e8509e4` on PR #314 and raised two P1 and two P2 findings. All four were verified against the code, confirmed genuine, and fixed; each is small, local to code this PR introduced, and now carries a mutation-verified regression.

### P1 — retry authorization derived from caller-supplied state

`_require_retry_authorization` checked only the fields of the `ModelAttemptOutcomeRecord` the caller handed in, so a fabricated record naming the predecessor and claiming `RETRY_REQUIRES_NEW_ATTEMPT` defeated the gate entirely while the repository held an uncertain outcome or none at all — precisely the blind duplicate invocation `MA-006` exists to prevent.

This is a recurrence of an already-understood class rather than a new one-off: the repository's own governing rule is that caller-supplied state is evidence to re-verify, never authority to trust, and `prepare_attempt` already applies exactly that treatment to a supplied Source Handling decision. The first `MA-006` hardening checked the *shape* of the evidence instead of resolving the authority. Per `docs/HUNTER_IMPLEMENTATION_CONTRACT.md`, `MA-006` has been **strengthened in place** rather than supplemented with a new entry: the gate now resolves the predecessor's durable outcome from persistence, strict-known at the new attempt's cutoff and following the supersession chain, and rejects a supplied record that disagrees.

### P1 — the transmitted prompt was not bound to the prepared attempt

`dispatch` built the wire request from the caller's `EvidencePromptArtifact` without checking it against `attempt.prompt_artifact_id`. Worse, `EvidencePromptArtifact.artifact_id` excludes `content` and the dataclass performs no self-check, so even an artifact retaining the prepared identity could carry arbitrary bytes while durable lineage still named the prepared prompt — defeating the ADR 0034 guarantee that a response is attributable to an exact canonical prompt. Registered as `MA-008`. Both axes are now verified before the handoff is consumed: identity equality, and the bytes actually hashing and measuring to what that identity was derived from.

### P2 — historical response reads ignored supersession

`strict_known_response_artifact` ordered ascending with `LIMIT 1`, so once `append_outcome` admitted a superseding correction carrying revised response evidence, every later strict-known read returned the original permanently. Registered as `MA-009`. `authoritative_outcome()` now resolves the head of the supersession chain — the outcome no other outcome *known at the same cutoff* supersedes — and the response read follows it, while the superseded original is preserved rather than overwritten.

### P2 — a transport version mismatch was accepted

The post-send consistency check compared `transport_identity` but not `transport_version`, so a transport misreporting its version had that version persisted even though the attempt and profile were bound to another exact one, making recorded lineage contradict the durable execution profile. Version mismatch is now treated identically to identity mismatch, and the recorded outcome carries the profile's bound version.

### Second review round — CodeRabbit

CodeRabbit reviewed the same head and raised six findings. One duplicated the Codex retry P1 above and was already fixed by it. Four more were verified, confirmed, fixed, and mutation-verified:

- **An outcome could name response evidence that was never written** (`MA-010`). `append_outcome` compared attempt, handoff, and profile lineage between an outcome and its artifact, but never the artifact identity the outcome declares — and never rejected an outcome claiming an identity while supplying no artifact. Since the artifact row is keyed by its own computed identity, a bypassing caller could persist an outcome referencing an identity absent from the store.
- **A post-send contradiction discarded the lineage of a completed invocation** (`MA-011`). A transport may report a pairing the outcome family forbids, such as `RESPONSE_RECEIVED` with `UNKNOWN` certainty. Record construction correctly refuses it, but the resulting error propagated out of `dispatch` *after* the handoff was consumed and the provider invoked, so no outcome was written for a send that really happened. It is now recorded as `INTERNAL_ADAPTER_ERROR` with honest uncertainty, joining the existing post-send fallbacks.
- **A persistence-failure record asserted a completion the transport never observed** (`MA-012`). `_record_capture_persistence_failure` hardcoded `PROVIDER_RETURNED_COMPLETION`, but that path also runs for a malformed response whose observation is `UNKNOWN`. The observed evidence is now carried through.
- **A malformed numeric profile parameter became uncertain delivery** (also `MA-012`). Body construction fails inside `transport.send`, after the handoff is consumed, and was recorded as uncertain even though no request byte had been offered. The transport now reports that as proven non-delivery — overstating uncertainty is as untruthful as overstating certainty, and needlessly blocks a later attempt.

Two of these mutation runs initially reported "proven" falsely because the append-only conflict check fired before the linkage checks under test; both regressions were rewritten against an attempt carrying no prior outcome so only the guard under test can account for the refusal.

### Third round — two latent transport bugs and a proxy guard of my own

- **`endpoint_url` on the transport was a silent second source of truth.** `send` builds the wire request from `request.endpoint_url`, which the adapter derives from the profile's endpoint class, so the transport's own field had no effect: a deployment configuring `OpenAIChatCompletionsTransport(endpoint_url=...)` would have transmitted a credential to the adapter-configured endpoint instead, with no signal. The two must now agree.
- **Correlation identity was lost to header casing.** `dict(...)` over the response headers drops the case-insensitive lookup `http.client.HTTPMessage` provides, and the lookup enumerated three spellings, so `X-Request-ID` returned `None` even where the category was authorized. Header names are normalized instead.
- **One of my own regressions was a text-presence proxy.** The routing-deferral test grepped adapter source and stripped two exact prose fragments before scanning, so it held only while a docstring stayed byte-identical — a recurrence of the guarded class `PRH-011`, and a rewording would have produced exactly the false-positive blockage `PRH-009` forbids. It now walks the AST over definition, import, name, and attribute identifiers.

That last one is worth stating plainly rather than burying: the defect was in a guard I wrote, in a repository that already carries a registered defect class for precisely that mistake. `PRH-011` is annotated with the recurrence. No new automated guard is added for it — that class's boundary is the governed-artifact Markdown parser and cannot reach test code, and adding a merge-blocking check merely because it is automatable is the overreach `PRH-009` warns against.

Its mutation proof needed the counterfactual planted in the *source* rather than the test, since it is an absence assertion: a planted `_select_profile_fallback` symbol makes it fail, and a reworded docstring — which broke the previous version — no longer does.

### Raised, not implemented — pre-dispatch refusal persistence

CodeRabbit additionally observed that `prepare_attempt` raises `PreDispatchRefused` and **no service path persists that refusal**, so the CO-23 coverage here proves representability (such a record is constructible and round-trips with no attempt or handoff identity) rather than a persisted round trip.

The observation is correct and the gap is real. It is **not fixed in this PR**, for two reasons stated plainly rather than waved away:

1. It concerns the *refusal* path, decided inside Phase A's `prepare_attempt`, not the dispatch path Issue #313 scopes. Phase B made such a record representable for the first time by introducing the outcome family; it did not create the gap.
2. Persisting one requires verification semantics that do not yet exist. `append_outcome` re-resolves the attempt authority and rejects what it cannot resolve — which is precisely the state a `SOURCE_HANDLING_BLOCKED` refusal records. Persisting a refusal therefore needs a governed rule for how persistence re-verifies a record whose authority is *by definition* unresolvable, and inventing that here would be exactly the kind of unauthorized persistence semantic `CLAUDE.md` and Issue #313 forbid.

Recommended as a separate, narrowly scoped follow-up issue against CO-23, with the design question — how persistence re-verifies a BLOCKED-authority record — settled first.

## Known limitations

- **Residual cross-substrate window.** The Source Handling authority store and the evidence database are separate substrates, so `BEGIN IMMEDIATE` cannot also lock the authority store. Re-resolution inside the write transaction narrows the exposure to that transaction, but a restrictive successor backdated to at or before the attempt cutoff and published inside that window would not be observed. This is inherited from Phase A, unchanged by Phase B, and closing it requires a governed cross-substrate commit primitive that does not exist and was not invented here.
- **An expired handoff raises rather than recording an outcome.** The attempt then remains nonterminal and recovery reports it as uncertain, even though nothing was transmitted. This is over-conservative in the safe direction — it can never cause a duplicate invocation — and closing it out is a governed operator action rather than an adapter inference.
- **Reconciliation is classification, not an API call.** The profile classifies idempotency capability and blocks retry accordingly. Actively querying a provider's status or idempotency endpoint to resolve an uncertain attempt is not implemented, so an uncertain attempt stays uncertain and retry stays blocked, exactly as ADR 0034 requires.
- **Pre-dispatch refusals are representable but not persisted by any service path.** See the section above; recommended as a follow-up against CO-23 rather than designed here.
- **Activation obligations remain open.** Production endpoint configuration, credential provisioning, and live provider idempotency and reconciliation behaviour can only be discharged against a real deployment and are not claimed here.

## Provider and network operational notes

- The transport reaches a provider only when a deployment supplies **both** an endpoint URL for the profile's endpoint class and a `TransportCredential`. Neither is present in the repository, so no code path in this contribution can make a live call as merged.
- An unconfigured endpoint class fails closed as a local pre-send failure; there is no default endpoint to fall back to.
- The transient request refuses a non-HTTPS endpoint, so the credential cannot be put on the wire in the clear.
- The idempotency key, where the profile classifies idempotency `SUPPORTED`, is sent as an `Idempotency-Key` header and is derived from the attempt identity, so it is stable for reconciling that attempt and structurally cannot be inherited by a later one.

## Explicit absence statement

This contribution introduces **no** `ResponseValidator`, semantic response validation, extraction promotion, canonical evidence or knowledge promotion, second provider, multi-provider routing, fallback, provider ranking, dynamic model choice, load balancing, hedging, autonomous retry loop, dashboard or UI work, scheduler work, Comparative Valuation CLI activation, Evidence Assembly CLI activation, valuation or opportunity change, or governance redesign. It weakens no Source Handling, Artifact Guard, Quality Gates, workflow-state, Merge Readiness, historical-replay, or append-only guarantee. Governance surfaces import no Model Adapter, transport, provider, or credential dependency, and a regression asserts it.

## Verification

Run on the exact branch head with the CI-pinned toolchain from `requirements/ci-constraints.txt`:

```text
python scripts/hunter_pr_preflight.py --mode normal
```

Architecture Index Guard, Artifact Guard, Ruff, Black, Mypy, and the full Pytest suite all pass.

## Deferred boundaries

`ResponseValidator` is the next planned lifecycle, followed by the validated extraction and knowledge-proposal boundary, after which development returns to the Valuation / Opportunity Intelligence chain. Multi-provider routing remains separately governed and requires its own ADPR and ADR.
