# ADR 0030: Hunter Intelligence Evolution

## Status

Proposed.

## Context

Project Hunter began as a domain decision-support system, but its development has produced reusable intelligence capabilities beyond market analysis. PR #200 demonstrated that trusted AI-assisted development requires evidence-aware context construction, independent governance, replayable review records, and architectural learning from implementation failures.

These capabilities must not remain implicit in chat history or be repeatedly rediscovered by different agents. Hunter needs one durable architectural roadmap that distinguishes domain intelligence from the AI, engineering, and architectural intelligence required to build and govern the system itself.

This ADR defines direction, authority boundaries, sequencing, interaction rules, provider-independence rules, and evaluation criteria. It does not claim that all described engines already exist or authorize immediate implementation without scoped design, independent review, and follow-up work.

## Decision

Project Hunter will evolve through four cooperating intelligence layers.

### Layer 1 — Domain Intelligence

Domain Intelligence owns evidence-backed analysis and decision support for Hunter's subject matter.

Its responsibilities include discovery, identity and trust, market and macro evidence, on-chain evidence, developer, protocol, governance, security, tokenomics, valuation, historical validation, opportunity timing, and final decision-support outputs.

Domain Intelligence must consume governed evidence and must not assume authority over AI-runtime governance or repository-development decisions.

### Layer 2 — AI Intelligence

AI Intelligence owns the governed construction and execution of model-facing work.

Its target capabilities include:

- AI Context Intelligence;
- authoritative evidence selection;
- context and dependency ranking;
- prompt construction and versioning;
- token and completion budgeting;
- provider and model capability adapters;
- provider health, scoring, routing, and automatic failover;
- semantic chunking and lossless coverage accounting;
- cross-chunk synthesis;
- prompt and response provenance;
- exact replay and correction lineage;
- model disagreement and escalation handling.

The Prompt Intelligence Operating System is the priority consumer-facing subsystem of this layer. It must produce model-specific Prompt Packages from governed, model-independent context packages rather than embedding repository authority, evidence-selection policy, or provider identity inside ad hoc prompt strings.

A Prompt Package must eventually identify the task intent, role, evidence selected, evidence omitted and why, exact context ranges, capability requirements, token budget, output contract, provenance, replay identity, and the execution backend selected for each attempt.

### Layer 3 — Engineering Intelligence

Engineering Intelligence owns executable development quality and operational integrity.

Its responsibilities include deterministic quality gates, CI verification, workflow orchestration, automation, replay checks, provenance validation, ADR and implementation-contract conformance, dependency review, merge-safety verification, repair-plan generation, and controlled delegation to implementation agents.

Engineering Intelligence may diagnose and plan remediation. It must not silently rewrite governance rules or permit an implementation agent to approve its own changes.

### Layer 4 — Architectural Intelligence

Architectural Intelligence owns the extraction and preservation of reusable architectural knowledge from significant changes.

Its responsibilities include:

- distinguishing local patches from architectural improvements;
- extracting reusable patterns and principles;
- identifying new engine boundaries;
- proposing ADRs and canonical-document updates;
- detecting architectural debt and roadmap consequences;
- reviewing Technical Defense and independent-review outcomes;
- classifying findings as present blockers or future milestones;
- preserving historical architectural case studies;
- evolving the roadmap through verified evidence rather than novelty.

Architectural Intelligence does not replace final human architectural authority. It supplies evidence, classifications, and proposals.

## Layer Interaction Model

The layers exchange governed artifacts rather than informal assumptions.

1. Domain Intelligence produces or consumes typed domain evidence and decision-support outputs.
2. AI Intelligence receives a bounded task intent plus governed evidence references and produces versioned Prompt Packages, model executions, structured responses, provider-routing records, and provenance records.
3. Engineering Intelligence validates execution integrity, policy compliance, replayability, coverage, workflow state, and merge or operational consequences.
4. Architectural Intelligence consumes Technical Defense, review findings, runtime evidence, and delivery outcomes to propose reusable patterns, ADR changes, debt classifications, and roadmap updates.

The primary exchange contracts are:

- Domain Evidence Package — typed domain evidence with provenance, confidence, missingness, and replay identity.
- AI Context Package — model-independent selected context with exact source identities, ranges, ranking reasons, and omission reasons.
- Prompt Package — versioned role, task, instructions, context references, output schema, capability requirements, budget, routing policy, and replay metadata.
- Provider Execution Record — selected provider/model, health state, capability match, attempt number, error classification, latency, token usage, cost where available, retry/failover decision, and correction lineage.
- Review Evidence Package — reviewed source/base pair, complete coverage manifest, requests, responses, synthesis, findings, and decision evidence.
- Architecture Learning Package — original failure, review findings, accepted correction, deferred debt, reusable principle, and canonical follow-up.

No layer may infer authority merely because it receives an artifact. Authority remains with the canonical owner named by repository governance and ADRs.

## Roles and Operational Assignment

For each significant milestone or PR, the PR description or linked governed artifact must assign the following roles explicitly:

- Implementer — the human or AI agent authorized to change the scoped files and produce Technical Defense.
- Automated Governance — the repository workflows and governed review services that execute deterministic and model-assisted checks.
- Independent Reviewer — a human or AI reviewer who did not implement the reviewed change and performs no implementation during that review assignment.
- Final Repository Authority — the authorized human who decides whether evidence is sufficient and whether merge may proceed.

One participant may hold different roles at different times, but never Implementer and Independent Approver for the same review iteration. Reassignment must be explicit in the PR record. Automated Governance never receives final architectural or merge authority merely by producing a status.

## Provider Independence and Automatic Failover

AI providers and models are replaceable execution backends, not architectural dependencies.

The following rules govern all future model-facing Hunter capabilities:

- No production or governance workflow may depend on exactly one AI provider when an approved alternative can satisfy the required capability contract.
- Upstream services must request capabilities such as structured output, reasoning, context size, latency class, privacy requirements, or tool use; they must not hard-code a provider identity unless a governing ADR explicitly requires it.
- Provider routing must consider current health, capability match, context and completion limits, structured-output support, rate-limit state, quota state, latency, observed result quality, and configured cost policy.
- HTTP 429, TPM exhaustion, TPD exhaustion, provider timeout, service unavailability, maintenance, regional unavailability, or equivalent transient provider failure must trigger immediate failover to the next eligible approved backend rather than waiting for quota reset.
- Authentication failure, invalid configuration, policy rejection, unsupported capability, or malformed provider output must be classified explicitly. Failover is permitted only to a backend that can satisfy the same governed task and output contract.
- Retry loops must be bounded. Once a failure is classified as provider-wide or quota-wide, remaining chunks or tasks must not continue sending futile requests to the same unhealthy backend.
- Work must resume from the last durable completed unit, such as the current chunk, request, or stage. A provider switch must not force restart of already verified work unless replay or consistency rules require it.
- Every attempt, failure, provider switch, and resumed execution must be preserved in Provider Execution Records and included in replay/provenance evidence.
- Mixed-provider execution is allowed only when the final evidence package identifies which provider/model produced each result and when aggregation or synthesis rules remain valid across those results.
- The workflow may return `REVIEW_FAILED`, unavailable, or another fail-closed state only after no eligible approved provider can complete the governed task, or when switching provider would violate determinism, policy, privacy, replay, or output-contract requirements.

Initial target backends should provide diversity across independent provider infrastructures. The architecture should be able to support at least four configured external providers plus an optional local backend without requiring changes to consumers. Concrete providers, model versions, secrets, cost ceilings, and routing priority belong to implementation configuration and the dedicated Prompt Intelligence OS ADR, not to this strategic roadmap.

## Cross-Layer Rules

- The four layers are logical authority boundaries, not a requirement for four monolithic packages.
- Shared services must have one canonical owner and explicit consumers.
- Domain-specific knowledge must not leak into generic AI context, provider, replay, or governance infrastructure.
- AI-generated findings are evidence inputs; deterministic policy owns merge and operational consequences.
- Every model-facing decision must eventually support provenance and replay sufficient to reconstruct what evidence, context, prompt version, provider, model, parameters, attempts, failures, failovers, and responses produced it.
- Missing or unreviewed required evidence must remain explicit and fail closed where a trustworthy decision is claimed.
- The layers must be developed incrementally under ADR 0029 HDM; this roadmap is not authorization for a single large implementation PR.

## Implementation Sequence and Milestone Gates

The approved near-term sequence is:

1. Complete, independently review, and merge PR #200 within its bounded Hunter Governance Review mission.
2. Review, accept, and merge PR #201 containing ADR 0029, this ADR, and the AI Development Playbook.
3. Create and accept a dedicated ADR for the Prompt Intelligence Operating System before runtime implementation.
4. Implement Prompt Intelligence Operating System Phase 1 for Project Hunter repository-development and review tasks only.
5. Implement AI Context Intelligence as the governed source-resolution, selection, ranking, exact-range, and omission-accounting subsystem used by Prompt Intelligence.
6. Implement the Prompt Builder over governed Context Packages and provider/model-independent Prompt Plans.
7. Implement exact Replay and Provenance for Prompt Packages, requests, responses, corrections, provider attempts, failovers, and review decisions.
8. Implement Model and Provider Adapters plus Provider Health and Routing for capability negotiation, token counting, context and completion limits, structured output, health scoring, quota classification, bounded retry, and automatic failover.
9. Upgrade Hunter Governance Review into Review Engine 2.0 using the preceding components.
10. Only after these foundations are validated may repair orchestration, Architectural Intelligence automation, or broad Domain Intelligence reuse begin.

Each milestone requires its own bounded scope, acceptance criteria, Technical Defense, independent review, and explicit decision before the next milestone begins. Calendar dates must not be invented in an architecture ADR; concrete delivery targets belong to governed issues or milestone plans created when capacity and dependencies are known.

## Scope-Control and Proportionality Rules

- Discovery of a useful capability during implementation does not place that capability inside the active milestone automatically.
- A newly discovered item remains in the current PR only when leaving it unresolved would violate the PR's explicit acceptance criteria or create false approval, evidence loss, authority violation, unsafe persistence, non-replayable claimed behavior, or misleading coverage.
- New reusable runtimes, canonical owners, persistence families, or broader product capabilities require a separately scoped ADR, issue, or PR unless strictly necessary to close an immediate unsafe path.
- Low-risk maintenance uses a proportionate subset of HDM; architecture, authority, governance, persistence, replay, security, and model-runtime changes receive the full lifecycle.
- Complexity must demonstrate measurable value against a named risk or outcome. Passing review is not itself sufficient justification for adding a platform capability to the current milestone.

## Evaluation Criteria

The methodology and intelligence roadmap will be evaluated using milestone-specific evidence rather than a single vanity score.

### Delivery and Scope Metrics

- percentage of significant PRs with explicit scope, non-goals, assigned roles, and acceptance criteria;
- number of findings correctly deferred without entering the active PR;
- number of reopened PRs caused by unbounded scope or missing authority analysis;
- cycle time from implementation completion to independently supported merge decision.

### Prompt and Context Metrics

- prompt-token reduction relative to an equivalent complete manual baseline;
- percentage of Prompt Packages with exact context ranges, omission reasons, and replay identity;
- context precision: selected evidence judged relevant divided by all selected evidence;
- required-context coverage and number of fail-closed executions caused by missing required evidence;
- prompt reuse and exact replay success rate.

### Provider and Continuity Metrics

- percentage of model-facing workflows with at least two eligible approved providers;
- provider-quota and provider-outage recovery time;
- percentage of eligible provider failures recovered automatically without human intervention;
- futile-request count after quota-wide or provider-wide failure classification;
- workflow restart rate caused by provider switching;
- per-provider structured-output validity, latency, failure, and confirmed-quality rates;
- percentage of mixed-provider executions with complete per-attempt provenance;
- number of workflows halted while another eligible approved provider was healthy.

### Review and Governance Metrics

- false-approval incidents discovered after merge;
- false-rejection incidents confirmed through independent review;
- percentage of significant reviews with durable Technical Defense and independent review artifacts;
- complete evidence-package rate;
- review latency, provider-quota failure rate, and recovery time;
- percentage of claimed mandatory gates with independently verified repository enforcement.

### Architectural Learning Metrics

- percentage of significant review cycles producing a documented Knowledge Extraction result;
- number of accepted reusable principles or patterns derived from production evidence;
- number of duplicate architectural solutions prevented by canonical reuse;
- age and closure rate of intentionally deferred architectural debt.

Metrics must be interpreted with context. They must not incentivize lower review quality, artificial prompt minimization, suppression of findings, cheapest-provider routing at the expense of correctness, or premature merging.

## Planned Longer-Term Evolution

After the approved near-term sequence:

1. Add repair-plan generation and controlled delegation to Claude, Codex, or other implementation agents.
2. Add Architectural Intelligence automation for knowledge extraction, ADR proposals, debt classification, and roadmap integration.
3. Reuse mature AI and engineering capabilities in Domain Intelligence only after personal validation shows that decisions are traceable, replayable, provider-resilient, and trustworthy.

## Consequences

- Hunter's internal engineering capabilities become explicit product-quality architecture rather than incidental scripts.
- Future PRs must identify which layer owns each new responsibility and which layers only consume it.
- Prompt Builder, Context Intelligence, Provider Runtime, Evidence Runtime, Replay Runtime, Merge Review, and Architectural Knowledge Extraction must be implemented as modular capabilities with explicit contracts.
- Provider failures become recoverable operational events rather than single points of workflow failure when another eligible backend exists.
- The roadmap creates follow-up obligations but intentionally avoids claiming unimplemented capabilities as complete.
- Some components may later be extracted into independent products, provided Hunter-specific domain assumptions do not become dependencies of the generic core.
- The project accepts additional design discipline in exchange for model portability, auditability, continuity, reduced manual prompt hand-offs, lower avoidable token consumption, and reduced false approval or opaque decision risk.

## Alternatives Considered

- Keep all AI and review behavior inside individual features. Rejected because it duplicates provider, budgeting, context, provenance, replay, and governance responsibilities.
- Build a standalone platform outside Hunter immediately. Rejected because current requirements and evidence arose inside Hunter, while premature extraction would lose operational feedback and duplicate repository knowledge.
- Treat prompt construction as a small utility only. Rejected because trustworthy model execution requires authority resolution, evidence selection, budgeting, coverage, provenance, synthesis, provider routing, and replay beyond string formatting.
- Bind Hunter to one preferred provider and wait for quota resets. Rejected because provider quota or outage would become a single point of failure and could halt governed work despite another capable backend being available.
- Retry the same provider indefinitely. Rejected because quota-wide and provider-wide failures make repeated requests wasteful and delay recovery.
- Collapse Engineering Intelligence and Architectural Intelligence into one layer. Rejected because enforcing current rules and extracting future architectural knowledge are distinct authorities and require different review boundaries.
- Assign fixed calendar dates inside this ADR. Rejected because dates without capacity and dependency evidence become misleading commitments; governed milestone issues should own delivery scheduling.
