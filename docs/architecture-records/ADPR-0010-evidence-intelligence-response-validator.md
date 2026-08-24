# ADPR-0010 — Evidence Intelligence ResponseValidator Boundary

## Metadata

- ADPR ID: `ADPR-0010`
- Status: `READY_FOR_REVIEW`
- Version: 1.0
- Author: OpenAI GPT-5.6 Sol — architecture preparation agent
- Reviewers: not yet assigned; independent architecture audit required
- Created: 2026-08-24
- Approved: not yet approved
- Related Epic: not yet created
- Related Issue: #316
- Planned or produced ADR: proposed future ADR — Evidence Intelligence ResponseValidator Boundary
- Supersedes: not applicable
- Superseded by: not applicable

## Executive Summary

Merged ADR 0034 Phase B now gives Hunter a governed provider-attempt path through an immutable `ModelAttemptRecord`, single-use `ModelHandoffRecord`, one provider transport, append-only `ModelAttemptOutcomeRecord`, and governed `ProviderResponseArtifact`. The accepted architecture intentionally stops before semantic response validation. Hunter therefore has transport evidence that a provider returned something, but no accepted authority that may decide whether that response conforms to the requested output contract, schema, lineage, evidence-reference constraints, or validation policy.

This preparation evaluates five materially distinct ownership options. The recommendation is a **separate Hunter Evidence Intelligence `ResponseValidator` service boundary**, downstream of the Model Adapter and upstream of any extraction/knowledge-proposal lifecycle. The validator consumes immutable upstream lineage and exact versioned validation authority; it may emit an append-only `ResponseValidationRecord`, but it cannot promote provider output to canonical truth. It uses a transient, credential-screened live response view when exact response bytes are processable but not durably retainable, and it never regenerates prohibited historical bytes or re-invokes the provider for replay.

The preparation concludes `READY_FOR_ADR`, subject to independent architecture audit. Issue #315 is not a blocker to this boundary because `SOURCE_HANDLING_BLOCKED` is a pre-dispatch refusal with no provider response to validate; the validator must not depend on that refusal family for normal response validation.

## Problem Statement

### Current condition

Current `main` at baseline `b43be1007566faf5b0274c7bf3c8bb05a743ab10` contains ADR 0034 Phase A and Phase B runtime. `src/hunter/evidence_intelligence/model_adapter.py` explicitly states that the boundary ends after governed response capture and that there is no `ResponseValidator`, semantic validation, extraction promotion, or knowledge promotion. A provider response is transport evidence only.

The legacy `src/hunter/evidence_intelligence/provider.py` predates this boundary and directly creates `ExtractionProposal` from provider output after limited forbidden-capability checks. That legacy path cannot be re-labelled as the new validator because it collapses transport execution and extraction proposal creation and does not carry the ADR 0031/0033/0034 lineage model.

### Desired condition

Hunter needs a deterministic, governed authority boundary that can decide whether captured provider output is valid **as a response to the exact requested contract**, while preserving Source Handling, strict-known replay, append-only history, provider independence, and the separation between validation and canonical truth/promotion.

### Decision required

Decide:

1. who owns response-validity semantics;
2. which exact inputs and historical coordinates are authoritative;
3. how validation rules/contracts are versioned and historically bound;
4. how validation works when response bytes may be processed live but may not be retained;
5. what immutable record represents validation and how correction/supersession works;
6. which validity dimensions belong here and which remain downstream;
7. where the boundary stops before extraction/knowledge proposal and canonical promotion.

### In scope

- canonical ownership of response validation;
- validator inputs, rule identities, and output record semantics;
- shape/syntax/schema/requested-output-contract checks;
- lineage and evidence-reference integrity checks that can be decided without acquiring domain truth authority;
- Source Handling interaction for processing and durability;
- transient response handoff for non-retainable exact bytes;
- replay, correction, supersession, persistence, migration, and legacy coexistence;
- failure/missingness taxonomy;
- downstream handoff to a later separately governed extraction/knowledge-proposal boundary;
- deterministic adversarial conformance obligations.

### Out of scope

- runtime implementation;
- canonical claim truth or source truth;
- extraction/knowledge promotion;
- valuation, mispricing, asymmetry, ranking, opportunity, timing, portfolio, or recommendation authority;
- provider/model routing, fallback, ranking, second provider, or dynamic selection;
- tool/action approval;
- Dashboard, scheduler, or governance workflow redesign;
- Issue #315 implementation;
- retroactive relabelling of legacy `AIProviderArtifact` or `ExtractionProposal` records.

## Problem Validation

This is an unresolved architecture problem rather than a missing implementation detail:

- ADR 0031 deliberately deferred provider response semantics and `ResponseValidator`.
- ADR 0034 deliberately ends the Model Adapter at response capture and states that transport success establishes no response truth, extraction validity, claim authority, or knowledge-promotion authority.
- ADR 0032 withholds response validation from the project-neutral core absent evidence for shared ownership.
- Current Phase B source repeats that no validator or semantic validation exists.
- The legacy provider runner directly creates extraction proposals and therefore cannot satisfy the new separation by simple reuse.

Canonical sources checked: ADR 0034, ADR 0033, ADR 0031, ADR 0032, ADR 0020, ADR 0016, ADR 0009, the ADPR lifecycle/template, current `model_adapter.py`, current legacy `provider.py`, current architecture index, and Issue #315 as a candidate dependency.

## Motivation

Without this boundary, Hunter has three unsafe choices: treat transport success as validity, let downstream extraction code invent validation policy, or reuse the legacy provider path that collapses execution and proposal semantics. Any of those would undo the authority separation created by the Prompt Machine and Model Adapter work.

A correct boundary also matters for historical auditability. A response that validated under rule version N must not silently become valid or invalid under current rule version N+1, and a response whose exact bytes were not retainable must not be reconstructed or re-fetched later merely to re-run validation.

## Existing Architecture

### Upstream authority and lineage

The validator sits after these already-governed records:

`EvidencePromptArtifact`
→ `EvidencePreModelBuildRecord`
→ `ModelExecutionProfile`
→ `ModelAttemptRecord`
→ `ModelHandoffRecord`
→ `ModelAttemptOutcomeRecord`
→ `ProviderResponseArtifact`

The Model Adapter owns execution and response-capture lineage. The ResponseValidator must consume that lineage and cannot rewrite it.

### Source Handling

ADR 0033 remains the sole owner of source-handling facts and policy. The validator is a consumer. Processing authority and durable retention authority remain separate. A response may be processable at validation time while its exact bytes, hash, size, or content-derived identifiers are not retainable.

### Replay

ADR 0020 requires strict-known historical reconstruction. Current state, current validator policy, and a new provider invocation cannot substitute for historically recorded state or explicit historical absence.

### Legacy path

`SecureAIProviderRunner` currently performs provider health, provider invocation, limited output screening, then directly persists `AIProviderArtifact` and `ExtractionProposal`. It remains historical/legacy architecture. No synthetic backfill to new validation records is permitted.

## Constraints

### Constitutional

- AI output is not authority merely because it exists or was successfully returned.
- Unknown and unavailable evidence must remain explicit rather than silently defaulting to permission or truth.
- Human and canonical governance authority remain separate from runtime model behavior.

### Governance and accepted ADRs

- ADR 0034: transport success is evidence only; ResponseValidator is separate.
- ADR 0033: Source Handling Authority is exclusive and consumer components cannot mint permission.
- ADR 0031: exact prompt/build identity is upstream-owned.
- ADR 0032: no automatic promotion to project-neutral shared ownership.
- ADR 0020: strict-known replay; no current-state substitution.
- ADR 0016: validation does not itself confer canonical knowledge authority.
- ADR 0009: service authority, provider mechanics, repository mechanics, and persistence remain separated.

### Technical

- Validation must be deterministic for the same exact available input bytes, lineage, contract, and validator rule version.
- The response itself remains non-deterministic provider output; only Hunter-side validation identity and decision construction may be described as deterministic.
- Provider/network access is unnecessary for validation and must not be introduced.

### Operational

- Validation must not make Hunter Governance Review or Merge Readiness depend on a provider or LLM.
- Validation failure must not itself trigger provider retry; retry remains a Model Adapter concern under ADR 0034.

### Persistence and migration

- New validation history is append-only.
- Legacy artifacts remain legacy; no fabricated validation backfill.
- Direct repository writes cannot mint a valid result.

### Replay and historical reconstruction

- Historical reads bind the exact validation rule/profile identity and cutoff.
- If exact response content was not durably retained, replay reports validation-input unavailability; it does not regenerate bytes or call the provider.

### Compatibility

- Existing Model Adapter record identities are immutable.
- Existing legacy `ExtractionProposal` identity/meaning is unchanged.

### Security and privacy

- Credential-bearing response content rejected by Phase B cannot be laundered into validator artifacts.
- Validator-derived diagnostics, excerpts, hashes, sizes, and normalized values are individually subject to Source Handling durable-category authorization.

### Performance and scalability

- Validation is local and deterministic; no network dependency is required.
- Rules should support bounded parsing/validation and explicit size/complexity limits to avoid parser/regex/resource exhaustion.

### Evidence and provenance

Every validation decision must preserve exact lineage to the response capture, attempt/outcome, prompt/build, output contract, validator rule/profile version, and the Source Handling coordinates that authorized any durable derived evidence.

## Evidence Inventory

| ID | Evidence | Authority/source | Finding | Quality and limitations | Supports or challenges |
|---|---|---|---|---|---|
| E-001 | ADR 0034 accepted decision | `docs/ADR/0034-...md` | Model Adapter ends at response capture; transport success grants no validity; validator is separate | Binding architecture | Supports separate validator boundary |
| E-002 | Phase B implementation | `src/hunter/evidence_intelligence/model_adapter.py` on baseline `b43be100...` | Outcome and response capture exist; module explicitly says no ResponseValidator/semantic validation | Direct runtime evidence | Supports next-boundary necessity |
| E-003 | Source Handling authority | ADR 0033 + current runtime | Processing and durable retention are distinct and historically governed | Binding architecture/runtime | Requires transient validation path for non-retainable content |
| E-004 | Pre-model authority | ADR 0031 | Prompt/build and requested-output contract lineage are upstream-owned | Binding architecture | Validator must consume, not rewrite, contract identity |
| E-005 | Project-neutral boundary | ADR 0032 | Response validation is not admitted to generic core | Binding architecture | Challenges generic-core ownership now |
| E-006 | Strict-known replay | ADR 0020 | Current/latest substitution is prohibited | Binding architecture | Requires version-bound validation history |
| E-007 | Legacy provider runner | `src/hunter/evidence_intelligence/provider.py` | Directly turns provider result into `ExtractionProposal` after limited checks | Direct runtime evidence; legacy only | Rejects legacy reuse as canonical validator |
| E-008 | Issue #315 | CO-23 follow-up | Concerns persistence of pre-dispatch `SOURCE_HANDLING_BLOCKED` refusal where no provider response exists | Open architecture follow-up | Not a validator blocker |
| E-009 | Phase B response durability behavior | ADR 0034 + Phase B code | Response content can be processable while exact durable evidence is unavailable by policy or credential risk | Binding/runtime evidence | Requires live transient validation input and explicit replay unavailability |

## Assumptions

| ID | Assumption | Rationale | Confidence | Falsification condition | Consequence if false |
|---|---|---|---|---|---|
| A-001 | Response validity can be decided without network/provider re-invocation | Required checks concern captured bytes, contracts, lineage, and rules | High | A required validity dimension depends on provider-side mutable state | Split that dimension into separate governed evidence acquisition; do not put network into validator |
| A-002 | The requested output contract can be identified immutably from upstream build/prompt lineage | ADR 0031 records output-contract inputs/identity | High | Current runtime lacks a durable exact identity sufficient to bind validation | ADR must add a subordinate contract identity derived from existing immutable upstream state, not caller authority |
| A-003 | Non-retainable response bytes may still be processed live when Source Handling says processing is allowed | ADR 0033 distinguishes processing from retention | High | Source Handling policy forbids validation processing for that response subject | Validation returns blocked/unavailable; no workaround |
| A-004 | Response validation is Hunter-specific today | ADR 0032 has no two-consumer evidence | High | A second independent consumer demonstrates the same contract and semantics | Revisit shared-core admission separately under ADR 0032 |
| A-005 | Issue #315 pre-dispatch refusal persistence is not required to validate an answered provider response | No response exists on that refusal path | High | Validator is required to consume pre-dispatch refusal history for correctness | Treat #315 as explicit prerequisite for that dependent feature only |

## Architectural Dimensions

1. canonical authority owner;
2. validation input ownership and immutable lineage;
3. distinction between transport success, response validity, and canonical truth;
4. validation policy/rule/schema version identity;
5. transient versus durable response content;
6. Source Handling per-category durability for validator-derived evidence;
7. failure/missingness taxonomy;
8. append-only persistence and repository anti-bypass;
9. strict-known replay and historical unavailability;
10. correction/supersession semantics;
11. downstream extraction/knowledge-proposal handoff;
12. legacy coexistence/migration;
13. security/credential exclusion;
14. deterministic governance isolation;
15. future shared-core admission without premature generic ownership.

## Candidate Options

### Option 1 — Separate Evidence Intelligence ResponseValidator service — RECOMMENDED

- Description: a Hunter-owned validator service downstream of Model Adapter, consuming immutable response/attempt/build lineage plus an exact versioned `ResponseValidationProfile` and upstream requested-output-contract identity.
- Authority and ownership: sole owner of response-validity decisions; owns neither transport outcome nor canonical truth/promotion.
- Boundaries: accepts a durable `ProviderResponseArtifact` plus, when exact bytes are not durably retainable but processing is allowed, a single-use transient credential-screened `ResponseValidationInput` handed off by the Model Adapter during the live attempt lifecycle.
- Persistence and replay: emits append-only `ResponseValidationRecord`; historical replay uses exact recorded profile/rule/contract identity and persisted inputs only; otherwise records explicit input unavailability.
- Evidence and provenance: exact lineage to response artifact/capture state, outcome, attempt, build/prompt, contract, validation profile, rule versions, and Source Handling decisions governing any derived durable fields.
- Compatibility: additive; legacy path remains legacy.
- Advantages: strongest authority separation; supports non-retainable live validation; replay-safe; provider-independent.
- Disadvantages: introduces one new service/record boundary and transient handoff contract.
- Failure modes: stale rule substitution, caller-supplied schema laundering, direct repository forged VALID, transient-input reuse, diagnostic leakage.
- Migration implications: no backfill; new path only.
- Reversibility: high before activation; append-only history remains auditable.
- Open dependencies: future ADR and implementation; no dependency on #315 for answered-response validation.

### Option 2 — Embed validation inside Model Adapter

- Description: Model Adapter both executes providers and decides response validity.
- Authority and ownership: collapses execution evidence and semantic validity into one owner.
- Boundaries: simplest call path but erases ADR 0034's explicit stop before ResponseValidator.
- Persistence and replay: possible but semantically coupled to transport lifecycle.
- Evidence and provenance: easy lineage access.
- Compatibility: conflicts with the accepted separation.
- Advantages: fewer modules and handoffs.
- Disadvantages: authority concentration; transport-success/validity laundering risk; ADR 0034 conflict.
- Failure modes: provider/transport result silently influences semantic validity; retry logic couples to validation.
- Migration implications: would require amending accepted architecture.
- Reversibility: poor after coupling.
- Open dependencies: new architecture amendment.

### Option 3 — Delegate validation to downstream extraction/knowledge layer

- Description: provider output reaches extraction proposal logic and that layer decides validity.
- Authority and ownership: downstream consumer becomes authority over whether its own input is valid.
- Boundaries: validation and promotion become difficult to distinguish.
- Persistence and replay: risks validation being implicit inside proposal state.
- Evidence and provenance: weaker independent audit trail.
- Compatibility: conflicts with the explicit future validator boundary and ADR 0016 authority separation.
- Advantages: fewer intermediate records.
- Disadvantages: authority laundering and promotion coupling.
- Failure modes: malformed response reaches proposal creation; validated means promoted by implication.
- Migration implications: recreates legacy design defect.
- Reversibility: poor.
- Open dependencies: none, but architecturally inferior.

### Option 4 — Shared generic validator core with Hunter authority adapter

- Description: generic validation engine shared across projects, with Hunter adapter owning policy.
- Authority and ownership: split generic mechanics / Hunter authority.
- Boundaries: potentially clean if there is real multi-consumer evidence.
- Persistence and replay: feasible.
- Evidence and provenance: feasible.
- Compatibility: ADR 0032 admission evidence is absent today.
- Advantages: future reuse.
- Disadvantages: premature abstraction and ownership complexity.
- Failure modes: generic core accidentally acquires Hunter policy authority; lowest-common-denominator contracts.
- Migration implications: unnecessary now.
- Reversibility: medium.
- Open dependencies: at least two independent consumers and separate admission review.

### Option 5 — Provider-specific response validation

- Description: each provider transport validates its own output.
- Authority and ownership: provider-specific transport becomes validity owner.
- Boundaries: collapses transport evidence into semantic authority.
- Persistence and replay: historical meaning depends on provider implementation version and risks hidden provider semantics.
- Evidence and provenance: weaker provider-neutral comparison.
- Compatibility: directly violates ADR 0034 transport boundary.
- Advantages: provider-specific schema knowledge is local.
- Disadvantages: highest authority-laundering risk, lock-in, inconsistent validity semantics.
- Failure modes: `200 OK` or provider parser success becomes Hunter validity.
- Migration implications: would require accepted-architecture reversal.
- Reversibility: poor.
- Open dependencies: none; rejected.

## Comparative Analysis

| Criterion | Option 1 | Option 2 | Option 3 | Option 4 | Option 5 |
|---|---|---|---|---|---|
| Correctness | High | Medium/low | Low | High if admitted | Low |
| Constitutional compliance | High | Low due boundary collapse | Low | High conditionally | Low |
| Governance compliance | High | Conflicts ADR 0034 | Weak | Blocked by ADR 0032 evidence gate | Conflicts ADR 0034 |
| Authority clarity | High | Medium/low | Low | Medium/high | Low |
| Replayability | High | Medium | Medium/low | High | Low/medium |
| Evidence integrity | High | Medium | Low | High | Low |
| Maintainability | High | Medium initially | Medium initially | Medium | Low across providers |
| Scalability | High | Medium | Medium | High | Low |
| Operational complexity | Medium | Low | Low | High | Medium/high |
| Migration risk | Low | Medium/high | High | Medium | High |
| Implementation effort | Medium | Low | Low | High | Medium |
| Reversibility | High | Medium/low | Low | Medium | Low |
| Long-term extensibility | High | Medium | Low | High after evidence | Low |

## Recommended Methodology Contract

The future ADR should bind the following minimum contract.

### Canonical owner

`ResponseValidator` is a separate Hunter Evidence Intelligence service and sole owner of **response-validity decisions**. It is a consumer of Model Adapter lineage, Source Handling Authority, upstream output-contract identity, and versioned validator rules.

### Validation profile/rule identity

Each validation event uses an immutable `ResponseValidationProfile` identity that binds at least:

- schema/version of the profile;
- validator implementation contract identity/version;
- requested-output-contract identity from the governing pre-model build;
- exact schema/shape rule identity where applicable;
- canonicalization/parser identity/version;
- evidence-reference validation rule identity/version;
- bounded resource/size policy identity;
- prohibited-capability/security rule identity if that rule belongs to semantic response acceptance rather than transport capture;
- required validation dimensions.

Caller-provided ad hoc schemas/rules are evidence/requests only and cannot become authority. Any caller-selected profile must resolve to an already-governed canonical profile applicable at the validation cutoff.

### Validation input and transient handoff

A durable `ProviderResponseArtifact` is the canonical response-capture lineage record, but it may not contain exact bytes. Therefore the validator supports two input modes:

1. **Durable validation input** — exact response content is durably authorized and available from the historical response artifact.
2. **Transient validation input** — exact response content is processable but not durably retainable. The Model Adapter supplies a single-use, in-memory, credential-screened `ResponseValidationInput` bound to the exact response capture, attempt, profile, and validation cutoff. The transient payload is never persisted merely because validation occurred.

If neither exact durable bytes nor an authorized transient view is available, semantic content validation is `INPUT_UNAVAILABLE`, not implicitly valid or invalid.

### Validation dimensions

The validator may decide only dimensions whose evidence is available and whose authority belongs here:

- parse/syntax validity;
- expected top-level shape;
- schema conformance;
- requested-output-contract conformance;
- required/forbidden field presence;
- bounded type/range/enum constraints explicitly encoded by the governed contract;
- lineage consistency between response capture and the exact attempt/build/profile;
- evidence-reference structural integrity (for example, whether referenced IDs are syntactically and lineage-valid), but not whether the referenced source claim is true;
- forbidden capability/action-request structure where the governing validation policy assigns that check here;
- explicit partial/missing content classification.

It may not decide source truth, claim truth, valuation truth, ranking, opportunity, recommendation, or canonical knowledge promotion.

### Validation result family

Use an immutable append-only `ResponseValidationRecord`. It should bind at least:

- validation record identity/schema version;
- `ProviderResponseArtifact` identity or governed response-capture identity where content is transient-only;
- `ModelAttemptOutcomeRecord` identity proving an answered/captured response state where applicable;
- attempt/handoff/execution-profile/build/prompt lineage identities;
- `ResponseValidationProfile` identity/version;
- requested-output-contract identity/version;
- validation cutoff and creation time;
- overall state;
- per-dimension states and stable reason codes;
- exact input availability mode (`DURABLE`, `TRANSIENT_NOT_RETAINED`, `UNAVAILABLE`);
- any durable diagnostics only where their own Source Handling categories authorize them;
- supersedes identity when correcting an earlier validation record.

Overall states should distinguish at least:

- `VALID`;
- `INVALID_SYNTAX`;
- `INVALID_SCHEMA`;
- `INVALID_OUTPUT_CONTRACT`;
- `INVALID_LINEAGE`;
- `INVALID_EVIDENCE_REFERENCE_STRUCTURE`;
- `PARTIAL_RESPONSE`;
- `INPUT_UNAVAILABLE`;
- `RULE_UNAVAILABLE`;
- `SOURCE_HANDLING_BLOCKED` for validation-time processing restrictions only;
- `SECURITY_BLOCKED`;
- `VALIDATOR_ERROR` / explicit unknown state.

`VALID` means only “valid under this exact validation contract.” It never means true, correct, authoritative, or promoted.

### Persistence separation

Repository code stores and verifies. Before accepting a `VALID` or other validation record it mechanically verifies referenced upstream identities exist and match, the profile/rule identity is canonical for the cutoff, the result structure is internally consistent, and every durable derived field is authorized. It does not choose rules or derive semantic validity independently from caller input.

### Replay

Historical reconstruction selects the applicable append-only validation record and exact rule/profile identity under strict-known coordinates. It does not re-run the current validator against old bytes as a substitute for history. Re-validation is a **new validation event** with its own identity and cutoff, linked to the predecessor; it never rewrites the original.

If historical exact bytes were never retained, replay returns the recorded validation result plus explicit `TRANSIENT_NOT_RETAINED` input state where such a result was validly produced live. If no validation result exists, replay reports absence; it does not recreate one from current state.

### Correction/supersession

Corrections are append-only and non-branching for one validation subject/profile lineage. A successor names the exact predecessor. Historical cutoff reads return the latest applicable correction knowable at that cutoff, never blindly the oldest and never current/latest without cutoff filtering.

### Downstream handoff

A later extraction/knowledge-proposal service may consume only validation states explicitly accepted by its own separately governed contract, normally `VALID`. The handoff carries validation identity and lineage. The validator itself creates no extraction proposal and performs no knowledge promotion.

## Falsification Results

| Scenario | Option 1 result | Implication |
|---|---|---|
| Provider returns syntactically valid JSON that violates requested contract | Survives: schema/contract dimensions can mark invalid independently of transport success | Confirms need for separate validator |
| Caller supplies permissive schema/rule | Survives: caller input cannot mint canonical profile/rule | Prevents authority laundering |
| Current rules differ from historical rules | Survives: exact profile/rule identity is bound to each validation record | Preserves replay truth |
| Response processable but not retainable | Survives via single-use transient validation input with explicit non-retention | Avoids false requirement to persist prohibited bytes |
| Validated response mistaken for canonical truth | Survives if `VALID` is explicitly scoped to contract conformance and promotion is prohibited | Boundary remains clear |
| Malformed response reaches extraction | Survives if downstream requires explicit valid validation identity | Fail closed |
| Rule version changes without identity change | Invalidates implementation; profile identity must cover every semantic rule input | Mandatory conformance case |
| Direct repository write fabricates `VALID` | Invalidates implementation; persistence must verify canonical rule/profile and lineage | Mandatory anti-bypass guard |
| Supersession returns oldest result | Invalidates replay contract | Requires strict-known latest-applicable non-branching correction |
| Validation failure triggers blind provider retry | Rejected; validator has no retry authority | Preserves ADR 0034 |
| Credential-bearing echo reaches validator durability | Rejected by Phase B capture gate and validator durable-payload gate | Security invariant |
| #315 treated as implicitly solved | Rejected; separate issue remains open and unrelated to answered-response validation | Scope discipline |

Options 2, 3, and 5 fail accepted authority-separation constraints. Option 4 remains a future candidate only after ADR 0032 admission evidence exists.

## Rejected Options

### Embedded Model Adapter validation

Rejected because ADR 0034 explicitly terminates Model Adapter authority before semantic validation. Reconsider only through a separately audited amendment with evidence that separation is harmful and does not create authority laundering.

### Downstream extraction-owned validation

Rejected because a consumer must not become authority over whether its own upstream input is valid, and because it collapses validation and promotion.

### Generic shared-core ownership now

Rejected for the present lifecycle because ADR 0032 requires independent multi-consumer evidence that does not exist. Reconsider after at least two consumers demonstrate an identical stable contract.

### Provider-specific validation

Rejected because the transport/provider cannot acquire canonical response-validity authority.

## Risks

| Risk | Category | Likelihood | Impact | Mitigation | Residual uncertainty |
|---|---|---|---|---|---|
| `VALID` is misread as canonical truth | Authority | Medium | High | Name/scoping, prohibited promotion surface, downstream separate authority | Human/code misuse still possible; regressions required |
| Caller schema becomes de facto authority | Authority | Medium | High | canonical versioned profile/rule resolution; reject ungoverned rule identity | Rule-registry mechanics remain for implementation design |
| Transient content leaks into persistence | Privacy/security | Medium | High | per-category durable validation and structural credential gate before record construction | Need exhaustive field map in implementation |
| Historical revalidation substitutes current rules | Replay | Medium | High | strict-known rule/profile identity; revalidation is new event | Requires adversarial replay tests |
| Validation diagnostics leak content | Evidence/privacy | Medium | Medium/high | every diagnostic category independently governed | Exact category mapping deferred to implementation contract |
| Parser/resource exhaustion | Operational/security | Low/medium | Medium | bounded size/depth/complexity policy in validation profile | Provider response shapes vary |
| Validator becomes hidden retry authority | Boundary | Low | High | no provider/network dependency; validation failure cannot dispatch | Must be tested structurally |
| Legacy proposals appear validated | Migration | Medium | High | no backfill, distinct record family, explicit legacy state | Consumers need migration discipline |
| Shared-core abstraction introduced too early | Maintainability | Medium | Medium | remain Hunter-owned until ADR 0032 evidence gate passes | Future duplication possible but reversible |

## Open Questions

| Question | Blocking? | Owner | Required evidence or action | Status |
|---|---|---|---|---|
| Exact closed vocabulary and field-category registry mapping for validator diagnostics | No for ADR readiness; yes before implementation activation | future ADR/implementation issue | design contract + adversarial durability matrix | Open |
| Exact parser/schema technology | No | implementation issue | choose deterministic local implementation compatible with governed profile | Deferred |
| Whether forbidden-capability structural checks belong entirely in capture, validation, or both as independent gates | No; ADR must define ownership without weakening Phase B | future ADR | map Phase B capture security vs semantic contract validation | Open |
| Whether a later second consumer justifies shared generic core | No | ADR 0032 admission lifecycle | independent multi-consumer evidence | Deferred |
| Does Issue #315 block answered-response validation? | No | #315 separate lifecycle | only revisit if a concrete validator dependency on pre-dispatch refusal history is proven | Resolved non-blocking |

## Mandatory Conformance Cases

A future ADR/implementation must make these deterministic and adversarially testable:

1. `SUCCEEDED_TRANSPORT` alone cannot produce `VALID`.
2. A syntactically valid response violating the exact requested output contract is invalid.
3. Caller-supplied permissive schema/rule cannot become canonical validation authority.
4. Validator profile identity changes whenever any semantic rule/canonicalizer/parser contract changes.
5. Current validator rules cannot substitute for historical profile/rule identity.
6. Validation can operate on an authorized transient response when exact bytes are not retainable, while persisting zero prohibited bytes/hash/size/content-derived IDs.
7. A transient validation input is single-use and bound to the exact response capture/attempt; it cannot validate a different response.
8. When neither durable nor transient exact input is authorized/available, result is explicit `INPUT_UNAVAILABLE`, never implicit `VALID`.
9. `VALID` exposes no canonical truth/promotion authority and cannot itself create an extraction proposal.
10. Malformed/partial/contract-invalid responses cannot cross the validated handoff expected by downstream extraction.
11. Direct repository writes cannot fabricate `VALID` under an ungoverned or mismatched profile/rule.
12. Every durable diagnostic/excerpt/hash/size/normalized value is independently Source Handling-authorized.
13. Credential-bearing echoed content cannot enter durable validation records.
14. Historical replay does not invoke provider/network or regenerate prohibited bytes.
15. Re-validation under a new rule is a new append-only event and does not rewrite the original.
16. Correction/supersession is non-branching and strict-known cutoff reads select the latest applicable successor, not the oldest or current unconditionally.
17. Validation failure/unknown state cannot trigger Model Adapter retry or consume a provider dispatch handoff.
18. Provider-specific transport cannot mint validation records or choose validation rules.
19. Legacy `ExtractionProposal` / `AIProviderArtifact` cannot be retroactively accepted as `ResponseValidationRecord`.
20. Hunter Governance Review and Merge Readiness import no validator/provider/credential dependency.
21. Issue #315 remains separately unresolved unless explicitly completed; no validator test may treat CO-23 persistence as satisfied by implication.
22. A deliberately weakened version of every reusable authority/replay/durability guard causes its named regression to fail.

## Constitution Review

The recommendation preserves evidence-first, fail-closed behavior: unknown validity remains unknown, provider output is not promoted by success, and prohibited historical evidence is not reconstructed. No trading, portfolio, recommendation, or autonomous-action authority is introduced.

## Governance Review

The recommendation obeys the repository's Stage 1 architecture-preparation lifecycle. It does not implement runtime code, does not modify accepted ADRs, and does not self-approve. Independent architecture audit remains mandatory before ADR drafting. Merge remains owner-only.

## Quality Assessment

- Problem correctness: HIGH — the gap is explicitly deferred by accepted architecture and confirmed by current source.
- Scope completeness: HIGH — ownership, input, rules, persistence, replay, correction, Source Handling, legacy migration, and downstream boundary are covered.
- Option completeness: HIGH — separate service, embedded adapter, downstream owner, generic core, and provider-specific ownership are compared.
- Canonical consistency: HIGH — recommended option specializes without amending ADR 0031/0032/0033/0034/0020/0016/0009.
- Authority clarity: HIGH — validation authority is singular and distinct from transport and promotion.
- Evidence integrity: HIGH — current runtime and binding documents are distinguished from assumptions.
- Replayability: HIGH — exact rule/profile identity and explicit historical unavailability are binding recommendations.
- Falsifiability: HIGH — hostile cases and 22 conformance obligations are explicit.
- Migration safety: HIGH — no legacy relabelling/backfill.
- Operational safety: HIGH — no provider/network or governance dependency.
- Remaining uncertainty: ACCEPTABLE — exact field-category vocabulary and implementation mechanics remain correctly deferred.

## Architecture Readiness

- Outcome: `READY`
- Rationale: the decision is necessary, evidence-backed, consistent with accepted architecture, and has a clear preferred option with explicit boundaries and falsifiable obligations.
- Missing evidence: no material evidence missing for architecture selection. Concrete diagnostic-category mapping and parser mechanics are implementation-design prerequisites, not ownership blockers.
- Unresolved conflicts: none found. Issue #315 is separate and non-blocking for answered-response validation.

## ADR Readiness

- Outcome: `READY_FOR_ADR`
- Proposed ADR title: Evidence Intelligence ResponseValidator Boundary
- Proposed ADR scope: Hunter-owned response-validity authority between ADR 0034 governed response capture and a later validated extraction/knowledge-proposal boundary; exact validation profile/rule identity; transient/durable input semantics; append-only validation record; Source Handling; replay/correction; legacy coexistence; downstream handoff.
- Decisions the ADR must fix: canonical owner, validation dimensions, rule/profile identity, transient handoff, result family/state semantics, durability/replay/correction, persistence anti-bypass, downstream stop boundary.
- Matters the ADR must leave open: parser/library choice, concrete database schema, final closed diagnostic reason vocabulary, future generic-core admission, extraction/knowledge-promotion architecture, routing/multi-provider work.

## Final Recommendation

Adopt **Option 1: a separate Hunter Evidence Intelligence ResponseValidator service boundary**. Keep it provider-neutral but Hunter-owned. It receives governed response-capture lineage plus exact validation authority, validates content either from authorized durable bytes or a single-use transient live view, emits append-only contract-conformance evidence, and stops before extraction/promotion. It has no network, retry, provider-selection, domain-truth, or canonical-promotion authority.

Proceed to independent architecture audit. Do not draft or accept an ADR until that independent audit returns an ADR-ready verdict with no blocking finding.

## Decision History

| Date | State | Change | Author or reviewer |
|---|---|---|---|
| 2026-08-24 | READY_FOR_REVIEW | Initial preparation completed from post-PR #314 `main`; recommends separate Evidence Intelligence ResponseValidator and records #315 as non-blocking | OpenAI GPT-5.6 Sol — architecture preparation agent |

## Traceability

- Epic: not yet created
- Issue: #316
- Preparation working document: Issue #316 scope plus this record's evidence analysis
- Checklist review: self-assessment complete; independent architecture audit not yet performed
- ADPR: `ADPR-0010`
- ADR: not yet created
- Implementation plan: not yet created
- PR: not yet created
- Merge commit: not yet created
- Release: not yet assigned

## Immutability and Supersession

After `APPROVED`, this record is historical evidence. Corrections that change substantive reasoning require a new ADPR that explicitly supersedes this record. Non-substantive link completion and typographical corrections must remain auditable in version history.
