# ADR 0031: AI Context and Prompt Intelligence Foundation

## Status

Accepted.

## Date

2026-08-08.

## Context

This ADR defines architecture only. It does not authorize a production LLM integration, a Model Adapter, provider credentials, or any change to Hunter Governance Review. Runtime implementation may begin only after this ADR is accepted and a separately scoped implementation plan is approved.

The governing preparation record is [ADPR-0006](../architecture-records/ADPR-0006-ai-context-prompt-intelligence-foundation.md). Its second independent preparation audit confirmed that the Evidence Intelligence-specific option is ready to govern revision of this ADR. That preparation outcome did not itself accept this ADR and does not authorize implementation.

Project Hunter has no active production LLM execution path. Evidence Intelligence defines an injected `AIExtractionProvider` boundary, but no concrete external model adapter or canonical prompt builder exists. Its provider output is persisted only as a proposal and is not canonical knowledge. Hunter Governance Review is deliberately deterministic, repository-native, and protected by regression tests that prohibit an LLM dependency.

Hunter already has accepted architectural foundations that this decision extends:

- ADR 0002 requires evidence provenance, explicit unavailable states, and historical replay from evidence that existed at the cutoff.
- ADR 0004 requires trust, identity, source reliability, conflict handling, and unavailable states before intelligence.
- ADR 0009 separates provider access, service authority, and mechanical repository persistence.
- ADR 0016 prohibits implementation existence, storage, or similarly named outputs from promoting themselves to canonical analytical authority.
- ADR 0020 requires deterministic strict-known selection, immutable historical records, exact source identity, explicit missingness, and no fallback to latest or current state.

PR #200 supplied additional operational evidence. Character limits did not provide a trustworthy token budget; lossless chunking without a call-volume plan exhausted provider quotas; resolving evidence did not prove that a model reviewed it; and provider quota or billing state was unsuitable as a dependency of merge authority. Hunter therefore needs model-facing contracts before it introduces a model-facing runtime.

ADRs 0029 and 0030 are both Proposed and non-binding. Their HDM and four-layer descriptions are relevant design context but are not authority for this ADR. This decision is grounded independently in the Constitution, Project Principles, Development Governance, and accepted ADRs. If ADR 0030 is later accepted, its sequencing must be reconciled with this ADR's binding rule that context resolution, selection, omission accounting, and budgeting precede prompt compilation.

## Problem

Hunter cannot currently answer, after an AI-shaped execution, why it ran, which exact sources and revisions it considered, what it selected or omitted, which policy and budget produced the prompt, what exact prompt bytes were handed to a model, or whether the historical pre-model state can be reconstructed.

A prompt string utility would not close this gap. Retrieval authority, source trust, selection, missingness, budgeting, prompt compilation, model invocation, response validation, downstream authority, persistence, and replay are distinct responsibilities. Collapsing them would create opaque authority and prevent deterministic audit.

Hunter needs the smallest coherent Evidence Intelligence pre-model foundation that:

1. deterministically constructs a model-independent Evidence Intelligence context decision from governed inputs;
2. makes every required missing item and every non-inclusion observable;
3. compiles an exact model-facing prompt artifact without owning retrieval or model routing;
4. preserves identities and records sufficient for exact pre-model reconstruction when governing retention policy permits;
5. leaves Model Adapter, provider routing, and response-validation architecture independently evolvable; and
6. keeps all AI output non-authoritative unless a separate canonical owner consumes it through an already-authorized deterministic or human decision boundary.

## Decision

Hunter establishes an Evidence Intelligence-specific deterministic pre-model foundation with four internally distinct current stage owners and three future boundaries:

```text
EvidenceExtractionIntent
    -> EvidenceContextResolver
    -> EvidenceContextSelector
    -> EvidenceContextSelectionLedger
    -> EvidenceContextBudgetAllocator
    -> EvidenceContextAllocationResult + EvidenceContextPackage
    -> EvidencePromptCompiler
    -> EvidencePromptPlan + EvidencePromptArtifact
    -> EvidencePreModelBuildRecord
    -> future ModelAdapter
    -> future ResponseValidator
    -> ExtractionProposal
    -> existing deterministic Evidence Intelligence validation
    -> existing authorized claim-persistence boundary
```

No single `AIService` may own this complete chain. These contracts are owned within Evidence Intelligence and grant no repository-wide Context Intelligence or Prompt Intelligence authority. The current foundation ends at an exact `EvidencePromptArtifact` and an immutable `EvidencePreModelBuildRecord`. That record is a subordinate build artifact linked to the initiating `PipelineRun`, workflow run, task identity, or equivalent existing execution owner. It never replaces Hunter's top-level execution identity and never owns provider attempts or responses. Model invocation, provider attempts, response semantics, and any full AI-interaction aggregate require later Model Adapter architecture.

Generic Context/Prompt ownership is explicitly deferred. A second real AI consumer must trigger a new ADPR and ADR that compare both demonstrated consumer contracts. Any shared abstraction must be extracted only from proven common semantics, preserve existing Evidence Intelligence identities and lineage, define adapters and migration explicitly, and leave consumer-specific authority with its original owner.

## Architectural Boundaries

### EvidenceContextResolver

The resolver converts governed `EvidenceSpan` coordinates into exact source references and resolution outcomes within Evidence Intelligence.

It owns:

- exact `EvidenceSpan`, underlying document/evidence version, repository commit where applicable, range, and content-hash resolution;
- source existence and temporal-resolution checks required by the applicable source contract;
- exact resolution evidence used by the selector's governed eligibility policy; and
- explicit `RESOLVED`, `UNRESOLVED_REQUIRED`, `UNRESOLVED_OPTIONAL`, and `NOT_ATTEMPTED` outcomes.

It must not rank relevance, allocate prompt budget, render prompt prose, select a provider, invoke a model, or establish canonical truth.

### EvidenceContextSelector

The selector applies one versioned `EvidenceContextSelectionPolicy` to resolved source references and an `EvidenceExtractionIntent`.

It owns:

- eligible Evidence Intelligence span/source classes;
- required and optional context;
- deterministic relevance rules and ordering;
- inclusion priority before budget allocation;
- exclusion and rejection rules; and
- applicability, eligibility, rejection, selection, and non-selection reasons.

It must not retrieve unplanned sources, generate prompt prose, inspect provider health, change source authority, or use model output to revise the historical selection decision.

### EvidenceContextBudgetAllocator

The allocator applies a versioned budget policy and one declared model-capability constraint profile to the selected context.

It owns:

- input-capacity and completion-reserve accounting;
- section and source-class allocations;
- exact-versus-estimated token-accounting classification;
- coverage requirements;
- budget-exclusion decisions; and
- deterministic overflow outcomes.

It must not silently truncate content, change source meaning, summarize with an LLM, choose a provider, or downgrade required context to optional.

### EvidencePromptCompiler

The compiler converts an `EvidencePromptPlan` and finalized `EvidenceContextPackage` into an exact `EvidencePromptArtifact`.

It owns:

- deterministic semantic-section ordering;
- template and fragment application;
- trusted instruction placement;
- untrusted-context delimiting and escaping;
- explicit missingness disclosure required by the prompt specification; and
- exact rendered message content and hashes.

It must not perform arbitrary repository or evidence retrieval, alter selection or omission decisions, route models, invoke providers, validate model truth, or make governance decisions.

### Future boundaries

A future `ModelAdapter` may consume an `EvidencePromptArtifact` and capability requirements. It may not modify upstream intent, source resolution, context selection, or prompt content without producing a new, separately identified build.

A future `ResponseValidator` may validate syntax, schema, prohibited capabilities, evidence references, and Evidence Intelligence proposal eligibility. Validation does not promote AI output to canonical authority.

Evidence Intelligence may convert a validated response into an `ExtractionProposal` only through its existing authorized service boundary. Canonical promotion remains governed by its accepted authority and never by this foundation. Other AI consumers require their own architecture decision.

## Canonical Concepts and Contracts

The names below are canonical Evidence Intelligence architectural concepts. They are not generic cross-consumer interfaces. Exact language types, module paths, and SQL layouts are implementation details.

### EvidenceExtractionIntent

`EvidenceExtractionIntent` is the provider-independent reason and authority envelope for Evidence Intelligence model-facing extraction work. It must identify:

- task type and bounded task objective;
- initiating Evidence Intelligence workflow stage;
- target identity and exact temporal or repository scope where applicable;
- expected output-contract identity and version;
- requested output-authority ceiling, which is fixed at `ExtractionProposal` for this decision;
- permitted capabilities;
- explicitly forbidden capabilities;
- required context-policy identity;
- replay mode and historical cutoff coordinates when applicable; and
- schema version and deterministic content identity.

The authority-ceiling field records a requested maximum; it grants no authority by its presence. Evidence Intelligence's existing deterministic validation and authorized claim-persistence boundary independently decide what, if anything, may become a canonical `KnowledgeClaim`. The intent must never name a preferred provider as architectural authority. Operational capability requirements may be declared without selecting a provider.

### EvidenceContextSourceReference and resolved source views

`EvidenceContextSourceReference` identifies an exact `EvidenceSpan` without duplicating the span, document, or source owner's canonical record. It points to the stable `EvidenceSpan` identity and records only additional pre-model decision coordinates required by Evidence Intelligence.

Every reference must carry, as applicable:

- Evidence Intelligence span/source class and canonical owner;
- `EvidenceSpan` identity and exact underlying record or artifact identity/version;
- repository path and exact commit for repository sources;
- byte range, line range, span identity, or other exact location coordinate;
- content hash and content length;
- effective, recorded, and known-at time semantics for temporal evidence;
- authority classification;
- instruction-trust classification;
- source-data handling classification or a governed reference from which it is derived;
- provenance chain;
- required or optional classification under the active policy; and
- resolution status.

A resolver may expose an immutable resolved view for selection and budgeting, but this ADR does not establish a generic `ContextItem` record family. The view references exact `EvidenceSpan` records and adds only Evidence Intelligence pre-model decision metadata. A later reusable resolved-view contract requires a new ADPR/ADR after a second real consumer demonstrates common semantics and non-duplicative ownership.

### EvidenceContextSelectionPolicy and EvidenceContextSelectionPlan

An `EvidenceContextSelectionPolicy` is a governed Evidence Intelligence specification with one logical identity, explicit semantic version, content hash, and schema version. It declares:

- eligible Evidence Intelligence span/source classes and resolvers;
- required-context rules;
- optional-context rules;
- deterministic relevance and ordering rules;
- source-class and item priority rules;
- security and authority exclusions;
- historical eligibility rules;
- minimum coverage requirements; and
- the reason-code vocabulary used by selection ledgers and allocation results.

An `EvidenceContextSelectionPlan` binds one intent, one policy version, and exact `EvidenceSpan` resolver inputs before resolution begins. Its planned candidate set must be derived from the canonical Evidence Intelligence span inventory for the governed target at the applicable cutoff, or a caller-supplied span tuple must be validated for exact set equality against that inventory before resolution. A caller-prefiltered tuple is not candidate-set authority. Any mismatch fails closed, and every canonical candidate must remain observable in the selection ledger with its resolution, selection, or non-inclusion reason. The plan prevents callers or prompt templates from silently narrowing or expanding source scope during execution.

Relevance may later use deterministic lexical, graph, or learned signals only when the policy records the signal version and deterministic tie-breaking rule. Non-deterministic model selection is not authorized by this ADR.

### EvidenceContextSelectionLedger

`EvidenceContextSelectionLedger` is the model-independent Evidence Intelligence decision ledger for `EvidenceSpan` resolution and selection. It is not a repository-wide AI ledger. Its explicit name and namespace distinguish it from Hunter Governance Review's existing `ContextManifest`; neither contract implements, imports, replaces, or extends the other.

The ledger records:

- every source reference considered under the plan;
- every resolution result;
- every required coordinate with an unresolved result;
- every selected item;
- every selection decision and applicable non-selection or rejection reason;
- one stable reason code and explanation for every unresolved, rejected, or non-selected coordinate;
- exact source identities, revisions, ranges, hashes, and ordering;
- selection-policy identity/version;
- required model-independent coverage and achieved resolution/selection coverage; and
- deterministic ledger identity.

The ledger never contains a capability constraint, token allocation, budget exclusion, finalized package membership, or provider/model identity. For equal intent, exact historical source state, resolver behavior, and selection policy, it has one stable identity regardless of the later model capacity.

### EvidenceContextAllocationResult and EvidenceContextPackage

`EvidenceContextAllocationResult` applies one minimal, versioned capability constraint and one budget-policy version to one `EvidenceContextSelectionLedger`. It records per-item inclusion or budget exclusion, capacity accounting, coverage outcome, reason codes, and its own deterministic capability-specific identity.

`EvidenceContextPackage` contains only the finalized ordered items authorized for prompt rendering by one allocation result. Its identity is derived from the allocation-result identity, finalized source identities, exact content hashes/ranges, order, applicable policy identities, and schema version. It cannot prove that a model reviewed the content; review requires a later successful invocation record linked to this exact package and prompt artifact.

### EvidenceContextBudget

The budget model has two layers:

1. model-independent budget intent, including required-context coverage, priority classes, and reserved output capacity; and
2. a minimal build-time capability constraint, including its identity/version, supported accounting unit, maximum input capacity, and reserved completion capacity.

Phase 1 requires only one explicit versioned capability constraint used to prove allocation behavior. A generic `ModelCapabilityProfile` registry, provider mapping, capability discovery system, or routing contract is deferred until Model Adapter work demonstrates the need.

The budget record must preserve:

- budget-policy identity/version;
- capability-constraint identity/version;
- maximum input capacity;
- reserved completion capacity;
- accounting method and tokenizer/version when exact tokens are claimed;
- estimated-token method and explicit uncertainty when estimation is used;
- diagnostic byte and character counts;
- allocation per section/source class;
- included and excluded amounts;
- coverage result; and
- final outcome.

Valid outcomes are at least `READY`, `REPLAN_REQUIRED`, and `INSUFFICIENT_BUDGET`. `READY` is prohibited when any mandatory item or mandatory coverage rule is unsatisfied or when the complete compiled prompt input exceeds the capability constraint after reserved completion capacity is applied. Capacity accounting covers the complete rendered prompt in the capability constraint's supported accounting unit, including selected context, trusted instructions, output contract, missingness disclosures, delimiters, and other versioned template overhead. The build must either reserve deterministic prompt overhead before allocation reports `READY`, or withhold final `READY` and fail closed through an exact post-compilation capacity check before an `EvidencePromptArtifact` or `EvidencePreModelBuildRecord` is considered ready. Overflow produces `REPLAN_REQUIRED` or `INSUFFICIENT_BUDGET`; it never yields a ready artifact. Estimation may support planning but may not be represented as exact accounting.

Chunking or summarization is a new, explicit selection/build plan with its own coverage and provenance. It is never an implicit overflow behavior.

### EvidencePromptPlan

`EvidencePromptPlan` is a provider-independent semantic representation, not a concatenated string. It contains ordered, typed sections such as:

- trusted system constraints;
- task instructions;
- authority and non-authority declaration;
- output contract;
- context references/content slots;
- missingness and coverage disclosure; and
- provenance or reconstruction metadata required for the task.

Each section declares its purpose, instruction-trust level, requiredness, template or fragment identity/version, context references, and deterministic order. Canonical architecture is referenced through structured identities and exact context sources; prompt prose does not become its canonical owner.

### EvidencePromptArtifact

`EvidencePromptArtifact` is the exact canonical pre-model payload handed to a future Model Adapter. It contains, and durably persists only when governing source policy permits:

- ordered roles/channels and exact rendered content bytes;
- text encoding, Unicode/newline rules, and canonicalization format/version;
- EvidencePromptPlan identity;
- `EvidenceContextSelectionLedger`, `EvidenceContextAllocationResult`, and EvidenceContextPackage identities;
- prompt specification, template, and fragment versions;
- prompt-compiler identity/version;
- capability-constraint and allocation-result identities;
- per-message and complete-content hashes;
- exact measured size; and
- deterministic artifact identity.

Provider-specific request wrapping, authentication, transport headers, and provider parameters are not part of `EvidencePromptArtifact`; they belong to later Model Adapter architecture. If a future adapter transforms model-facing message content, the transformed exact payload must be recorded as a distinct provider-request artifact and may not be treated as the original prompt artifact. If policy prohibits durable exact-byte retention, the build records reconstruction unavailability rather than treating hashes or regenerated current content as an exact artifact history.

### EvidencePreModelBuildRecord

`EvidencePreModelBuildRecord` is the immutable record of one completed or failed pre-model build. It identifies:

- the initiating `PipelineRun`, workflow run, task identity, or equivalent canonical execution owner;
- intent identity;
- source-resolution and selection-plan identities;
- `EvidenceContextSelectionLedger` identity;
- `EvidenceContextAllocationResult` and EvidenceContextPackage identities when allocation completed;
- EvidencePromptPlan and EvidencePromptArtifact identities when compilation completed;
- exact-reconstruction capability outcome and reason codes;
- the requested output-authority ceiling recorded by the intent; and
- schema and deterministic identity versions.

The record does not own top-level run identity, provider attempts, retries, failover, responses, or a completed AI interaction. Future Model Adapter architecture may define immutable invocation and response records that reference a successful build record and the existing execution owner. Corrections and replans create successor build records with explicit lineage; they never mutate the original. A failure or unavailable provider attempt will belong to later operational-attempt architecture and cannot mutate pre-model identities or canonical source truth.

## Determinism Model

### Pre-model determinism

For equal canonical intent, exact historical source bytes and references, equal model-independent selection ledger, equal capability-specific allocation inputs, equal prompt specification, equal prompt-compiler identity/version, and equal canonicalization format/version, Hunter must produce byte-identical:

- resolution and selection outcomes;
- selection-ledger dimensions, reason codes, and ordering;
- EvidenceContextPackage content and order;
- budget calculations and outcome;
- EvidencePromptPlan;
- EvidencePromptArtifact roles, content bytes, and hashes; and
- deterministic content identities.

`Same` means canonical serialized bytes and content-addressed identities are equal. Wall-clock timestamps, request latency, process identifiers, and operational correlation or attempt IDs are excluded from deterministic content identities.

Provider or model identity alone must not alter intent, source-resolution, selection-plan, or `EvidenceContextSelectionLedger` identity. A different explicit capability constraint may legitimately produce a different `EvidenceContextAllocationResult`, EvidenceContextPackage, EvidencePromptPlan build, EvidencePromptArtifact, and `EvidencePreModelBuildRecord`; it must not rewrite historical resolution or selection truth.

### Canonical byte contract

Every artifact participating in a byte-identity claim uses one versioned canonical byte contract that fixes:

- UTF-8 text encoding without an implicit byte-order mark;
- Unicode normalized to NFC;
- carriage-return and carriage-return/line-feed sequences normalized to line feed, with no final newline inserted or removed unless the versioned prompt specification explicitly requires it;
- repository paths represented relative to the exact repository root with `/` separators, no redundant segments, and case preserved;
- explicit ordering for source entries, prompt sections, messages, and all other sequences whose order is semantically meaningful;
- deterministic ordering of sets and mapping/object keys by their canonical UTF-8 serialized key bytes;
- locale-independent base-10 finite numeric serialization with no leading plus sign, exponent notation, negative zero, or insignificant leading/trailing zeros; non-finite values are rejected;
- lowercase boolean literals and an explicit null representation distinct from an absent field;
- exact whitespace semantics for templates, delimiters, indentation, separators, and trailing spaces;
- source-byte and range semantics, including whether coordinates address original bytes or a named/versioned normalized rendition;
- prompt-specification, template, and fragment identities/versions;
- prompt-compiler implementation identity/version; and
- canonicalization format/version.

No operating-system default, locale, filesystem behavior, library iteration order, renderer default, tokenizer default, or dependency upgrade may affect canonical bytes implicitly. Any dependency whose behavior can affect bytes is pinned by, or incorporated into, the prompt-compiler or canonicalization identity. Tokenizer identity/version additionally governs any exact token-accounting claim, but tokenization does not redefine the canonical prompt bytes.

The invariant is:

```text
equal canonical intent
    + exact historical source bytes/references
    + equal EvidenceContextSelectionLedger
    + equal capability-specific allocation inputs
    + equal prompt specification
    + equal prompt-compiler identity/version
    + equal canonicalization format/version
    -> equal canonical EvidencePromptArtifact bytes and digest
```

### Model non-determinism

A model response is an observed execution result. Temperature zero, a provider seed, or a provider deterministic-mode claim does not convert it into deterministic Hunter authority. Re-executing the same EvidencePromptArtifact creates a new attempt and response record.

### Post-model deterministic validation

Response schema validation, prohibited-capability checks, evidence-reference checks, and Evidence Intelligence proposal-eligibility rules must be deterministic for fixed response bytes and validator versions. A valid response remains an `ExtractionProposal`; validation alone never creates a canonical claim.

## Versioning and Identity

Hunter's canonicalization and SHA-256 identity patterns apply.

- Specifications and policies have a stable logical identity plus explicit semantic version, schema version, and content hash.
- Resolved source views, selection ledgers, allocation results, packages, plans, prompt artifacts, provider-request artifacts, responses, and validation results are content-addressed where those artifacts exist.
- `EvidenceExtractionIntent` has a deterministic specification identity derived from its semantic content. An operational request may separately reference that identity with an attempt correlation record.
- `EvidencePreModelBuildRecord` has a deterministic build identity, references the existing initiating run/workflow/task owner, and has no operational-attempt or response ownership.
- Runtime timestamps are recorded where operationally meaningful but excluded from deterministic content identities.
- Corrections create immutable successors with predecessor identity, correction reason, authorizing authority, effective/recorded/known time where applicable, and schema version.
- Random UUIDs may be used only for operational correlation where no reproducible identity claim is made. They cannot replace deterministic artifact identities.

Identity layering is explicit:

| Layer | Capability influence | Identity rule for one task/source state targeting two capacities |
| --- | --- | --- |
| Existing `PipelineRun`, workflow run, or task owner | None from this foundation | Same initiating owner; ADR 0031 creates no replacement run identity. |
| `EvidenceExtractionIntent` | None | Same intent identity. |
| Source-resolution inputs/results, `EvidenceContextSelectionPlan`, and `EvidenceContextSelectionLedger` | None | Same resolution, plan, and ledger identities. |
| `EvidenceContextAllocationResult` and EvidenceContextPackage | Explicit versioned constraint | May differ. |
| EvidencePromptPlan build, EvidencePromptArtifact, and `EvidencePreModelBuildRecord` | Through the allocation result and explicit compiler constraint | May differ and receive distinct deterministic identities. |
| Provider operational attempt/response | Deferred | Later architecture owns these records; they do not alter any upstream identity. |

## Context Selection and Missingness

Context decisions are orthogonal dimensions, not one enum or linear workflow state. Every planned source coordinate has one value for each applicable dimension:

| Dimension | Owner | Minimum values and meaning |
| --- | --- | --- |
| Applicability and eligibility | `EvidenceContextSelector` applying `EvidenceContextSelectionPolicy` | `APPLICABLE_ELIGIBLE`, `NOT_APPLICABLE`, or `REJECTED`. Rejection records whether security, trust, authority, temporal, retention, or contract policy disallowed the coordinate. |
| Resolution outcome | `EvidenceContextResolver` | `RESOLVED`, `UNRESOLVED_REQUIRED`, `UNRESOLVED_OPTIONAL`, or `NOT_ATTEMPTED`. Resolution records exact content/provenance availability and never decides relevance or budget inclusion. |
| Selection decision | `EvidenceContextSelector` | `SELECTED`, `NOT_SELECTED`, or `NOT_EVALUATED`. Selection is model/capability independent. |
| Allocation and inclusion | `EvidenceContextBudgetAllocator` | `INCLUDED`, `BUDGET_EXCLUDED`, or `NOT_ALLOCATED`. This dimension exists only in `EvidenceContextAllocationResult`, not in the model-independent ledger. |
| Reason | Owner of the decision that produced it | A stable reason code for every rejection, unresolved result, non-selection, budget exclusion, or other non-inclusion. Reasons supplement rather than replace the dimensions. |

The plan itself establishes that a coordinate was considered; `CONSIDERED` is therefore not a separate state value. The following combination rules are deterministic:

- `SELECTED` requires `APPLICABLE_ELIGIBLE` and `RESOLVED`.
- `INCLUDED` requires `SELECTED` and `RESOLVED`.
- `BUDGET_EXCLUDED` requires `SELECTED` and `RESOLVED`; therefore `RESOLVED + SELECTED + BUDGET_EXCLUDED` is valid.
- `UNRESOLVED_REQUIRED` or `UNRESOLVED_OPTIONAL` implies `NOT_EVALUATED` for selection and `NOT_ALLOCATED` for inclusion.
- `NOT_APPLICABLE` or `REJECTED` may be determined before content resolution and then requires `NOT_ATTEMPTED`, `NOT_EVALUATED`, and `NOT_ALLOCATED` unless policy explicitly requires resolution evidence to establish the rejection; that exception is recorded by reason code.
- `NOT_SELECTED` requires an eligible, resolved source and records the policy reason formerly described informally as omission.
- No dimension may infer another dimension that is not fixed by these legal combinations.

`EvidenceContextSelectionLedger` records the applicability, resolution, selection, and reason dimensions. `EvidenceContextAllocationResult` separately records allocation/inclusion and its reason. Invalid combinations fail closed and produce no successful EvidenceContextPackage.

Required missing context produces a deterministic fail-closed result declared by policy. No historical resolution may fall back to current repository state, latest evidence, a different semantic source, zero, neutral, summary, or fabricated content.

## Budget and Coverage Semantics

Silent truncation is prohibited.

Budget allocation is deterministic for fixed inputs and records exact accounting assumptions. Required content either fits with its required coverage or the build returns `INSUFFICIENT_BUDGET` or `REPLAN_REQUIRED`; the allocator may not conceal the failure by clipping bytes, dropping ranges, changing requiredness, or claiming partial content as complete.

`REPLAN_REQUIRED` is terminal for the current pre-model build attempt. It grants no authority to the allocator, compiler, Model Adapter, or arbitrary orchestrator logic. Only the already-authorized initiating consumer or a separately authorized planning service may request a successor plan/build.

Any successor that changes required context, optionality, coverage, source classes, ranking/priority semantics, budget policy, selection policy, task semantics, or output semantics creates new versioned intent, policy, or plan identities as applicable and a new `EvidencePreModelBuildRecord` linked to the prior failed build. Previous ledgers, allocation results, packages, prompts, and build records remain immutable. No replan may silently convert required context to optional context, lower coverage under the same policy identity, or hide repeated attempts under one deterministic identity.

Coverage is measured against exact source ranges and content hashes. It distinguishes:

- planned/considered coverage from selection-plan coordinates;
- eligible and resolved coverage from ledger dimensions;
- selected coverage from the selection dimension;
- included prompt coverage from allocation dimensions; and, only after a future invocation,
- successfully submitted and reviewed coverage.

Provider-call count, per-call input limit, aggregate token/rate/quota exposure, and synthesis requirements belong to later execution planning. A future execution planner must account for all of them; this ADR does not authorize naive chunk fan-out.

## Prompt Compilation

Prompt compilation is a pure build step over a finalized EvidenceContextPackage and versioned EvidencePromptPlan specification.

Trusted instructions and untrusted content are separate section classes. Context content is delimited and escaped according to a versioned compilation rule and remains data even when it contains phrases that resemble system or developer instructions.

The compiler must expose exact rendering, section provenance, compiler identity/version, canonicalization format/version, and hashes. It must not insert hidden model-specific instructions, silently omit sections, fetch additional context, or reinterpret authority. Any capability-specific compilation constraint is introduced through the explicit versioned constraint used by allocation and creates a new identifiable build artifact without changing the selection ledger.

## Authority and Security Boundaries

Authority classification and instruction trust are separate dimensions. A repository file may be authoritative evidence without being an instruction source. A PR body, external document, EvidenceSpan, source code comment, retrieved web page, or model response is untrusted context unless a governed policy explicitly classifies an exact source/revision as trusted instruction content.

Prompt text is not the security boundary. Runtime capability controls and consumer boundaries must prevent model output from requesting or performing:

- repository or canonical-record writes;
- schema or migration changes;
- arbitrary tool invocation;
- unauthorized network retrieval;
- configuration or credential mutation;
- governance-rule changes;
- architectural promotion; or
- direct canonical claim creation.

Existing Evidence Intelligence prompt-injection detection and forbidden-response-capability checks remain valid defense-in-depth behavior. They do not replace structural isolation, least-capability adapters, validation, or canonical service authority.

AI output remains an `ExtractionProposal`. Successful schema validation proves only conformance to the response contract; it does not prove truth, correctness, sufficiency, or authority.

### Data handling and retention

Before inclusion or durable persistence, every source reference must carry, or deterministically derive from governed source policy, a handling classification sufficient to decide whether its exact bytes may be processed, retained, and reconstructed. This boundary covers at least credentials/secrets, personal or sensitive material where applicable, licensed or restricted material, ephemeral/non-retainable content, and repository material later removed from current `HEAD`.

- Credentials and secrets must not be intentionally persisted in EvidencePromptArtifact history. Detection produces rejection or a fail-closed build according to policy; it does not authorize silent redaction followed by a claim of exact reconstruction.
- Artifact access controls and retention/deletion behavior must be at least as restrictive as the governing source classification and policy.
- Removal from current `HEAD` does not permit substitution with current content. Historical bytes may remain reconstructable only when their governing retention policy permits it.
- When exact required material cannot legally or safely be retained, the build records an explicit reconstruction-capability outcome such as `EXACT_RECONSTRUCTION_UNAVAILABLE`, the affected source identities, and stable reason codes. It never fabricates, summarizes, or substitutes content under the original identity.
- Hashes and metadata may be retained only to the extent permitted by the same policy and cannot be represented as proof that exact bytes remain reconstructable.

Exact historical pre-model reconstruction is therefore conditional, not universal. It is available only when all required exact historical material, compiler/canonicalization versions, and build artifacts may be retained and accessed under governing policy.

## Provenance, Reconstruction, and Re-execution

Hunter distinguishes six claims:

1. `source reconstruction`: retrieve the exact retained historical source bytes/renditions and coordinates; unavailable when governing retention policy prohibited their preservation.
2. `context-selection reconstruction`: reproduce the model-independent resolution and selection ledger from retained exact inputs and versions.
3. `exact pre-model reconstruction`: reconstruct byte-identical EvidencePromptArtifact content from retained historical sources, ledger, allocation inputs, prompt specification, compiler identity/version, and canonicalization format/version.
4. `submitted provider-request reconstruction`: retrieve the exact immutable provider-specific request artifact recorded by later Model Adapter architecture.
5. `historical response retrieval`: retrieve the immutable original response bytes, usage, parameters, validation outcome, and attempt provenance without calling a provider.
6. `provider re-execution`: submit an existing EvidencePromptArtifact through a later adapter as a new operational attempt; response equality is not claimed.

`Historical execution reconstruction` means inspecting the retained original pre-model, submitted-request, response, and attempt records. It is not re-execution and is available only for the portions that governing retention/security policy permitted Hunter to preserve. Provider re-execution is always a new operational attempt and never reconstructs the historical call.

Minimum persistable provenance, subject to governing source policy, includes intent, source identities/revisions/ranges/hashes, dimensional resolution and selection decisions, every non-inclusion reason, policy versions, allocation calculations, EvidencePromptPlan, exact EvidencePromptArtifact bytes/hashes when retainable, explicit reconstruction capability, and initiating run/workflow/task plus predecessor-build linkage. Later model integration must separately decide retention of provider/model identity, parameters, exact request, response, usage, failures, validation outcome, and attempt ordering.

Historical records are immutable while retained. A correction or reinterpretation appends a successor and preserves the original response and the state knowable at its original cutoff. Governed retention deletion may remove prohibited payload bytes without rewriting them; any permitted tombstone or surviving metadata records that exact reconstruction is unavailable and why. Current repository state is never substituted for an unavailable historical revision.

## First Migration Target

The only consumer authorized by this decision is Evidence Intelligence's existing provider seam. Migration is conceptual and separately implemented:

```text
ExtractionRequest
    -> EvidenceExtractionIntent (proposal-only evidence extraction)
    -> exact EvidenceSpan references + ExtractionSchema reference
    -> fixed/versioned EvidenceContextSelectionPolicy + EvidenceContextSelectionPlan
    -> EvidenceContextSelectionLedger
    -> one explicit versioned capability constraint
    -> EvidenceContextAllocationResult + EvidenceContextPackage
    -> EvidencePromptPlan
    -> EvidencePromptArtifact
    -> EvidencePreModelBuildRecord linked to the existing processing/workflow run identity
```

Phase 1 ends at that provider-free build record. It proves deterministic construction and reconstruction behavior without invoking `AIExtractionProvider`, producing a provider response, or changing canonical claim state.

The migration preserves these existing boundaries:

- `EvidenceSpan` remains the canonical evidence-span record and is referenced, not copied into a competing evidence authority.
- `ExtractionSchema` remains the consumer-owned structured output contract until a later accepted ADR changes it.
- `SecureAIProviderRunner` retains its existing provider-health, prompt-injection, forbidden-capability, and proposal-coordination responsibilities; Phase 1 neither replaces nor expands it.
- `ExtractionProposal` remains proposal-only.
- deterministic validation and the authorized claim-persistence service remain mandatory before any canonical `KnowledgeClaim` exists.

`ExtractionRequest` may initiate the Phase 1 adapter without changing its current contract, but its span tuple is request input only and cannot define the complete governed candidate set. Phase 1 must derive that set from, or validate it for exact equality against, the canonical Evidence Intelligence span inventory for the governed target before building the selection plan. The Evidence Intelligence pre-model foundation references or adapts `EvidenceSpan`, `ExtractionSchema`, `SecureAIProviderRunner`, `ExtractionProposal`, and deterministic claim validation/persistence; it does not replace their authority. `AIExtractionProvider` remains untouched until a later Model Adapter decision provides a migration path and must not be silently reinterpreted.

## Governance Non-Integration Guarantee

This ADR does not introduce AI into Hunter Governance Review.

The merge-critical path remains:

```text
deterministic repository context
    -> deterministic validators
    -> deterministic freshness and missing-evidence checks
    -> deterministic merge-status decision
```

No Evidence Intelligence pre-model contract, EvidencePromptArtifact, provider, model, or response defined or anticipated here is required for the `Hunter Governance Review` status. Existing zero-LLM architecture regression tests remain mandatory.

A later AI-assisted engineering reviewer may consume repository context and produce an advisory artifact only under a separate accepted ADR. It may not replace, approve, weaken, or become a dependency of deterministic governance, and external provider availability may not control merge status.

## Future Extension Boundaries

This foundation leaves Evidence Intelligence-scoped interfaces for later decisions without reserving generic ownership:

- A later Evidence Intelligence Model Adapter decision may consume exact `EvidencePromptArtifact` records plus capability requirements and produce immutable invocation, health, and failure records. It does not own context or prompt policy.
- Evidence Intelligence response validation may consume exact response bytes, `ExtractionSchema`, context/provenance references, and validator versions. It emits validation evidence and `ExtractionProposal` eligibility, not canonical truth.
- Historical Evidence Intelligence experimentation may compare policies, prompts, providers, and responses through new build and later invocation records without rewriting historical truth or promoting experimental output.
- A second real AI consumer receives no right to reuse, extend, or write these contracts. It triggers a new ADPR/ADR to compare both actual consumer boundaries, identify demonstrated commonality, and decide whether shared ownership is justified.
- Any later common abstraction must preserve historical Evidence Intelligence identities, define adapters and migration or supersession, and leave non-common contracts with their consumer owner.

## Explicit Non-Goals

This ADR does not define or authorize:

- a concrete external provider or model;
- provider routing, fallback, retry, health scoring, credentials, or cost policy;
- a tokenizer library or exact token-estimation algorithm;
- SQL tables, migration identifiers, package names, or deployment topology;
- detailed relevance weights or learned retrieval;
- semantic summarization or chunk synthesis;
- a production Prompt Builder implementation;
- generic Context Intelligence or Prompt Intelligence ownership;
- repository-wide AI intent, selection-ledger, prompt, or pre-model build contracts;
- a second AI consumer or automatic reuse of Evidence Intelligence contracts by one;
- Response Validator business rules;
- Review Engine 2.0;
- AI merge authority or any modification to Hunter Governance Review;
- direct AI mutation of repositories, schemas, configurations, or canonical records; or
- acceptance of ADR 0029 or ADR 0030.

## Migration and Adoption Sequence

Phase 1 proves only the provider-free Evidence Intelligence vertical slice defined above, using exact `EvidenceSpan` references, one fixed/versioned selection policy, one explicit capability constraint, deterministic allocation, canonical prompt compilation, and an immutable build record under the existing run/workflow identity.

Phase 1 does not require or authorize generic multi-source repository retrieval, provider routing, retry/failover, production credentials, provider response records, a full AI execution aggregate, a generic model registry, governance integration, Review Engine work, another AI consumer, or a second run/execution owner. It introduces only the Evidence Intelligence semantics necessary for that vertical slice.

After Phase 1 is independently verified, later separately scoped decisions may add Evidence Intelligence Model Adapter and response records or provider integration. Any second real AI consumer must begin with a new ADPR/ADR; shared contracts may be extracted only from demonstrated commonality between the two consumers, never inferred from this ADR. Hunter Governance Review is permanently excluded unless a separate accepted ADR explicitly changes that boundary without making provider availability part of merge authority.

## Verification Requirements

Future implementation must prove:

- orthogonal applicability/eligibility, resolution, selection, allocation, and reason dimensions;
- deterministic acceptance of legal dimension combinations and fail-closed rejection of illegal combinations;
- model-independent intent, resolution, plan, and `EvidenceContextSelectionLedger` identities;
- capability-specific allocation, package, prompt, and pre-model build identities;
- two capability constraints preserve the same upstream selection identity while permitting different downstream build identities;
- exact repository revision and evidence-version binding;
- complete canonical-candidate inventory coverage and stable reason codes for every non-included item;
- required missingness and historical cutoff behavior with no current/latest fallback;
- no silent truncation and fail-closed complete-prompt budget overflow, including all compiled prompt overhead;
- canonical Unicode, newline, path, collection, mapping, numeric, boolean/null, whitespace, source-range, and serialization behavior;
- compiler/canonicalization version changes produce explicitly different artifact identities;
- equal canonical inputs reconstruct byte-identical EvidencePromptArtifact bytes and digest when retention policy permits;
- runtime timestamps excluded from deterministic IDs;
- trusted instructions structurally separated from untrusted context;
- `REPLAN_REQUIRED` terminates the current build and every successor has explicit identity and predecessor lineage;
- replanning cannot weaken requiredness, coverage, source scope, or task/output semantics under an unchanged identity;
- credentials/secrets and non-retainable material follow fail-closed handling and never acquire a false exact-reconstruction claim;
- exact-reconstruction unavailability is explicit and never falls back to current or fabricated content;
- `EvidencePreModelBuildRecord` remains subordinate to the existing initiating execution/run/workflow owner and owns no attempts or responses;
- model responses and successful validation remain non-authoritative;
- correction lineage preserves original records and historical response truth;
- `EvidenceSpan`, `ExtractionSchema`, `SecureAIProviderRunner`, `ExtractionProposal`, and deterministic claim authority remain owned by Evidence Intelligence; and
- Hunter Governance Review retains no LLM/provider dependency.

Architecture regression tests must inspect the governance package and workflows, ownership dependencies, forbidden capability boundaries, and absence of prompt-time arbitrary retrieval. Contract and integration tests must reconstruct artifacts from persisted identities rather than from the current workspace. Verification applies incrementally: Phase 1 proves only the provider-free build obligations; provider request, response, validation, and re-execution obligations remain deferred with their architecture.

The minimum test classes are:

| Test class | Required invariants |
| --- | --- |
| Unit | Legal/illegal context-dimension combinations; stable reason codes; deterministic ordering and allocation arithmetic; canonical Unicode/newline/path/mapping/collection/numeric/boolean/null/whitespace serialization; compiler and canonicalization version sensitivity; exact EvidencePromptArtifact digest; runtime timestamp exclusion. |
| Contract | Resolver returns exact revision/range/hash coordinates; selector owns model-independent dimensions only; allocation cannot rewrite ledger truth; compiler cannot retrieve sources; build record requires an existing run/workflow/task owner and owns no attempts/responses; `EvidenceSpan` and `ExtractionSchema` semantics remain unchanged. |
| Integration | Two capacities share one selection-ledger identity and may produce different allocation/package/prompt/build identities; retained historical inputs reconstruct identical bytes; current/latest fallback is rejected; replan successors preserve lineage and cannot weaken semantics silently; secret/non-retainable material yields fail-closed handling and explicit reconstruction unavailability; Evidence Intelligence remains proposal-only. |
| Architecture regression | Governance `ContextManifest` remains separate from `EvidenceContextSelectionLedger`; Hunter Governance Review remains LLM/provider independent; no second top-level execution ontology appears; prompt compilation has no arbitrary repository/network retrieval; model/provider configuration cannot become upstream source authority; forbidden mutation/tool capabilities remain outside the model boundary. |

## Consequences

- Evidence Intelligence gains one canonical pre-model build boundary subordinate to existing execution ownership without prematurely selecting providers or storage technology.
- Context selection and omission become inspectable, versioned decisions rather than prompt-builder implementation details.
- Exact prompt reconstruction may require retaining more data than hash-only AI-provider records preserve and remains unavailable where governing source policy forbids that retention.
- Evidence Intelligence must define proposal authority and capability restrictions before requesting model-facing extraction work.
- Budget failure becomes an explicit build outcome and may make some tasks unavailable until an authorized successor plan exists.
- Provider and response architecture remain separate follow-up decisions, reducing initial scope but delaying production model integration.
- Evidence Intelligence gains a safe first migration path while preserving its existing proposal and claim-authority boundaries.
- Hunter avoids creating generic cross-consumer Context/Prompt owners before a second real consumer demonstrates commonality; later extraction will require a separate governed decision and explicit migration.
- Governance remains deterministic and insulated from provider availability, quota, billing, and model behavior.

## Alternatives Considered

### Build a simple prompt string utility

Rejected. It would hide retrieval, selection, omission, budgeting, authority, and replay inside string construction and could not explain why context was absent.

### Put the entire chain behind one AIService

Rejected. It would combine source authority, prompt rendering, provider execution, response validation, and downstream authority in one owner, violating explicit boundaries and repository-purity principles.

### Define Context Intelligence and Prompt Intelligence in separate ADRs now

Rejected for the foundation. Their boundary and shared identities must be decided together to prevent prompt compilation from absorbing selection. Provider execution and response semantics remain separate later decisions.

### Establish a generic cross-consumer Context and Prompt foundation now

Rejected. Evidence Intelligence is the only evidenced AI consumer, so repository-wide owners and generic identities would encode unproven commonality and create a durable abstraction surface without demonstrated reuse. A second real consumer is the reconsideration trigger; its new ADPR/ADR must compare both actual contracts before extracting any shared authority.

### Persist only hashes and regenerate content from current sources

Rejected. Current state cannot reconstruct historical prompt bytes, deleted or corrected evidence, historical omissions, or original responses. This would make exact reconstruction claims false. When retention policy prohibits exact historical bytes, Hunter records reconstruction unavailability rather than using current state.

### Allow silent truncation or best-effort context

Rejected. PR #200 demonstrated that disconnected size limits fail operationally, while Hunter's accepted missingness and replay rules prohibit hidden evidence loss.

### Use an LLM to select context

Rejected for this foundation. It would make pre-model execution non-deterministic and create an unrecorded model dependency before a model request could be explained. A future accepted ADR may define model-assisted advisory ranking only if deterministic policy, provenance, fallback, and non-authority boundaries remain intact.

### Migrate Hunter Governance Review first

Rejected. The current gate intentionally has no LLM dependency, and provider availability is not suitable as merge authority. Evidence Intelligence is the bounded, proposal-only first consumer.

## Supersession and Relationship to Existing ADRs

- ADR 0002 is extended and reaffirmed: AI context, prompt, and pre-model build artifacts inherit evidence-first provenance, missingness, and historical-reconstruction obligations.
- ADR 0004 is extended and reaffirmed: source authority and instruction trust are explicit prerequisites and untrusted content does not become instruction or authority.
- ADR 0009 is extended and reaffirmed: resolver, selector, budget allocator, compiler, adapter, validator, consumer service, and repository persistence remain separate responsibilities.
- ADR 0016 is reaffirmed: existence of an AI artifact, successful provider call, or successful validation does not promote output to canonical authority.
- ADR 0020 is extended and reaffirmed: historical source selection is strict-known, immutable, deterministic, and has no latest/current or neutral fallback.
- ADR 0029 remains Proposed and is neither accepted, amended, nor superseded by this ADR. Development Governance supplies the binding lifecycle used for this contribution.
- ADR 0030 remains Proposed and is neither accepted nor superseded. This ADR independently establishes only the Evidence Intelligence-specific pre-model foundation. If ADR 0030 later proceeds to acceptance, its implementation sequence and overlapping contract language must preserve this consumer scope and must not infer generic ownership from this Accepted decision.

No accepted ADR is superseded by this decision.
