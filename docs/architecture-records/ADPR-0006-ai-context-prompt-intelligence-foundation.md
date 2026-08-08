# ADPR-0006: AI Context and Prompt Intelligence Foundation

## Metadata

- ADPR ID: `ADPR-0006`
- Status: `APPROVED`
- Version: 3
- Author: Project Hunter Architecture Team
- Reviewers: second independent Architecture Preparation Audit
- Created: 2026-08-08
- Approved: 2026-08-09
- Related Epic: not yet created
- Related Issue: [#206](https://github.com/fafa33/Project-Hunter/issues/206)
- Planned or produced ADR: [ADR 0031](../ADR/0031-ai-context-prompt-intelligence-foundation.md) (`Proposed`)
- Supersedes: not applicable
- Superseded by: not applicable
- Preparation self-assessment: `READY_FOR_ADR`
- Draft PR: [#207](https://github.com/fafa33/Project-Hunter/pull/207)
- ADR alignment commit: `12ac95d3582bf569fe76beb29c06a274133cf080`
- Independent preparation audit: revision 1 at `8450917ac415779781c78f365750029fa1bab8a4` concluded `ADPR_REVISION_REQUIRED`; revision 2 at `7a64f54bf51fd9f81ee859f9ca63389e5938bdac` passed and is ready to govern ADR revision

This record remediates formal review finding F-007. ADR 0031 was drafted before its mandatory permanent preparation record, contrary to the normal order in `docs/architecture-records/README.md`. This record makes that sequence deviation explicit, reconstructs the preserved decision basis from Issue #206, repository authority, the ADR draft, and its Technical Defense evidence, and does not retroactively approve either this preparation or ADR 0031.

This record authorizes no production implementation, provider integration, Phase 1 work, ADR status change, or modification to Hunter Governance Review.

## Executive Summary

Project Hunter has an Evidence Intelligence provider boundary but no canonical way to determine, explain, budget, serialize, and preserve the exact context and prompt presented to a future model. The evaluated options range from local prompt utilities and a monolithic AI service to deferral, a generic provider-independent foundation, and a governed Evidence Intelligence-specific foundation that defers generalization until a second real consumer demonstrates commonality.

The updated comparison recommends the Evidence Intelligence-specific foundation first. It establishes the same deterministic selection, allocation, canonical-byte, subordinate-lineage, security, and reconstruction rules inside the only evidenced consumer boundary, while deferring generic Context/Prompt ownership until a second real AI consumer proves which contracts are actually shared. Hunter Governance Review remains deterministic and zero-LLM.

The second independent preparation audit confirmed that this record fairly represents the decision basis and that Option 7 is ready to govern ADR revision. Proposed ADR 0031 was then revised at `12ac95d3582bf569fe76beb29c06a274133cf080` to establish the Evidence Intelligence-specific foundation and defer generic Context/Prompt ownership. The preparation outcome is therefore `READY_FOR_ADR`; ADR 0031 remains separately Proposed pending renewed exact-pair formal review and acceptance.

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

PR #200 supplied operational counterevidence to treating character limits, resolved evidence, or provider availability as sufficient execution control. Issue #206 defines the acceptance surface. PR #207 durably summarizes that two targeted Technical Defense cycles identified and then reported F-001 through F-006 as resolved. No full Technical Defense artifact or exact hostile-review report is durably published in the repository, Issue #206, or PR #207, so those summaries are context only and are not treated as reproducible proof in this revision.

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
| E-009 | Technical Defense summary | [Draft PR #207](https://github.com/fafa33/Project-Hunter/pull/207), `## Technical Defense`, and [Issue #206](https://github.com/fafa33/Project-Hunter/issues/206) | The durable summaries state that F-001 through F-006 were identified and later reported resolved across context dimensions, identity layering, ownership, byte determinism, replanning, and retention/reconstruction. | No full Technical Defense transcript, finding matrix, or exact artifact locator is durably published. The summary is not independently reproducible and is context only, not proof that an option survived. | Identifies prior challenge areas; neither proves nor selects an option. |
| E-010 | Prior hostile-review and independent ADPR-audit summaries | Review tasks associated with commits `964003e97dbef57c0ab005cfc400a0285c7642e2`, `8450917ac415779781c78f365750029fa1bab8a4`, and `7a64f54bf51fd9f81ee859f9ca63389e5938bdac` | The reviews produced F-007 and then F-PA-001 through F-PA-003; the second independent preparation audit found all three resolved and confirmed Option 7 was ready to govern ADR revision. | The exact commit identities, audit outcome, and resulting corrections are recorded in this ADPR and PR #207. Full earlier review transcripts remain absent and are not treated as independent proof beyond their durable summaries. | Confirms preparation readiness and Option 7 without supplying ADR acceptance authority. |
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
- Advantages: Deterministic, testable, provider-neutral, secure, directly reusable by a second consumer, and incrementally migratable.
- Disadvantages: Commits generic owners, identities, and contracts before evidence from a second consumer establishes that they are common rather than Evidence Intelligence-specific.
- Failure modes: Policy gaps can block builds; retained bytes increase handling obligations; generic abstractions can mismatch later consumers or remain unused.
- Migration implications: Start with one provider-free Evidence Intelligence vertical slice; later decisions add adapter and response contracts.
- Reversibility: Medium; no runtime data exists yet, but an accepted generic authority contract would require a later ADR to narrow or replace.
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

### Option 7: Evidence Intelligence-specific pre-model foundation first

- Description: Define the complete deterministic pre-model path only for Evidence Intelligence's existing extraction boundary; defer generic Context/Prompt ownership until a second real AI consumer demonstrates common contracts.
- Authority and ownership: Evidence Intelligence owns consumer-specific execution intent, exact `EvidenceSpan`/`ExtractionSchema` selection, allocation, prompt compilation, and subordinate pre-model build records without gaining claim, scoring, governance, or top-level execution authority.
- Boundaries: Resolver, selector, allocator, compiler, and persistence responsibilities remain internally distinct and service-owned, but their contracts are explicitly Evidence Intelligence-specific rather than repository-wide AI authorities.
- Persistence and replay: Immutable consumer-scoped ledgers, allocation results, prompt artifacts, and subordinate build records use the same strict-known, canonical-byte, correction, retention, and reconstruction-unavailable rules.
- Evidence and provenance: Exact `EvidenceSpan`, source revision/range/hash, `ExtractionSchema`, policy, reason, capability, compiler, canonicalization, and predecessor lineage are retained when governing policy permits.
- Compatibility: Preserves Evidence Intelligence's proposal-only outputs, existing execution ownership, accepted ADR constraints, and zero-LLM Governance Review; does not claim generic ownership over future consumers.
- Advantages: Matches the only evidenced consumer, minimizes speculative abstraction, keeps initial ownership local and explicit, and provides concrete evidence from which later shared contracts can be extracted.
- Disadvantages: A second consumer may require a new ADPR/ADR, adapters, and extraction of shared contracts; consumer-specific names and persistence may need migration rather than direct reuse.
- Failure modes: Evidence Intelligence can accidentally treat its local contracts as de facto generic authority; later extraction can duplicate identities or lose lineage unless the second decision defines exact migration and compatibility.
- Migration implications: The first implementation remains one provider-free Evidence Intelligence vertical slice. A second consumer triggers comparison of both real contracts, selection of genuinely common semantics, and an explicit migration/supersession plan.
- Reversibility: High before provider activation and while only one consumer exists; the scoped records can remain historical Evidence Intelligence artifacts even if shared interfaces are later introduced.
- Open dependencies: The same concrete execution identity, source-handling policy, storage, fixed capability constraint, and later Model Adapter decisions remain deferred; generalization additionally depends on evidence from a second real consumer.

## Comparative Analysis

| Criterion | Option 1: Local utility | Option 2: Monolith | Option 3: Split now | Option 4: Generic foundation | Option 5: Defer | Option 6: Governance first | Option 7: Evidence-specific first |
|---|---|---|---|---|---|---|---|
| Correctness | Low | Low | Medium | High across anticipated consumers | Not resolved | Low | High for the evidenced consumer |
| Constitutional compliance | Low | Low | Medium | High | Neutral while deferred | Low | High |
| Governance compliance | Low | Low | Medium | High, subject to audit | Neutral while deferred | Unacceptable | High, subject to audit |
| Authority clarity | Low | Low | Medium | High through new generic owners | None | Low | High through scoped Evidence Intelligence owners |
| Replayability | Low | Medium but entangled | Medium | High when policy permits | None | Low | High when policy permits |
| Evidence integrity | Low | Low | Medium | High | Existing evidence only | Low | High |
| Maintainability | Low | Low | Medium | Medium until commonality is evidenced | Medium before implementation | Low | High while one consumer exists; medium during later extraction |
| Scalability | Low across consumers | Central bottleneck | Medium | High for compatible future consumers | Unknown | Provider-bound | Medium; a second consumer requires governed extraction |
| Operational complexity | Hidden and duplicated | High and centralized | Medium | Medium and explicit | Deferred | High governance risk | Low-medium and explicit |
| Migration risk | High long-term | High | Medium | Medium: low first-slice integration, higher risk of premature generic mismatch | High once work starts | Unacceptable | Low initially; medium if a second consumer later requires extraction |
| Implementation effort | Low initially | High | Medium | Medium-high | None now | High | Medium-low |
| Reversibility | Low historical | Low | Medium | Medium because generic authority becomes durable | High while deferred | Low | High while consumer-scoped |
| Long-term extensibility | Low | Low | Medium | High if later consumers match; lower if they do not | Unknown | Low | Medium; extensibility is purchased through a later evidence-based decision |
| Abstraction risk | High through duplication | High through centralization | Medium | Medium-high before a second consumer exists | Low while deferred | High | Low while one consumer exists |
| Second-consumer impact | Duplicate local redesign | Central service already exists but conflicts remain | Integration decision still required | Direct reuse if contracts fit; amendment if they do not | Entire architecture still required | Governance coupling remains invalid | New ADPR/ADR and explicit extraction/migration required |

Options 4 and 7 both satisfy the fixed determinism, security, replay, subordinate-ownership, non-authority, and zero-LLM constraints. Option 4 optimizes for reuse if a compatible second consumer appears. Option 7 optimizes for the evidence that exists now: Evidence Intelligence is the only concrete consumer, and no repository evidence establishes a second consumer's source model, ownership, or prompt requirements. Under the same criteria, Option 7 has lower current abstraction, implementation, migration, and reversibility risk; Option 4 has lower future second-consumer extraction cost only if its generic contracts prove compatible.

## Falsification Results

The non-leading options fail direct constraint tests:

| Option | Concrete invalidation condition | Result |
|---|---|---|
| 1 | A second consumer independently chooses different omission reasons or serialization for the same source state. | Fails: no shared governed record or policy constrains the two utilities. |
| 2 | The service validates its own provider output and a caller treats the validated artifact as canonical. | Fails: source, provider, validation, and downstream authority collapse into one owner contrary to accepted boundaries. |
| 3 | Context ADR A assigns package membership to its selector while Prompt ADR B assigns it to its compiler. | Fails as presently defined: the shared allocation handoff remains undecided. |
| 5 | Provider implementation begins before a source-selection owner exists and adopts provider token limits as source eligibility. | Fails prospectively: deferral permits provider constraints to become upstream authority. |
| 6 | The provider is unavailable while a merge decision is required. | Fails: Governance Review can no longer produce its deterministic result independently. |

Options 4 and 7 remain viable and receive the same document-level counterexamples. These are preparation analyses, not production or architecture-regression tests.

| Scenario | Option 4: generic foundation | Option 7: Evidence-specific first | Result and decision effect |
|---|---|---|---|
| A second AI consumer appears with repository commit/path/range context rather than `EvidenceSpan`. | The new consumer supplies a resolver/source policy and reuses generic intent, ledger, allocation, and compiler contracts. Existing Evidence Intelligence identities remain unchanged. It survives without ownership transfer if the generic source abstraction actually fits. | The new consumer cannot write Evidence Intelligence records. A new ADPR/ADR compares both real consumers, extracts only shared contracts, and defines adapters plus lineage-preserving migration. It survives without collision but not without governed redesign. | Favors Option 4 only after a compatible second consumer exists; current evidence cannot prove compatibility. |
| One task is built for two capacities, one fitting all selected context and one excluding optional context. | One model-independent ledger identity feeds two capability-specific allocation, package, prompt, and build identities; the smaller build records budget exclusions. | The same layering occurs in the Evidence Intelligence-scoped ledger and build families. | Both survive; this behavior does not require repository-wide generic ownership. |
| Historical source bytes become non-retainable after the prompt was built. | Retained metadata/tombstone records the governing deletion and `RECONSTRUCTION_UNAVAILABLE`; hashes cannot recreate bytes or justify exact reconstruction. | The Evidence Intelligence build record applies the identical rule to its `EvidenceSpan` payload and surviving metadata. | Both survive if deletion never falls back to current bytes. |
| A caller replans by changing a required span to optional while retaining the original task/policy identity. | The current build terminates; the successor is rejected unless an authorized planner supplies a new semantic/policy identity and predecessor lineage. | The Evidence Intelligence consumer follows the same terminal rule within its scoped policy family. | Both survive; neither permits silent weakening under the old identity. |
| Later Model Adapter execution introduces retries and provider failover. | Each attempt references the immutable pre-model build; retries/failover create later attempt identities and cannot mutate selection or prompt bytes. | A later adapter decision references the Evidence Intelligence build in the same way; consumer scope does not grant attempt ownership to the compiler. | Both survive without moving provider behavior upstream. |
| Governance code imports the AI context abstraction to build its `ContextManifest`. | The dependency is rejected: generic AI context and Governance `ContextManifest` have different owners and the future architecture-regression suite must forbid the import. | The dependency is also rejected: Governance cannot import Evidence Intelligence-specific prompt/context contracts, and the future suite must forbid it. | Both survive only by rejecting the contribution; no such regression test is claimed as already implemented. |
| Evidence Intelligence remains Hunter's only AI consumer for an extended period. | Generic owners and contracts remain broader than demonstrated use, increasing durable abstraction and governance surface without reuse evidence. | The scoped foundation remains aligned with its sole consumer and creates no unused cross-repository AI authority. | Option 4 does not survive the current evidence-proportionality test; Option 7 survives and is preferred. |

A future incompatible second consumer could falsify Option 4 by proving that its source, policy, allocation, or prompt semantics cannot be reused without amendment. A future second consumer with substantially identical semantics could falsify the continued sufficiency of Option 7 by making duplicated consumer-specific contracts less maintainable than governed extraction. Today only the first condition is observable: Evidence Intelligence is the sole concrete consumer. The comparison therefore selects Option 7 while preserving a clear reconsideration trigger.

## Rejected Options

- **Option 1, consumer-local prompt utility:** rejected because it hides or duplicates authority and cannot provide uniform deterministic reconstruction. Reconsider only for ephemeral, non-Hunter prototypes that create no repository artifact or production behavior.
- **Option 2, monolithic AI service:** rejected because it collapses accepted boundaries and makes model behavior upstream of its own explanation. Reconsider only if future accepted architecture replaces the current ownership hierarchy, which this decision does not propose.
- **Option 3, separate foundation decisions now:** rejected because the selection/allocation/compiler handoff and identity layering must be fixed together. Reconsider for Model Adapter and Response Validator, which are deliberately later decisions with separable ownership.
- **Option 4, generic provider-independent foundation now:** not selected because no second consumer currently evidences shared source, policy, allocation, or prompt semantics, while acceptance would create durable generic owners. Reconsider when a second real consumer exists or concrete Evidence Intelligence design proves that consumer-scoped ownership cannot preserve the fixed boundaries.
- **Option 5, defer until provider selection:** rejected because provider choice must not define upstream source authority or identity. Reconsider only if independent evidence falsifies the ability to express a provider-neutral capability constraint.
- **Option 6, migrate Governance Review first:** rejected because Governance Review must remain deterministic and zero-LLM. Reconsideration requires a separate governance amendment through the full constitutional process; it is explicitly outside this decision.
- **Hash-only persistence with regeneration from current sources:** rejected within every option because it creates false historical reconstruction. Reconsider only where no exact-reconstruction claim is made and explicit unavailability is acceptable.
- **Silent truncation or best-effort required coverage:** rejected within every option because it conceals missingness and changes task semantics. No reconsideration condition exists under current accepted authority.

## Risks

| Risk | Category | Likelihood | Impact | Mitigation | Residual uncertainty |
|---|---|---|---|---|---|
| Consumer-specific contracts require later extraction | Maintainability/migration | Medium | Medium | Require a new ADPR/ADR when a second consumer exists; compare real contracts; preserve old identities through explicit adapters and supersession. | The future consumers' commonality and migration cost are unknown. |
| Source handling policy is absent or inconsistent | Security/governance | Medium | High | Fail closed; require source-owner classification; record reconstruction unavailability. | Concrete policy coverage is implementation evidence not yet collected. |
| Capability/token arithmetic is provider-specific | Technical | Medium | Medium | Require an explicit versioned capability constraint; do not infer provider behavior. | A later Model Adapter ADR may need tokenizer-specific contracts. |
| Exact byte retention increases sensitive-data exposure | Security/operations | Medium | High | Exclude secrets, inherit source policy, record retention/deletion/legal-hold state, minimize retained scope. | Storage controls and retention durations remain implementation-specific. |
| Replanning creates identity explosion or confusing lineage | Operational | Low | Medium | Terminal predecessor, one authorized successor path, stable reason codes, explicit semantic/policy version changes. | Concrete operator ergonomics remain untested. |
| AI artifacts are mistaken for canonical intelligence | Authority | Medium | High | Preserve proposal-only outputs and existing deterministic/human authority boundary; require future architecture-regression tests before implementation. | Future consumers require separate authority review. |
| Governance and AI context types converge by naming | Ownership | Low | High | Keep distinct owner, purpose, schema, lifecycle, and package; require future regression tests that forbid cross-imports. | Future refactors must preserve separation. |
| Retrospective preparation conceals missing contemporaneous reasoning | Governance | Medium | High | Record the sequence deviation, evidence limitations, exact prior commit, and mandatory independent audit. | The audit passed, but absent contemporaneous artifacts remain a disclosed historical limitation. |

## Open Questions

| Question | Blocking? | Owner | Required evidence or action | Status |
|---|---|---|---|---|
| Does revision 2 resolve F-PA-001 through F-PA-003 and fairly represent the decision basis? | Yes for preparation approval | Independent architecture auditor | Perform a second independent audit under `docs/ARCHITECTURE_AUDIT_PROTOCOL.md` and the audit template. | Resolved; the second independent audit passed at `7a64f54bf51fd9f81ee859f9ca63389e5938bdac`. |
| Does Proposed ADR 0031 match the rederived Option 7 recommendation? | Yes for ADR progression | ADR revision owner | Reconcile ADR 0031 with the audited preparation outcome through a separately reviewed ADR revision. | Resolved for preparation alignment at `12ac95d3582bf569fe76beb29c06a274133cf080`; ADR 0031 now expresses Option 7 and remains Proposed pending renewed exact-pair review. |
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

Determination: compatible. The second independent preparation audit identified no remaining preparation blocker or constitutional conflict.

## Governance Review

- Development Governance Stage 1 and the Architecture Decision Preparation Guide apply because the decision creates new architectural ownership, persistence, replay, and subsystem boundaries.
- This record follows the canonical ADPR template, uses the next monotonic identifier, and is registered in `docs/architecture-index.md`.
- The normal create-preparation-before-ADR order was violated. The deviation remains explicit; the second independent preparation audit passed, ADR 0031 remains Proposed, and no implementation has begun.
- Accepted ADRs 0002, 0004, 0009, 0016, and 0020 are extended or reaffirmed without changing their owners or guarantees.
- ADRs 0029 and 0030 are Proposed and non-binding; neither is amended, superseded, or treated as authority.
- The revised ADR's `EvidenceContextSelectionLedger` remains distinct from Governance `ContextManifest`, and `EvidencePreModelBuildRecord` remains subordinate to existing execution identity. These are Evidence Intelligence-specific contracts and create no generic cross-consumer authority.
- Hunter Governance Review remains deterministic and zero-LLM. This decision does not modify its code, workflow, status publisher, or authority.
- Evidence Intelligence remains proposal-only and is only the first future migration target. No Phase 1 implementation is authorized.
- The Architecture Audit Protocol, not this self-assessment, controls the independent preparation verdict.

Determination: governance-compatible and preparation-approved. The independent preparation audit and ADR alignment are complete; renewed exact-pair contribution review remains required because the source head changed during remediation.

## Quality Assessment

| Dimension | Rating | Evidence and rationale | Blocking limitation |
|---|---|---|---|
| Problem correctness | EXCELLENT | The gap is grounded in current Evidence Intelligence and missing pre-model ownership, not an implementation preference. | None identified. |
| Scope completeness | EXCELLENT | In/out scope, consumer-specific and generic boundaries, later decisions, and prohibited governance changes are explicit. | None identified. |
| Canonical consistency | GOOD | Constitution, Principles, accepted ADRs, Evidence Intelligence, and governance ownership were checked; Proposed ADRs are non-binding. | None remaining for preparation. |
| Evidence integrity | GOOD | Repository authority, implementation evidence, operational evidence, issue criteria, review summaries, and absent durable artifacts are distinguished. | Full prior review artifacts are not durably published; this revision does not rely on them as proof. |
| Assumption discipline | GOOD | Three implementation-facing assumptions have confidence, falsification, and fail-closed consequences. | None identified for the ADR decision. |
| Option completeness | GOOD | Seven materially distinct options include both a generic foundation and a governed Evidence Intelligence-specific-first foundation; the second audit confirmed the option set. | None remaining for preparation. |
| Comparative fairness | GOOD | Every option uses the same normalized fields and criteria, including ownership, replay, migration, reversibility, abstraction risk, and second-consumer impact; the second audit confirmed the rederived ranking. | None remaining for preparation. |
| Falsifiability | GOOD | Concrete document-level scenarios show where Options 4 and 7 survive, require redesign, or lose the evidence-proportionality test. | No production or architecture-regression test is claimed as completed. |
| Authority and ownership clarity | GOOD | Option 7 keeps pre-model responsibilities distinct inside Evidence Intelligence and prohibits claim, execution, provider-attempt, and governance authority leakage. | Generic ownership remains deliberately undecided until a second consumer exists. |
| Persistence and replay quality | EXCELLENT | Layered identity, immutable lineage, byte retention policy, strict-known reconstruction, and unavailability are resolved. | Concrete storage remains intentionally open. |
| Evidence and provenance quality | GOOD | Exact source/policy/reason/capability/compiler/canonicalization lineage is required; hashes do not substitute for bytes. | None identified. |
| Operational quality | GOOD | Terminal failure, replanning authority, quota isolation, retention/deletion, and observability states are defined. | Deployment and provider operations are later decisions. |
| Implementation and migration impact | GOOD | Option 7 defines a narrow provider-free Evidence Intelligence scope and the governed extraction cost if a second consumer appears; code and activation remain deferred. | Phase 1 plan not yet created by design. |
| Testability and validation | GOOD | Legal dimensions, layered identities, byte equality, strict-known behavior, forbidden authority, and isolation yield explicit future verification obligations. | No implementation or architecture-regression testing has occurred. |
| Maintainability and extensibility | GOOD | Consumer scoping avoids premature generic authority while requiring explicit future extraction from two real contracts. | A second consumer will incur a governed migration decision. |
| Risk quality | GOOD | Governance, security, authority, operational, and retrospective-preparation risks include mitigation and uncertainty; the second audit identified no additional blocker. | Implementation risks remain future gates. |
| Traceability | ACCEPTABLE | Issue, ADPR, Proposed ADR, Draft PR, exact review commits, review-summary limitations, and missing durable artifacts are explicit. | Full prior Technical Defense and hostile-review reports are not durably published. |

No mandatory quality dimension is below `ACCEPTABLE`. The second independent audit confirmed the preparation basis, and the produced ADR now matches the rederived recommendation. ADR acceptance remains a separate lifecycle decision.

## Architecture Readiness

- Outcome: `READY`
- Rationale: The problem, authority, constraints, complete option set, equal-criteria comparison, concrete counterexamples, identity, determinism, security, retention, failure, and migration boundaries are sufficiently resolved to select Option 7.
- Missing evidence: No second consumer exists to justify generic ownership. Concrete source handling policies, execution identity, storage, and capability constraint are required before implementation, not to select the consumer-scoped architecture.
- Unresolved conflicts: None for preparation. Proposed ADR 0031 now expresses Option 7; exact-pair formal review and acceptance remain separate gates.

## ADR Readiness

- Outcome: `READY_FOR_ADR`
- Proposed ADR title: ADR 0031: AI Context and Prompt Intelligence Foundation
- Proposed ADR scope: A deterministic provider-independent pre-model foundation scoped to Evidence Intelligence, with generic Context/Prompt ownership deferred until a second real consumer demonstrates commonality.
- Decisions the ADR must fix: Evidence Intelligence-scoped context dimensions and identities; internal responsibility separation; canonical byte contract; subordinate build ownership; terminal replanning; retention/deletion/reconstruction semantics; proposal-only authority; zero-LLM Governance Review; and the trigger for later generic extraction.
- Matters the ADR must leave open: Provider/model selection, adapter/routing/retry behavior, response contracts and re-execution, storage technology, production schemas, credentials, deployment, and later consumers.

This preparation outcome was confirmed by the second independent audit and ADR 0031 was aligned separately. Approval of this ADPR records the accepted decision basis only; it does not accept ADR 0031 or authorize implementation.

## Final Recommendation

Recommend Option 7, the Evidence Intelligence-specific pre-model foundation first. It closes the only evidenced consumer gap with the same deterministic selection, layered identity, canonical-byte, strict-known, non-authority, security, retention, and reconstruction rules as Option 4, while avoiding durable generic owners whose commonality has not been demonstrated.

ADR 0031 has been reconciled to this recommendation. A second real consumer remains the reconsideration trigger for generic extraction: that later preparation must compare the two actual contracts, define common ownership, preserve historical identities, and specify migration. Do not implement Phase 1 or change ADR status as part of preparation approval.

## Decision History

| Date | State | Change | Author or reviewer |
|---|---|---|---|
| 2026-08-08 | READY_FOR_REVIEW | Created ADPR-0006 to remediate F-007, reconstructed the preserved decision basis, recorded the sequence deviation, and linked Proposed ADR 0031 without changing its decision or status. | Project Hunter Architecture Team |
| 2026-08-08 | READY_FOR_REVIEW | Revision 2 addressed F-PA-001 through F-PA-003: disclosed absent durable review artifacts, added and fairly compared an Evidence Intelligence-specific-first option, replaced circular falsification with concrete counterexamples, selected Option 7 from current evidence, and marked the produced ADR `NEEDS_REVISION` without editing it. | Project Hunter Architecture Team |
| 2026-08-09 | APPROVED | Recorded the passing second independent preparation audit for revision 2, confirmed Option 7 as the governing recommendation, and verified that Proposed ADR 0031 was aligned at `12ac95d3582bf569fe76beb29c06a274133cf080`. F-FR-001 traceability is resolved; ADR acceptance and implementation remain unauthorized. | Independent preparation audit and Project Hunter Architecture Team |

## Traceability

- Epic: not yet created.
- Issue: [#206](https://github.com/fafa33/Project-Hunter/issues/206).
- Preparation working document: no separate repository artifact; the decision basis was developed in the ADR 0031 review sequence associated with Issue #206 and Draft PR #207. PR #207 records durable summaries of F-001 through F-006, the preparation-audit sequence, and F-FR-001 through F-FR-003 remediation; full earlier review transcripts remain unavailable and are not treated as reproducible proof.
- Checklist review: author self-review against `docs/checklists/ARCHITECTURE_DECISION_PREPARATION_CHECKLIST.md`; second independent preparation audit completed with a passing verdict.
- ADPR: ADPR-0006, this record, `APPROVED`.
- ADR: [ADR 0031](../ADR/0031-ai-context-prompt-intelligence-foundation.md), `Proposed`.
- Implementation plan: not yet created; no implementation authorized.
- PR: [Draft PR #207](https://github.com/fafa33/Project-Hunter/pull/207); its body records the audit sequence, ADR alignment, formal-review remediation, and exact current head before renewed review.
- Formal ADR review pair: head `12ac95d3582bf569fe76beb29c06a274133cf080`, base `8dfd663ddf1db7a7b54bdd46eedca8aac0d36ff0`; review returned `CHANGES REQUIRED` for F-FR-001 through F-FR-003. The remediation commit requires renewed exact-pair review.
- Preparation audit: revision 1 at `8450917ac415779781c78f365750029fa1bab8a4` concluded `ADPR_REVISION_REQUIRED` with F-PA-001 through F-PA-003. Revision 2 at `7a64f54bf51fd9f81ee859f9ca63389e5938bdac` passed and confirmed Option 7 was ready to govern ADR revision. PR #207 records the durable audit summary; absent full earlier transcripts are not treated as proof beyond that summary.
- Merge commit: not yet created.
- Release: not yet assigned.

## Immutability and Supersession

This record is `APPROVED` historical evidence. Corrections that change substantive reasoning require a new ADPR that explicitly supersedes this record. Non-substantive link completion and typographical corrections must remain auditable in version history.
