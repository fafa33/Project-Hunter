# ADPR-0006: AI Context and Prompt Intelligence Foundation

## Metadata

- ADPR ID: `ADPR-0006`
- Status: `READY_FOR_REVIEW`
- Version: 1
- Author: Project Hunter Architecture Team
- Reviewers:
- Created: 2026-08-08
- Approved:
- Related Epic: not yet created
- Related Issue: [#206](https://github.com/fafa33/Project-Hunter/issues/206)
- Planned or produced ADR: [ADR 0031](../ADR/0031-ai-context-prompt-intelligence-foundation.md) (`Proposed`)
- Supersedes: not applicable
- Superseded by: not applicable
- Preparation self-assessment: `READY_FOR_ADR`
- Draft PR: [#207](https://github.com/fafa33/Project-Hunter/pull/207)
- ADR-bearing commit reviewed before this record: `964003e97dbef57c0ab005cfc400a0285c7642e2`
- Independent preparation audit: required and not yet performed

This record remediates formal review finding F-007. ADR 0031 was drafted before its mandatory permanent preparation record, contrary to the normal order in `docs/architecture-records/README.md`. This record makes that sequence deviation explicit, reconstructs the preserved decision basis from Issue #206, repository authority, the ADR draft, and its Technical Defense evidence, and does not retroactively approve either this preparation or ADR 0031.

This record authorizes no production implementation, provider integration, Phase 1 work, ADR status change, or modification to Hunter Governance Review.

## Executive Summary

Project Hunter has an Evidence Intelligence provider boundary but no canonical way to determine, explain, budget, serialize, and preserve the exact context and prompt presented to a future model. The evaluated options range from local prompt utilities and a monolithic AI service to deferral and a staged, provider-independent foundation.

The recommended option establishes deterministic Context Intelligence and Prompt Intelligence contracts through an exact pre-model build record. It keeps source and selection identity model-independent, permits capability-dependent allocation and prompt identities, preserves existing execution ownership, makes reconstruction conditional on retention policy, and leaves model invocation and response semantics to later decisions. Hunter Governance Review remains deterministic and zero-LLM. The first migration target is a narrow, provider-free Evidence Intelligence vertical slice.

The preparation self-assessment is `READY_FOR_ADR`: the architectural questions are resolved in proposed ADR 0031 and the earlier F-001 through F-006 Technical Defense findings were closed. This is not an independent audit verdict. An independent preparation audit remains mandatory before the preparation can be approved or the ADR can advance through formal acceptance.

## Problem Statement

### Current condition

Hunter has no active production LLM execution path. Evidence Intelligence exposes an injected `AIExtractionProvider`, stable evidence spans, a versioned extraction schema, and proposal-only provider outputs. It does not have canonical context resolution, selection, omission accounting, budgeting, prompt compilation, exact prompt identity, or a pre-model reconstruction record.

Without those contracts, a future AI-shaped execution could not reliably prove why it ran, which exact sources and versions were applicable, selected, omitted, or unavailable, which policies and capability constraints shaped the request, or which exact bytes were presented to a model.

### Desired condition

Define the smallest provider-independent foundation that deterministically constructs and records the complete pre-model decision, fails closed on missing required context and capacity failures, supports exact historical reconstruction only when governing retention policy permits, and leaves AI output non-authoritative.

### Decision required

Select the ownership, identity, persistence, determinism, security, replanning, reconstruction, and initial-migration boundaries for Context Intelligence and Prompt Intelligence without selecting a provider, implementing a model adapter, or changing an existing canonical authority.

### In scope

- Model-independent execution intent, context resolution, selection, omission, and selection-ledger identity.
- Capability-dependent allocation, context package, prompt plan, exact prompt artifact, and subordinate pre-model build identity.
- Canonical byte serialization and versioning requirements.
- Fail-closed replanning, security, retention, deletion, and reconstruction-unavailable semantics.
- Authority boundaries among sources, context selection, prompt compilation, future model invocation, response validation, and downstream consumers.
- Evidence Intelligence as the first narrow migration target.
- Deterministic, zero-LLM isolation for Hunter Governance Review.

### Out of scope

- Production code, schema migrations, provider credentials, provider selection, routing, retries, pricing, quotas, or deployment.
- Model Adapter, response-envelope, response-validation, or full interaction-aggregate architecture.
- Model-generated canonical claims, scores, recommendations, merge decisions, or governance authority.
- Phase 1 implementation or any later migration.
- Changes to ADR 0029, ADR 0030, or any accepted ADR.

## Problem Validation

The problem is architectural rather than a local implementation gap because it assigns authority and identity across retrieval, selection, allocation, compilation, execution ownership, persistence, replay, and downstream consumption. The Constitution, Project Principles, Development Governance, canonical Evidence Intelligence boundary, accepted ADRs 0002, 0004, 0009, 0016, and 0020, and the deterministic Governance Review contract were checked.

PR #200 supplied operational counterevidence to treating character limits, resolved evidence, or provider availability as sufficient execution control. Issue #206 defines the acceptance surface. Two targeted Technical Defense cycles tested the proposed contracts: the first identified F-001 through F-006; the revised ADR closed them; the exact-pair formal review then identified only the missing preparation artifact F-007 as blocking. Those reviews support, but do not replace, the independent ADPR audit required by the Architecture Audit Protocol.

ADRs 0029 and 0030 are `Proposed` and non-binding. Their descriptions may inform option discovery, but neither is governing authority for this preparation or ADR 0031.

## Motivation

Resolving the problem before provider integration prevents prompt construction from silently becoming a retrieval authority, prevents model configuration from rewriting source-selection history, and makes missingness and omission auditable. It also avoids false replay claims when source policy prohibits retaining historical bytes.

Deferral would leave Evidence Intelligence without a governed path from its existing stable spans and schemas to an exact provider request. Premature implementation would force ownership, identity, serialization, retention, and failure behavior into local code before those decisions had independent review.

## Existing Architecture

- The Project Constitution owns evidence authority, determinism, explicit boundaries, single ownership, explainability, and governance hierarchy.
- Accepted ADR 0002 owns evidence provenance, unavailable states, and historical evidence obligations.
- Accepted ADR 0004 owns trust-before-intelligence, source identity, reliability, conflict, and unavailable-state requirements.
- Accepted ADR 0009 separates service decisions, provider access, and mechanical repository persistence.
- Accepted ADR 0016 prevents stored or validated artifacts from promoting themselves to canonical authority.
- Accepted ADR 0020 requires immutable, strict-known selection with exact source identity and no current/latest fallback.
- Evidence Intelligence owns `EvidenceSpan`, `ExtractionSchema`, `AIExtractionProvider`, `ExtractionProposal`, deterministic proposal validation, and eventual canonical claim persistence through its existing owner. Provider output remains proposal-only.
- Existing pipeline, workflow, or task execution identity remains the top-level owner. No AI-specific pre-model artifact may create a competing execution ontology.
- Hunter Governance Review owns a repository-governance `ContextManifest`, which records canonical documents and ADR/ADPR references resolved at an exact commit. It is not an AI context-selection ledger.
- Hunter Governance Review is deterministic and has no LLM/provider dependency.
- No current component owns the proposed AI context-selection ledger, capability allocation, canonical prompt bytes, or subordinate pre-model build record.

## Constraints

### Constitutional

- Missing, unknown, and conflicting context must remain explicit.
- Equal governed inputs must produce equal authoritative pre-model decisions.
- Each concept must have one canonical owner and explicit boundaries.
- Every meaningful downstream use must preserve evidence, reasoning, uncertainty, and provenance.
- This decision must follow the governance hierarchy and cannot derive authority from a Proposed ADR.

### Governance and accepted ADRs

- Development Governance requires preparation, independent audit, ADR review, exact-pair review, and final validation as distinct gates.
- ADRs 0002, 0004, 0009, 0016, and 0020 constrain provenance, trust, service/repository separation, non-promotion, and strict-known replay.
- ADRs 0029 and 0030 remain Proposed, non-binding, and unchanged.
- This ADPR and ADR 0031 may extend and reaffirm accepted contracts but may not implicitly amend or supersede them.

### Technical

- Context applicability, eligibility, resolution, selection, allocation/inclusion, and reason are orthogonal dimensions with deterministic legal combinations.
- Source and selection identities must not depend on a selected model capability.
- Allocation, package, prompt, and build identities may depend on capability constraints.
- Prompt bytes require an explicit, versioned canonicalization and serialization contract.
- Runtime timestamps must not contaminate deterministic identity.

### Operational

- Required context failure and capacity overflow fail closed.
- `REPLAN_REQUIRED` terminates the current build; only the initiating owner or its explicit delegate may authorize a successor.
- Provider availability, quota, billing, or model behavior cannot become merge or governance authority.
- Observability must distinguish build failure, reconstruction unavailability, and future provider failure.

### Persistence and migration

- Persisted artifacts are immutable and corrections append lineage rather than rewriting history.
- A pre-model build record is subordinate to an existing run, workflow, or task owner.
- Migration is additive and starts with a provider-free Evidence Intelligence vertical slice.
- Storage technology, concrete schema, and production activation remain implementation decisions after acceptance.

### Replay and historical reconstruction

- Historical source selection is strict-known and never falls back to current/latest content.
- Exact reconstruction means reconstructing the pre-model artifact bytes, not reproducing a model response.
- If required historical bytes cannot lawfully be retained, the result must be explicitly reconstruction-unavailable.
- Re-execution is a distinct later operation requiring exact provider, model, configuration, and response contracts.

### Compatibility

- Existing Evidence Intelligence types and proposal/canonical-authority boundaries remain owned by Evidence Intelligence.
- Governance `ContextManifest` and `AIContextSelectionLedger` remain separate in owner, purpose, schema, and lifecycle.
- No second top-level execution owner is introduced.

### Security and privacy

- Credentials, secrets, authentication material, and prohibited source content cannot enter prompt artifacts.
- Exact prompt persistence is allowed only under the governing source-data handling policy.
- Retention, deletion, legal hold, and reconstruction availability must be explicit per artifact and constituent source.
- Untrusted context cannot become instructions, tools, repository access, or configuration mutation rights.

### Performance and scalability

- A capability constraint must be explicit before allocation.
- Allocation uses deterministic arithmetic, ordering, and overflow behavior; silent truncation is prohibited.
- The foundation must permit different downstream builds from one model-independent selection without re-resolving or reselecting sources.

### Evidence and provenance

- Exact source revision, span/range, content digest, trust classification, policy versions, reason codes, and predecessor lineage must be recorded where applicable.
- Hashes prove identity but do not substitute for retained historical bytes.
- AI outputs remain proposals or recommendations until an existing deterministic or human authority accepts them through a separately authorized boundary.

## Evidence Inventory

| ID | Evidence | Authority/source | Finding | Quality and limitations | Supports or challenges |
|---|---|---|---|---|---|
| E-001 | Evidence, determinism, integrity, ownership, explainability, and governance rules | `docs/PROJECT_CONSTITUTION.md` | The pre-model path must be evidence-backed, reproducible, explicitly owned, explainable, and governed. | Highest internal authority; constrains but does not select a component design. | Supports deterministic separated foundation; challenges opaque or duplicated authority. |
| E-002 | Evidence-first, trust-before-intelligence, deterministic-default, no-fabrication, maintainability principles | `docs/PROJECT_PRINCIPLES.md` | Context must preserve trust, missingness, provenance, and deterministic behavior. | Canonical principles; architectural details remain for the ADR. | Supports fail-closed deterministic selection and reconstruction. |
| E-003 | Provenance, unavailable states, trust, service boundaries, non-promotion, strict-known replay | Accepted ADRs 0002, 0004, 0009, 0016, and 0020 | Existing authority already constrains the proposed foundation and must not be duplicated or weakened. | Binding architectural authority; none owns the new pre-model contracts. | Supports the recommended boundaries. |
| E-004 | Current AI provider and claim-authority boundary | `docs/EVIDENCE_INTELLIGENCE_LAYER.md` | Providers receive stable spans and schemas; outputs are proposals; deterministic validation and claim persistence retain authority. | Canonical description of the first consumer; production phase remains in progress. | Supports Evidence Intelligence as a bounded first migration target. |
| E-005 | Governance context contract | `scripts/hunter_governance_review/contracts.py` | Governance `ContextManifest` proves which governing records resolved at an exact commit. | Direct implementation evidence; its name alone could invite a collision if the AI ledger were not distinguished. | Challenges shared naming/ownership; supports explicit separation. |
| E-006 | Governance implementation and regression contract | `docs/HUNTER_GOVERNANCE_REVIEW.md`, `.github/workflows/hunter-governance-review.yml`, and governance tests | Governance Review is deterministic, repository-native, and provider-independent. | Direct documentation/workflow/test evidence; external required-status publication remains repository configuration. | Requires zero-LLM isolation. |
| E-007 | PR #200 operational review evidence | [PR #200](https://github.com/fafa33/Project-Hunter/pull/200) | Character limits were not a token budget; lossless chunking could exhaust quotas; resolved evidence did not prove model review; provider state was unsuitable as merge authority. | Historical operational evidence, not canonical architectural authority; conditions may differ for later providers. | Challenges ad hoc prompt building and provider-dependent governance. |
| E-008 | Governing acceptance surface | [Issue #206](https://github.com/fafa33/Project-Hunter/issues/206) | Defines ADR 0031 scope, criteria, zero-LLM constraint, Evidence Intelligence target, and non-goals. | Governing issue; acceptance still pending. | Supports scope and traceability. |
| E-009 | First and second Technical Defense artifacts | Review evidence associated with ADR 0031 preparation | F-001 through F-006 exposed and then verified context dimensions, identity layering, ownership, byte determinism, replanning, and retention/reconstruction. | Targeted review evidence supplied for this decision; not a substitute for an independent ADPR audit. | Supports revised contracts; challenges the original draft. |
| E-010 | Exact-pair hostile review of ADR-bearing commit `964003e97dbef57c0ab005cfc400a0285c7642e2` against `8dfd663ddf1db7a7b54bdd46eedca8aac0d36ff0` | Formal review artifact for Draft PR #207 | No new substantive architecture blocker was identified; F-007 found the mandatory ADPR/preparation audit missing. | Exact-pair review evidence; invalidated for Draft-to-Ready purposes by this new commit and cannot serve as the independent audit of this ADPR. | Supports the architecture basis and requires this remediation plus re-review. |
| E-011 | Proposed decision contract | `docs/ADR/0031-ai-context-prompt-intelligence-foundation.md` | Specifies owners, identities, byte contract, replanning, retention, reconstruction, governance isolation, and Phase 1 scope. | Proposed and non-binding until formal acceptance. It is evidence of the prepared decision, not governing authority. | Provides the exact ADR relationship this record evaluates. |

## Assumptions

| ID | Assumption | Rationale | Confidence | Falsification condition | Consequence if false |
|---|---|---|---|---|---|
| A-001 | The first implementation can bind each pre-model build to an existing canonical run, workflow, or task identity. | Hunter already executes work through existing pipeline/workflow concepts; the ADR prohibits a second owner. | High | Phase 1 discovery finds no stable initiating identity with immutable reference semantics. | Phase 1 is blocked pending a separate ownership decision; the build record must not invent an owner. |
| A-002 | Governing source owners can supply or reference a data-handling classification sufficient to decide prompt retention. | Existing sources already have owners; the proposed foundation consumes, rather than invents, handling authority. | Medium | Any in-scope source has no authoritative handling policy or classification. | That source cannot enter a retainable exact prompt; the build fails closed or records explicit reconstruction unavailability as policy directs. |
| A-003 | One fixed capability constraint is sufficient to validate the provider-free Phase 1 allocation and compilation path. | Phase 1 intentionally excludes provider selection and invocation. | High | Exact allocation cannot be tested without choosing a provider or unresolved tokenizer semantics. | Phase 1 remains blocked until a separately accepted capability/adapter decision supplies the missing constraint; no provider is selected implicitly. |

These assumptions do not grant authority. Their failure invokes a fail-closed or separately governed decision path.

## Architectural Dimensions

- **Authority and ownership:** source owners, resolver, selector, allocator, compiler, future adapter, validator, consumer, and canonical downstream authority must remain distinct.
- **Context semantics:** applicability, eligibility, resolution, selection, allocation/inclusion, and reason codes must form deterministic legal combinations.
- **Identity layering:** intent, resolution, and selection identities are model-independent; allocation, package, prompt, and build identities are capability-dependent.
- **Execution ownership:** the build record is subordinate to an existing execution owner and owns no attempts or responses.
- **Persistence and correction:** immutable records, append-only correction, exact lineage, and mechanical persistence are required.
- **Canonical bytes:** Unicode, newlines, paths, collections, mappings, numbers, booleans/null, whitespace, source ranges, ordering, and serialization need versioned rules.
- **Replanning:** failure is terminal for the current build; authorized successors preserve predecessor lineage and cannot silently weaken semantics.
- **Security and trust:** source trust and instruction trust are separate; secrets and prohibited data never enter prompts.
- **Retention and reconstruction:** policy controls byte retention; reconstruction unavailability is explicit and cannot fall back to current content.
- **AI non-authority:** provider output cannot become a canonical claim, score, recommendation, or governance decision by existence or validation alone.
- **Governance isolation:** Governance Review remains deterministic and its `ContextManifest` remains separate.
- **Migration and reversibility:** the first slice is narrow, additive, provider-free, and removable without changing current production behavior.
- **Testability:** legal-state validation, deterministic identities, byte equality, strict-known lookup, dependency boundaries, and forbidden capabilities must be machine-verifiable.

## Candidate Options

### Option 1: Consumer-local prompt utility

- Description: Each consumer retrieves context and builds a prompt string locally.
- Authority and ownership: Consumer implicitly owns resolution, selection, allocation, and compilation.
- Boundaries: Few explicit boundaries; model configuration can leak into source selection.
- Persistence and replay: Optional local logging; no canonical pre-model record.
- Evidence and provenance: Depends on consumer discipline and is not uniform.
- Compatibility: Easy to add without changing existing services.
- Advantages: Lowest initial implementation effort.
- Disadvantages: Duplicated authority, opaque omission, inconsistent security and identity.
- Failure modes: Silent truncation, current-state regeneration, missing lineage, false replay.
- Migration implications: Every consumer later requires bespoke migration.
- Reversibility: Code is removable, but historical executions remain unreconstructable.
- Open dependencies: Every consumer must invent policy, serialization, and retention.

### Option 2: Monolithic AI service with model-assisted selection

- Description: One service retrieves, selects, budgets, prompts, invokes, validates, and returns AI output; an LLM may rank context.
- Authority and ownership: One AI owner absorbs source, execution, and response responsibilities.
- Boundaries: Provider, tool, retrieval, compiler, validator, and consumer boundaries collapse.
- Persistence and replay: A single interaction log may be stored, but upstream and downstream identities are entangled.
- Evidence and provenance: Model behavior influences selection before that behavior can itself be explained.
- Compatibility: Conflicts with accepted separation and non-promotion contracts.
- Advantages: Simple caller interface and potentially rapid provider integration.
- Disadvantages: Duplicated source authority, nondeterministic pre-model state, large security surface.
- Failure modes: Provider outage blocks all behavior; prompt injection gains capabilities; validated output appears authoritative.
- Migration implications: Requires centralizing current consumer and governance paths.
- Reversibility: Low because persistence and ownership are entangled.
- Open dependencies: Provider, model, tool, response, and canonical-consumer decisions are all premature.

### Option 3: Separate Context and Prompt decisions immediately

- Description: Decide Context Intelligence and Prompt Intelligence in independent ADRs before fixing their shared handoff.
- Authority and ownership: Each side has a named owner, but the selection/package boundary is initially unresolved.
- Boundaries: Clear locally; shared identity and omission semantics may diverge.
- Persistence and replay: Each side may persist separately without a complete reconstruction chain.
- Evidence and provenance: Context lineage can be preserved, but prompt lineage may reinterpret it.
- Compatibility: Potentially compatible if both decisions later converge.
- Advantages: Smaller individual ADRs and independent implementation cadence.
- Disadvantages: The compiler may absorb allocation or selection decisions during the gap.
- Failure modes: Duplicate plans, incompatible identities, missing cross-boundary reason semantics.
- Migration implications: Requires coordination and likely an integration ADR before use.
- Reversibility: Medium; local contracts can change before acceptance.
- Open dependencies: Shared package identity, byte contract, and terminal failure ownership remain unresolved.

### Option 4: Staged provider-independent Context and Prompt foundation

- Description: Decide the shared pre-model chain together, ending at exact prompt bytes and a subordinate build record; defer model and response architecture.
- Authority and ownership: Resolver, selector, ledger, allocator, compiler, existing execution owner, future adapter/validator, and downstream authority are explicit and non-overlapping.
- Boundaries: Model-independent selection precedes capability-dependent allocation and prompt compilation.
- Persistence and replay: Immutable ledgers/artifacts and exact identities support reconstruction when retention policy permits; otherwise unavailability is explicit.
- Evidence and provenance: Exact source, policy, reason, constraint, compiler, canonicalization, and predecessor lineage is required.
- Compatibility: Extends and reaffirms accepted ADRs without changing current Evidence Intelligence or Governance authority.
- Advantages: Deterministic, testable, provider-neutral, secure, and incrementally migratable.
- Disadvantages: More up-front contract work and no immediate model invocation.
- Failure modes: Policy gaps can block builds; retained bytes increase handling obligations; excess abstraction could outrun the first slice.
- Migration implications: Start with one provider-free Evidence Intelligence vertical slice; later decisions add adapter and response contracts.
- Reversibility: High before production persistence; additive interfaces do not change current runtime behavior.
- Open dependencies: Concrete storage, first capability constraint, Model Adapter, and response architecture remain later governed work.

### Option 5: Defer all foundation work until a provider is selected

- Description: Wait for a concrete provider/model and derive context and prompt contracts from its API.
- Authority and ownership: Remain undefined until the adapter decision.
- Boundaries: Provider constraints are likely to shape upstream selection and identity.
- Persistence and replay: Not available before the later decision.
- Evidence and provenance: Existing Evidence Intelligence lineage remains, but no exact provider-request lineage exists.
- Compatibility: Avoids immediate repository changes but risks provider coupling.
- Advantages: Uses concrete tokenizer and API evidence.
- Disadvantages: Leaves the architectural gap unresolved and invites implementation-first decisions.
- Failure modes: Provider-specific prompt and token semantics become canonical by accident.
- Migration implications: Evidence Intelligence cannot begin even a provider-free build slice.
- Reversibility: High while deferred; lower after provider-shaped implementation begins.
- Open dependencies: Entire decision depends on provider procurement and later architecture.

### Option 6: Migrate Hunter Governance Review first

- Description: Use the new context/prompt path and an LLM inside Governance Review before Evidence Intelligence.
- Authority and ownership: Provider behavior becomes a dependency of repository merge governance.
- Boundaries: Governance evidence resolution and AI context selection risk collision.
- Persistence and replay: Repository evidence can be pinned, but model output and provider availability remain nondeterministic.
- Evidence and provenance: Model review may supplement evidence but cannot prove complete deterministic coverage.
- Compatibility: Violates the zero-LLM governance constraint and current regression contract.
- Advantages: Could add advisory prose to review findings.
- Disadvantages: Quota, billing, outage, model drift, and prompt injection affect governance.
- Failure modes: False approval, unavailable required status, or non-reproducible merge authority.
- Migration implications: Requires modifying a protected governance subsystem before a bounded consumer.
- Reversibility: Low once required status depends on the provider path.
- Open dependencies: Provider, model, review-completeness, and authority contracts are unresolved.

## Comparative Analysis

| Criterion | Option 1: Local utility | Option 2: Monolith | Option 3: Split now | Option 4: Staged foundation | Option 5: Defer | Option 6: Governance first |
|---|---|---|---|---|---|---|
| Correctness | Low | Low | Medium | High | Not resolved | Low |
| Constitutional compliance | Low | Low | Medium | High | Neutral while deferred | Low |
| Governance compliance | Low | Low | Medium | High, subject to independent audit | Neutral while deferred | Unacceptable |
| Authority clarity | Low | Low | Medium | High | None | Low |
| Replayability | Low | Medium but entangled | Medium | High when policy permits | None | Low |
| Evidence integrity | Low | Low | Medium | High | Existing evidence only | Low |
| Maintainability | Low | Low | Medium | High | Medium before implementation | Low |
| Scalability | Low across consumers | Central bottleneck | Medium | High through layered identities | Unknown | Provider-bound |
| Operational complexity | Hidden and duplicated | High and centralized | Medium | Medium and explicit | Deferred | High governance risk |
| Migration risk | High long-term | High | Medium | Low through narrow slice | High once work starts | Unacceptable |
| Implementation effort | Low initially | High | Medium | Medium | None now | High |
| Reversibility | Low historical | Low | Medium | High before activation | High while deferred | Low |
| Long-term extensibility | Low | Low | Medium | High | Unknown | Low |

Option 4 is the only option that satisfies every fixed authority, determinism, security, retention, governance-isolation, and migration constraint without selecting a provider or inventing a second execution owner.

## Falsification Results

| Option | Invalidation test or counterexample | Result |
|---|---|---|
| 1 | Can two consumers produce identical source selection, omission reasons, canonical bytes, and historical reconstruction without a shared owner? | Failed. Local utilities duplicate policy and cannot guarantee shared identity or replay. |
| 2 | Can one service own retrieval through response without colliding with accepted service/source/canonical authorities or making pre-model selection nondeterministic? | Failed. Ownership collision and model-assisted selection violate fixed constraints. |
| 3 | Can Context and Prompt be decided separately while preventing either side from owning allocation and shared artifact identity? | Failed for the foundation stage. The unresolved handoff is itself the architectural decision. |
| 4 | Do two capabilities preserve one upstream ledger identity while allowing different downstream identities? | Survived. The layered identity contract requires exactly this test. |
| 4 | Can required missing context, budget overflow, secret input, or retention-prohibited bytes fail without silent weakening or false replay? | Survived. Terminal build outcomes and explicit reconstruction unavailability are mandatory. |
| 4 | Could the build record become a second execution owner or the AI ledger become Governance `ContextManifest`? | Survived. Both collisions are explicitly prohibited and architecture-regression tested. |
| 4 | Could replanning silently reduce required coverage under the same task identity? | Survived. The current build terminates, authority is explicit, predecessor lineage is preserved, and semantic/policy changes require new identities. |
| 4 | Could an exact prompt digest be claimed without canonical bytes or retainability? | Survived. Serialization is versioned, and byte reconstruction is claimed only when source policy permits retention. |
| 5 | Does deferral prevent provider semantics from becoming upstream architecture? | Failed prospectively. It supplies no boundary when implementation starts and leaves the validated problem unresolved. |
| 6 | Can Governance Review remain deterministic and available when provider quota, billing, network, or model behavior changes? | Failed. Provider dependence contradicts the fixed zero-LLM constraint. |

The first Technical Defense falsified earlier forms of Option 4 on six dimensions: an invalid single context state machine, entangled identities, ownership collisions, incomplete byte determinism, unsafe replanning, and false retention/replay claims. Revised ADR 0031 introduced orthogonal dimensions, layered identities, explicit ownership subordination, a versioned byte contract, terminal lineage-preserving replanning, and policy-controlled reconstruction. The targeted second defense verified F-001 through F-006 as resolved. This preparation records that evidence but leaves its independent audit to a different reviewer.

## Rejected Options

- **Option 1, consumer-local prompt utility:** rejected because it hides or duplicates authority and cannot provide uniform deterministic reconstruction. Reconsider only for ephemeral, non-Hunter prototypes that create no repository artifact or production behavior.
- **Option 2, monolithic AI service:** rejected because it collapses accepted boundaries and makes model behavior upstream of its own explanation. Reconsider only if future accepted architecture replaces the current ownership hierarchy, which this decision does not propose.
- **Option 3, separate foundation decisions now:** rejected because the selection/allocation/compiler handoff and identity layering must be fixed together. Reconsider for Model Adapter and Response Validator, which are deliberately later decisions with separable ownership.
- **Option 5, defer until provider selection:** rejected because provider choice must not define upstream source authority or identity. Reconsider only if independent evidence falsifies the ability to express a provider-neutral capability constraint.
- **Option 6, migrate Governance Review first:** rejected because Governance Review must remain deterministic and zero-LLM. Reconsideration requires a separate governance amendment through the full constitutional process; it is explicitly outside this decision.
- **Hash-only persistence with regeneration from current sources:** rejected within every option because it creates false historical reconstruction. Reconsider only where no exact-reconstruction claim is made and explicit unavailability is acceptable.
- **Silent truncation or best-effort required coverage:** rejected within every option because it conceals missingness and changes task semantics. No reconsideration condition exists under current accepted authority.

## Risks

| Risk | Category | Likelihood | Impact | Mitigation | Residual uncertainty |
|---|---|---|---|---|---|
| Foundation abstractions outrun the first consumer | Maintainability | Medium | Medium | Limit Phase 1 to one Evidence Intelligence vertical slice and defer adapter/response architecture. | Later consumers may require new contracts through separate ADRs. |
| Source handling policy is absent or inconsistent | Security/governance | Medium | High | Fail closed; require source-owner classification; record reconstruction unavailability. | Concrete policy coverage is implementation evidence not yet collected. |
| Capability/token arithmetic is provider-specific | Technical | Medium | Medium | Require an explicit versioned capability constraint; do not infer provider behavior. | A later Model Adapter ADR may need tokenizer-specific contracts. |
| Exact byte retention increases sensitive-data exposure | Security/operations | Medium | High | Exclude secrets, inherit source policy, record retention/deletion/legal-hold state, minimize retained scope. | Storage controls and retention durations remain implementation-specific. |
| Replanning creates identity explosion or confusing lineage | Operational | Low | Medium | Terminal predecessor, one authorized successor path, stable reason codes, explicit semantic/policy version changes. | Concrete operator ergonomics remain untested. |
| AI artifacts are mistaken for canonical intelligence | Authority | Medium | High | Preserve proposal-only outputs and existing deterministic/human authority boundary; architecture regression tests. | Future consumers require separate authority review. |
| Governance and AI context types converge by naming | Ownership | Low | High | Distinct owner, purpose, schema, lifecycle, package, and regression tests. | Future refactors must preserve separation. |
| Retrospective preparation conceals missing contemporaneous reasoning | Governance | Medium | High | Record the sequence deviation, evidence limitations, exact prior commit, and mandatory independent audit. | Independent audit may require revision or find preparation incomplete. |

## Open Questions

| Question | Blocking? | Owner | Required evidence or action | Status |
|---|---|---|---|---|
| Does this reconstructed preparation fairly and completely represent the decision basis? | Yes for preparation approval; no for author self-assessment | Independent architecture auditor | Audit ADPR-0006 under `docs/ARCHITECTURE_AUDIT_PROTOCOL.md` and the audit template. | Open; deliberately not performed in this task. |
| Which existing execution identity will the first build reference? | No for ADR; yes before Phase 1 implementation | Future Phase 1 planner | Identify and test the exact existing run/workflow/task record; open a separate decision if none is sufficient. | Deferred. |
| Which source-owner handling policies permit exact prompt-byte retention? | No for ADR; yes per source before use | Source owners and security reviewer | Supply authoritative classification, retention, deletion, and legal-hold rules. | Deferred and fail-closed. |
| What exact fixed capability constraint will Phase 1 use? | No for ADR; yes before Phase 1 implementation | Future Phase 1 planner | Select a provider-neutral test constraint without choosing a model adapter. | Deferred. |
| What provider, response, and re-execution contracts apply later? | No | Future Model Adapter/Response ADR owner | Perform separate architecture preparation after this foundation is accepted. | Out of scope. |

## Constitution Review

- **Rule 1:** A traceable pre-model path supports trustworthy evidence-driven decision support; it does not treat AI volume as value.
- **Rule 2:** Missing, conflicting, excluded, and non-retainable context remain explicit; AI output is non-authoritative.
- **Rule 3:** Deterministic selection, allocation, canonical bytes, policy versions, and strict-known reconstruction preserve reproducibility where lawful retention permits.
- **Rule 4:** Resolver, selector, allocator, compiler, execution owner, future adapter/validator, and downstream authority are explicit.
- **Rule 5:** The AI ledger does not duplicate Governance `ContextManifest`, and the build record does not duplicate execution ownership.
- **Rule 6:** Exact sources, policies, omissions, reasons, identities, bytes or explicit unavailability, and lineage make the pre-model decision explainable.
- **Rule 7:** Versioned contracts, additive migration, and deferred provider architecture preserve maintainability and evolution.
- **Rule 8:** This record restores the mandatory preparation artifact, records the order deviation, and requires independent audit and ADR acceptance before implementation.
- **Rule 9:** No constitutional change is proposed.

Determination: compatible, subject to independent preparation audit. No constitutional conflict is known.

## Governance Review

- Development Governance Stage 1 and the Architecture Decision Preparation Guide apply because the decision creates new architectural ownership, persistence, replay, and subsystem boundaries.
- This record follows the canonical ADPR template, uses the next monotonic identifier, and is registered in `docs/architecture-index.md`.
- The normal create-preparation-before-ADR order was violated. The deviation is not hidden: ADR 0031 remains Proposed, no implementation has begun, and this record must pass an independent preparation audit before formal ADR acceptance proceeds.
- Accepted ADRs 0002, 0004, 0009, 0016, and 0020 are extended or reaffirmed without changing their owners or guarantees.
- ADRs 0029 and 0030 are Proposed and non-binding; neither is amended, superseded, or treated as authority.
- `AIContextSelectionLedger` is distinct from Governance `ContextManifest`; `AIPreModelBuildRecord` is subordinate to existing execution identity.
- Hunter Governance Review remains deterministic and zero-LLM. This decision does not modify its code, workflow, status publisher, or authority.
- Evidence Intelligence remains proposal-only and is only the first future migration target. No Phase 1 implementation is authorized.
- The Architecture Audit Protocol, not this self-assessment, controls the independent preparation verdict.

Determination: governance-compatible as a remediation contribution, with independent ADPR audit and renewed exact-pair contribution review still outstanding.

## Quality Assessment

| Dimension | Rating | Evidence and rationale | Blocking limitation |
|---|---|---|---|
| Problem correctness | EXCELLENT | The gap is grounded in current Evidence Intelligence and missing pre-model ownership, not an implementation preference. | None identified. |
| Scope completeness | EXCELLENT | In/out scope, later boundaries, first migration, and prohibited governance changes are explicit. | None identified. |
| Canonical consistency | GOOD | Constitution, Principles, accepted ADRs, Evidence Intelligence, and governance ownership were checked; Proposed ADRs are non-binding. | Independent audit required. |
| Evidence integrity | GOOD | Repository authority, implementation evidence, operational evidence, issue criteria, defense evidence, and limitations are distinguished. | Retrospective review artifacts require independent confirmation. |
| Assumption discipline | GOOD | Three implementation-facing assumptions have confidence, falsification, and fail-closed consequences. | None identified for the ADR decision. |
| Option completeness | GOOD | Six materially distinct ownership/sequencing options plus cross-cutting persistence alternatives are represented. | None identified. |
| Comparative fairness | GOOD | Every option uses the same fields and common criteria, including advantages, costs, failure, migration, and reversibility. | None identified. |
| Falsifiability | GOOD | Every option and the six corrected defense dimensions have explicit invalidation tests. | Independent auditor must challenge the results. |
| Authority and ownership clarity | EXCELLENT | Source, resolver, selector, ledger, allocator, compiler, execution, governance, future adapter/validator, and downstream owners are separated. | None identified. |
| Persistence and replay quality | EXCELLENT | Layered identity, immutable lineage, byte retention policy, strict-known reconstruction, and unavailability are resolved. | Concrete storage remains intentionally open. |
| Evidence and provenance quality | GOOD | Exact source/policy/reason/capability/compiler/canonicalization lineage is required; hashes do not substitute for bytes. | None identified. |
| Operational quality | GOOD | Terminal failure, replanning authority, quota isolation, retention/deletion, and observability states are defined. | Deployment and provider operations are later decisions. |
| Implementation and migration impact | GOOD | A narrow provider-free Evidence Intelligence slice is defined; code, storage, adapter, and activation are deferred. | Phase 1 plan not yet created by design. |
| Testability and validation | EXCELLENT | Legal dimensions, layered identities, byte equality, strict-known behavior, forbidden authority, and isolation have deterministic tests. | Tests do not exist because implementation is prohibited at this stage. |
| Maintainability and extensibility | GOOD | Shared foundation avoids consumer duplication while adapter/response concerns remain separable. | Must resist speculative expansion beyond Phase 1. |
| Risk quality | GOOD | Governance, security, authority, operational, and retrospective-preparation risks include mitigation and uncertainty. | Independent audit may identify additional risks. |
| Traceability | GOOD | Issue, ADPR, Proposed ADR, Draft PR, exact prior review pair, and missing audit are recorded. | New contribution commit and independent audit artifact do not yet exist inside this record. |

No mandatory dimension is self-rated below `GOOD`. This is an author self-assessment only.

## Architecture Readiness

- Outcome: `READY`
- Rationale: The problem, authority, constraints, option set, comparison, falsification, identity, determinism, security, retention, failure, and migration boundaries are sufficiently resolved for the proposed decision.
- Missing evidence: No material evidence is known to be missing for ADR drafting; concrete source handling policies, execution identity, storage, and capability constraint are required before Phase 1 implementation, not to choose this architecture.
- Unresolved conflicts: None known. Independent preparation audit remains required and may reject this determination.

## ADR Readiness

- Outcome: `READY_FOR_ADR`
- Proposed ADR title: ADR 0031: AI Context and Prompt Intelligence Foundation
- Proposed ADR scope: Deterministic provider-independent context resolution, selection, allocation, prompt compilation, exact pre-model build identity/reconstruction, security/retention, authority isolation, and a narrow Evidence Intelligence first migration target.
- Decisions the ADR must fix: Orthogonal context dimensions; identity layering; owner boundaries; canonical byte contract; terminal replanning; retention/deletion/reconstruction semantics; AI non-authority; zero-LLM Governance Review; Phase 1 scope.
- Matters the ADR must leave open: Provider/model selection, adapter/routing/retry behavior, response contracts and re-execution, storage technology, production schemas, credentials, deployment, and later consumers.

This outcome is a preparation-author self-assessment. It is not `APPROVED`, does not accept ADR 0031, and does not authorize implementation.

## Final Recommendation

Recommend Option 4, the staged provider-independent Context and Prompt foundation expressed by Proposed ADR 0031. It is the only evaluated option that closes the validated pre-model architecture gap while preserving accepted ownership, deterministic governance, strict-known history, non-authoritative AI output, policy-controlled reconstruction, and incremental migration.

Submit ADPR-0006 to an independent architecture preparation audit. Only after that audit resolves any material findings should ADR 0031 resume the formal exact-pair acceptance process. Do not implement Phase 1 or change ADR status as part of preparation review.

## Decision History

| Date | State | Change | Author or reviewer |
|---|---|---|---|
| 2026-08-08 | READY_FOR_REVIEW | Created ADPR-0006 to remediate F-007, reconstructed the preserved decision basis, recorded the sequence deviation, and linked Proposed ADR 0031 without changing its decision or status. | Project Hunter Architecture Team |

## Traceability

- Epic: not yet created.
- Issue: [#206](https://github.com/fafa33/Project-Hunter/issues/206).
- Preparation working document: no separate repository artifact; the decision basis was developed in the ADR 0031 audit and defense sequence associated with Issue #206 and Draft PR #207.
- Checklist review: author self-review against `docs/checklists/ARCHITECTURE_DECISION_PREPARATION_CHECKLIST.md`; independent confirmation pending.
- ADPR: ADPR-0006, this record, `READY_FOR_REVIEW`.
- ADR: [ADR 0031](../ADR/0031-ai-context-prompt-intelligence-foundation.md), `Proposed`.
- Implementation plan: not yet created; no implementation authorized.
- PR: [Draft PR #207](https://github.com/fafa33/Project-Hunter/pull/207), which must be updated by the contribution workflow to cover the new exact head before further exact-pair review.
- Prior reviewed pair: head `964003e97dbef57c0ab005cfc400a0285c7642e2`, base `8dfd663ddf1db7a7b54bdd46eedca8aac0d36ff0`; superseded for Draft-to-Ready review by this remediation commit.
- Preparation audit: not yet performed; required under `docs/ARCHITECTURE_AUDIT_PROTOCOL.md` using `docs/ARCHITECTURE_AUDIT_TEMPLATE.md`.
- Merge commit: not yet created.
- Release: not yet assigned.

## Immutability and Supersession

After `APPROVED`, this record is historical evidence. Corrections that change substantive reasoning require a new ADPR that explicitly supersedes this record. Non-substantive link completion and typographical corrections must remain auditable in version history.
