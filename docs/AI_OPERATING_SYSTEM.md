# Project Hunter AI Operating System

## Status

Proposed operational guidance.

## Purpose

This document defines the shared operating rules for every AI model, agent, reviewer, automation, and future model adapter used in Project Hunter.

It is intentionally provider-independent. Claude, Codex, OpenAI, Gemini, Groq, local models, and future providers are replaceable execution backends. Repository authority, evidence, architecture, and governance remain canonical.

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
8. Final human merge decision.

Low-risk maintenance may use a proportionate subset, but authority, replay, persistence, governance, security, and model-runtime changes require the full lifecycle.

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
STOP CONDITIONS:
```

The prompt must define the task boundary clearly enough that the model can distinguish merge blockers from improvements and future roadmap work.

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

## Implementation Rules

- Do not redesign the project while fixing a bounded issue unless repository evidence proves redesign is necessary.
- Do not silently change authority boundaries.
- Do not introduce new persistence families, services, runtimes, or canonical owners without explicit scope and authority.
- Do not hide incomplete verification behind passing unit tests.
- Live verification defects must be reported transparently.
- External provider failures must be distinguished from implementation failures.
- No model may merge, self-approve, or weaken governance without explicit human authorization.

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

## Communication and Reporting

Models and agents must:

- report uncertainty directly;
- distinguish verified facts from inference;
- state what was not tested;
- avoid claiming production readiness without production evidence;
- summarize changed files, tests, live verification, remaining blockers, and deferred work;
- avoid persuasive language that is not backed by evidence.

## Human Authority

Final architectural and merge authority remains human.

Human authority may classify scope and accept risk, but it must not pretend failed evidence is successful evidence. Overrides must be explicit, documented, and consistent with repository governance.

## Relationship to Future Architecture

This document is shared operating guidance, not a claim that Prompt Intelligence OS, Context Intelligence, Provider Intelligence, Model Adapter, Replay Runtime, or Review Engine 2.0 are already implemented.

Those capabilities require dedicated ADRs, bounded milestones, acceptance criteria, Technical Defense, independent review, and explicit merge decisions.
