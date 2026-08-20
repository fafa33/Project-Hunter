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

Five materially distinct options were evaluated. The recommended architecture is a **consumer-owned, provider-neutral Evidence Intelligence Model Adapter contract with provider-specific transport implementations, while deferring multi-provider routing**. The first implementation should admit at most one explicitly configured, versioned execution profile at a time. A provider/model choice that changes the capability constraint requires a new pre-model build; a provider-specific transformation of canonical prompt content creates a distinct `ProviderRequestArtifact` or an explicit request-evidence-unavailable state, with exact bytes, hashes, sizes, or content-derived identity persisted only when the applicable Source Handling durable dispositions authorize those categories; every invocation/retry creates an immutable `ModelAttemptRecord`; and any exact response bytes are a separate `ProviderResponseArtifact` governed by Source Handling Authority. Credentials and authentication material are structurally excluded from all canonical artifacts.

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

Hunter has one explicit, auditable boundary between the completed pre-model build and any external model execution. The boundary must preserve exact identities and bytes when their durable categories are authorized, otherwise preserve explicit unavailability without regenerating prohibited evidence; independently enforce Source Handling Authority; structurally exclude credentials; distinguish provider-specific request transformation from the canonical prompt artifact; record every attempt/retry/failure immutably; make unavailable states explicit; and prevent model execution success from acquiring validation or canonical promotion authority.

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

ADR 0033 makes the handoff safety problem architectural rather than merely operational. Future model-facing components are consumers only; unresolved handling authority is `BLOCKED`; every handling decision derives from exact Source Handling facts and policy applicable and knowable at the relevant cutoff; and persistence must independently rederive rather than trust caller-supplied decisions.

This is therefore a real unresolved architecture decision involving component ownership, external-provider boundary, persistence, replay, security, migration, and future extensibility. It is not an ordinary implementation choice.

## Motivation

Without this architecture, Hunter has three unsafe failure modes:

1. **Opaque execution** — a response may exist without proof of the exact canonical prompt and exact provider request that produced it when such proof is durably authorized, or without an explicit policy-governed unavailability state when it is not.
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
- **Explainability:** every persisted AI-shaped outcome must be traceable to exact governed inputs and execution evidence to the extent durable evidence is authorized, with explicit unavailable states otherwise.
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
- Provider-specific transformed content must have its own transient representation and, when durable request evidence is authorized, its own distinct durable artifact semantics.
- Capability constraints used to make a prompt fit are upstream build inputs. Selecting a provider/model whose effective capability differs cannot silently reuse an incompatible build.
- External model execution is non-deterministic and network-dependent; historical audit must not pretend that re-invocation reproduces the original response.

### Operational

- Provider outages, timeouts, rate limits, quota exhaustion, billing unavailability, safety refusal, malformed/partial responses, and unsupported capabilities are normal explicit outcomes, not exceptional gaps to hide.
- Retry must be bounded and observable; a retry is a new attempt, not mutation of an old attempt.
- Every new invocation or retry is a new live processing event and must re-evaluate Source Handling authority at that attempt's own cutoff; prior build-time or prior-attempt `ALLOW` never authorizes a later network send.
- A first implementation should be operable with one explicit execution profile without forcing premature routing architecture.

### Persistence and migration

- Attempt, request, response, and failure history is append-only/immutable once recorded, but only fields and categories whose durable dispositions permit persistence may be recorded.
- Corrections are new records or superseding metadata, not mutation of historical bytes.
- Existing ADR 0031 build/artifact identities remain unchanged.
- Existing provider-runner records remain historical records of their original schema; migration must not relabel them as if they had Model Adapter request lineage they never recorded.

### Replay and historical reconstruction

- Exact audit reconstruction of a historical build or attempt may use only persisted artifacts and authority known at that historical artifact's recorded cutoff.
- A new provider invocation or retry is not historical replay and must resolve Source Handling authority applicable and knowable at the new attempt cutoff; restrictive successors known by then must block the new operation when they remove processing authority.
- Re-invoking a provider is a new execution, not historical replay.
- If exact request or response bytes were not authorized for retention, reconstruction is explicitly unavailable rather than regenerated from current state.
- A digest, measured size, content-derived identifier, or other derived request/response evidence may be retained only when the exact Source Handling durable disposition permits that category; hashes are not automatically safe.

### Compatibility

- ADR 0031 prompt/build identities remain historically valid.
- ADR 0033 handling identities and historical cutoffs remain authoritative for reconstruction; they do not authorize later live processing after a restrictive successor becomes applicable/knowable.
- `AIExtractionProvider` may be adapted or deprecated later, but existing historical artifacts are not rewritten.
- Response Validator and downstream proposal validation remain separate.

### Security and privacy

- Credentials, bearer tokens, API keys, cookies, authentication headers, client secrets, and equivalent transport secrets are never canonical request artifacts.
- Provider request durability may contain only governed non-secret data whose exact durable categories are authorized; otherwise request content/hash/content-derived identity remains explicitly unavailable.
- Source Handling Authority must allow model processing at the live attempt cutoff immediately before any network handoff.
- Durable request/response content and content-derived metadata must pass the applicable attempt-time field-category/disposition enforcement independently at persistence.
- Provider responses are untrusted external data and may not request tools, repository writes, schema changes, or other capabilities outside the authorized adapter contract.

### Performance and scalability

- Provider transport latency is expected and must not leak into deterministic pre-model identity.
- Request/response byte retention may be expensive and must follow handling policy rather than convenience.
- Multi-provider fan-out, racing, or score-based routing is deliberately not required for the first boundary.

### Evidence and provenance

A model attempt must preserve enough evidence to answer, when authorized:

- which `EvidencePreModelBuildRecord` and `EvidencePromptArtifact` were used;
- which provider request was transmitted, including exact durable request artifact/bytes when authorized or an explicit policy-governed request-evidence-unavailable outcome when not;
- which versioned execution profile/provider/model/protocol was used;
- which attempt/retry produced the result;
- which response artifact or explicit unavailable outcome resulted;
- which Source Handling authority state and **attempt cutoff** permitted the live handoff and new durable surfaces, while separately preserving the build cutoff used for upstream historical lineage; and
- whether exact reconstruction is available or unavailable.

## Evidence Inventory

| ID | Evidence | Authority/source | Finding | Quality and limitations | Supports or challenges |
|---|---|---|---|---|---|
| E-001 | Model Adapter explicitly deferred | Accepted ADR 0031 | Current architecture ends at pre-model build; later Model Adapter required | Highest architectural authority for this scope; direct | Requires new architecture |
| E-002 | Provider-specific request wrapping must be distinct | Accepted ADR 0031 | Provider-facing transformation must remain distinct from canonical prompt; durable exact bytes/derived IDs remain handling-governed | Direct architectural requirement | Supports separate request evidence semantics |
| E-003 | Project-neutral core admission/exclusions | Accepted ADR 0032 | Routing, credentials, invocation, provider selection and response validation are not shared-core authority; concrete shared contracts require two-consumer evidence | Direct; no current second-consumer Model Adapter evidence | Challenges shared-core option |
| E-004 | Source Handling canonical ownership | Accepted ADR 0033 | Model Adapter is consumer only; unresolved authority blocks model-facing processing; live attempts cannot inherit an older permission | Direct | Requires independent adapter enforcement |
| E-005 | Durable category and authorization mechanics | Source Handling Design Contract | Field/category dispositions and exact payload-bound authorization govern durable surfaces; content-derived hashes/identities are not automatically retainable | Runtime contract subordinate to accepted ADRs; status text says ready for review but mechanics are implemented/integrated in current runtime | Supports request/response persistence controls |
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
| A-002 | Provider APIs can represent the canonical prompt either directly or through a deterministic transform whose exact non-secret request content can be identified in memory and durably recorded only when handling permits | Common transport property and ADR 0031 already anticipates transformations | High | A provider requires opaque mutable server-side prompt transformation that cannot be represented/audited even transiently | That provider is not admissible under this boundary without new architecture |
| A-003 | Source Handling decisions applicable at each model attempt are sufficient to constrain request/response durability when combined with the field-category registry | Follows ADR 0033 design | Medium | Provider responses introduce a new durable data category not expressible by the governed registry | Source Handling design must be extended before response persistence |
| A-004 | Remote model output cannot be reproduced deterministically merely by replaying the same request | External model execution is outside Hunter deterministic control | High | Provider supplies a verifiable deterministic execution contract Hunter can prove | Architecture may later add a stronger reproducibility mode; current audit/replay distinction remains safe |

## Architectural Dimensions

1. **Authority and ownership** — who owns Model Adapter execution semantics and who is prohibited from acquiring upstream/downstream authority.
2. **Consumer versus shared core** — whether provider mechanics remain Hunter-owned under ADR 0032 admission rules.
3. **Prompt immutability** — preserving canonical prompt bytes while permitting provider-specific transport representations.
4. **Execution profile identity** — exact provider/model/protocol/capability binding without giving the provider selection authority over upstream build semantics.
5. **Provider request evidence** — exact non-secret bytes/structure when durably authorized, plus explicit unavailable semantics when content-derived durability is prohibited.
6. **Attempt identity and retries** — append-only execution lineage and explicit outcomes, with attempt-time authority re-evaluation for every live send.
7. **Response identity** — exact/raw or unavailable response evidence distinct from validated proposal semantics.
8. **Source Handling enforcement** — live processing authority resolved at the attempt cutoff and durable-category decisions independently re-resolved at persistence; historical build cutoff is lineage/reconstruction only.
9. **Credential exclusion** — transport secrets excluded structurally from canonical bytes, logs, diagnostics, and hashes.
10. **Failure/missingness** — timeout, quota, billing, refusal, malformed data, provider outage, unsupported capability, and persistence restrictions are explicit.
11. **Persistence/replay** — exact historical audit versus new re-invocation.
12. **Provenance/observability** — proof chain from build to request to attempt to response, subject to durable dispositions.
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

- **Description:** Define a new Evidence Intelligence `ModelAdapter` contract that consumes an exact completed pre-model build and one explicit versioned `ModelExecutionProfile`. Provider-specific transports deterministically map the canonical prompt to a provider request in memory. The adapter records only request, attempt, outcome, and response evidence whose durable categories are authorized, with explicit unavailable states otherwise. It does not choose among profiles dynamically.
- **Authority and ownership:** Evidence Intelligence owns the adapter/attempt contract. Source Handling remains sole handling authority. Provider transports own protocol mechanics only. Repositories persist mechanically. Response Validator remains separate.
- **Boundaries:** Clean handoff: `PreModelBuild -> ModelAdapter -> transient ProviderRequest -> ProviderRequestArtifact or request-evidence-unavailable -> ModelAttemptRecord -> ProviderResponseArtifact/unavailable -> future ResponseValidator`.
- **Persistence and replay:** Append-only immutable attempt lineage; exact request/response reconstruction only when authorized; re-invocation always a new attempt; new live attempts re-resolve handling at their own cutoff.
- **Evidence and provenance:** Strong linkage from prompt/build to profile/attempt and, when policy permits, exact request/response artifacts; otherwise explicit policy-governed unavailability.
- **Compatibility:** Existing provider runner can remain historical and later be adapted behind a transport or deprecated without rewriting old records.
- **Advantages:** Preserves ADR 0031 identities, minimizes provider lock-in, keeps routing out of scope, provides deterministic test seam, preserves validation separation.
- **Disadvantages:** Introduces new records and a migration boundary rather than reusing old provider artifacts directly.
- **Failure modes:** An execution profile may be mismatched to the build's capability constraint; a restrictive successor may invalidate live processing; content-derived durable fields may be prohibited. Architecture must fail closed or record explicit unavailability rather than adapt/persist silently.
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
- **Persistence and replay:** Can satisfy exact lineage for one provider when handling permits its durable evidence.
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

**Boundary case:** provider requires transformation of message structure. The option survives because the transformed non-secret request exists as a distinct transient representation; exact bytes/hash/content-derived identity become a distinct durable `ProviderRequestArtifact` only when the relevant Source Handling dispositions permit those categories, otherwise durability is explicitly unavailable.

**Boundary case:** provider/model capability differs from the build capability. The option survives only by failing closed and requiring a new build; silent adaptation is prohibited.

**Boundary case:** a build was allowed at its historical cutoff but a restrictive successor becomes known before a new invocation or retry. The option survives only by re-resolving Source Handling at the new attempt cutoff and blocking the network send when processing is no longer allowed. The build cutoff remains historical lineage, not live authorization.

**Boundary case:** exact request or response retention, hashes, or content-derived IDs are forbidden. The option survives by persisting only categories explicitly authorized by the relevant durable dispositions and recording explicit unavailability for prohibited evidence; a mandatory content-derived artifact identity is not required.

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

Before network transmission, the provider transport deterministically creates the exact non-secret provider-facing request in memory. The Model Adapter then evaluates the applicable durable dispositions independently from processing permission.

When the relevant Source Handling dispositions authorize durable request evidence, a `ProviderRequestArtifact` may record only the allowed categories, linked as applicable to:

- `EvidencePreModelBuildRecord` identity;
- `EvidencePromptArtifact` identity;
- `ModelExecutionProfile` identity/version;
- transport/request protocol identity/version;
- exact non-secret canonical provider-facing message/body bytes **only when exact-content retention is authorized**;
- exact content hashes, measured size, encoding/canonicalization metadata, or other `CONTENT_DERIVED_ID`/derived fields **only when each field's durable category is explicitly authorized**;
- a content-derived deterministic request identity **only when its durable category is explicitly authorized**;
- explicit per-category reconstruction availability/unavailability and reason codes.

No content-derived hash, size, digest, canonical-byte fingerprint, or identity is presumed safe merely because model processing is allowed. When those durable categories are denied, the request may still be transmitted if live processing is authorized, but prohibited evidence is not persisted. The attempt instead records an authorized non-content-derived request-evidence outcome such as `REQUEST_EVIDENCE_UNAVAILABLE_BY_POLICY`; if even that metadata category is not authorized, no substitute identifier is fabricated.

A durable request artifact, when one exists, is always semantically distinct from `EvidencePromptArtifact`, even when its authorized content mapping is byte-equivalent. This prevents provider wrapping from retroactively becoming canonical prompt content. When no durable request artifact is permitted, that absence is explicit and does not collapse back into the prompt artifact.

Authentication headers, bearer tokens, API keys, cookies, client secrets, signing secrets, and equivalent credentials are structurally outside the transient canonical request representation used for evidence and outside every canonical hash. Their existence may be represented only by a non-secret credential-slot/configuration identity if separately authorized; secret values never appear.

### Source Handling execution gate

A live model invocation has **two distinct Source Handling coordinates** that must not be conflated:

1. **Build lineage coordinate.** The adapter may re-resolve the build's historical Source Handling state at the build cutoff to verify that the existing `EvidencePreModelBuildRecord` and `EvidencePromptArtifact` remain valid historical lineage. This resolution is reconstruction/audit evidence only and grants no permission for a new network operation.
2. **Attempt authorization coordinate.** Immediately before preparing/transmitting provider-facing bytes for every invocation or retry, the Model Adapter independently resolves Source Handling authority for the same governed document/version scope using the new attempt's effective context and strict-known **attempt cutoff**. The live send is authorized only by this attempt-time resolution.

The attempt-time gate verifies that:

- all required handling facts, policy, field-category registry, authorization rule, and provenance applicable and knowable at the attempt cutoff resolve without ambiguity;
- `processing_decision == ALLOW` at the attempt cutoff;
- any restrictive successor fact/policy known by that cutoff, including withdrawal/deletion or processing prohibition, takes effect for the new network operation and blocks it when applicable;
- provider-request and later response durable field/category dispositions permit each newly persisted field/category; processing permission alone never grants durability;
- no caller/provider decision, prior build decision, prior attempt decision, or existence of historical bytes substitutes for current attempt-time authority.

Every retry is a new live attempt and repeats this gate. A prior successful attempt does not authorize the next retry.

The build cutoff remains immutable lineage for historical reconstruction. It must never be replaced by current authority when reconstructing the old build, just as the old build cutoff must never be reused as permission for a new live send. A restriction learned only after an already-completed historical attempt does not retroactively rewrite that attempt; it governs future operations from the point it becomes applicable/knowable under Source Handling semantics.

Implementation design must minimize and explicitly bound the time-of-check/time-of-use interval between attempt-time authority resolution and network transmission. If the implementation cannot establish an acceptable bounded handoff, it must fail closed rather than treat a stale permission as current.

### Model attempt

Every network invocation creates a new immutable `ModelAttemptRecord` identity. At minimum it links, to the extent each durable category is authorized:

- execution owner;
- pre-model build ID;
- prompt artifact ID;
- provider request artifact ID **when a durable request artifact exists**, otherwise an explicit authorized request-evidence-unavailable outcome/reason;
- execution profile ID;
- attempt ordinal;
- predecessor/retry attempt ID where applicable;
- attempt effective context and attempt cutoff used for live Source Handling authorization;
- build cutoff retained separately for upstream lineage/reconstruction;
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

The Model Adapter ends after transport-level response capture and normalization. A separately governed future `ResponseValidator` consumes the response evidence and may validate syntax/schema/capability/evidence-reference rules. Only after that boundary may Evidence Intelligence produce an `ExtractionProposal` through its authorized service path.

The existing legacy provider runner's direct proposal creation is not adopted as the new Model Adapter rule. Historical behavior remains historical; any future migration must preserve old schema identity while routing new executions through the new boundary.

### Routing boundary

Provider/model routing is deferred. V1 architecture supports one explicit execution profile per attempt and no score-based, health-based, cost-based, quota-based, or fallback selection among alternative profiles.

If Hunter later needs automatic selection among two or more provider/model profiles, that creates a new authority/operational decision covering selection inputs, cost/latency/quality criteria, failure fallback, capability compatibility, and deterministic audit. That work requires a separate ADPR/ADR or explicit accepted amendment.

### Replay and re-execution

- Reconstructing a historical build or attempt means reading its persisted immutable evidence under that artifact's historical cutoff and authority state; current/later authority never backfills historical absence.
- Re-invoking a provider, even with identical transient request bytes and model identifier, is a **new attempt**, not replay of the old response.
- Every new invocation/retry resolves live Source Handling at its own attempt cutoff; an older build or attempt `ALLOW` is never reused as live permission.
- No current provider version or current configuration may backfill missing historical execution evidence.
- When exact bytes, hashes, measured sizes, or content-derived identifiers were not durably authorized, reconstruction remains explicitly unavailable for those categories.

### Mandatory conformance cases

The resulting ADR and implementation contract must require deterministic tests for these defect classes before any live provider integration:

1. **Revocation between build and send:** build-time Source Handling resolves `ALLOW`; before invocation a restrictive successor becomes applicable/knowable. The new attempt must resolve at its own cutoff and return `SOURCE_HANDLING_BLOCKED` without transmitting bytes. Historical reconstruction of the build must still use the original build cutoff.
2. **Revocation between attempts:** attempt 1 is authorized; a restrictive successor becomes applicable/knowable before retry. Attempt 2 must re-resolve and block. Prior success cannot authorize retry.
3. **Processing allowed, content durability denied:** model processing is `ALLOW`, while request exact bytes, `CONTENT_DERIVED_ID`, hashes, measured size, or equivalent derived durable categories are denied. Transmission may proceed, but no prohibited bytes/hash/derived identity is persisted; request evidence is explicitly unavailable by policy, and `ModelAttemptRecord` does not require a prohibited request-artifact ID.
4. **Response echo under restrictive durability:** provider response echoes protected source content while response retention/hash categories are denied. The attempt records only authorized metadata/unavailable state and persists no prohibited response bytes or derived digest.
5. **No authority laundering:** caller/provider supplied handling decisions, prior build decisions, prior attempt decisions, or presence of existing prompt bytes cannot make either a live processing gate or durable category permissive.
6. **Cutoff separation:** historical replay/reconstruction uses the historical artifact cutoff; live invocation uses the attempt cutoff. Tests must fail if either coordinate is substituted for the other.

These are permanent regression obligations for the Model Adapter boundary, not review-only prose. A future implementation that cannot encode them as deterministic tests is not ready for provider activation.

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
| Request transformation changes model-facing meaning | Correctness | Medium | High | Distinct transient request representation; distinct durable artifact only when authorized; never mutate prompt artifact | Provider SDKs may perform hidden transformations; such SDK modes are inadmissible unless observable |
| Credentials leak into artifacts/logs/hashes | Security | Low-medium | Critical | Structural exclusion, secret-free request canonicalization, tests scanning all durable/log surfaces | Third-party SDK diagnostics require explicit hardening |
| Source content is processed after authority becomes unresolved/revoked | Security/governance | Low-medium | High | Resolve strict-known Source Handling at every attempt cutoff immediately before handoff; restrictive successors block future sends | Time-of-check/time-of-use interval must be bounded in implementation design |
| Content-derived request evidence persists when durability is denied | Security/privacy | Low-medium | High | Per-category durability checks; no mandatory hash/content-derived request ID; explicit unavailable state | Exact field-category mapping must be validated before implementation |
| Raw model response persists prohibited source echoes | Security/privacy | Medium | High | Govern request/response durable categories via Source Handling; fail closed on unresolved disposition | Output-specific category coverage may need design refinement |
| Adapter transport success is mistaken for valid proposal | Authority | Medium | High | Separate Response Validator; attempt outcome named transport-only; no proposal creation in adapter | Legacy runner semantics may confuse migration unless clearly versioned |
| Multi-provider fallback sneaks in as implementation convenience | Governance/operational | Medium | Medium-high | V1 explicitly forbids routing/fallback; separate architecture required | Pressure may rise during outages/quota limits |
| Legacy provider artifacts are retroactively relabeled | Migration | Low | High | Preserve historical schema/identity; no synthetic backfill | Mapping old records to new audit views may remain partial |
| Remote provider behavior changes despite same model name | Replay | High | Medium-high | Record exact safe provider/model/version/protocol metadata when authorized; distinguish audit from re-invocation | Provider may not expose immutable model revision identifiers |
| Provider-specific SDK hides wire request bytes | Evidence | Medium | High | Require deterministic observable transient request representation; reject opaque modes/providers | Some provider SDKs may need lower-level HTTP transport |
| Project-neutral extraction becomes desirable later | Long-term | Medium | Low | Keep consumer contract narrow and transport interface clean; use ADR 0032 admission later | Future consumer semantics unknown |

## Open Questions

| Question | Blocking? | Owner | Required evidence or action | Status |
|---|---|---|---|---|
| Exact Python module/type names and SQL layout | No | future implementation | Design mechanically under accepted ADR | Deferred |
| Exact closed attempt reason-code vocabulary | No | future design/implementation | Contract tests before runtime | Deferred |
| Which concrete provider should first implement the transport | No | future separately scoped implementation | Operational/provider evaluation after architecture acceptance | Deferred |
| Whether provider exposes immutable model revision IDs | No for architecture | future provider adapter | Record best available exact safe identity when authorized; mark unknown explicitly | Deferred |
| Whether response bytes require an output-specific Source Handling category expansion | No for ownership decision; may block response persistence | Source Handling design/implementation | Validate field-category coverage before Model Adapter runtime | Required before implementation if current registry is insufficient |
| Exact allowed non-content-derived attempt/request-unavailable metadata categories | No for ownership decision; may block implementation | Source Handling design/implementation | Validate field-category registry and durable dispositions before runtime | Required before implementation |
| Maximum acceptable time-of-check/time-of-use interval for live handling authorization | No for ownership decision; may block implementation | future Model Adapter design | Define bounded handoff/fail-closed semantics before network activation | Required before implementation |
| Automatic multi-provider routing | No; explicitly out of V1 | future architecture | New evidence/ADPR if operational need appears | Deferred |
| Shared project-neutral Model Adapter | No; explicitly not admitted | future ADR 0032 admission process | Two independent consumer contracts | Deferred |

No open question blocks the ownership/boundary decision proposed here. Implementation remains blocked where the table explicitly requires pre-runtime resolution.

## Constitution Review

- **Rule 2 — Evidence Authority:** satisfied by exact build/request/attempt/response lineage when authorized and explicit unavailable states otherwise. Adapter success carries no analytical authority.
- **Rule 3 — Deterministic Intelligence:** satisfied by keeping deterministic identities, canonicalization, transforms, and replay under Hunter control while explicitly refusing to represent remote model output as deterministic replay.
- **Rule 4 — Architectural Integrity:** strengthened by separating prompt compilation, transport, routing, response validation, and canonical promotion.
- **Rule 5 — Single Source of Truth:** Source Handling remains the sole handling authority; Model Adapter owns execution semantics only; Response Validator and domain promotion remain separate owners.
- **Rule 6 — Explainability:** attempt provenance can explain what governed build/profile/attempt produced a response or failure, with exact request/response evidence only where durability permits and explicit unavailable states otherwise.
- **Rule 7 — Long-Term Evolution:** provider-specific transports are replaceable without rewriting canonical pre-model history.
- **Rule 8 — Governance:** architecture preparation precedes ADR and implementation.

No constitutional conflict is identified.

## Governance Review

- `ARCHITECTURE_DECISION_PREPARATION_GUIDE.md` applies because this change creates a new external-service subsystem boundary and defines persistence/replay/security semantics.
- ADR 0031 is reaffirmed and extended at its explicitly deferred Model Adapter boundary; no upstream intent/context/prompt ownership is changed.
- ADR 0032 remains controlling for project-neutral admission. This record deliberately keeps the concrete Model Adapter Hunter-owned.
- ADR 0033 remains sole Source Handling authority. The Model Adapter independently consumes/rederives but never publishes handling policy/facts. Historical build authority is used for lineage/reconstruction only; every new live attempt resolves applicable/knowable authority at the attempt cutoff.
- ADR 0020 strict-known semantics are applied to historical execution audit without using historical cutoffs as live authorization.
- ADR 0009 provider/service/repository separation is preserved.
- The deterministic Governance Review / Merge Readiness path is isolated and remains zero-LLM.
- Human merge approval remains required for any future implementation contribution.

No unresolved governance conflict is identified.

## Quality Assessment

| Dimension | Rating | Evidence and rationale | Blocking limitation |
|---|---|---|---|
| Problem correctness | EXCELLENT | Gap is explicit in Accepted ADR 0031 and observable between current pre-model and provider runtime | None |
| Scope completeness | GOOD | Ownership, handoff, persistence, replay, security, routing, validation, migration, and non-goals are explicit | Exact runtime schemas deferred intentionally |
| Canonical consistency | EXCELLENT | ADRs 0031/0032/0033/0020/0009 and Constitution checked directly; live-attempt and historical-cutoff semantics separated | None |
| Evidence integrity | GOOD | Accepted ADRs and current source are primary evidence; limitations/stale index prose disclosed | No live provider evidence, not required for boundary decision |
| Assumption discipline | GOOD | Four assumptions isolated with falsification conditions | A-003 requires implementation-time category validation |
| Option completeness | GOOD | Legacy extension, Hunter-neutral adapter, shared core, gateway, and direct provider options covered | None material identified |
| Comparative fairness | GOOD | Same correctness/authority/replay/complexity/migration criteria applied | Cost numbers unavailable and not fabricated |
| Falsifiability | GOOD | Each viable option challenged with current ADR/source counterevidence and boundary cases, including revocation and durability-denied cases | Independent audit still required |
| Authority and ownership clarity | EXCELLENT | Model Adapter, Source Handling, transport, repository, routing, validator, and promotion boundaries separated | None |
| Persistence and replay quality | EXCELLENT | Immutable attempt lineage, attempt-time live authority, historical cutoff isolation, category-conditional durable evidence, strict-known audit vs new invocation explicit | Exact schema deferred |
| Evidence and provenance quality | EXCELLENT | Exact lineage where authorized plus explicit unavailable semantics; no prohibited digest assumed safe | Provider model revision visibility may be limited |
| Operational quality | GOOD | Outage/timeout/rate/quota/billing/refusal/security/capability outcomes, retry semantics, and live re-authorization addressed | Concrete timeout/retry/TOCTOU values deferred |
| Implementation and migration impact | GOOD | New V1 families coexist with legacy provider records; no historical rewrite | Detailed migration API deferred |
| Testability and validation | EXCELLENT | Permanent conformance cases require revocation-between-build/send, revocation-between-retries, durability-denied request/response evidence, no authority laundering, and cutoff separation | Operational live canary belongs later |
| Maintainability and extensibility | EXCELLENT | Provider transports replaceable; routing/shared core deferred until evidence | None |
| Risk quality | GOOD | Material security/authority/replay/migration risks include mitigation and residual uncertainty | None blocking |
| Traceability | GOOD | Issue #287, ADPR-0009, PR #288, planned ADR 0034 identified; later merge/release remain explicitly absent | Independent review not yet complete |

All mandatory quality dimensions are at least `ACCEPTABLE`; Constitution/canonical consistency and Governance are at least `GOOD`. No self-identified blocking architecture question remains.

## Architecture Readiness

- Outcome: `READY`
- Rationale: the architectural problem is validated by accepted ADRs and current runtime shape; material ownership, security, persistence, replay, migration, routing, and validation boundaries are resolved at architecture level; live attempt authorization is separated from historical reconstruction; durability of exact/content-derived evidence is policy-conditional; implementation mechanics are separable and explicitly deferred.
- Missing evidence: no live provider operational evidence; not required for choosing the ownership/handoff architecture. Concrete provider selection remains a later implementation input.
- Unresolved conflicts: none identified.

## ADR Readiness

- Outcome: `READY_FOR_ADR`
- Proposed ADR title: **Evidence Intelligence Model Adapter and Provider Attempt Boundary**
- Proposed ADR scope: bind the consumer-owned Model Adapter ownership, `EvidencePromptArtifact` handoff, distinct provider-request semantics with disposition-conditional durable exact/content-derived evidence, execution profile/attempt/response lineage, attempt-time Source Handling execution and persistence invariants, credential exclusion, historical audit versus re-invocation, routing deferral, Response Validator separation, and legacy migration rules.
- Decisions the ADR must fix:
  1. Model Adapter is Hunter Evidence Intelligence owned.
  2. Contract is provider-neutral with subordinate provider transports.
  3. Provider-specific transformed request is distinct from `EvidencePromptArtifact`; durable exact bytes/hash/content-derived request identity exist only when the relevant Source Handling categories permit them, otherwise request evidence is explicitly unavailable.
  4. Every invocation/retry is an immutable attempt with lineage and its own live Source Handling attempt cutoff.
  5. Build cutoff is historical lineage/reconstruction only and cannot authorize a later live send; restrictive successors applicable/knowable at an attempt cutoff govern that new operation.
  6. Request/response durability is independently governed per durable category; processing `ALLOW` does not grant persistence.
  7. Credentials are structurally excluded.
  8. Transport success grants no validation or canonical authority.
  9. Response Validator remains separate.
  10. Multi-provider routing is deferred.
  11. Historical reconstruction never means provider re-invocation.
  12. Legacy provider records retain original identity and are not synthetic-backfilled.
- Matters the ADR must leave open:
  - concrete provider/model choice;
  - exact runtime schemas/SQL/module paths;
  - retry counts/timeouts/backoff policy;
  - maximum live authorization TOCTOU interval;
  - provider SDK/library choice;
  - exact response validation rules;
  - exact Source Handling field-category mapping for request/response derived metadata;
  - future routing architecture;
  - future ADR 0032 shared-core admission.

## Final Recommendation

Adopt **Option 2: an Evidence Intelligence-owned, provider-neutral Model Adapter contract with provider-specific transport implementations, one explicit versioned execution profile per attempt, and no automatic routing in V1**.

This option is the smallest boundary that actually solves the accepted architecture gap. It preserves ADR 0031 prompt/build identity, keeps ADR 0033 handling authority singular, separates historical build lineage from live attempt authorization, prevents processing permission from laundering prohibited durable hashes/identities, obeys ADR 0032's anti-premature-generalization rule, allows provider substitution without rewriting history, and keeps Response Validator and canonical promotion outside transport authority.

After independent architecture audit returns an ADR-ready verdict, create proposed ADR 0034 as a separate governed lifecycle contribution. Runtime provider integration remains blocked until ADR 0034 is separately accepted and a dedicated implementation issue freezes the mandatory conformance cases above as deterministic contract tests before concrete provider code.

## Decision History

| Date | State | Change | Author or reviewer |
|---|---|---|---|
| 2026-08-20 | READY_FOR_REVIEW | Initial ADPR-0009 created from Issue #287 after PR #286 merge; recommendation is Hunter-owned provider-neutral adapter with routing deferred | ChatGPT / repository owner-directed |
| 2026-08-20 | READY_FOR_REVIEW | P1 review remediation: separated live attempt cutoff from historical build cutoff; made request hashes/content-derived identity durability conditional; added permanent regression/conformance obligations | ChatGPT / repository owner-directed |

## Traceability

- Epic: not yet created
- Issue: #287
- Preparation working document: this ADPR serves as the permanent preparation record; no separate working file created
- Checklist review: author self-assessment completed against preparation guide and quality standard; Codex P1 review findings on PR #288 remediated; independent architecture audit remains required
- ADPR: `docs/architecture-records/ADPR-0009-evidence-intelligence-model-adapter.md`
- ADR: proposed ADR 0034, not yet created
- Implementation plan: not yet created; blocked on ADR acceptance
- PR: #288
- Merge commit: not yet created
- Release: not yet assigned

## Immutability and Supersession

After `APPROVED`, this record becomes historical evidence. Substantive reasoning changes require a new ADPR that explicitly supersedes this record. Non-substantive traceability completion and typographical corrections must remain auditable in repository history.