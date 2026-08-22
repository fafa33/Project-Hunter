# ADPR-0009 — Evidence Intelligence Model Adapter and Provider Attempt Boundary

## Metadata

- ADPR ID: `ADPR-0009`
- Status: `READY_FOR_REVIEW`
- Version: 1
- Author: ChatGPT / repository owner-directed architecture work
- Reviewers: independent architecture auditor not yet assigned
- Created: 2026-08-20
- Approved: not yet approved
- Related Epic: not yet created
- Related Issue: #287
- Planned or produced ADR: proposed ADR 0034 — Evidence Intelligence Model Adapter and Provider Attempt Boundary
- Supersedes: none
- Superseded by: none

## Executive Summary

Project Hunter now has an Accepted ADR 0031 pre-model architecture and a concrete provider-free runtime that deterministically produces an `EvidencePromptArtifact` and `EvidencePreModelBuildRecord`. It also has Accepted ADR 0033 Source Handling Authority and a runtime integration that blocks model-facing processing unless exact historical handling authority permits it. What it does not have is an accepted architecture for the next boundary: converting an immutable prompt artifact into an exact provider request, executing a model attempt, recording attempt/provenance/failure state, and handing a response to a later validator without granting the model or transport canonical authority.

The repository also contains an older `AIExtractionProvider` / `SecureAIProviderRunner` path. That path operates over `ExtractionRequest(document_id, spans, schema, ...)`, performs provider health checks, persists `AIProviderArtifact` and `ExtractionProposal`, and does not consume the ADR 0031 `EvidencePromptArtifact`. It is therefore evidence that provider execution mechanics exist, but it is not the architecture-authorized handoff from the current pre-model chain. The next architecture must define how these generations coexist or migrate rather than silently treating the older provider path as the new Model Adapter.

Five materially distinct options were evaluated. The recommended architecture is a **consumer-owned, provider-neutral Evidence Intelligence Model Adapter contract with provider-specific transport implementations, while deferring multi-provider routing**. The first implementation should admit at most one explicitly configured, versioned execution profile at a time. A provider/model choice that changes the capability constraint requires a new pre-model build. A provider-specific transport returns a deterministic, non-secret request transformation result but owns no durable artifact semantics. The Model Adapter alone applies Source Handling, creates any authorized `ProviderRequestArtifact`, prepares an immutable pre-send attempt, binds a single-use handoff snapshot to that attempt, and invokes the transport. Exact request/response bytes, hashes, sizes, or content-derived identities are persisted only when the applicable durable dispositions authorize those categories. Credentials and authentication material are structurally excluded from all canonical artifacts.

A successful adapter attempt is transport evidence only. It cannot validate response truth, create an `ExtractionProposal` by itself, or promote anything into canonical Hunter knowledge. `ResponseValidator` remains a separate future boundary. Provider/model routing, if Hunter later needs to select among multiple execution profiles, also remains a separately governed decision.

Self-assessment outcome: `READY_FOR_ADR`, subject to independent architecture audit. No runtime implementation, provider credential, provider call, Response Validator, routing authority, or governance LLM dependency is authorized by this record.

## Problem Statement

### Current condition

Accepted ADR 0031 ends the current model-facing foundation at an exact `EvidencePromptArtifact` and immutable `EvidencePreModelBuildRecord`. It explicitly defers model invocation, provider attempts, provider request artifacts, response semantics, provider routing, credentials, retries, quotas, billing, and response validation to later architecture.

The implemented pre-model runtime now provides concrete `EvidencePromptArtifact` and `EvidencePreModelBuildRecord` types. The build path requires Source Handling Authority, resolves exact historical facts/policy/registry/authorization rule, and blocks model-facing work when `processing_decision != ALLOW`.

Separately, `src/hunter/evidence_intelligence/provider.py` contains an older provider boundary:

- `AIExtractionProvider` protocol;
- `ExtractionRequest` carrying document/spans/schema rather than `EvidencePromptArtifact`;
- `SecureAIProviderRunner` health/error/security handling;
- persisted `AIProviderArtifact`, `AIProviderHealth`, `ExtractionProposal`, and security events.

That path demonstrates useful implementation experience but predates the accepted ADR 0031/0033 handoff. It does not prove which exact canonical prompt bytes were sent to a provider, has no distinct provider-request artifact linked to `EvidencePromptArtifact`, and cannot by itself establish the new architecture.

### Desired condition

Hunter has one explicit, auditable boundary between the completed pre-model build and any external model execution. The boundary must preserve exact identities and bytes when their durable categories are authorized, otherwise preserve explicit unavailability without regenerating prohibited evidence; independently enforce Source Handling Authority; structurally exclude credentials; distinguish provider-specific request transformation from the canonical prompt artifact; durably establish each attempt before network transmission; make uncertain-delivery and persistence failures explicit; and prevent model execution success from acquiring validation or canonical promotion authority.

### Decision required

Decide:

1. who owns the Model Adapter and provider-attempt semantics;
2. whether the adapter contract is provider-neutral or provider-specific;
3. how `EvidencePromptArtifact` becomes an exact provider request without rewriting upstream history;
4. what identities and immutable artifacts represent execution profiles, provider requests, pre-send attempts, handoffs, outcomes, failures, and responses;
5. how Source Handling Authority applies atomically at the handoff and to durable request/response surfaces;
6. how uncertain delivery, idempotency, retry, reconciliation, and persistence failure behave;
7. whether provider/model routing belongs in this boundary or remains separately deferred; and
8. where the Model Adapter ends and a future Response Validator begins.

### In scope

- Evidence Intelligence Model Adapter ownership and package boundary;
- provider-neutral adapter contract versus provider-specific transports;
- exact prompt-to-provider-request handoff;
- provider execution profile identity/version;
- provider request artifact identity and byte semantics;
- atomic attempt-time Source Handling handoff semantics;
- model attempt/retry/failure identity and lineage;
- provider idempotency/correlation, uncertain-delivery, reconciliation, and retry rules;
- provider response artifact identity and retention semantics;
- Source Handling Authority re-resolution/enforcement at the model boundary;
- credentials/secret exclusion;
- observability, provenance, audit, and historical reconstruction semantics;
- migration/compatibility with ADR 0031 pre-model artifacts and the older provider runner;
- explicit boundary to future Response Validator;
- decision on whether multi-provider routing is part of this slice.

### Out of scope

- live provider/model invocation;
- API keys, secrets, billing credentials, provider account configuration, or production endpoints;
- concrete Response Validator implementation or business validation rules;
- canonical claim/knowledge promotion;
- autonomous tool use or model-side tools;
- multi-provider scoring/routing implementation;
- generic cross-project Model Adapter ownership without ADR 0032 admission evidence;
- Hunter Governance Review or Merge Readiness LLM/provider dependency;
- Market Validation, trading, portfolio allocation, dashboard, scheduler, SaaS, or unrelated domain work.

## Problem Validation

The architectural gap is explicit in Accepted ADR 0031: the current foundation ends at `EvidencePromptArtifact` / `EvidencePreModelBuildRecord`, while Model Adapter, provider invocation, provider attempts, request artifacts, response semantics, and response validation require later architecture.

The gap is also visible in runtime shape. `pre_model.py` exposes the canonical pre-model artifact and Source Handling gated build. The older `provider.py` execution path accepts document/spans/schema instead of the canonical prompt artifact, so there is no architecture-governed identity-preserving handoff between the two. Treating the existing provider runner as sufficient would bypass the exact artifact lineage ADR 0031 introduced.

ADR 0032 further prevents solving this by prematurely promoting a Model Adapter into project-neutral ownership: provider selection, routing, credentials, invocation, and response validation are explicitly prohibited core authority, and concrete shared contracts require evidence from at least two independent consumers.

ADR 0033 makes the handoff safety problem architectural rather than merely operational. Future model-facing components are consumers only; unresolved handling authority is `BLOCKED`; every handling decision derives from exact Source Handling facts and policy applicable and knowable at the relevant cutoff; and persistence must independently rederive rather than trust caller-supplied decisions.

This is therefore a real unresolved architecture decision involving component ownership, external-provider boundary, persistence, replay, security, migration, failure recovery, and future extensibility. It is not an ordinary implementation choice.

## Motivation

Without this architecture, Hunter has five unsafe failure modes:

1. **Opaque execution** — a response may exist without proof of the exact canonical prompt and exact provider request that produced it when such proof is durably authorized, or without an explicit policy-governed unavailability state when it is not.
2. **Authority laundering** — provider choice, transport transformation, caller flags, or adapter success may accidentally acquire authority over source handling, prompt content, response validity, or canonical promotion.
3. **Historical false replay** — a later provider re-call may be mistaken for reconstruction of the original attempt, or current provider configuration may be substituted for historically unavailable request/response state.
4. **Revocation race** — authority may change between a loose pre-send check and network handoff unless the send consumes one immutable attempt-time authority snapshot.
5. **Duplicate/unknown execution** — a crash or timeout after provider acceptance can make blind retry issue a second billable invocation while the first remains unresolved.

A narrow Model Adapter boundary allows Hunter to add provider execution later without collapsing context selection, prompt compilation, transport, routing, validation, persistence, and domain authority into a generic `AIService`.

## Existing Architecture

### Accepted pre-model boundary

ADR 0031 establishes:

```text
EvidenceExtractionIntent
  -> Context resolution / selection / allocation
  -> EvidencePromptPlan
  -> EvidencePromptArtifact
  -> EvidencePreModelBuildRecord
  -> future ModelAdapter
  -> future ResponseValidator
  -> ExtractionProposal
  -> existing deterministic validation / authorized persistence
```

`EvidencePromptArtifact` owns exact canonical pre-model content, hashes, compiler/canonicalization identities, and measured size. Provider-specific wrapping, authentication, transport headers, and provider parameters are explicitly outside that artifact. If a future adapter transforms model-facing message content, ADR 0031 requires the transformed provider-facing representation to be distinct from the original prompt artifact; durable exact bytes or content-derived identifiers may be recorded only when Source Handling authorizes their categories.

### Source Handling boundary

ADR 0033 assigns exclusive authority for source-handling facts and policy to Evidence Intelligence Source Handling Authority. Callers, providers, prompt construction, repositories, generic cores, and future Model Adapter components are consumers only. Unknown/missing/conflicting/ambiguous authority blocks model-facing processing. Persistence independently resolves authority and rederives all handling decisions.

The Source Handling Design Contract gives concrete mechanics: processing, retention, reconstruction, access, and deletion/lifecycle decisions are separate derived outcomes; durable fields are governed by a historical field-category registry and policy dispositions; authorization is exact-payload bound; credentials/secrets are structurally constrained; and strict-known resolution is cutoff-bound. Historical reconstruction never substitutes current state, while a new live operation must use authority applicable and knowable at that new operation's cutoff.

### Project-neutral boundary

ADR 0032 establishes only a project-neutral portability boundary and admission rule. It explicitly withholds provider/model selection, routing, credentials, invocation, and response validation from the project-neutral core. No concrete current Hunter contract is admitted by default.

### Existing provider execution path

`provider.py` supplies useful operational precedent:

- provider metadata and health;
- explicit proposed/unavailable/rejected outcomes;
- provider exception normalization;
- prompt-injection detection over spans;
- forbidden response-capability checks;
- provider artifact/proposal persistence through the Evidence Intelligence repository.

However, it predates the current pre-model architecture and operates from `ExtractionRequest` rather than `EvidencePromptArtifact`. Its provider artifact identity is based on document/provider/schema/payload status and does not prove the exact provider request bytes. Its direct proposal creation also crosses the future Model Adapter -> Response Validator separation now required by ADR 0031. It should be treated as migration evidence, not as binding Model Adapter architecture.

### Repository and persistence separation

Accepted ADR 0009 (`Repository Purification`) separates provider integration, service authority, repository mechanics, and persistence. The Model Adapter may own execution semantics but must not make the repository an authority or let persistence code choose providers, alter prompts, validate meaning, or grant permissions.

## Constraints

### Constitutional

- **Evidence Authority:** exact request/response lineage and unavailable states must remain evidence-backed; unknown remains unknown.
- **Deterministic Intelligence:** the deterministic pre-model portion and all identities/serialization under Hunter control must remain reproducible. Remote model output itself is not made deterministic by assertion.
- **Architectural Integrity:** prompt compilation, transport, routing, validation, and canonical promotion remain distinct responsibilities.
- **Single Source of Truth:** Model Adapter execution semantics, Source Handling authority, response validation, and canonical domain authority each require one owner and may not compete.
- **Explainability:** every persisted AI-shaped outcome must be traceable to exact governed inputs and execution evidence to the extent durable evidence is authorized, with explicit unavailable states otherwise.
- **Long-Term Evolution:** provider substitution must not require rewriting historical prompt/build identity.

### Governance and accepted ADRs

The complete accepted-ADR index was checked for material applicability rather than assuming every accepted ADR governs this boundary.

- ADR 0031 directly governs the concrete Hunter pre-model chain and explicitly requires separate Model Adapter architecture.
- ADR 0032 directly governs project-neutral admission and explicitly excludes provider/model selection, routing, invocation, credentials, and response validation from shared-core authority.
- ADR 0033 directly governs Source Handling; the Model Adapter is a consumer only.
- ADR 0020 directly governs strict-known historical selection and prohibits current/latest fallback during historical reconstruction.
- ADR 0009 directly governs provider/service/repository separation.
- ADR 0025 was reviewed and is out of scope for this decision: it owns Canonical Valuation Evidence Assembly, not Evidence Intelligence model transport, provider attempts, or response handling. This ADPR changes none of its authority, record families, evidence assembly semantics, or runtime activation gates.
- ADR 0026 was reviewed and is out of scope for this decision: it owns Canonical Comparative Valuation methodology/peer semantics, not Evidence Intelligence model invocation. This ADPR creates no valuation/comparative-valuation authority or dependency.
- ADR 0028 was reviewed and is out of scope for the Model Adapter ownership decision: it governs valuation-methodology/evidence-assembly supporting authorities and exact semantic lineage into assembled valuation evidence. This ADPR does not modify those owners, records, sufficiency semantics, or the Evidence Assembly activation state.
- No AI/provider capability may enter Hunter Governance Review or Merge Readiness.

The out-of-scope determinations above are negative-scope evidence, not claims that those ADRs are unimportant repository-wide.

### Technical

- `EvidencePromptArtifact` is immutable and historically meaningful; the adapter may not mutate it in place.
- Provider-specific transformed content must have its own transient representation and, when durable request evidence is authorized, its own distinct durable artifact semantics.
- Durable request-artifact identity, Source Handling enforcement, retention decisions, and repository persistence remain Model Adapter responsibilities; provider transports own wire-format transformation and network mechanics only.
- Capability constraints used to make a prompt fit are upstream build inputs. Selecting a provider/model whose effective capability differs cannot silently reuse an incompatible build.
- External model execution is non-deterministic and network-dependent; historical audit must not pretend that re-invocation reproduces the original response.

### Operational

- Provider outages, timeouts, rate limits, quota exhaustion, billing unavailability, safety refusal, malformed/partial responses, unsupported capabilities, and uncertain delivery are normal explicit outcomes, not exceptional gaps to hide.
- Retry must be bounded and observable; a retry is a new attempt, not mutation of an old attempt.
- Every new invocation or retry is a new live processing event and must re-evaluate Source Handling authority at that attempt's own cutoff; prior build-time or prior-attempt `ALLOW` never authorizes a later network send.
- The attempt must exist durably before the network call. If delivery may have occurred but its result is unknown, automatic retry is prohibited until reconciliation or provider idempotency proves a duplicate send is safe.
- A first implementation should be operable with one explicit execution profile without forcing premature routing architecture.

### Persistence and migration

- Attempt, request, response, handoff, outcome, and failure history is append-only/immutable once recorded, but only fields and categories whose durable dispositions permit persistence may be recorded.
- A pre-send `ModelAttemptRecord` is immutable. Terminal state is represented by a separate append-only `ModelAttemptOutcomeRecord`; no attempt record is updated in place.
- Corrections are new records or superseding metadata, not mutation of historical bytes.
- Existing ADR 0031 build/artifact identities remain unchanged.
- Existing provider-runner records remain historical records of their original schema; migration must not relabel them as if they had Model Adapter request lineage they never recorded.

### Replay and historical reconstruction

- Exact audit reconstruction of a historical build or attempt may use only persisted artifacts and authority known at that historical artifact's recorded cutoff.
- A new provider invocation or retry is not historical replay and must resolve Source Handling authority applicable and knowable at the new attempt cutoff; restrictive successors known by then must block the new operation when they remove processing authority.
- Re-invoking a provider is a new execution, not historical replay.
- If exact request or response bytes were not authorized for retention, reconstruction is explicitly unavailable rather than regenerated from current state.
- A digest, measured size, content-derived identifier, or other derived request/response evidence may be retained only when the exact Source Handling durable disposition permits that category; hashes are not automatically safe.
- A nonterminal pre-send attempt remaining after crash/restart is evidence of uncertainty, not permission to retry.

### Compatibility

- ADR 0031 prompt/build identities remain historically valid.
- ADR 0033 handling identities and historical cutoffs remain authoritative for reconstruction; they do not authorize later live processing after a restrictive successor becomes applicable/knowable.
- `AIExtractionProvider` may be adapted or deprecated later, but existing historical artifacts are not rewritten.
- Response Validator and downstream proposal validation remain separate.

### Security and privacy

- Credentials, bearer tokens, API keys, cookies, authentication headers, client secrets, and equivalent transport secrets are never canonical request artifacts.
- Provider request durability may contain only governed non-secret data whose exact durable categories are authorized; otherwise request content/hash/content-derived identity remains explicitly unavailable.
- Source Handling Authority must allow model processing at the live attempt cutoff before any network handoff.
- A live send must consume a single-use handoff snapshot bound to the exact attempt and request-evidence state; it may not perform an unbound check and later send arbitrary bytes.
- Durable request/response content and content-derived metadata must pass the applicable attempt-time field-category/disposition enforcement independently at persistence.
- Provider responses are untrusted external data and may not request tools, repository writes, schema changes, or other capabilities outside the authorized adapter contract.

### Performance and scalability

- Provider transport latency is expected and must not leak into deterministic pre-model identity.
- Request/response byte retention may be expensive and must follow handling policy rather than convenience.
- Multi-provider fan-out, racing, or score-based routing is deliberately not required for the first boundary.

### Evidence and provenance

A model attempt must preserve enough evidence to answer, when authorized:

- which `EvidencePreModelBuildRecord` and `EvidencePromptArtifact` were used;
- which deterministic transport transformation was produced and, when permitted, which durable request artifact represented it;
- which versioned execution profile/provider/model/protocol was used;
- which pre-send attempt and single-use handoff snapshot authorized dispatch;
- which exact Source Handling authority identities and **attempt cutoff** governed that handoff, while separately preserving the build cutoff for upstream historical lineage;
- whether provider delivery was known, known-not-delivered, or uncertain;
- which provider idempotency/correlation evidence was available;
- which response artifact or explicit unavailable/persistence-failure outcome resulted; and
- whether exact reconstruction is available or unavailable.

## Evidence Inventory

| ID | Evidence | Authority/source | Finding | Quality and limitations | Supports or challenges |
|---|---|---|---|---|---|
| E-001 | Model Adapter explicitly deferred | Accepted ADR 0031 | Current architecture ends at pre-model build; later Model Adapter required | Highest architectural authority for this scope; direct | Requires new architecture |
| E-002 | Provider-specific request wrapping must be distinct | Accepted ADR 0031 | Provider-facing transformation must remain distinct from canonical prompt; durable exact bytes/derived IDs remain handling-governed | Direct architectural requirement | Supports separate request evidence semantics |
| E-003 | Project-neutral core admission/exclusions | Accepted ADR 0032 | Routing, credentials, invocation, provider selection and response validation are not shared-core authority; concrete shared contracts require two-consumer evidence | Direct; no current second-consumer Model Adapter evidence | Challenges shared-core option |
| E-004 | Source Handling canonical ownership | Accepted ADR 0033 | Model Adapter is consumer only; unresolved authority blocks model-facing processing; live attempts cannot inherit an older permission | Direct | Requires independent adapter enforcement |
| E-005 | Durable category and authorization mechanics | Source Handling Design Contract | Field/category dispositions and exact payload-bound authorization govern durable surfaces; content-derived hashes/identities are not automatically retainable | Runtime contract subordinate to accepted ADRs; mechanics are implemented/integrated in current runtime | Supports request/response persistence controls |
| E-006 | Concrete prompt/build runtime | `src/hunter/evidence_intelligence/pre_model.py` | `EvidencePromptArtifact`, `EvidencePreModelBuildRecord`, capability constraints, and Source Handling gated model processing exist | Direct current source observation | Defines exact upstream handoff |
| E-007 | Existing provider runner | `src/hunter/evidence_intelligence/provider.py` | Older runner has provider health/error/security/proposal mechanics but consumes spans/schema rather than PromptArtifact | Direct source; predates ADR 0031/0033 handoff | Useful migration precedent; insufficient as final architecture |
| E-008 | Pre-PR hygiene / governance cleanup complete | merged PRs #285 and #286 | Governance migration scaffolding retired and deterministic exact-head preflight hardened before Prompt Machine resumes | Current repository history | Removes process blocker, not architecture evidence |
| E-009 | ADPR numbering | `docs/architecture-index.md` and architecture-record README | ADPR-0001 through ADPR-0008 allocated; numbers are monotonic and never reused | Direct current index | Confirms ADPR-0009 |
| E-010 | ADR numbering | `docs/ADR/README.md` | ADR 0033 is latest allocated accepted ADR; no ADR 0034 exists | Direct current index/search | Supports planned ADR 0034 |
| E-011 | Accepted valuation/evidence-assembly ADR scope | ADRs 0025, 0026, 0028 and architecture index | These accepted decisions govern valuation/evidence-assembly authorities and do not own Evidence Intelligence model transport/provider attempts | Direct accepted architecture; negative-scope check | Confirms no hidden competing owner for this boundary |
| E-012 | Current architecture-index lifecycle/runtime distinction | `docs/architecture-index.md` in this contribution | ADPR-0006 remains `APPROVED` as preparation while current source contains later ADR 0031 provider-free runtime implementation | Direct repository evidence | Removes stale lifecycle/runtime contradiction |

### Evidence limitations

- No live provider benchmark, quota experiment, cost comparison, or production credential evidence is used here. Those are operational implementation inputs, not prerequisites for deciding the ownership boundary.
- The existing provider runner proves code shape and past semantics, not the correctness of a future Model Adapter.
- The architecture index previously conflated ADPR lifecycle status with downstream implementation state for ADR 0031. This contribution corrects the index to state both separately; this ADPR relies on current source plus accepted ADRs for runtime evidence.
- No second independent consumer has demonstrated a Model Adapter contract under ADR 0032. That absence is material to shared-core admission.
- Provider idempotency and reconciliation capabilities vary by provider. The architecture therefore requires explicit capability representation and fail-closed retry behavior rather than assuming idempotency exists.

## Assumptions

| ID | Assumption | Rationale | Confidence | Falsification condition | Consequence if false |
|---|---|---|---|---|---|
| A-001 | The first authorized Model Adapter implementation can target one explicit execution profile at a time | Avoids routing architecture before evidence exists | High | A real required workflow cannot operate without runtime choice among multiple providers/models | Multi-provider routing must be separately prepared before implementation |
| A-002 | Provider APIs can represent the canonical prompt either directly or through a deterministic transform whose exact non-secret request content can be identified in memory and durably recorded only when handling permits | Common transport property and ADR 0031 already anticipates transformations | High | A provider requires opaque mutable server-side prompt transformation that cannot be represented/audited even transiently | That provider is not admissible under this boundary without new architecture |
| A-003 | Source Handling decisions applicable at each model attempt are sufficient to constrain request/response durability when combined with the field-category registry | Follows ADR 0033 design | Medium | Provider responses introduce a new durable data category not expressible by the governed registry | Source Handling design must be extended before response persistence |
| A-004 | Remote model output cannot be reproduced deterministically merely by replaying the same request | External model execution is outside Hunter deterministic control | High | Provider supplies a verifiable deterministic execution contract Hunter can prove | Architecture may later add a stronger reproducibility mode; current audit/replay distinction remains safe |
| A-005 | The persistence substrate used by the Model Adapter can durably create a pre-send attempt and one immutable handoff snapshot before network dispatch | Required to avoid orphan sends with no attempt lineage | Medium | Repository/storage cannot provide durable pre-send commit or unique handoff consumption semantics | Provider activation remains blocked until an authorized durable outbox/transaction mechanism is designed |

## Architectural Dimensions

1. **Authority and ownership** — who owns Model Adapter execution semantics and who is prohibited from acquiring upstream/downstream authority.
2. **Consumer versus shared core** — whether provider mechanics remain Hunter-owned under ADR 0032 admission rules.
3. **Prompt immutability** — preserving canonical prompt bytes while permitting provider-specific transport representations.
4. **Execution profile identity** — exact provider/model/protocol/capability binding without giving the provider selection authority over upstream build semantics.
5. **Provider request evidence** — exact non-secret bytes/structure when durably authorized, plus explicit unavailable semantics when content-derived durability is prohibited.
6. **Artifact ownership** — transport returns deterministic wire transformation; Model Adapter alone owns durable request/attempt/handoff/outcome artifact creation and persistence semantics.
7. **Attempt identity and retries** — append-only pre-send attempt lineage, idempotency/correlation, uncertain-delivery, reconciliation, and explicit retry eligibility.
8. **Response identity** — exact/raw or unavailable response evidence distinct from validated proposal semantics.
9. **Source Handling enforcement** — live processing authority resolved at the attempt cutoff and bound into one immutable handoff snapshot; historical build cutoff is lineage/reconstruction only.
10. **Credential exclusion** — transport secrets excluded structurally from canonical bytes, logs, diagnostics, and hashes.
11. **Failure/missingness** — timeout, quota, billing, refusal, malformed data, provider outage, unsupported capability, uncertain delivery, and persistence failure are explicit.
12. **Persistence/replay** — exact historical audit versus new re-invocation.
13. **Provenance/observability** — proof chain from build to transformation/request to pre-send attempt to handoff to outcome/response, subject to durable dispositions.
14. **Routing** — whether selecting among providers/models is part of the adapter or a later authority.
15. **Validation boundary** — transport success versus Response Validator semantics.
16. **Migration** — coexistence with legacy `AIExtractionProvider` / `SecureAIProviderRunner` records.
17. **Testability** — deterministic contract and fake-transport tests without live provider dependencies.
18. **Reversibility/no lock-in** — ability to add/replace provider transports without rewriting prompt/build history.
19. **Governance isolation** — no provider/LLM dependency in merge authority.

## Candidate Options

### Option 1 — Extend the existing provider runner in place

- **Description:** Modify `SecureAIProviderRunner` / `AIExtractionProvider` so the current `ExtractionRequest` path also accepts or derives from `EvidencePromptArtifact`, keeping the existing provider/proposal flow as the main abstraction.
- **Authority and ownership:** Evidence Intelligence owned, but existing runner currently combines health, transport, response capability checks, artifact persistence, and proposal creation.
- **Boundaries:** Smallest code movement but risks retaining pre-ADR mixed responsibilities.
- **Persistence and replay:** Would require adding request/attempt lineage to existing artifacts or creating parallel fields.
- **Evidence and provenance:** Can be improved, but old and new records share names with different guarantees.
- **Compatibility:** High short-term compatibility; high semantic migration risk.
- **Advantages:** Low immediate implementation effort; reuses provider health/error code.
- **Disadvantages:** Encourages silent upgrade of old artifacts into guarantees they did not historically possess; blurs Response Validator boundary.
- **Failure modes:** Direct proposal creation can bypass future validation separation; provider request bytes may remain implicit; uncertain-delivery recovery is not defined.
- **Migration implications:** Complex version branching inside a legacy abstraction.
- **Reversibility:** Medium.
- **Open dependencies:** Must redesign multiple existing record semantics simultaneously.

### Option 2 — Consumer-owned provider-neutral Model Adapter with provider-specific transports; routing deferred

- **Description:** Define a new Evidence Intelligence `ModelAdapter` contract that consumes an exact completed pre-model build and one explicit versioned `ModelExecutionProfile`. Provider-specific transports deterministically map the canonical prompt to a non-secret provider request transformation result but do not create durable artifacts. The Model Adapter applies Source Handling, creates any authorized request artifact, persists a pre-send attempt, creates/consumes the attempt-bound handoff snapshot, invokes transport, and records immutable outcomes. It does not choose among profiles dynamically.
- **Authority and ownership:** Evidence Intelligence owns adapter/request/attempt/handoff/outcome semantics. Source Handling remains sole handling authority. Provider transports own protocol transformation/network mechanics only. Repositories persist mechanically. Response Validator remains separate.
- **Boundaries:** `PreModelBuild -> ModelAdapter -> transport transformation result -> authorized ProviderRequestArtifact or request-evidence-unavailable -> pre-send ModelAttemptRecord -> ModelHandoffRecord -> transport send -> ModelAttemptOutcomeRecord + ProviderResponseArtifact/unavailable -> future ResponseValidator`.
- **Persistence and replay:** Append-only attempt/handoff/outcome lineage; exact request/response reconstruction only when authorized; re-invocation always a new attempt; uncertain delivery blocks blind retry pending reconciliation/idempotency proof.
- **Evidence and provenance:** Strong linkage from prompt/build to profile, request-evidence state, attempt, handoff, delivery certainty, and response/outcome.
- **Compatibility:** Existing provider runner can remain historical and later be adapted behind a transport or deprecated without rewriting old records.
- **Advantages:** Preserves ADR 0031 identities, minimizes provider lock-in, keeps routing out of scope, defines crash/retry safety, provides deterministic test seam, preserves validation separation.
- **Disadvantages:** Introduces new records and a migration boundary rather than reusing old provider artifacts directly; requires durable pre-send commit/handoff semantics.
- **Failure modes:** Capability mismatch, restrictive successor, prohibited durable fields, uncertain delivery, or persistence failure all fail closed or remain explicit; no silent adaptation/retry.
- **Migration implications:** New V1 record families coexist with legacy provider artifacts; no backfill of nonexistent request lineage.
- **Reversibility:** High; transports can be replaced while canonical pre-model and attempt history remain stable.
- **Open dependencies:** Exact storage/outbox mechanics and provider-specific idempotency implementation belong to later design/implementation, but the required semantics are fixed here.

### Option 3 — Promote Model Adapter into project-neutral Prompt Intelligence core now

- **Description:** Put a reusable model/provider adapter contract under a project-neutral top-level core and have Hunter use it through an adapter.
- **Authority and ownership:** Shared core would own invocation mechanics.
- **Persistence and replay:** Could be neutral, with Hunter persisting outputs.
- **Evidence and provenance:** Potentially reusable.
- **Compatibility:** Requires cross-consumer semantic evidence that does not exist.
- **Advantages:** Maximum theoretical reuse.
- **Disadvantages:** Violates ADR 0032 admission discipline today; risks provider/credential/routing authority leaking into core.
- **Failure modes:** Premature abstraction and domain-policy loss.
- **Migration implications:** High.
- **Reversibility:** Medium-low once external consumers bind.
- **Open dependencies:** At least one additional real consumer with versioned Model Adapter semantics and an admission review.

### Option 4 — Standalone provider gateway/service from day one

- **Description:** Externalize model invocation into an independent network service that accepts prompt artifacts and returns response artifacts.
- **Authority and ownership:** Gateway owns transport execution; Hunter adapter owns domain mapping.
- **Persistence and replay:** Requires cross-service identity, auth, durable protocol, versioning, and deployment semantics immediately.
- **Evidence and provenance:** Can be strong if designed well.
- **Compatibility:** Adds a new production boundary not justified by current scale/evidence.
- **Advantages:** Strong operational isolation; could serve multiple future consumers.
- **Disadvantages:** Large complexity, secret management, network trust, availability, version skew, and migration burden.
- **Failure modes:** Gateway becomes de facto routing/credential/retention authority or a new single point of failure.
- **Migration implications:** High.
- **Reversibility:** Low-medium.
- **Open dependencies:** Multi-consumer need, deployment/security architecture, service ownership, compatibility protocol.

### Option 5 — Direct single-provider transport with no provider-neutral adapter contract

- **Description:** Implement one concrete provider class directly from `EvidencePromptArtifact` to one external API, recording attempt artifacts but without an explicit neutral Model Adapter interface.
- **Authority and ownership:** Evidence Intelligence/provider-specific implementation combined.
- **Persistence and replay:** Can satisfy exact lineage for one provider when handling permits its durable evidence.
- **Evidence and provenance:** Potentially strong for the selected provider.
- **Compatibility:** Lowest initial abstraction cost.
- **Advantages:** Small initial implementation and minimal generic surface.
- **Disadvantages:** Provider-specific request/response semantics leak into service contracts; later provider substitution requires refactoring canonical execution code.
- **Failure modes:** Provider model names/parameters become accidental canonical authority; retry/idempotency semantics can become provider-specific architecture by accident.
- **Migration implications:** Medium-high if a second provider is later needed.
- **Reversibility:** Medium.
- **Open dependencies:** Concrete provider chosen before architecture proves the stable consumer-owned seam.

## Comparative Analysis

| Criterion | Option 1: extend legacy runner | Option 2: neutral Hunter adapter + transports | Option 3: shared core now | Option 4: standalone gateway | Option 5: direct provider |
|---|---|---|---|---|---|
| Correctness against ADR 0031 handoff | Medium | **High** | Medium | High | Medium-high |
| Constitutional compliance | Medium | **High** | Low-medium today | Medium | Medium |
| Governance compliance | Medium | **High** | **Low today** (ADR 0032 admission unmet) | Medium | Medium-high |
| Authority clarity | Medium-low | **High** | Medium | Medium | Medium-low |
| Prompt identity preservation | Medium | **High** | High | High | High if carefully implemented |
| Source Handling isolation | Medium | **High** | Medium | Medium | Medium |
| Response Validator separation | Low-medium | **High** | High | High | Medium |
| Replay/audit clarity | Medium | **High** | High | High but distributed | High for one provider |
| Crash/uncertain-delivery safety | Low | **High** | Medium | High if specified | Medium-low |
| Credential exclusion boundary | Medium | **High** | Medium | High but operationally complex | Medium |
| Provider lock-in | Medium-high | **Low** | Low | Low | **High** |
| Operational complexity | Low-medium | **Medium** | Medium | **High** | Low |
| Migration risk | High semantic risk | **Low-medium** | High | High | Medium-high |
| Implementation effort | Low-medium | Medium | High | Very high | Low |
| Reversibility | Medium | **High** | Medium | Low-medium | Medium |
| Long-term extensibility | Medium | **High** | High only after admission | High | Low-medium |
| Deterministic testability without live provider | Medium | **High** | High | Medium | Medium-high |

Option 2 gives the best current balance: it creates only the consumer-owned seam Hunter demonstrably needs, preserves future provider substitution, satisfies ADR 0032 by not claiming shared ownership, and fixes handoff/retry semantics without introducing multi-provider routing or a network gateway.

## Falsification Results

### Option 1 falsification

**Hypothesis:** the existing provider runner can simply be declared the Model Adapter.

**Counterevidence:** it consumes `ExtractionRequest` built from spans/schema rather than the canonical `EvidencePromptArtifact`, directly creates `ExtractionProposal`, and lacks the pre-send attempt/handoff/uncertain-delivery semantics required here. The hypothesis fails unless the runner is materially redesigned, at which point preserving it as the architectural abstraction gives little benefit and creates legacy semantic ambiguity.

**Result:** does not survive as the preferred architecture. Some health/error/detector mechanics may be reused later as implementation details.

### Option 2 falsification

**Hypothesis:** a new provider-neutral Hunter-owned adapter seam is unnecessary abstraction because only one provider may be used initially.

**Test:** remove all multi-provider routing from the option and require only one explicit execution profile. The neutral seam still has independent value because it separates canonical prompt identity from provider-specific request transformation, Source Handling/artifact ownership, durable pre-send attempt identity, one-shot handoff, transport secrets, uncertain delivery, response evidence, and future validation. Those boundaries exist even with one provider.

**Boundary case:** provider requires transformation of message structure. The option survives because the transport returns a deterministic non-secret transformation result; the Model Adapter alone decides whether any exact bytes/hash/content-derived identity may become durable evidence.

**Boundary case:** provider/model capability differs from the build capability. The option survives only by failing closed and requiring a new build; silent adaptation is prohibited.

**Boundary case:** a build was allowed at its historical cutoff but a restrictive successor becomes known before a new invocation or retry. The option survives because the Model Adapter resolves at the attempt cutoff and atomically binds the resulting authority snapshot to a single-use handoff before dispatch. The build cutoff remains historical lineage, not live authorization.

**Boundary case:** exact request or response retention, hashes, or content-derived IDs are forbidden. The option survives by persisting only categories explicitly authorized by the relevant durable dispositions and recording explicit unavailability for prohibited evidence; a mandatory content-derived artifact identity is not required.

**Boundary case:** timeout/crash occurs after the provider may have accepted the request but before Hunter has a terminal outcome. The durable pre-send attempt remains nonterminal/uncertain; automatic retry is prohibited unless provider idempotency or reconciliation proves duplicate dispatch cannot occur.

**Boundary case:** a response is captured but terminal persistence fails. The provider call is never treated as safely retryable. The pre-send attempt remains durably unresolved and recovery records a persistence-failure/unknown outcome when canonical persistence resumes; exact response reconstruction may be unavailable if policy or failure prevented durable response capture.

**Result:** survives falsification.

### Option 3 falsification

**Hypothesis:** invocation is generic enough to put directly into the ADR 0032 neutral core.

**Counterevidence:** ADR 0032 explicitly withholds provider/model selection, routing, credentials, invocation, and response validation, and requires two-consumer evidence before concrete contract admission. No such second-consumer Model Adapter contract exists.

**Result:** falsified under current authority. Reconsider after admission evidence exists.

### Option 4 falsification

**Hypothesis:** a network gateway is safer because secrets and provider code are isolated.

**Counterexample:** isolation also creates a new service identity, authentication protocol, persistence boundary, availability dependency, deployment lifecycle, version compatibility matrix, and potential de facto routing/handling authority. None is required by current evidence.

**Result:** fails proportionality and scope today. Reconsider after multiple real consumers/providers make independent deployment valuable.

### Option 5 falsification

**Hypothesis:** provider-specific code is the smallest correct V1 and can be generalized later.

**Counterexample:** even one provider needs stable distinctions among canonical prompt, transport transformation, Source Handling-owned durability decisions, pre-send attempt, handoff, idempotency/correlation, uncertain outcome, raw response, and validation. If those concepts exist only in provider-specific types, the first provider's protocol becomes the accidental architecture.

**Result:** viable as an implementation shortcut but inferior as architecture. Reconsider only if Hunter permanently commits to one provider and accepts the migration cost explicitly in a later decision.

## Recommended Architecture

### Ownership

Create an Evidence Intelligence-owned Model Adapter boundary. The canonical adapter contract belongs to Hunter Evidence Intelligence, not to the project-neutral core and not to the provider transport.

Provider-specific transports are subordinate implementations. They may:

- deterministically translate the adapter's canonical prompt/profile inputs into a **non-secret transport transformation result**;
- perform the network call only when given a valid single-use dispatch handoff by the Model Adapter;
- return raw transport response/error/correlation evidence to the Model Adapter.

They may not:

- select or modify context;
- mutate `EvidencePromptArtifact`;
- create or persist `ProviderRequestArtifact`, `ModelAttemptRecord`, `ModelHandoffRecord`, `ModelAttemptOutcomeRecord`, or `ProviderResponseArtifact` as canonical Hunter artifacts;
- create Source Handling authority or decide retention/durability;
- choose a different capability constraint silently;
- validate response truth;
- create canonical knowledge;
- approve tools or autonomous actions;
- alter Hunter governance state.

The Model Adapter owns the durable execution artifact graph and calls repositories mechanically after rederiving the exact authorized durable state. This avoids ownership inversion where provider-specific code would own canonical artifact identity or retention semantics.

### Execution profile

Introduce a versioned `ModelExecutionProfile` concept owned by the Model Adapter boundary. It identifies only execution-relevant facts, such as:

- profile identity/version;
- provider transport identity/version;
- exact provider and model identifier/version/endpoint-class identity that is safe to persist;
- request protocol/version;
- required capability-constraint identity or exact compatibility requirement;
- deterministic non-secret request parameters that affect model execution;
- prohibited capabilities/tools;
- response format expectation identity;
- provider idempotency/correlation capability classification (`SUPPORTED`, `UNAVAILABLE`, or explicit unknown/blocking state).

V1 does **not** dynamically select among profiles. The authorized caller/workflow supplies or is bound to one explicitly configured profile, and the adapter verifies it. Multi-profile/provider routing is a separate future architecture decision.

If the execution profile's capability contract does not exactly satisfy the capability constraint used by the `EvidencePreModelBuildRecord`, invocation fails closed. A different capability requires a new allocation/prompt/build identity upstream.

### Provider request artifact

The provider transport first returns a deterministic, non-secret **transport transformation result** in memory. That result is not a Hunter durable artifact and carries no Source Handling or persistence authority.

The Model Adapter then independently resolves the applicable Source Handling durable dispositions. When those dispositions authorize durable request evidence, the **Model Adapter**, not the transport, constructs and persists a `ProviderRequestArtifact` through the mechanical repository boundary. It may record only the allowed categories, linked as applicable to:

- `EvidencePreModelBuildRecord` identity;
- `EvidencePromptArtifact` identity;
- `ModelExecutionProfile` identity/version;
- transport/request protocol identity/version;
- exact non-secret canonical provider-facing message/body bytes **only when exact-content retention is authorized**;
- exact content hashes, measured size, encoding/canonicalization metadata, or other `CONTENT_DERIVED_ID`/derived fields **only when each field's durable category is explicitly authorized**;
- a content-derived deterministic request identity **only when its durable category is explicitly authorized**;
- explicit per-category reconstruction availability/unavailability and reason codes.

No content-derived hash, size, digest, canonical-byte fingerprint, or identity is presumed safe merely because model processing is allowed. When those durable categories are denied, the request may still be transmitted if live processing is authorized, but prohibited evidence is not persisted. The attempt instead records an authorized non-content-derived request-evidence outcome such as `REQUEST_EVIDENCE_UNAVAILABLE_BY_POLICY`; if even that metadata category is not authorized, no substitute identifier is fabricated.

A durable request artifact, when one exists, is always semantically distinct from `EvidencePromptArtifact`, even when its authorized content mapping is byte-equivalent. When no durable request artifact is permitted, that absence is explicit and does not collapse back into the prompt artifact.

Authentication headers, bearer tokens, API keys, cookies, client secrets, signing secrets, and equivalent credentials are structurally outside the transport transformation result used for Hunter evidence and outside every canonical hash. Their existence may be represented only by a non-secret credential-slot/configuration identity if separately authorized; secret values never appear.

### Atomic Source Handling handoff

A live model invocation has two Source Handling coordinates that must not be conflated:

1. **Build lineage coordinate.** The adapter may re-resolve the build's historical Source Handling state at the build cutoff to verify historical lineage. This is reconstruction/audit evidence only and grants no permission for a new network operation.
2. **Attempt authorization coordinate.** For every invocation or retry, the Model Adapter resolves Source Handling for the governed document/version using the new attempt's effective context and strict-known **attempt cutoff**. The live send is authorized only by this attempt-time resolution.

The loose pattern `resolve -> later send` is prohibited. Before any network call, the Model Adapter must establish an immutable `ModelHandoffRecord` (name may vary in implementation but semantics are mandatory) as **execution evidence, not Source Handling authority**. Its creation and authority resolution must occur from one serializable/atomic Source Handling snapshot or an equivalent Source Handling-issued snapshot/capability primitive. If the storage/authority substrate cannot provide an equivalent atomic snapshot-to-handoff guarantee, Model Adapter activation remains blocked.

The handoff binds, to the extent each durable category is authorized:

- handoff identity and schema version;
- pre-send `ModelAttemptRecord` identity;
- build/prompt/execution-profile identities;
- durable `ProviderRequestArtifact` identity when permitted, otherwise the explicit request-evidence-unavailable state;
- exact fact, policy, field-category-registry, authorization-rule, and required provenance identities resolved for the attempt;
- attempt effective context and strict-known attempt cutoff;
- `processing_decision == ALLOW`;
- exact durable-disposition identities/results applicable to request/response categories;
- an opaque single-use dispatch nonce/capability identity that is not content-derived and is persisted only when its metadata category is authorized;
- expiry/dispatch-validity bound required by the implementation contract.

The provider transport may execute a network send only by consuming a valid handoff bound to the same attempt/profile/transformation state. Consumption must be single-use through a unique/compare-and-set/transactional-outbox equivalent so two workers cannot dispatch the same attempt twice. The handoff does **not** become a second handling authority: its facts/decisions are valid only because they are bound to the exact canonical Source Handling snapshot at the attempt cutoff.

A restrictive successor already applicable/knowable at the handoff cutoff must be included and can block creation of the handoff. A successor that becomes applicable/knowable only **after** the handoff's committed cutoff does not retroactively rewrite that already-created execution evidence; every later retry/new attempt must create a new handoff and therefore sees the later authority. This defines the atomic authorization point instead of pretending the system can continuously re-check authority during an external network call.

### Model attempt, durable commit, and uncertain delivery

Every possible network invocation starts with a durable immutable `ModelAttemptRecord` **before** any provider call. The attempt record establishes identity and lineage but not a terminal result. At minimum, subject to durable dispositions, it links:

- execution owner;
- pre-model build ID;
- prompt artifact ID;
- provider request artifact ID when one durably exists, otherwise the explicit authorized request-evidence-unavailable outcome/reason;
- execution profile ID;
- attempt ordinal;
- predecessor/retry attempt ID where applicable;
- attempt effective context/cutoff and build cutoff as distinct coordinates;
- created/recorded time;
- provider idempotency capability and an opaque attempt-scoped idempotency key/correlation slot when supported and safe to persist.

Terminal result is a separate append-only `ModelAttemptOutcomeRecord`; the pre-send attempt is never updated in place. The outcome links the attempt/handoff and records terminal or uncertainty state, response-artifact identity when one exists, safe provider correlation metadata, timestamps, and reconstruction availability.

Provider idempotency/retry rules are binding:

- When the provider supports a trustworthy idempotency key, Hunter uses one stable opaque attempt-scoped key for reconciliation of **that attempt**. A new intentional retry is a new Hunter attempt and must not silently reuse semantic state from the predecessor.
- When provider idempotency is unavailable or cannot prove whether a request was accepted, a timeout, connection loss, process crash, or ambiguous provider error after dispatch produces `DELIVERY_UNKNOWN` / `OUTCOME_UNKNOWN` semantics. It is not classified as safe non-delivery.
- An uncertain attempt is **not automatically retryable**. Recovery must first reconcile using provider correlation/status/idempotency facilities when available. If reconciliation cannot establish non-delivery or a definitive terminal result, the attempt remains unknown and automated retry is blocked; a separately governed operator/policy decision may later authorize a new attempt.
- A retry may proceed automatically only from outcomes whose semantics prove that no provider execution occurred, or after reconciliation establishes a safe retry condition. Rate-limit/validation errors are not assumed non-delivery unless the provider contract proves that property.
- Crash recovery scans durable pre-send attempts lacking terminal outcome. Such attempts are reconstructed as uncertain pending reconciliation, never as failed-safe-to-retry.

High-level outcome families must distinguish at least:

- `SUCCEEDED_TRANSPORT` — response received; says nothing about semantic validity;
- `PROVIDER_UNAVAILABLE`;
- `TIMEOUT_CONFIRMED_NO_DELIVERY` when the provider contract proves no dispatch/acceptance;
- `DELIVERY_UNKNOWN` / `OUTCOME_UNKNOWN`;
- `RATE_LIMITED` with explicit delivery certainty;
- `QUOTA_UNAVAILABLE`;
- `BILLING_UNAVAILABLE`;
- `CAPABILITY_UNSUPPORTED`;
- `PROVIDER_REFUSED`;
- `MALFORMED_TRANSPORT_RESPONSE`;
- `SECURITY_BLOCKED`;
- `SOURCE_HANDLING_BLOCKED`;
- `RESPONSE_CAPTURED_PERSISTENCE_FAILED` as an operational observation when response capture succeeded but canonical terminal persistence failed;
- `INTERNAL_ADAPTER_ERROR`.

If a response is captured but canonical persistence of the response/outcome fails, the adapter must **not retry**. While the process is alive it reports `RESPONSE_CAPTURED_PERSISTENCE_FAILED` and retains response bytes only within the already-authorized transient/durable handling rules. If the canonical store itself is unavailable and therefore cannot record that failure, the durable pre-send attempt intentionally remains nonterminal; recovery treats it as `OUTCOME_UNKNOWN`, performs reconciliation, and never fabricates a terminal result. A later recovery record may state that exact response evidence is unavailable if it could not be retained legally or durably.

### Provider response artifact

A received provider response creates a distinct `ProviderResponseArtifact` or explicit unavailable durable state **under Model Adapter ownership**. The transport returns raw response evidence but does not own canonical artifact creation. The Model Adapter applies Source Handling and records, as authorized:

- attempt/outcome identity;
- provider request artifact ID when one durably exists, otherwise the authorized request-evidence-unavailable linkage;
- execution profile ID;
- exact response protocol/version;
- exact raw or canonical response bytes when Source Handling allows retention;
- content hash only when the relevant durable category allows hash retention;
- measured size, encoding/canonicalization metadata, or other content-derived fields only when their categories are authorized;
- provider finish/status metadata that is safe and governed;
- reconstruction availability/unavailability reason.

Provider response bytes are untrusted external data. Merely persisting them does not make them an `ExtractionProposal` and does not validate schema, citations, truth, evidence use, prohibited capabilities, or domain eligibility.

### Response Validator boundary

The Model Adapter ends after transport-level response capture, normalization, and authorized persistence/outcome recording. A separately governed future `ResponseValidator` consumes the response evidence and may validate syntax/schema/capability/evidence-reference rules. Only after that boundary may Evidence Intelligence produce an `ExtractionProposal` through its authorized service path.

The existing legacy provider runner's direct proposal creation is not adopted as the new Model Adapter rule. Historical behavior remains historical; any future migration must preserve old schema identity while routing new executions through the new boundary.

### Routing boundary

Provider/model routing is deferred. V1 architecture supports one explicit execution profile per attempt and no score-based, health-based, cost-based, quota-based, or fallback selection among alternative profiles.

If Hunter later needs automatic selection among two or more provider/model profiles, that creates a new authority/operational decision covering selection inputs, cost/latency/quality criteria, failure fallback, capability compatibility, and deterministic audit. That work requires a separate ADPR/ADR or explicit accepted amendment.

### Replay and re-execution

- Reconstructing a historical build or attempt means reading its persisted immutable evidence under that artifact's historical cutoff and authority state; current/later authority never backfills historical absence.
- Re-invoking a provider, even with identical transient request bytes and model identifier, is a **new attempt**, not replay of the old response.
- Every new invocation/retry resolves live Source Handling at its own attempt cutoff and creates a new attempt/handoff; an older build or attempt `ALLOW` is never reused as live permission.
- No current provider version or current configuration may backfill missing historical execution evidence.
- When exact bytes, hashes, measured sizes, or content-derived identifiers were not durably authorized, reconstruction remains explicitly unavailable for those categories.
- A persisted pre-send attempt without a terminal outcome is replayed as uncertain state, not rewritten into a failure or success based on later convenience.

### Mandatory conformance cases

The resulting ADR and implementation contract must require deterministic tests for these defect classes before any live provider integration:

1. **Revocation between build and handoff:** build-time Source Handling resolves `ALLOW`; before invocation a restrictive successor becomes applicable/knowable. The attempt-time atomic snapshot must block handoff creation and transmit no bytes. Historical reconstruction of the build still uses the original build cutoff.
2. **Revocation between attempts:** attempt 1 is authorized; a restrictive successor becomes applicable/knowable before retry. Attempt 2 must resolve a new snapshot and block. Prior success cannot authorize retry.
3. **Atomic snapshot/dispatch binding:** the transport cannot send without a handoff bound to the exact attempt/profile/request-evidence state; stale, mismatched, expired, reused, or concurrently consumed handoffs are rejected. Tests prove one handoff cannot dispatch twice.
4. **Processing allowed, content durability denied:** model processing is `ALLOW`, while request exact bytes, `CONTENT_DERIVED_ID`, hashes, measured size, or equivalent derived durable categories are denied. Transmission may proceed, but no prohibited bytes/hash/derived identity is persisted; `ModelAttemptRecord` does not require a prohibited request-artifact ID.
5. **Response echo under restrictive durability:** provider response echoes protected source content while response retention/hash categories are denied. The attempt records only authorized metadata/unavailable state and persists no prohibited response bytes or derived digest.
6. **Ownership inversion guard:** fake provider transports can return transformation/response evidence but cannot construct/persist canonical request/attempt/handoff/outcome/response artifacts or decide Source Handling dispositions.
7. **Durable-before-send:** transport invocation is impossible until the pre-send attempt and valid handoff are durably established.
8. **Crash/timeout after possible acceptance:** an incomplete post-dispatch attempt becomes `DELIVERY_UNKNOWN`/`OUTCOME_UNKNOWN`; no automatic retry occurs until reconciliation proves a safe retry condition.
9. **Idempotency capability:** supported provider idempotency is exercised deterministically; unsupported/unknown idempotency never defaults to retry-safe.
10. **Response captured, persistence fails:** no retry is emitted; if terminal failure cannot be persisted, the durable pre-send attempt remains nonterminal and recovery treats it as unknown pending reconciliation.
11. **No authority laundering:** caller/provider supplied handling decisions, prior build decisions, prior attempt decisions, or presence of existing prompt bytes cannot make either a live processing gate or durable category permissive.
12. **Cutoff separation:** historical replay/reconstruction uses the historical artifact cutoff; live invocation uses the attempt/handoff cutoff. Tests fail if either coordinate is substituted for the other.
13. **Accepted-ADR applicability review:** architecture review must either cite every materially applicable accepted ADR or explicitly record a justified out-of-scope determination; absence of a named ADR cannot be mistaken for having reviewed it.
14. **Lifecycle/runtime status consistency:** architecture-index tests/checks or deterministic review tooling must distinguish immutable ADPR lifecycle state from later runtime implementation state and reject a known contradiction such as “not started” while canonical runtime evidence is present.

These are permanent regression obligations for the Model Adapter boundary and architecture-maintenance process, not review-only prose. A future implementation that cannot encode items 1-12 as deterministic tests is not ready for provider activation. Items 13-14 must be enforced by deterministic repository review/preflight tooling before future architecture contributions claim canonical consistency; the exact reusable check location may be chosen in the next implementation/governance hardening slice rather than by this architecture-only PR.

## Rejected Options

### Extend the legacy runner as the architecture

Rejected because it predates the canonical prompt artifact handoff and combines responsibilities that ADR 0031 now separates. Reconsider only as an internal compatibility adapter behind the new boundary, never as proof that old records possess new lineage.

### Shared project-neutral Model Adapter now

Rejected by ADR 0032's two-consumer admission rule and explicit provider/invocation exclusions. Reconsider when two independent consumers expose versioned equivalent execution contracts and an architecture review admits the common semantics.

### Standalone gateway/service now

Rejected as premature. Reconsider when multiple real consumers/providers, independent release cadence, isolation requirements, or deployment economics make a service boundary cheaper and safer than in-process adapter isolation.

### Direct provider-specific architecture

Rejected because provider-specific semantics would become the accidental stable seam. Reconsider only if a later accepted decision intentionally makes one provider a long-term architectural dependency.

## Risks

| Risk | Category | Likelihood | Impact | Mitigation | Residual uncertainty |
|---|---|---|---|---|---|
| Execution-profile/capability mismatch reuses an invalid prompt build | Correctness | Medium | High | Exact compatibility check; fail closed; require new pre-model build | Exact compatibility representation remains implementation design |
| Request transformation changes model-facing meaning | Correctness | Medium | High | Transport returns deterministic non-secret transformation; Model Adapter owns durable artifact semantics; never mutate prompt artifact | Provider SDKs may perform hidden transformations; such modes are inadmissible unless observable |
| Credentials leak into artifacts/logs/hashes | Security | Low-medium | Critical | Structural exclusion, secret-free transformation evidence, tests scanning durable/log surfaces | Third-party SDK diagnostics require explicit hardening |
| Source content is processed after authority becomes unresolved/revoked | Security/governance | Low-medium | High | Atomic attempt-time authority snapshot bound to single-use handoff; new attempt for every retry | Exact transaction/capability implementation must be proven before activation |
| Handoff is reused or mismatched | Security/correctness | Low | High | Single-use consumption, unique attempt binding, expiry, deterministic rejection tests | Distributed-worker implementation details remain later design |
| Content-derived request evidence persists when durability is denied | Security/privacy | Low-medium | High | Per-category durability checks; no mandatory hash/content-derived request ID; explicit unavailable state | Exact field-category mapping must be validated before implementation |
| Duplicate provider billing/execution after uncertain timeout/crash | Operational/cost | Medium | High | Durable pre-send attempt, provider idempotency classification, no blind retry, reconciliation | Some providers may expose no reconciliation API; such attempts can remain unknown |
| Response captured but canonical persistence fails | Evidence/operational | Low-medium | High | No retry; nonterminal durable attempt becomes unknown; reconcile on recovery; preserve response only if handling and storage permit | Exact response may be irrecoverable after process loss |
| Raw model response persists prohibited source echoes | Security/privacy | Medium | High | Model Adapter governs response durable categories; fail closed on unresolved disposition | Output-specific category coverage may need design refinement |
| Adapter transport success is mistaken for valid proposal | Authority | Medium | High | Separate Response Validator; attempt outcome named transport-only; no proposal creation in adapter | Legacy runner semantics may confuse migration unless clearly versioned |
| Multi-provider fallback sneaks in as implementation convenience | Governance/operational | Medium | Medium-high | V1 explicitly forbids routing/fallback; separate architecture required | Pressure may rise during outages/quota limits |
| Legacy provider artifacts are retroactively relabeled | Migration | Low | High | Preserve historical schema/identity; no synthetic backfill | Mapping old records to new audit views may remain partial |
| Remote provider behavior changes despite same model name | Replay | High | Medium-high | Record exact safe provider/model/version/protocol metadata when authorized; distinguish audit from re-invocation | Provider may not expose immutable model revision identifiers |
| Provider-specific SDK hides wire request bytes | Evidence | Medium | High | Require deterministic observable transient transformation; reject opaque modes/providers | Some provider SDKs may need lower-level HTTP transport |
| Architecture index becomes stale again | Governance/evidence | Medium | Medium | Explicit lifecycle/runtime distinction plus future deterministic repository check required by conformance case 14 | Generic cross-ADR status inference needs a reusable checker |

## Open Questions

| Question | Blocking? | Owner | Required evidence or action | Status |
|---|---|---|---|---|
| Exact Python module/type names and SQL layout | No | future implementation | Design mechanically under accepted ADR | Deferred |
| Exact closed attempt/outcome reason-code vocabulary | No | future design/implementation | Contract tests before runtime | Deferred |
| Which concrete provider should first implement the transport | No | future separately scoped implementation | Operational/provider evaluation after architecture acceptance | Deferred |
| Whether provider exposes immutable model revision IDs | No for architecture | future provider adapter | Record best available exact safe identity when authorized; mark unknown explicitly | Deferred |
| Whether response bytes require an output-specific Source Handling category expansion | No for ownership decision; may block response persistence | Source Handling design/implementation | Validate field-category coverage before Model Adapter runtime | Required before implementation if current registry is insufficient |
| Exact allowed non-content-derived attempt/request-unavailable metadata categories | No for ownership decision; may block implementation | Source Handling design/implementation | Validate field-category registry and durable dispositions before runtime | Required before implementation |
| Concrete serializable snapshot / handoff persistence mechanism | No for ADR ownership decision; **yes before runtime activation** | future Model Adapter design | Prove atomic Source Handling snapshot-to-handoff and single-use dispatch semantics | Required before implementation |
| Provider-specific idempotency/correlation/reconciliation mechanics | No for architecture; **yes for each provider before activation** | provider transport implementation | Classify supported/unavailable semantics and prove retry behavior | Required per provider |
| Generic deterministic accepted-ADR coverage/status-consistency checker | No for Model Adapter architecture; required before future architecture contribution claims rely on the same defect classes | Engineering/Governance hardening | Add reusable repository check/preflight derived from conformance cases 13-14 | Follow-up hardening required |
| Automatic multi-provider routing | No; explicitly out of V1 | future architecture | New evidence/ADPR if operational need appears | Deferred |
| Shared project-neutral Model Adapter | No; explicitly not admitted | future ADR 0032 admission process | Two independent consumer contracts | Deferred |

No open question blocks the ownership/boundary decision proposed here. Items explicitly marked required before implementation/provider activation are hard runtime gates, not permissive deferrals.

## Constitution Review

- **Rule 2 — Evidence Authority:** satisfied by exact build/request/attempt/handoff/outcome/response lineage when authorized and explicit unavailable/unknown states otherwise. Adapter success carries no analytical authority.
- **Rule 3 — Deterministic Intelligence:** satisfied by keeping deterministic identities, canonicalization, transformation, attempt/handoff/outcome records, and replay under Hunter control while refusing to represent remote model output or uncertain delivery as deterministic fact.
- **Rule 4 — Architectural Integrity:** strengthened by separating prompt compilation, Model Adapter artifact ownership, provider transport, routing, response validation, and canonical promotion.
- **Rule 5 — Single Source of Truth:** Source Handling remains the sole handling authority; Model Adapter owns execution artifact semantics only; provider transport owns protocol mechanics only; Response Validator and domain promotion remain separate owners.
- **Rule 6 — Explainability:** attempt provenance can explain the governed build/profile, attempt-time authority snapshot, handoff, delivery certainty, and response/outcome, subject to durable dispositions.
- **Rule 7 — Long-Term Evolution:** provider-specific transports are replaceable without rewriting canonical pre-model history.
- **Rule 8 — Governance:** architecture preparation precedes ADR and implementation.

No constitutional conflict is identified.

## Governance Review

- `ARCHITECTURE_DECISION_PREPARATION_GUIDE.md` applies because this change creates a new external-service subsystem boundary and defines persistence/replay/security semantics.
- ADR 0031 is reaffirmed and extended at its explicitly deferred Model Adapter boundary; no upstream intent/context/prompt ownership is changed.
- ADR 0032 remains controlling for project-neutral admission. This record deliberately keeps the concrete Model Adapter Hunter-owned.
- ADR 0033 remains sole Source Handling authority. `ModelHandoffRecord` is execution evidence bound to an exact Source Handling snapshot; it does not publish handling facts/policy or become a second authority.
- ADR 0020 strict-known semantics are applied to historical execution audit without using historical cutoffs as live authorization.
- ADR 0009 provider/service/repository separation is preserved, and provider transport is explicitly prohibited from owning canonical durable execution artifacts.
- ADRs 0025, 0026, and 0028 were reviewed and explicitly determined non-governing for this Model Adapter/provider-attempt boundary; their valuation/evidence-assembly owners and semantics remain unchanged.
- The deterministic Governance Review / Merge Readiness path is isolated and remains zero-LLM.
- Human merge approval remains required for any future implementation contribution.

No unresolved governance conflict is identified.

## Quality Assessment

| Dimension | Rating | Evidence and rationale | Blocking limitation |
|---|---|---|---|
| Problem correctness | EXCELLENT | Gap is explicit in Accepted ADR 0031 and observable between current pre-model and provider runtime | None |
| Scope completeness | EXCELLENT | Ownership, handoff, atomic authorization, durable attempt/outcome, uncertain delivery, persistence, replay, security, routing, validation, migration, and non-goals are explicit | Exact runtime schemas deferred intentionally |
| Canonical consistency | EXCELLENT | Material accepted ADRs are cited; ADRs 0025/0026/0028 have explicit out-of-scope determinations; lifecycle/runtime index contradiction corrected | Independent audit still required |
| Evidence integrity | GOOD | Accepted ADRs and current source are primary evidence; limitations and status correction disclosed | No live provider evidence, not required for boundary decision |
| Assumption discipline | GOOD | Five assumptions isolated with falsification conditions | A-005 must be proven before runtime activation |
| Option completeness | GOOD | Legacy extension, Hunter-neutral adapter, shared core, gateway, and direct provider options covered | None material identified |
| Comparative fairness | GOOD | Same correctness/authority/replay/operational/migration criteria applied | Cost numbers unavailable and not fabricated |
| Falsifiability | EXCELLENT | Revocation, forbidden durability, ownership inversion, atomic handoff, uncertain delivery, idempotency, and persistence-failure boundary cases tested conceptually | Independent audit still required |
| Authority and ownership clarity | EXCELLENT | Source Handling, Model Adapter, transport, repository, routing, validator, and promotion boundaries separated | None |
| Persistence and replay quality | EXCELLENT | Durable pre-send attempt, immutable handoff/outcome, strict-known cutoff separation, uncertain-delivery recovery, no blind retry | Exact storage mechanism must be proven before activation |
| Evidence and provenance quality | EXCELLENT | Exact lineage where authorized plus explicit unavailable/unknown semantics; no prohibited digest assumed safe | Provider reconciliation visibility may be limited |
| Operational quality | EXCELLENT | Outage/timeout/rate/quota/billing/refusal/security/capability, idempotency, crash, uncertain delivery, and persistence failure addressed | Provider-specific mechanics remain later design |
| Implementation and migration impact | GOOD | New V1 families coexist with legacy provider records; no historical rewrite; transport cannot own canonical artifacts | Detailed SQL/API layout deferred |
| Testability and validation | EXCELLENT | Fourteen permanent conformance cases define reusable regression guards, including review defect classes | Architecture-only PR does not itself add runtime tests |
| Maintainability and extensibility | EXCELLENT | Provider transports replaceable; routing/shared core deferred until evidence; artifact ownership centralized | None |
| Risk quality | EXCELLENT | Material security/authority/replay/retry/persistence/status risks include mitigation and residual uncertainty | None blocking for ADR readiness |
| Traceability | GOOD | Issue #287, ADPR-0009, PR #288, planned ADR 0034 identified; later merge/release remain absent | Independent architecture audit not yet complete |

All mandatory quality dimensions are at least `ACCEPTABLE`; Constitution/canonical consistency and Governance are at least `GOOD`. No self-identified blocking architecture question remains. Runtime activation remains blocked on the explicitly identified implementation/provider proofs.

## Architecture Readiness

- Outcome: `READY`
- Rationale: the architectural problem is validated by accepted ADRs and current runtime shape; material ownership, security, atomic handoff, persistence, uncertain-delivery/retry, replay, migration, routing, and validation boundaries are resolved at architecture level; implementation mechanisms can now be designed mechanically against fixed semantics.
- Missing evidence: no live provider operational evidence; not required for choosing ownership/handoff architecture. Each provider must later prove idempotency/reconciliation and transport observability before activation.
- Unresolved conflicts: none identified.

## ADR Readiness

- Outcome: `READY_FOR_ADR`
- Proposed ADR title: **Evidence Intelligence Model Adapter and Provider Attempt Boundary**
- Proposed ADR scope: bind the consumer-owned Model Adapter ownership, transport-only wire mechanics, disposition-conditional provider-request/response evidence, durable pre-send attempt, atomic Source Handling handoff snapshot, immutable attempt outcome, uncertain-delivery/idempotency/reconciliation rules, credential exclusion, historical audit versus re-invocation, routing deferral, Response Validator separation, and legacy migration rules.
- Decisions the ADR must fix:
  1. Model Adapter is Hunter Evidence Intelligence owned.
  2. Contract is provider-neutral with subordinate provider transports; transport cannot own canonical durable artifacts or Source Handling decisions.
  3. Provider-specific transformed request is distinct from `EvidencePromptArtifact`; durable exact bytes/hash/content-derived request identity exist only when the relevant Source Handling categories permit them, otherwise request evidence is explicitly unavailable.
  4. Every possible invocation has a durable immutable pre-send attempt before network dispatch.
  5. Every live attempt creates/consumes a single-use handoff bound to one atomic strict-known Source Handling snapshot at the attempt cutoff; build cutoff is lineage/reconstruction only.
  6. Terminal attempt state is append-only and separate from the immutable pre-send attempt.
  7. Uncertain provider delivery is a first-class outcome; blind retry is prohibited until idempotency/reconciliation proves retry safety.
  8. Response-captured persistence failure cannot trigger retry; nonterminal durable attempts recover as unknown pending reconciliation.
  9. Request/response durability is independently governed per durable category; processing `ALLOW` does not grant persistence.
  10. Credentials are structurally excluded.
  11. Transport success grants no validation or canonical authority.
  12. Response Validator remains separate.
  13. Multi-provider routing is deferred.
  14. Historical reconstruction never means provider re-invocation.
  15. Legacy provider records retain original identity and are not synthetic-backfilled.
- Matters the ADR must leave open:
  - concrete provider/model choice;
  - exact runtime schemas/SQL/module paths;
  - concrete serializable transaction/outbox implementation satisfying the handoff invariant;
  - retry counts/timeouts/backoff for outcomes already proven retry-safe;
  - provider-specific idempotency/correlation/reconciliation API mechanics;
  - provider SDK/library choice;
  - exact response validation rules;
  - exact Source Handling field-category mapping for request/response derived metadata;
  - future routing architecture;
  - future ADR 0032 shared-core admission.

## Final Recommendation

Adopt **Option 2: an Evidence Intelligence-owned, provider-neutral Model Adapter contract with provider-specific transport implementations, one explicit versioned execution profile per attempt, durable pre-send attempt + atomic Source Handling handoff, and no automatic routing in V1**.

This option is the smallest boundary that actually solves the accepted architecture gap without pushing canonical artifact ownership into provider-specific code. It preserves ADR 0031 prompt/build identity, keeps ADR 0033 handling authority singular, makes revocation and handoff atomic at a defined strict-known cutoff, prevents processing permission from laundering prohibited durable hashes/identities, makes uncertain provider delivery and retry safety explicit, obeys ADR 0032's anti-premature-generalization rule, allows provider substitution without rewriting history, and keeps Response Validator and canonical promotion outside transport authority.

After independent architecture audit returns an ADR-ready verdict, create proposed ADR 0034 as a separate governed lifecycle contribution. Runtime provider integration remains blocked until ADR 0034 is separately accepted and a dedicated implementation issue freezes mandatory conformance cases 1-12 as deterministic contract tests before concrete provider code. The recurring architecture-maintenance defect classes in cases 13-14 require a reusable deterministic repository check before future architecture contributions claim canonical consistency.

## Decision History

| Date | State | Change | Author or reviewer |
|---|---|---|---|
| 2026-08-20 | READY_FOR_REVIEW | Initial ADPR-0009 created from Issue #287 after PR #286 merge; recommendation is Hunter-owned provider-neutral adapter with routing deferred | ChatGPT / repository owner-directed |
| 2026-08-20 | READY_FOR_REVIEW | P1 review remediation: separated live attempt cutoff from historical build cutoff; made request hashes/content-derived identity durability conditional; added permanent regression/conformance obligations | ChatGPT / repository owner-directed |
| 2026-08-20 | READY_FOR_REVIEW | CodeRabbit major-review remediation: reviewed accepted ADR 0025/0026/0028 applicability, reconciled index lifecycle/runtime status, centralized durable artifact ownership in Model Adapter, defined atomic handoff, durable pre-send attempts, uncertain-delivery/idempotency/reconciliation semantics, and expanded permanent conformance guards | ChatGPT / repository owner-directed |

## Traceability

- Epic: not yet created
- Issue: #287
- Preparation working document: this ADPR serves as the permanent preparation record; no separate working file created
- Checklist review: author self-assessment completed against preparation guide and quality standard; initial Codex P1 findings and subsequent CodeRabbit major findings on PR #288 remediated; the independent architecture audit then required was subsequently completed and returned final verdict `READY_FOR_ADR` with no blocking finding (PR #290, merge commit `09603c7b0ee3076f902190a0c1c1d223f9d71d8b`)
- ADPR: `docs/architecture-records/ADPR-0009-evidence-intelligence-model-adapter.md`
- ADR: [ADR 0034](../ADR/0034-evidence-intelligence-model-adapter-provider-attempt-boundary.md) drafted under Issue #299; `Proposed`, not yet accepted
- Implementation plan: not yet created; blocked on ADR acceptance
- PR: #288
- Merge commit: `cd1ef1981975f15dd26d48031b00c8b55c28f3d5`
- Release: not yet assigned

## Immutability and Supersession

After `APPROVED`, this record becomes historical evidence. Substantive reasoning changes require a new ADPR that explicitly supersedes this record. Non-substantive traceability completion and typographical corrections must remain auditable in repository history.