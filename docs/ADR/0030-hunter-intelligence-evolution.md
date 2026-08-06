# ADR 0030: Hunter Intelligence Evolution

## Status

Proposed.

## Context

Project Hunter began as a domain decision-support system, but its development has produced reusable intelligence capabilities beyond market analysis. PR #200 demonstrated that trusted AI-assisted development requires evidence-aware context construction, independent governance, replayable review records, and architectural learning from implementation failures.

These capabilities must not remain implicit in chat history or be repeatedly rediscovered by different agents. Hunter needs one durable architectural roadmap that distinguishes domain intelligence from the AI, engineering, and architectural intelligence required to build and govern the system itself.

This ADR defines direction and authority boundaries. It does not claim that all described engines already exist or authorize immediate implementation without scoped design, independent review, and follow-up work.

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
- semantic chunking and lossless coverage accounting;
- cross-chunk synthesis;
- prompt and response provenance;
- exact replay and correction lineage;
- model disagreement and escalation handling.

The Prompt Intelligence Engine is a consumer-facing component of this layer. It must produce model-specific prompts from governed, model-independent context packages rather than embedding repository authority or evidence-selection policy inside ad hoc prompt strings.

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

## Cross-Layer Rules

- The four layers are logical authority boundaries, not a requirement for four monolithic packages.
- Shared services must have one canonical owner and explicit consumers.
- Domain-specific knowledge must not leak into generic AI context, provider, replay, or governance infrastructure.
- AI-generated findings are evidence inputs; deterministic policy owns merge and operational consequences.
- Every model-facing decision must eventually support provenance and replay sufficient to reconstruct what evidence, context, prompt version, provider, model, parameters, and responses produced it.
- Missing or unreviewed required evidence must remain explicit and fail closed where a trustworthy decision is claimed.
- The layers must be developed incrementally under ADR 0029 HDM; this roadmap is not authorization for a single large implementation PR.

## Planned Evolution Sequence

1. Complete and independently approve the PR #200 governance gate within its bounded scope.
2. Establish immutable AI Review Evidence Packages containing exact review pairs, complete diff coverage, authoritative context ranges, coverage digests, model/provider configuration, chunk inputs and outputs, synthesis results, and correction lineage.
3. Build provider and model capability contracts for token counting, context limits, structured output, retries, and rate limits.
4. Build the reusable AI Context Intelligence and Prompt Intelligence components over those evidence packages.
5. Upgrade Merge Review into a governed multi-pass review and policy-decision runtime.
6. Add repair-plan generation and controlled delegation to Claude, Codex, or other implementation agents.
7. Add Architectural Intelligence automation for knowledge extraction, ADR proposals, debt classification, and roadmap integration.
8. Reuse the mature AI and engineering layers in Domain Intelligence only after personal validation shows that their decisions are traceable, replayable, and trustworthy.

## Consequences

- Hunter's internal engineering capabilities become explicit product-quality architecture rather than incidental scripts.
- Future PRs must identify which layer owns each new responsibility and which layers only consume it.
- Prompt Builder, Context Intelligence, Provider Runtime, Evidence Runtime, Replay Runtime, Merge Review, and Architectural Knowledge Extraction must be implemented as modular capabilities with explicit contracts.
- The roadmap creates follow-up obligations but intentionally avoids claiming unimplemented capabilities as complete.
- Some components may later be extracted into independent products, provided Hunter-specific domain assumptions do not become dependencies of the generic core.
- The project accepts additional design discipline in exchange for model portability, auditability, and reduced false approval or opaque decision risk.

## Alternatives Considered

- Keep all AI and review behavior inside individual features. Rejected because it duplicates provider, budgeting, context, provenance, replay, and governance responsibilities.
- Build a standalone platform outside Hunter immediately. Rejected because current requirements and evidence arose inside Hunter, while premature extraction would lose operational feedback and duplicate repository knowledge.
- Treat prompt construction as a small utility only. Rejected because trustworthy model execution requires authority resolution, evidence selection, budgeting, coverage, provenance, synthesis, and replay beyond string formatting.
- Collapse Engineering Intelligence and Architectural Intelligence into one layer. Rejected because enforcing current rules and extracting future architectural knowledge are distinct authorities and require different review boundaries.
