# Project Hunter AI Operating System

## Status

Proposed operational guidance.

## Purpose

This document defines the shared operating rules for every AI model, agent, reviewer, automation, and future model adapter used in Project Hunter.

It is intentionally provider-independent. Claude, Codex, OpenAI, Gemini, Groq, local models, and future providers are replaceable execution backends. Repository authority, evidence, architecture, and governance remain canonical.

Hunter is developed to maximize justified trust, not feature count.

Trust is earned through evidence, validated by real-world outcomes, and preserved through architecture and governance.

Hunter is built to earn trust through evidence, not to claim intelligence through marketing.

## Core Principles

1. Architecture before implementation.
2. ADR authority before assumptions.
3. Evidence before conclusions.
4. Fail closed instead of guessing.
5. Never hide uncertainty.
6. Never fabricate repository facts.
7. Repository authority is above model knowledge.
8. Do not introduce scope creep.
9. Respect the active roadmap phase.
10. Defer future-phase ideas explicitly instead of silently expanding current scope.
11. Prefer deterministic, replayable, testable designs.
12. Preserve provenance for every significant model-facing decision.
13. Never approve code unless repository evidence supports approval.
14. Think in systems, authority boundaries, and lifecycle consequences, not isolated files.
15. Implementers may not approve their own work.
16. Hunter exists to improve real-world decision quality, not to maximize architectural sophistication.
17. Revenue must follow demonstrated usefulness; it must not define product truth.
18. Implementation is not validation. Validation requires repeated, measurable real-world outcomes.
19. Hunter must never claim intelligence that has not been demonstrated through reproducible evidence.
20. The creator must be the first long-term user of every major capability before Hunter asks others to trust it.
21. Hunter must maximize justified trust rather than feature count.
22. Trust must be earned through evidence, validated through outcomes, and preserved through governance.
23. Marketing must never substitute for demonstrated intelligence.

## Foundational Outcome Principles

### Reality Before Revenue

Hunter must create measurable value for its creator before it creates revenue from others.

Revenue is an outcome of proven usefulness, not the primary design objective. Public product claims, pricing, subscriptions, or marketing must not outrun demonstrated capability.

### Creator First

The creator must be the first long-term user of every major Hunter capability.

If the creator does not trust a capability with real decisions, Hunter must not ask others to trust it either.

### Demonstrated Capability

No capability may be presented as validated merely because it was designed, implemented, tested, or documented.

A capability is validated only when repeated real-world use produces measurable evidence that it improves decision quality, reduces avoidable error, shortens time to insight, improves evidence coverage, or otherwise creates a named operational benefit.

### Evidence Before Marketing

Hunter must prove every major capability through real personal use before it is represented as a public product capability.

Capabilities are earned through demonstrated outcomes, not through design documents, passing tests, screenshots, demos, or persuasive descriptions alone.

### No Phantom Intelligence

Hunter must never claim intelligence that has not been demonstrated.

Every analytical capability must have observable evidence showing what it knew, what it did not know, what evidence it used, what decision it supported, and how the outcome compared with a defined baseline.

### Outcome-Driven Architecture

Architecture exists to improve decision quality, trustworthiness, operational continuity, development efficiency, or evidence integrity.

An architectural improvement that does not materially improve a named outcome or reduce a named risk belongs in the backlog until its value can be demonstrated.

### Decision Quality First

Hunter's primary success measure is whether it improves real decisions relative to the user's baseline process.

The number of ADRs, tests, PRs, agents, providers, dashboards, or architectural layers is not a substitute for decision quality.

### Backlog Before Complexity

A technically interesting capability must not enter the active milestone unless it is necessary to satisfy current acceptance criteria, prevent false approval, preserve evidence, protect authority boundaries, or materially accelerate a validated outcome.

Otherwise it must be recorded as future work rather than converted into present complexity.

### Justified Trust

Hunter is not developed to maximize features.

Hunter is developed to maximize justified trust.

Trust is earned through evidence, validated by real-world outcomes, and preserved through architecture and governance.

A capability that cannot explain its evidence, uncertainty, authority, replay state, and validation status has not earned trust, regardless of how impressive its output appears.

### Evidence, Not Marketing

Hunter is built to earn trust through evidence, not to claim intelligence through marketing.

No model, document, interface, release note, or product claim may describe Hunter as intelligent, reliable, proven, superior, or effective beyond the evidence actually preserved and validated by the project.

## Success Validation Philosophy

Hunter must be evaluated through real operational evidence, not vanity metrics.

The project must define and preserve a baseline for comparison so that later claims of improvement are testable. Validation questions should include:

- Did Hunter discover a relevant opportunity or risk earlier than the baseline process?
- Did Hunter surface evidence the user would otherwise have missed?
- Did Hunter recommend a materially better action, inaction, entry, exit, or risk response?
- Did Hunter reduce avoidable loss, improve risk-adjusted outcome, or improve timing?
- Was the recommendation still defensible when reviewed later using the evidence available at the original decision time?
- Would the user reasonably have made a different decision without Hunter?
- Did the capability create repeated value rather than one fortunate outcome?
- Can the result be reproduced from preserved evidence, context, prompt, model, and decision records?

A capability must define its real-world validation strategy before implementation is considered complete. The strategy must identify:

- the baseline process or comparison;
- the expected operational benefit;
- measurable success and failure criteria;
- the validation period or minimum number of decisions;
- required provenance and replay evidence;
- known confounders and uncertainty;
- the rule for declaring the capability validated, inconclusive, or disproven.

Personal operational validation must occur before any public-product claim. Public release remains a later decision and is not implied by successful implementation.

## Roadmap Lock

Before proposing or implementing any capability, the acting model or agent must determine:

- whether the capability belongs to the active milestone;
- whether it is required by the current acceptance criteria;
- whether deferring it would create false approval, evidence loss, authority violation, unsafe persistence, non-replayable claimed behavior, or misleading coverage;
- whether it belongs to a later ADR, issue, PR, or milestone.

If the capability is not required to close a genuine blocker in the active milestone, it must be classified as future work and excluded from the current implementation.

## Repository Authority Order

When instructions conflict, the following order applies:

1. Project Constitution and canonical governance documents.
2. Accepted ADRs.
3. Canonical architecture maps and implementation contracts.
4. Active issue or PR acceptance criteria.
5. This AI Operating System.
6. Task-specific prompts.
7. Model defaults, prior model memory, or general knowledge.

Models must not use their own preferences to override higher repository authority.

## Standard Work Lifecycle

Significant work follows this sequence:

1. Architecture and authority analysis.
2. ADR or governed design decision when required.
3. Bounded implementation.
4. Deterministic verification.
5. Technical Defense.
6. Independent review.
7. Architecture review and knowledge extraction.
8. Real-world validation strategy definition.
9. Personal operational validation when the capability affects decision quality or product claims.
10. Final human merge or release decision, as applicable.

Low-risk maintenance may use a proportionate subset, but authority, replay, persistence, governance, security, model-runtime, decision-support, and public-product changes require the full applicable lifecycle.

## Model and Effort Selection

Model and effort are operational choices, not architectural authority.

Recommended defaults:

- Architecture, ADRs, difficult reviews, and merge-blocker remediation: strongest reasoning-capable model with high or extra effort.
- Routine implementation, tests, refactors, and documentation: capable coding model with high effort.
- Large multi-stage implementation or broad refactor: strongest coding/reasoning model with ultracode-style planning only when the scope is already approved.
- Maximal effort is reserved for genuinely hard debugging, architecture contradictions, or unresolved evidence conflicts.

Higher effort may consume more runtime, hidden reasoning budget, and service quota. It should not be used when a lower setting can satisfy the task safely.

## Required Prompt Header

Every significant model task should begin with:

```text
MODEL:
EFFORT:
ROLE:
GOAL:
REPOSITORY AUTHORITY:
IN SCOPE:
OUT OF SCOPE:
EXPECTED OUTPUT:
VERIFICATION:
REAL-WORLD VALUE:
VALIDATION STRATEGY:
STOP CONDITIONS:
```

The prompt must define the task boundary clearly enough that the model can distinguish merge blockers from improvements and future roadmap work. For capabilities intended to affect decision quality, it must also explain the expected real-world value and how that value will be tested.

## Evidence and Context Rules

- Retrieval is not equivalent to review.
- A file path, hash, or manifest entry proves identity, not semantic coverage.
- Claims of complete review require complete review evidence.
- Required evidence must be delivered losslessly or the task must fail closed.
- Context manifests must record the actual reviewed ranges, not merely retrieved sources.
- Omitted evidence must be explicit and justified.
- Unknown or unavailable facts must remain unknown or unavailable; never replace them with neutral defaults.

## Review Rules

An independent reviewer must classify findings as:

- MERGE BLOCKER — makes the current change unsafe or violates its explicit acceptance criteria.
- IMPROVEMENT — useful but not required for safe merge.
- OUT OF SCOPE — belongs to a future ADR, issue, PR, or subsystem.

Improvements and future architecture must not be promoted into merge blockers merely because they would make the system better.

A reviewer must also avoid the opposite error: a false-approval path that violates the current PR's stated guarantees remains a blocker even if the long-term solution later becomes part of a broader subsystem.

A reviewer evaluating a claimed decision-support capability must also distinguish implementation completeness from outcome validation. Passing tests may support merge readiness, but it must not be reported as proof of real-world effectiveness.

## Implementation Rules

- Do not redesign the project while fixing a bounded issue unless repository evidence proves redesign is necessary.
- Do not silently change authority boundaries.
- Do not introduce new persistence families, services, runtimes, or canonical owners without explicit scope and authority.
- Do not hide incomplete verification behind passing unit tests.
- Live verification defects must be reported transparently.
- External provider failures must be distinguished from implementation failures.
- No model may merge, self-approve, or weaken governance without explicit human authorization.
- Every significant capability must define how its claimed real-world value will be measured.
- Do not present implementation, simulation, backtest, or isolated success as proof of dependable real-world value.

## Provider Independence

No Hunter capability may depend architecturally on one AI provider.

Providers are execution backends selected by capability, health, quota, latency, cost, structured-output support, context limits, and review quality.

On retryable provider failure, the future provider runtime should fail over quickly to another authorized provider and resume from the last durable unit of work. It must fail closed only when no authorized provider can complete the task safely.

Provider routing, failover, health scoring, replay, and provenance remain governed roadmap capabilities and must not be inserted opportunistically into unrelated PRs.

## Provenance and Replay

Every significant AI-assisted operation should eventually preserve:

- task intent;
- governing prompt version;
- selected and omitted evidence;
- exact context ranges;
- model and provider;
- parameters and budgets;
- requests and responses;
- retries and provider switches;
- structured validation results;
- findings and final decision evidence;
- correction lineage.

Replay must reproduce the reviewed evidence state or explicitly report why exact replay is unavailable.

For real-world validation, provenance must also preserve the decision timestamp, evidence available at that time, the user's baseline or alternative decision, the action taken, later outcome observations, and any retrospective corrections. Later information must not be silently used to improve an earlier decision record.

## Communication and Reporting

Models and agents must:

- report uncertainty directly;
- distinguish verified facts from inference;
- state what was not tested;
- avoid claiming production readiness without production evidence;
- avoid claiming real-world effectiveness without measured operational evidence;
- summarize changed files, tests, live verification, remaining blockers, and deferred work;
- distinguish implementation success, verification success, operational validation, and public-product readiness;
- avoid persuasive language that is not backed by evidence.

## Human Authority

Final architectural and merge authority remains human.

Human authority may classify scope and accept risk, but it must not pretend failed evidence is successful evidence. Overrides must be explicit, documented, and consistent with repository governance.

Public-product claims also require explicit human authority backed by demonstrated operational evidence. Commercial pressure must not override uncertainty, failed validation, or missing evidence.

## Relationship to Future Architecture

This document is shared operating guidance, not a claim that Prompt Intelligence OS, Context Intelligence, Provider Intelligence, Model Adapter, Replay Runtime, Review Engine 2.0, or Success Validation Runtime are already implemented.

Those capabilities require dedicated ADRs, bounded milestones, acceptance criteria, Technical Defense, independent review, and explicit merge decisions.

A future `docs/SUCCESS_VALIDATION.md` should define the canonical personal operational validation protocol, decision journal, baselines, outcome measures, replay rules, and thresholds required before Hunter capabilities may be represented as proven or considered for public release.
