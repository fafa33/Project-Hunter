# Project Hunter AI Review Protocol

Status: Mandatory engineering standard

This protocol governs every contribution to Project Hunter from Codex, Claude, human contributors, and future AI agents. It supplements `docs/PROJECT_CONSTITUTION.md`, `docs/PROJECT_PRINCIPLES.md`, `docs/VISION.md`, `docs/HUNTER_ARCHITECTURE_MANIFEST.md`, `docs/HUNTER_ARCHITECTURE_SPEC.md`, `docs/HUNTER_ROADMAP.md`, `docs/CANONICAL_RUNTIME_ARCHITECTURE.md`, `docs/DEVELOPMENT_GOVERNANCE.md`, `docs/CODEX_IMPLEMENTATION_GUIDE.md`, and the active sprint specification. When this protocol conflicts with a higher-level project governance document, the contribution must stop until the contradiction is resolved explicitly in documentation.

## 1. Purpose

Project Hunter is an evidence-first investment intelligence system. Its long-term reliability depends on strict separation between implementation, independent review, and merge approval. Independent review is mandatory because implementation summaries, passing tests, and local confidence are not sufficient proof that a change preserves architecture, replay safety, evidence integrity, persistence safety, or operational boundaries.

Every contribution must be reviewed as if it may become a permanent part of a system maintained for more than ten years. The reviewer must inspect the actual diff and relevant surrounding architecture, not rely on the implementer's narrative. The purpose of review is to prevent silent architectural drift, hidden behavior changes, undocumented runtime coupling, fabricated evidence paths, migration risks, and decision-system side effects.

This standard applies equally to documentation, configuration, migrations, tests, production code, provider adapters, automation, reports, and operational tooling.

## 2. Guiding Principles

### Evidence Over Assumptions

Contributors and reviewers must ground every technical claim in code, tests, migrations, configuration, documentation, or reproducible command output. Assumptions may guide investigation, but they must not be accepted as implementation facts.

### Small Scoped PRs

Each pull request must be small enough for a reviewer to understand the complete architectural effect. Large changes must be split unless the split would make the system temporarily inconsistent or unsafe.

### One Architectural Concern Per PR

A pull request must address one architectural concern. For example, a schema migration, provider boundary, replay contract, dashboard change, or scoring change should not be bundled with unrelated refactors or opportunistic cleanup.

### Deterministic Changes

The same inputs must produce the same outputs. Determinism applies to identity, persistence, evidence references, migrations, ordering, replay, reports, tests, and retry behavior.

### No Hidden Behavior Changes

Any change that affects runtime behavior, scoring, ranking, valuation, committee decisions, timing, reports, automation, provider access, or persistence semantics must be explicit in the PR scope and documentation. A hidden behavior change is a blocker even if tests pass.

### No Silent Architectural Drift

New code must extend approved boundaries rather than create competing runtime paths, duplicate canonical truth, or bypass repositories, adapters, execution identity, evidence, sufficiency, replay, or configuration conventions.

### Documentation Stays Synchronized With Implementation

Documentation must describe the system that exists. It must not claim unsupported coverage, operational providers, completed roadmap items, migration safety, replay behavior, or architectural status that the implementation does not prove.

## 3. Standard Development Lifecycle

Every contribution follows this lifecycle:

```text
Implementation
-> Self Review
-> Architectural Audit
-> Documentation Consistency Review
-> Fix
-> Re-review
-> Approved For Merge
-> Merge
-> Delete branch
```

No step may be skipped because the change appears simple. For documentation-only changes, the same lifecycle applies with the review scope limited to accuracy, consistency, governance hierarchy, and absence of unintended implementation effects.

## 4. Codex Responsibilities

Codex is responsible for production-safe implementation within the approved scope.

Codex must:

- read the relevant sprint specification, architecture documents, existing implementation, migrations, configuration, and tests before editing;
- preserve current runtime behavior unless the approved scope explicitly changes it;
- reuse existing repositories, provider boundaries, execution identity, evidence, sufficiency, replay, configuration, timestamp, migration, and checkpoint conventions;
- keep changes deterministic and idempotent;
- write migrations that preserve existing data and replay compatibility;
- add or update tests that prove the architectural invariants affected by the change;
- run the required quality gates for the scope;
- inspect the final diff before requesting review;
- perform a self-review that tries to break the implementation;
- report blockers honestly and stop when the approved architecture is unclear;
- prevent regressions in evidence integrity, source authority, historical cutoff behavior, unavailable-state handling, and decision-system boundaries.

Codex must not:

- implement later sprint phases early;
- modify unrelated runtime behavior;
- add hidden provider, scheduler, CLI, scoring, ranking, valuation, committee, timing, alert, recommendation, portfolio, or trading behavior;
- treat fixtures, samples, caller-supplied payloads, or convenience defaults as canonical evidence;
- stage, commit, push, tag, or release outside the user's explicit instruction.

## 5. Claude Responsibilities

Claude is responsible for independent review. Independence means Claude must verify the implementation directly rather than summarize or approve Codex's explanation.

Claude must review:

- architecture consistency with `docs/HUNTER_ARCHITECTURE_MANIFEST.md`, `docs/HUNTER_ARCHITECTURE_SPEC.md`, `docs/CANONICAL_RUNTIME_ARCHITECTURE.md`, and the active sprint;
- repository, provider, adapter, service, scheduler, CLI, dashboard, report, and persistence boundaries;
- documentation consistency across canonical architecture documents and feature-specific specifications;
- naming consistency, vocabulary preservation, and domain model clarity;
- migration safety, rollback assumptions, backfill behavior, replay safety, and compatibility with existing data;
- evidence lifecycle, source authority, immutable lineage, conflict preservation, and unavailable-state handling;
- deterministic behavior, retry safety, idempotency, ordering stability, and checkpoint or restart safety;
- boundary validation for security, caller trust, registry authority, provider credentials, and arbitrary payload injection;
- future maintainability, hidden coupling, duplicate truth, and API cleanliness.

Claude must classify findings as blockers only when they meet the blocker definition in this protocol. Non-blocking recommendations must not be used to delay merge once all blockers are resolved.

## 6. Merge Requirements

A pull request must never be merged before all of the following are true:

- required tests and quality gates pass or an explicitly approved exception is documented;
- Codex self-review is complete;
- independent architectural review is complete;
- documentation consistency review is complete;
- all release-blocking findings are fixed;
- re-review confirms that blocker fixes did not introduce new blockers;
- the diff contains only the approved scope;
- generated runtime data, secrets, credentials, caches, and unrelated local configuration are excluded;
- migration and replay implications are documented when applicable.

Approval is not valid if it is based only on test success. Review must include source inspection and architecture reasoning.

## 7. Blocker Definition

A blocker is a concrete defect that makes a change unsafe to merge because it violates project architecture, production safety, evidence integrity, replay correctness, operational boundaries, security, or documentation truth.

Blockers include:

- architecture violation;
- repository or provider boundary bypass;
- evidence leak, fabricated evidence, or mutable canonical evidence;
- source-authority bypass or arbitrary caller-controlled provenance;
- historical replay risk or cutoff leakage;
- migration risk, destructive schema behavior, or missing compatibility path;
- checkpoint inconsistency, restart risk, or non-idempotent retry behavior;
- runtime ambiguity or hidden behavior change;
- documentation contradiction with implementation or canonical architecture;
- security boundary violation, credential exposure, or privilege escalation path;
- scoring, ranking, valuation, committee, timing, alert, recommendation, portfolio, or trading side effect outside approved scope;
- tests that claim coverage but do not prove the required invariant;
- duplicate canonical truth or silent winner selection where conflicts must be preserved.

A blocker report must identify the root cause, risk, required fix, affected files, and required tests. It must be precise enough that the implementer can act without guessing.

## 8. Non-blocking Findings

Non-blocking findings are improvements that may increase clarity, performance, ergonomics, documentation depth, test breadth, or maintainability but do not make the current change unsafe to merge.

Recommendations are non-blocking when:

- the approved architecture remains intact;
- production behavior remains explicit and safe;
- replay, evidence, migration, and source authority are not compromised;
- the issue can be handled later without creating permanent ambiguity;
- the documentation is not false or materially misleading.

Reviewers must not inflate preferences into blockers. Conversely, contributors must not downgrade architectural violations to recommendations because a fix is inconvenient.

## 9. Required Review Report Format

### Blocker Report

Use this format for every release-blocking finding:

```text
BLOCKER:
Root cause:
Risk:
Fix:
Files:
Required tests:
```

Each field is mandatory. The report must name exact files and explain the invariant that is broken.

### Fixed Report

Use this format after blocker fixes:

```text
FIXED:
- files changed:
- architectural changes:
- migration impact:
- verification results:
- confirmation that all blockers are resolved:
```

The fixed report must distinguish implementation changes from verification results. It must not claim success for tests or reviews that were not run.

### Approval Report

Use this format only when no blockers remain:

```text
APPROVED FOR MERGE

Remaining improvements (non-blocking):
1. ...
2. ...
```

If there are no meaningful improvements, state `None` under remaining improvements.

## 10. Documentation Governance

Every architecture change must keep Project Hunter documentation internally consistent. Documentation is part of the architecture, not a release note afterthought.

The following documents and surfaces must remain aligned when affected:

- `docs/PROJECT_PRINCIPLES.md`;
- `docs/PROJECT_CONSTITUTION.md`;
- `docs/VISION.md`;
- `docs/HUNTER_ARCHITECTURE_MANIFEST.md`;
- `docs/HUNTER_ARCHITECTURE_SPEC.md`;
- `docs/HUNTER_ROADMAP.md`;
- `docs/CANONICAL_RUNTIME_ARCHITECTURE.md`;
- `docs/DEVELOPMENT_GOVERNANCE.md`;
- `docs/DASHBOARD.md`;
- `docs/PIPELINE_ORCHESTRATOR.md`;
- `docs/PIPELINE_PERSISTENCE_INTEGRATION.md`;
- `docs/INVESTMENT_COMMITTEE_ENGINE.md`;
- `docs/OPPORTUNITY_TIMING_ENGINE.md`;
- `docs/CODEX_IMPLEMENTATION_GUIDE.md`;
- `docs/SPRINTS/<version>.md`;
- configuration documentation and command examples for any changed operational surface.

Documentation review must verify:

- canonical runtime status is accurate;
- dashboard behavior remains read-only and presentation-only unless explicitly changed;
- pipeline orchestration boundaries remain clear;
- committee and timing documents correctly distinguish production and experimental paths;
- architecture specs do not contradict sprint implementation;
- source coverage and provider claims match verified behavior;
- unavailable, unknown, partial, stale, ambiguous, contested, proxy, and unsupported states remain explicit;
- strict known-by-Hunter replay and reconstructed replay are described distinctly where relevant;
- limitations are visible and not disguised as complete coverage.

When documents conflict, contributors must stop and resolve the conflict before merge. A sprint specification may narrow release scope, but it does not override the permanent principles, architecture manifest, or architecture specification.

## 11. One-PR Rule

Each PR must contain one architectural concern and no unrelated changes.

Allowed examples:

- one provider boundary and its tests;
- one additive migration and repository surface;
- one replay-safety fix and regression tests;
- one documentation governance update;
- one dashboard rendering change without runtime behavior changes.

Disallowed examples:

- combining scoring changes with provider acquisition;
- mixing migration fixes with cosmetic refactors;
- changing dashboard behavior while adding scheduler jobs;
- modifying runtime configuration while implementing unrelated domain models;
- adding a new adapter and changing committee decision logic in the same PR.

If a change requires multiple architectural concerns to remain safe, the PR description must state why they cannot be separated and the reviewer must evaluate that coupling explicitly.

## 12. AI Collaboration Protocol

The standard collaboration pattern is:

1. Codex implements the approved scope.
2. Codex runs required tests and performs a self-review.
3. Claude independently reviews the diff, architecture, documentation, tests, and surrounding boundaries.
4. Codex fixes only the confirmed blockers.
5. Claude re-verifies the fixes.
6. Merge occurs only after approval.

Human contributors follow the same pattern. A human may implement and another human may review, but the independence requirement remains. Future AI agents must be assigned an explicit role: implementer, reviewer, or verifier. An agent must not approve its own implementation without independent review unless the user explicitly limits the task to a local, non-mergeable draft.

The reviewer must try to break the implementation. The implementer must treat blocker reports as architectural requirements, not stylistic feedback.

## 13. Long-term Maintenance

This protocol exists to keep Project Hunter coherent over many years of incremental development.

Long-term systems fail when small exceptions accumulate: a provider bypasses the registry, a migration assumes an empty database, a replay report leaks current state, a dashboard triggers analysis, a fixture becomes canonical evidence, a scheduler starts work outside the approved lifecycle, or documentation silently claims more than the implementation supports. Each individual shortcut may seem harmless. Together they produce an architecture that cannot be trusted.

The mandatory review lifecycle prevents that drift by requiring every contribution to prove:

- the change is scoped;
- the implementation follows existing boundaries;
- persistence remains durable, indexed, idempotent, and replay-safe;
- evidence remains immutable and provenance-backed;
- operational state remains separate from analytical conclusions;
- unavailable and ambiguous states remain visible;
- documentation matches implementation;
- future maintainers can understand the decision without reconstructing intent from chat history.

Project Hunter should be maintained as a durable evidence system, not a sequence of disconnected feature additions. This protocol is the permanent operating standard for preserving that discipline.
