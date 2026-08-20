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

Five materially distinct options were evaluated. The recommended architecture is a **consumer-owned, provider-neutral Evidence Intelligence Model Adapter contract with provider-specific transport implementations, while deferring multi-provider routing**. The first implementation should admit at most one explicitly configured, versioned execution profile at a time. A provider/model choice that changes the capability constraint requires a new pre-model build; a provider-specific transformation of canonical prompt content creates a distinct, exact `ProviderRequestArtifact`; every invocation/retry creates an immutable `ModelAttemptRecord`; and any exact response bytes are a separate `ProviderResponseArtifact` governed by Source Handling Authority. Credentials and authentication material are structurally excluded from all canonical artifacts.

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

Hunter has one explicit, auditable boundary between the completed pre-model build and any external model execution. The boundary must preserve exact identities and bytes, independently enforce Source Handling Authority, structurally exclude credentials, distinguish provider-specific request transformation from the canonical prompt artifact, record every attempt/retry/failure immutably, make unavailable states explicit, and prevent model execution success from acquiring validation or canonical promotion authority.

### Decision required

Decide:

1. who owns the Model Adapter and provider-attempt semantics;
2. whether the adapter contract is provider-neutral or provider-specific;
3. how `EvidencePromptArtifact` becomes an exact provider request without rewriting upstream history;
4. what identities and immutable artifacts represent execution profiles, provider requests, attempts, failures, and responses;
5. how Source Handling Authority applies at the handoff and to durable request/response surfaces;
6. whether provider/model routing belongs in this boundary or remains separately deferred; and
7. where the Model Adapter ends and a future Response Validator begins.

### In scope

- Evidence Intelligence Model Adapter ownership and package boundary;
- provider-neutral adapter contract versus provider-specific transports;
- exact prompt-to-provider-request handoff;
- provider execution profile identity/version;
- provider request artifact identity and byte semantics;
- model attempt/retry/failure identity and lineage;
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

ADR 0033 makes the handoff safety problem architectural rather than merely operational. Future model-facing components are consumers only; unresolved handling authority is `BLOCKED`; every handling decision derives from exact historical Source Handling facts and policy; and persistence must independently rederive rather than trust caller-supplied decisions.

This is therefore a real unresolved architecture decision involving component ownership, external-provider boundary, persistence, replay, security, migration, and future extensibility. It is not an ordinary implementation choice.

## Motivation

Without this architecture, Hunter has three unsafe failure modes:

1. **Opaque execution** — a response may exist without proof of the exact canonical prompt and exact provider request that produced it.
2. **Authority laundering** — provider choice, transport transformation, caller flags, or adapter success may accidentally acquire authority over source handling, prompt content, response validity, or canonical promotion.
3. **Historical false replay** — a later provider re-call may be mistaken for reconstruction of the original attempt, or current provider configuration may be substituted for historically unavailable request/response state.

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

`EvidencePromptArtifact` owns exact canonical pre-model content, hashes, compiler/canonicalization identities, and measured size. Provider-specific wrapping, authentication, transport headers, and provider parameters are explicitly outside that artifact. If a future adapter transforms model-facing message content, ADR 0031 requires the transformed exact payload to be recorded as a distinct provider-request artifact rather than treated as the original prompt artifact.

### Source Handling boundary

ADR 0033 assigns exclusive authority for source-handling facts and policy to Evidence Intelligence Source Handling Authority. Callers, providers, prompt construction, repositories, generic cores, and future Model Adapter components are consumers only. Unknown/missing/conflicting/ambiguous authority blocks model-facing processing. Persistence independently resolves authority and rederives all handling decisions.

The Source Handling Design Contract gives concrete mechanics: processing, retention, reconstruction, access, and deletion/lifecycle decisions are separate derived outcomes; durable fields are governed by a historical field-category registry and policy dispositions; authorization is exact-payload bound; credentials/secrets are structurally constrained; current state is never historical fallback.

### Project-neutral boundary

ADR 0032 establishes only a project-neutral portability boundary and admission rule. It explicitly withholds provider/model selection, routing, credentials, invocation, and response validation from the project-neutral core. No concrete current Hunter contract is admitted by default.

### Existing provider execution path

`provider.py` supplies useful operational precedent:

- provider metadata and health;
- explicit proposed/unavailable/rejected outcomes;
- provider exception normalization;
- prompt-injection detection over spans;
- forbidden response-capability checks;
- immutable-ish provider artifact/proposal persistence through the Evidence Intelligence repository.

However, it predates the current pre-model architecture and operates from `ExtractionRequest` rather than `EvidencePromptArtifact`. Its provider artifact identity is based on document/provider/schema/payload status and does not prove the exact provider request bytes. Its direct proposal creation also crosses the future Model Adapter -> Response Validator separation now required by ADR 0031. It should be treated as migration evidence, not as binding Model Adapter architecture.

### Repository and persistence separation

Accepted ADR 0009 (`Repository Purification`) separates provider integration, service authority, repository mechanics, and persistence. The Model Adapter may own execution semantics but must not make the repository an authority or let persistence code choose providers, alter prompts, validate meaning, or grant permissions.

## Constraints

### Constitutional

- **Evidence Authority:** exact request/response lineage and unavailable states must remain evidence-backed; unknown remains unknown.
- **Deterministic Intelligence:** the deterministic pre-model portion and all identities/serialization under Hunter control must remain reproducible. Remote model output itself is not made deterministic by assertion.
- **Architectural Integrity:** prompt compilation, transport, routing, validation, and canonical promotion remain distinct responsibilities.
- **Single Source of Truth:** Model Adapter execution semantics, Source Handling authority, response validation, and canonical domain authority each require one owner and may not compete.
- **Explainability:** every persisted AI-shaped outcome must be traceable to exact governed inputs and execution evidence.
- **Long-Term Evolution:** provider substitution must not require rewriting historical prompt/build identity.

### Governance and accepted ADRs

- ADR 0031 governs the concrete Hunter pre-model chain and explicitly requires separate Model Adapter architecture.
- ADR 0032 prevents premature shared-core ownership and explicitly excludes provider/model selection/routing/invocation/credentials/response validation from core authority.
- ADR 0033 makes Model Adapter a consumer of Source Handling Authority only.
- ADR 0020 strict-known semantics prohibit current/latest fallback during historical reconstruction.
- ADR 0009 requires provider/service/repository separation.
- No AI/provider capability may enter Hunter Governance Review or Merge Readiness.

### Technical

- `EvidencePromptArtifact` is immutable and historically meaningful; the adapter may not mutate it in place.
- Provider-specific transformed content must have its own identity.
- Capability constraints used to make a prompt fit are upstream build inputs. Selecting a provider/model whose effective capability differs cannot silently reuse an incompatible build.
- External model execution is non-deterministic and network-dependent; historical audit must not pretend that re-invocation reproduces the original response.

### Operational

- Provider outages, timeouts, rate limits, quota exhaustion, billing unavailability, safety refusal, malformed/partial responses, and unsupported capabilities are normal explicit outcomes, not exceptional gaps to hide.
- Retry must be bounded and observable; a retry is a new attempt, not mutation of an old attempt.
- A first implementation should be operable with one explicit execution profile without forcing premature routing architecture.

### Persistence and migration

- Attempt, request, response, and failure history is append-only/immutable once recorded.
- Corrections are new records or superseding metadata, not mutation of historical bytes.
- Existing ADR 0031 build/artifact identities remain unchanged.
- Existing provider-runner records remain historical records of their original schema; migration must not relabel them as if they had Model Adapter request lineage they never recorded.

### Replay and historical reconstruction

- Exact audit reconstruction may use only persisted artifacts and authority known at the original cutoff.
- Re-invoking a provider is a new execution, not historical replay.
- If exact request or response bytes were not authorized for retention, reconstruction is explicitly unavailable rather than regenerated from current state.
- A digest may be retained only when the exact historical Source Handling disposition permits that durable category; hashes are not automatically safe.

### Compatibility

- ADR 0031 prompt/build identities remain historically valid.
- ADR 0033 handling identities and cutoffs remain authoritative and may not be replaced by current policy.
- `AIExtractionProvider` may be adapted or deprecated later, but existing historical artifacts are not rewritten.
- Response Validator and downstream proposal validation remain separate.

### Security and privacy

- Credentials, bearer tokens, API keys, cookies, authentication headers, client secrets, and equivalent transport secrets are never canonical request artifacts.
- Provider request artifacts contain only governed non-secret request data.
- Source Handling Authority must allow model processing before any network handoff.
- Durable request/response content must pass exact historical field-category/disposition enforcement independently at persistence.
- Provider responses are untrusted external data and may not request tools, repository writes, schema changes, or other capabilities outside the authorized adapter contract.

### Performance and scalability

- Provider transport latency is expected and must not leak into deterministic pre-model identity.
- Request/response byte retention may be expensive and must follow handling policy rather than convenience.
- Multi-provider fan-out, racing, or score-based routing is deliberately not required for the first boundary.

### Evidence and provenance

A model attempt must preserve enough evidence to answer, when authorized:

- which `EvidencePreModelBuildRecord` and `EvidencePromptArtifact` were used;
- which exact provider-request artifact was transmitted;
- which versioned execution profile/provider/model/protocol was used;
- which attempt/retry produced the result;
- which response artifact or explicit unavailable outcome resulted;
- which Source Handling authority state and cutoff permitted the handoff and durable surfaces; and
- whether exact reconstruction is available or unavailable.

## Evidence Inventory

| ID | Evidence | Authority/source | Finding | Quality and limitations | Supports or challenges |
|---|---|---|---|---|---|
| E-001 | Model Adapter explicitly deferred | Accepted ADR 0031 | Current architecture ends at pre-model build; later Model Adapter required | Highest architectural authority for this scope; direct | Requires new architecture |
| E-002 | Provider-specific request wrapping must be distinct | Accepted ADR 0031 | Transformed provider-facing payload must be a distinct provider-request artifact | Direct architectural requirement | Supports separate request artifact |
| E-003 | Project-neutral core admission/exclusions | Accepted ADR 0032 | Routing, credentials, invocation, provider selection and response validation are not shared-core authority; concrete shared contracts require two-consumer evidence | Direct; no current second-consumer Model Adapter evidence | Challenges shared-core option |
| E-004 | Source Handling canonical ownership | Accepted ADR 0033 | Model Adapter is consumer only; unresolved authority blocks model-facing processing | Direct | Requires independent adapter enforcement |
| E-005 | Durable category and authorization mechanics | Source Handling Design Contract | Historical field/category dispositions and exact payload-bound authorization govern durable surfaces | Runtime contract subordinate to accepted ADRs; status text says ready for review but mechanics are implemented/integrated in current runtime | Supports request/response persistence controls |
| E-006 | Concrete prompt/build runtime | `src/hunter/evidence_intelligence/pre_model.py` | `EvidencePromptArtifact`, `EvidencePreModelBuildRecord`, capability constraints, and Source Handling gated model processing exist | Direct current source observation | Defines exact upstream handoff |
| E-007 | Existing provider runner | `src/hunter/evidence_intelligence/provider.py` | Older runner has provider health/error/security/proposal mechanics but consumes spans/schema rather than PromptArtifact | Direct source; predates ADR 0031/0033 handoff | Useful migration precedent; insufficient as final architecture |
| E-008 | Pre-PR hygiene / governance cleanup complete | merged PRs #285 and #286 | Governance migration scaffolding retired and deterministic exact-head preflight hardened before Prompt Machine resumes | Current repository history | Removes process blocker, not architecture evidence |
| E-009 | ADPR numbering | `docs/architecture-index.md` and architecture-record README | ADPR-0001 through ADPR-0008 allocated; numbers are monotonic and never reused | Direct current index | Confirms ADPR-0009 |
| E-010 | ADR numbering | `docs/ADR/README.md` | ADR 0033 is latest allocated accepted ADR; no ADR 0034 exists | Direct current index/search | Supports planned ADR 0034 |

### Evidence limitations

- No live provider benchmark, quota experiment, cost comparison, or production credential evidence is used here. Those are operational implementation inputs, not prerequisites for deciding the ownership boundary.
- The existing provider runner proves code shape and past semantics, not the correctness of a future Model Adapter.
- The architecture index contains stale implementation-status prose for some earlier ADR 0031 work; this record relies on current source plus accepted ADRs for the actual runtime boundary and does not treat index prose as implementation authority.
- No second independent consumer has demonstrated a Model Adapter contract under ADR 0032. That absence is material to shared-core admission.

## Assumptions

| ID | Assumption | Rationale | Confidence | Falsification condition | Consequence if false |
|---|---|---|---|---|---|
| A-001 | The first authorized Model Adapter implementation can target one explicit execution profile at a time | Avoids routing architecture before evidence exists | High | A real required workflow cannot operate without runtime choice among multiple providers/models | Multi-provider routing must be separately prepared before implementation |
| A-002 | Provider APIs can represent the canonical prompt either directly or through a deterministic transform whose exact non-secret request content can be hashed/identified | Common transport property and ADR 0031 already anticipates transformations | High | A provider requires opaque mutable server-side prompt transformation that cannot be represented/audited | That provider is not admissible under this boundary without new architecture |
| A-003 | Source Handling decisions applicable to the prompt source material are sufficient to constrain request/response durability when combined with the field-category registry | Follows ADR 0033 design | Medium | Provider responses introduce a new durable data category not expressible by the governed registry | Source Handling design must be extended before response persistence |
| A-004 | Remote model output cannot be reproduced deterministically merely by replaying the same request | External model execution is outside Hunter deterministic control | High | Provider supplies a verifiable deterministic execution contract Hunter can prove | Architecture may later add a stronger reproducibility mode; current audit/replay distinction remains safe |

## Architectural Dimensions

1. **Authority and ownership** — who owns Model Adapter execution semantics and who is prohibited from acquiring upstream/downstream authority.
2. **Consumer versus shared core** — whether provider mechanics remain Hunter-owned under ADR 0032 admission rules.
3. **Prompt immutability** — preserving canonical prompt bytes while permitting provider-specific transport representations.
4. **Execution profile identity** — exact provider/model/protocol/capability binding without giving the provider selection authority over upstream build semantics.
5. **Provider request identity** — exact non-secret bytes/structure transmitted after deterministic transformation.
6. **Attempt identity and retries** — append-only execution lineage and explicit outcomes.
7. **Response identity** — exact/raw or unavailable response evidence distinct from validated proposal semantics.
8. **Source Handling enforcement** — processing and durable-category decisions independently re-resolved at the adapter/persistence boundaries.
9. **Credential exclusion** — transport secrets excluded structurally from canonical bytes, logs, diagnostics, and hashes.
10. **Failure/missingness** — timeout, quota, billing, refusal, malformed data, provider outage, unsupported capability, and persistence restrictions are explicit.
11. **Persistence/replay** — exact historical audit versus new re-invocation.
12. **Provenance/observability** — proof chain from build to request to attempt to response.
13. **Routing** — whether selecting among providers/models is part of the adapter or a later authority.
14. **Validation boundary** — transport success versus Response Validator semantics.
15. **Migration** — coexistence with legacy `AIExtractionProvider` / `SecureAIProviderRunner` records.
16. **Testability** — deterministic contract and fake-transport tests without live provider dependencies.
17. **Reversibility/no lock-in** — ability to add/replace provider transports without rewriting prompt/build history.
18. **Governance isolation** — no provider/LLM dependency in merge authority.

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
- **Failure modes:** Direct proposal creation can bypass future validation separation; provider request bytes may remain implicit.
- **Migration implications:** Complex version branching inside a legacy abstraction.
- **Reversibility:** Medium.
- **Open dependencies:** Must redesign multiple existing record semantics simultaneously.

### Option 2 — Consumer-owned provider-neutral Model Adapter with provider-specific transports; routing deferred

- **Description:** Define a new Evidence Intelligence `ModelAdapter` contract that consumes an exact completed pre-model build and one explicit versioned `ModelExecutionProfile`. Provider-specific transports deterministically map the canonical prompt to a provider request. The adapter records request, attempt, outcome, and response evidence. It does not choose among profiles dynamically.
- **Authority and ownership:** Evidence Intelligence owns the adapter/attempt contract. Source Handling remains sole handling authority. Provider transports own protocol mechanics only. Repositories persist mechanically. Response Validator remains separate.
- **Boundaries:** Clean handoff: `PreModelBuild -> ModelAdapter -> ProviderRequestArtifact -> ModelAttemptRecord -> ProviderResponseArtifact/unavailable -> future ResponseValidator`.
- **Persistence and replay:** Append-only immutable attempt lineage; exact request/response reconstruction only when authorized; re-invocation always a new attempt.
- **Evidence and provenance:** Strong exact linkage from prompt/build to request/profile/attempt/response.
- **Compatibility:** Existing provider runner can remain historical and later be adapted behind a transport or deprecated without rewriting old records.
- **Advantages:** Preserves ADR 0031 identities, minimizes provider lock-in, keeps routing out of scope, provides deterministic test seam, preserves validation separation.
- **Disadvantages:** Introduces new records and a migration boundary rather than reusing old provider artifacts directly.
- **Failure modes:** An execution profile may be mismatched to the build's capability constraint; architecture must fail closed rather than adapt silently.
- **Migration implications:** New V1 record families coexist with legacy provider artifacts; no backfill of nonexistent request lineage.
- **Reversibility:** High; transports can be replaced while canonical pre-model and attempt history remain stable.
- **Open dependencies:** Detailed schema/transport implementation belongs to later design/implementation after ADR acceptance.

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
- **Persistence and replay:** Can satisfy exact lineage for one provider.
- **Evidence and provenance:** Potentially strong for the selected provider.
- **Compatibility:** Lowest initial abstraction cost.
- **Advantages:** Small initial implementation and minimal generic surface.
- **Disadvantages:** Provider-specific request/response semantics leak into service contracts; later provider substitution requires refactoring canonical execution code.
- **Failure modes:** Provider model names/parameters become accidental canonical authority or leak upstream.
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
| Credential exclusion boundary | Medium | **High** | Medium | High but operationally complex | Medium |
| Provider lock-in | Medium-high | **Low** | Low | Low | **High** |
| Operational complexity | Low-medium | **Medium-low** | Medium | **High** | Low |
| Migration risk | High semantic risk | **Low-medium** | High | High | Medium-high |
| Implementation effort | Low-medium | Medium | High | Very high | Low |
| Reversibility | Medium | **High** | Medium | Low-medium | Medium |
| Long-term extensibility | Medium | **High** | High only after admission | High | Low-medium |
| Deterministic testability without live provider | Medium | **High** | High | Medium | Medium-high |

Option 2 gives the best current balance: it creates only the consumer-owned seam Hunter demonstrably needs, preserves future provider substitution, satisfies ADR 0032 by not claiming shared ownership, and avoids routing/service architecture before evidence exists.

## Falsification Results

### Option 1 falsification

**Hypothesis:** the existing provider runner can simply be declared the Model Adapter.

**Counterevidence:** it consumes `ExtractionRequest` built from spans/schema rather than the canonical `EvidencePromptArtifact`, and directly creates `ExtractionProposal`. It therefore lacks the exact ADR 0031 handoff and crosses the deferred Response Validator separation. The hypothesis fails unless the runner is materially redesigned, at which point preserving it as the architectural abstraction gives little benefit and creates legacy semantic ambiguity.

**Result:** does not survive as the preferred architecture. Some health/error/detector mechanics may be reused later as implementation details.

### Option 2 falsification

**Hypothesis:** a new provider-neutral Hunter-owned adapter seam is unnecessary abstraction because only one provider may be used initially.

**Test:** remove all multi-provider routing from the option and require only one explicit execution profile. The neutral seam still has independent value because it separates canonical prompt identity from provider-specific request transformation, attempt identity, transport secrets, response evidence, and future validation. Those boundaries exist even with one provider.

**Boundary case:** provider requires transformation of message structure. The option survives because transformed exact non-secret bytes receive a distinct `ProviderRequestArtifact` identity and do not mutate the prompt artifact.

**Boundary case:** provider/model capability differs from the build capability. The option survives only by failing closed and requiring a new build; silent adaptation is prohibited.

**Boundary case:** exact response retention is forbidden. The option survives by recording an explicit unavailable reconstruction state and only the durable categories Source Handling permits.

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

**Counterexample:** even one provider needs a stable distinction among canonical prompt, provider-transformed request, secrets, attempt, raw response, and validation. If those concepts are implemented only in provider-specific types, the first provider's protocol becomes the accidental architecture. A minimal neutral execution seam avoids that without introducing routing.

**Result:** viable as an implementation shortcut but inferior as architecture. Reconsider only if Hunter permanently commits to one provider and accepts the migration cost explicitly in a later decision.

## Recommended Architecture

### Ownership

Create an Evidence Intelligence-owned Model Adapter boundary. The canonical adapter contract belongs to Hunter Evidence Intelligence, not to the project-neutral core and not to the provider transport.

Provider-specific transports are subordinate implementations. They may translate request/response wire formats, perform network calls, and normalize transport errors. They may not:

- select or modify context;
- mutate `EvidencePromptArtifact`;
- create Source Handling authority;
- choose a different capability constraint silently;
- validate response truth;
- create canonical knowledge;
- approve tools or autonomous actions;
- alter Hunter governance state.

### Execution profile

Introduce a versioned `ModelExecutionProfile` concept owned by the Model Adapter boundary. It identifies only execution-relevant facts, such as:

- profile identity/version;
- provider transport identity/version;
- exact provider and model identifier/version/endpoint-class identity that is safe to persist;
- request protocol/version;
- required capability-constraint identity or exact compatibility requirement;
- deterministic non-secret request parameters that affect model execution;
- prohibited capabilities/tools;
- response format expectation identity.

V1 does **not** dynamically select among profiles. The authorized caller/workflow supplies or is bound to one explicitly configured profile, and the adapter verifies it. Multi-profile/provider routing is a separate future architecture decision.

If the execution profile's capability contract does not exactly satisfy the capability constraint used by the `EvidencePreModelBuildRecord`, invocation fails closed. A different capability requires a new allocation/prompt/build identity upstream.

### Provider request artifact

Before network transmission, the provider transport deterministically creates a `ProviderRequestArtifact` linked to:

- `EvidencePreModelBuildRecord` identity;
- `EvidencePromptArtifact` identity;
- `ModelExecutionProfile` identity/version;
- transport/request protocol identity/version;
- exact non-secret canonical provider-facing message/body bytes when retention is authorized;
- exact hashes, encoding/canonicalization, measured size, and deterministic request artifact identity;
- explicit reconstruction availability/unavailability.

The provider request artifact is always a distinct identity from `EvidencePromptArtifact`, even when its content mapping is byte-equivalent. This prevents provider wrapping from retroactively becoming canonical prompt content.

Authentication headers, bearer tokens, API keys, cookies, client secrets, signing secrets, and equivalent credentials are structurally outside this artifact and outside every canonical hash. Their existence may be represented only by a non-secret credential-slot/configuration identity if separately authorized; secret values never appear.

### Source Handling execution gate

Immediately before preparing/transmitting provider-facing bytes, the Model Adapter independently resolves exact Source Handling authority for the build's governed document/version scope and cutoff. It verifies that:

- the same exact historical handling authority required by the build is still resolvable as historical truth;
- `processing_decision == ALLOW`;
- provider-request durable field/category dispositions permit whatever request representation will be persisted;
- no caller/provider decision substitutes for authority.

A Model Adapter may not infer permission from the existence of an already-built prompt artifact. Historical bytes are evidence, not processing authority.

### Model attempt

Every network invocation creates a new immutable `ModelAttemptRecord` identity. At minimum it links:

- execution owner;
- pre-model build ID;
- prompt artifact ID;
- provider request artifact ID;
- execution profile ID;
- attempt ordinal;
- predecessor/retry attempt ID where applicable;
- started/finished/recorded coordinates;
- exact terminal outcome/reason code;
- provider correlation/request ID only when safe and governed;
- response artifact ID when one exists;
- explicit exact-reconstruction availability.

Retries never overwrite an attempt. A timeout followed by success is two attempts with lineage, not one mutated record.

Closed high-level outcome families should distinguish at least:

- `SUCCEEDED_TRANSPORT` — a response was received; says nothing about semantic validity;
- `PROVIDER_UNAVAILABLE` — provider health/outage;
- `TIMEOUT`;
- `RATE_LIMITED`;
- `QUOTA_UNAVAILABLE`;
- `BILLING_UNAVAILABLE`;
- `CAPABILITY_UNSUPPORTED`;
- `PROVIDER_REFUSED`;
- `MALFORMED_TRANSPORT_RESPONSE`;
- `SECURITY_BLOCKED`;
- `SOURCE_HANDLING_BLOCKED`;
- `INTERNAL_ADAPTER_ERROR`.

Exact vocabulary is design/implementation detail, but these failure distinctions must not be collapsed into a generic exception that destroys auditability.

### Provider response artifact

A received provider response creates a distinct `ProviderResponseArtifact` or explicit unavailable durable state. It records, as authorized:

- attempt ID;
- provider request artifact ID;
- execution profile ID;
- exact response protocol/version;
- exact raw or canonical response bytes when Source Handling allows retention;
- content hash only when the relevant durable category allows hash retention;
- measured size and encoding/canonicalization metadata;
- provider finish/status metadata that is safe and governed;
- reconstruction availability/unavailability reason.

Provider response bytes are untrusted external data. Merely persisting them does not make them an `ExtractionProposal` and does not validate schema, citations, truth, evidence use, prohibited capabilities, or domain eligibility.

### Response Validator boundary

The Model Adapter ends after transport-level response capture and normalization. A separately governed future `ResponseValidator` consumes the response evidence and may validate syntax/schema/capability/evidence-reference rules. Only after that boundary may Evidence Intelligence produce an `ExtractionProposal` through its authorized service path.

The existing legacy provider runner's direct proposal creation is not adopted as the new Model Adapter rule. Historical behavior remains historical; any future migration must preserve old schema identity while routing new executions through the new boundary.

### Routing boundary

Provider/model routing is deferred. V1 architecture supports one explicit execution profile per attempt and no score-based, health-based, cost-based, quota-based, or fallback selection among alternative profiles.

If Hunter later needs automatic selection among two or more provider/model profiles, that creates a new authority/operational decision covering selection inputs, cost/latency/quality criteria, failure fallback, capability compatibility, and deterministic audit. That work requires a separate ADPR/ADR or explicit accepted amendment.

### Replay and re-execution

- Reconstructing a historical attempt means reading its persisted immutable build/request/attempt/response evidence under the exact historical authority state.
- Re-invoking a provider, even with identical request bytes and model identifier, is a **new attempt**, not replay of the old response.
- No current provider version, current configuration, or current Source Handling policy may backfill missing historical execution evidence.
- When exact bytes were not retained, reconstruction remains explicitly unavailable.

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
| Request transformation changes model-facing meaning | Correctness | Medium | High | Distinct exact ProviderRequestArtifact; deterministic transform; never mutate prompt artifact | Provider SDKs may perform hidden transformations; such SDK modes are inadmissible unless observable |
| Credentials leak into artifacts/logs/hashes | Security | Low-medium | Critical | Structural exclusion, secret-free request canonicalization, tests scanning all durable/log surfaces | Third-party SDK diagnostics require explicit hardening |
| Source content is processed after authority becomes unresolved | Security/governance | Low-medium | High | Independent exact historical Source Handling resolution immediately before handoff | Operational race between authority retrieval and network send requires bounded transaction semantics in implementation design |
| Raw model response persists prohibited source echoes | Security/privacy | Medium | High | Govern request/response durable categories via Source Handling; fail closed on unresolved disposition | Output-specific category coverage may need design refinement |
| Adapter transport success is mistaken for valid proposal | Authority | Medium | High | Separate Response Validator; attempt outcome named transport-only; no proposal creation in adapter | Legacy runner semantics may confuse migration unless clearly versioned |
| Multi-provider fallback sneaks in as implementation convenience | Governance/operational | Medium | Medium-high | V1 explicitly forbids routing/fallback; separate architecture required | Pressure may rise during outages/quota limits |
| Legacy provider artifacts are retroactively relabeled | Migration | Low | High | Preserve historical schema/identity; no synthetic backfill | Mapping old records to new audit views may remain partial |
| Remote provider behavior changes despite same model name | Replay | High | Medium-high | Record exact safe provider/model/version/protocol metadata; distinguish audit from re-invocation | Provider may not expose immutable model revision identifiers |
| Provider-specific SDK hides wire request bytes | Evidence | Medium | High | Require deterministic observable request representation; reject opaque modes/providers | Some provider SDKs may need lower-level HTTP transport |
| Project-neutral extraction becomes desirable later | Long-term | Medium | Low | Keep consumer contract narrow and transport interface clean; use ADR 0032 admission later | Future consumer semantics unknown |

## Open Questions

| Question | Blocking? | Owner | Required evidence or action | Status |
|---|---|---|---|---|
| Exact Python module/type names and SQL layout | No | future implementation | Design mechanically under accepted ADR | Deferred |
| Exact closed attempt reason-code vocabulary | No | future design/implementation | Contract tests before runtime | Deferred |
| Which concrete provider should first implement the transport | No | future separately scoped implementation | Operational/provider evaluation after architecture acceptance | Deferred |
| Whether provider exposes immutable model revision IDs | No for architecture | future provider adapter | Record best available exact safe identity; mark unknown explicitly | Deferred |
| Whether response bytes require an output-specific Source Handling category expansion | No for ownership decision; may block response persistence | Source Handling design/implementation | Validate field-category coverage before Model Adapter runtime | Required before implementation if current registry is insufficient |
| Automatic multi-provider routing | No; explicitly out of V1 | future architecture | New evidence/ADPR if operational need appears | Deferred |
| Shared project-neutral Model Adapter | No; explicitly not admitted | future ADR 0032 admission process | Two independent consumer contracts | Deferred |

No open question blocks the ownership/boundary decision proposed here.

## Constitution Review

- **Rule 2 — Evidence Authority:** satisfied by exact build/request/attempt/response lineage and explicit unavailable states. Adapter success carries no analytical authority.
- **Rule 3 — Deterministic Intelligence:** satisfied by keeping deterministic identities, canonicalization, transforms, and replay under Hunter control while explicitly refusing to represent remote model output as deterministic replay.
- **Rule 4 — Architectural Integrity:** strengthened by separating prompt compilation, transport, routing, response validation, and canonical promotion.
- **Rule 5 — Single Source of Truth:** Source Handling remains the sole handling authority; Model Adapter owns execution semantics only; Response Validator and domain promotion remain separate owners.
- **Rule 6 — Explainability:** attempt provenance can explain what exact governed prompt/request/model profile produced a response or failure.
- **Rule 7 — Long-Term Evolution:** provider-specific transports are replaceable without rewriting canonical pre-model history.
- **Rule 8 — Governance:** architecture preparation precedes ADR and implementation.

No constitutional conflict is identified.

## Governance Review

- `ARCHITECTURE_DECISION_PREPARATION_GUIDE.md` applies because this change creates a new external-service subsystem boundary and defines persistence/replay/security semantics.
- ADR 0031 is reaffirmed and extended at its explicitly deferred Model Adapter boundary; no upstream intent/context/prompt ownership is changed.
- ADR 0032 remains controlling for project-neutral admission. This record deliberately keeps the concrete Model Adapter Hunter-owned.
- ADR 0033 remains sole Source Handling authority. The Model Adapter independently consumes/rederives but never publishes handling policy/facts.
- ADR 0020 strict-known semantics are applied to historical execution audit.
- ADR 0009 provider/service/repository separation is preserved.
- The deterministic Governance Review / Merge Readiness path is isolated and remains zero-LLM.
- Human merge approval remains required for any future implementation contribution.

No unresolved governance conflict is identified.

## Quality Assessment

| Dimension | Rating | Evidence and rationale | Blocking limitation |
|---|---|---|---|
| Problem correctness | EXCELLENT | Gap is explicit in Accepted ADR 0031 and observable between current pre-model and provider runtime | None |
| Scope completeness | GOOD | Ownership, handoff, persistence, replay, security, routing, validation, migration, and non-goals are explicit | Exact runtime schemas deferred intentionally |
| Canonical consistency | EXCELLENT | ADRs 0031/0032/0033/0020/0009 and Constitution checked directly | None |
| Evidence integrity | GOOD | Accepted ADRs and current source are primary evidence; limitations/stale index prose disclosed | No live provider evidence, not required for boundary decision |
| Assumption discipline | GOOD | Four assumptions isolated with falsification conditions | A-003 requires implementation-time category validation |
| Option completeness | GOOD | Legacy extension, Hunter-neutral adapter, shared core, gateway, and direct provider options covered | None material identified |
| Comparative fairness | GOOD | Same correctness/authority/replay/complexity/migration criteria applied | Cost numbers unavailable and not fabricated |
| Falsifiability | GOOD | Each viable option challenged with current ADR/source counterevidence and boundary cases | Independent audit still required |
| Authority and ownership clarity | EXCELLENT | Model Adapter, Source Handling, transport, repository, routing, validator, and promotion boundaries separated | None |
| Persistence and replay quality | GOOD | Immutable attempt lineage, distinct artifacts, strict-known audit vs new invocation explicit | Exact schema deferred |
| Evidence and provenance quality | EXCELLENT | Exact lineage chain and unavailable semantics specified | Provider model revision visibility may be limited |
| Operational quality | GOOD | Outage/timeout/rate/quota/billing/refusal/security/capability outcomes and retry semantics addressed | Concrete timeout/retry values deferred |
| Implementation and migration impact | GOOD | New V1 families coexist with legacy provider records; no historical rewrite | Detailed migration API deferred |
| Testability and validation | EXCELLENT | Deterministic fake-transport and artifact/identity/fail-closed tests are derivable without live provider | Operational live canary belongs later |
| Maintainability and extensibility | EXCELLENT | Provider transports replaceable; routing/shared core deferred until evidence | None |
| Risk quality | GOOD | Material security/authority/replay/migration risks include mitigation and residual uncertainty | None blocking |
| Traceability | GOOD | Issue #287, ADPR-0009, planned ADR 0034 identified; later PR/commit/release remain explicitly absent | Independent review/PR links not yet created |

All mandatory quality dimensions are at least `ACCEPTABLE`; Constitution/canonical consistency and Governance are at least `GOOD`. No self-identified blocking question remains.

## Architecture Readiness

- Outcome: `READY`
- Rationale: the architectural problem is validated by accepted ADRs and current runtime shape; material ownership, security, persistence, replay, migration, routing, and validation boundaries are resolved at architecture level; implementation mechanics are separable and explicitly deferred.
- Missing evidence: no live provider operational evidence; not required for choosing the ownership/handoff architecture. Concrete provider selection remains a later implementation input.
- Unresolved conflicts: none identified.

## ADR Readiness

- Outcome: `READY_FOR_ADR`
- Proposed ADR title: **Evidence Intelligence Model Adapter and Provider Attempt Boundary**
- Proposed ADR scope: bind the consumer-owned Model Adapter ownership, exact `EvidencePromptArtifact` handoff, distinct provider-request identity, execution profile/attempt/response lineage, Source Handling execution/persistence invariants, credential exclusion, historical audit versus re-invocation, routing deferral, Response Validator separation, and legacy migration rules.
- Decisions the ADR must fix:
  1. Model Adapter is Hunter Evidence Intelligence owned.
  2. Contract is provider-neutral with subordinate provider transports.
  3. Provider-specific transformed request is a distinct exact artifact.
  4. Every invocation/retry is an immutable attempt with lineage.
  5. Request/response durability is independently governed by exact Source Handling authority.
  6. Credentials are structurally excluded.
  7. Transport success grants no validation or canonical authority.
  8. Response Validator remains separate.
  9. Multi-provider routing is deferred.
  10. Historical reconstruction never means provider re-invocation.
  11. Legacy provider records retain original identity and are not synthetic-backfilled.
- Matters the ADR must leave open:
  - concrete provider/model choice;
  - exact runtime schemas/SQL/module paths;
  - retry counts/timeouts/backoff policy;
  - provider SDK/library choice;
  - exact response validation rules;
  - future routing architecture;
  - future ADR 0032 shared-core admission.

## Final Recommendation

Adopt **Option 2: an Evidence Intelligence-owned, provider-neutral Model Adapter contract with provider-specific transport implementations, one explicit versioned execution profile per attempt, and no automatic routing in V1**.

This option is the smallest boundary that actually solves the accepted architecture gap. It preserves ADR 0031 prompt/build identity, keeps ADR 0033 handling authority singular, obeys ADR 0032's anti-premature-generalization rule, allows provider substitution without rewriting history, and keeps Response Validator and canonical promotion outside transport authority.

After independent architecture audit returns an ADR-ready verdict, create proposed ADR 0034 as a separate governed lifecycle contribution. Runtime provider integration remains blocked until ADR 0034 is separately accepted and a dedicated implementation issue freezes deterministic contract tests before concrete provider code.

## Decision History

| Date | State | Change | Author or reviewer |
|---|---|---|---|
| 2026-08-20 | READY_FOR_REVIEW | Initial ADPR-0009 created from Issue #287 after PR #286 merge; recommendation is Hunter-owned provider-neutral adapter with routing deferred | ChatGPT / repository owner-directed |

## Traceability

- Epic: not yet created
- Issue: #287
- Preparation working document: this ADPR serves as the permanent preparation record; no separate working file created
- Checklist review: author self-assessment completed against preparation guide and quality standard; independent audit not yet performed
- ADPR: `docs/architecture-records/ADPR-0009-evidence-intelligence-model-adapter.md`
- ADR: proposed ADR 0034, not yet created
- Implementation plan: not yet created; blocked on ADR acceptance
- PR: not yet created
- Merge commit: not yet created
- Release: not yet assigned

## Immutability and Supersession

After `APPROVED`, this record becomes historical evidence. Substantive reasoning changes require a new ADPR that explicitly supersedes this record. Non-substantive traceability completion and typographical corrections must remain auditable in repository history.