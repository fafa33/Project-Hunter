# Project Hunter Constitution

## 1 Vision

Project Hunter is a long-term crypto research, intelligence, discovery, monitoring, and opportunity-hunting platform.

Its purpose is not merely to rank crypto projects. Its purpose is to discover high-conviction opportunities before they become obvious by continuously converting public evidence into durable intelligence.

Project Hunter must evolve as an institutional research platform: evidence-first, deterministic, extensible, auditable, and capable of supporting many analytical domains without architectural drift.

## 2 Mission

The engineering mission of Project Hunter is to provide accurate, deterministic, evidence-based decision support for crypto research and opportunity discovery.

The platform must support continuous intelligence across projects, sectors, markets, protocols, chains, developers, liquidity, valuation, macro conditions, on-chain behavior, and future analytical domains.

All implementation must prioritize institutional-quality research, long-term maintainability, transparent reasoning, and reproducible outputs over short-term feature velocity.

## 3 Core Engineering Philosophy

Project Hunter is governed by the following engineering philosophy:

- Long-term thinking over short-term convenience.
- Infrastructure before features.
- Quality before speed.
- Architecture before implementation.
- Evidence before opinion.
- Automation over manual work.
- Determinism over randomness.
- Reliability over convenience.
- Maintainability over cleverness.
- Explicit contracts over implicit behavior.

## 4 Architectural Principles

Project Hunter must be built according to the following architectural principles:

- Single Responsibility: every module, engine, repository, adapter, and renderer must have a clearly bounded purpose.
- Loose Coupling: components must depend on contracts, not concrete implementation details, wherever practical.
- High Cohesion: related behavior must remain together; unrelated behavior must not be combined for convenience.
- Dependency Injection: dependencies must be passed explicitly rather than created through hidden global state.
- Plugin Architecture: new analytical domains, data sources, and integrations must be addable without destabilizing existing systems.
- Interface-Driven Design: shared contracts must define how components communicate.
- Composition Over Inheritance: behavior should be assembled through small components unless inheritance is clearly justified.
- No Circular Dependencies: architectural layers must remain acyclic.
- No Hidden Side Effects: operations must make persistence, I/O, mutation, and external calls explicit.
- Configuration Over Hardcoding: weights, thresholds, sources, stage ordering, and operational settings must live in configuration.
- Future Extensibility: every stable subsystem must allow future expansion without rewriting core architecture.
- Backward Compatibility: persisted data, command behavior, reports, and public contracts must remain stable unless a formal migration is required.

## 5 Engine Design Rules

Every engine in Project Hunter must:

- Have one responsibility.
- Be independently testable.
- Communicate only through shared contracts.
- Never directly depend on another engine.
- Be replaceable without rewriting unrelated systems.
- Be discoverable through clear package structure and naming.
- Be pluggable through configuration or explicit orchestration.
- Consume persisted evidence, metrics, snapshots, or declared inputs.
- Produce structured outputs that can be persisted, rendered, tested, and audited.

Engines must not own persistence, command-line behavior, scheduling, report formatting, or unrelated orchestration logic.

## 6 Pipeline Principles

Project Hunter must maintain one permanent execution entry point for the analytical pipeline.

Pipeline execution must be deterministic. Given the same persisted inputs and configuration, the same pipeline stage must produce the same output.

Execution order must be configurable. Pipeline stages must declare their dependencies, required inputs, produced outputs, retry behavior, and failure policy.

Stages must support retries where external sources or transient operations are involved. Retry behavior must be explicit and bounded.

The pipeline must be designed for future distributed execution. Stage contracts must not assume that all execution occurs in one process, one machine, or one synchronous runtime.

## 7 Data Principles

Evidence is immutable.

No system may fabricate data. Missing values must remain missing. Unknown values must not be estimated unless an explicitly named estimation model exists and the output is clearly marked as estimated.

Every score must be traceable to evidence, metrics, snapshots, or persisted analytical records.

Every report must reference evidence-backed outputs. Reports must not introduce unsupported claims.

Historical snapshots must never be overwritten. New analysis creates new records. Historical analysis must remain reproducible from persisted data and configuration.

Data freshness, source quality, missing evidence, and confidence must remain first-class analytical concerns.

## 8 Scoring Principles

Scores must always be:

- Transparent.
- Reproducible.
- Deterministic.
- Evidence-backed.
- Explainable.
- Configurable.
- Auditable.

No black-box scoring is permitted.

Every score must include a numeric value, confidence, explanation, contributing metrics, missing evidence, and the relevant configuration weight or treatment.

Scoring must never query external APIs. Scoring must consume only persisted evidence, metrics, snapshots, or other approved persisted analytical records.

## 9 AI Principles

Future AI components are assistants.

AI never replaces evidence. AI may assist reasoning, summarization, classification, workflow guidance, or research triage only when its output remains subordinate to verifiable evidence.

AI cannot fabricate facts, sources, data, citations, historical events, financial claims, or legal claims.

Every AI-assisted output must remain explainable, reviewable, and traceable to evidence or clearly labeled as non-authoritative assistance.

AI components must not silently alter scores, evidence, rankings, alerts, backtests, or persisted analytical history.

## 10 Automation Principles

Automation exists to execute architecture.

Automation never owns business logic.

Schedulers execute workflows. Workflows execute engines. Engines execute analysis.

Automation layers must remain thin, observable, retryable, and replaceable. They must not contain scoring formulas, ingestion logic, ranking rules, alert conditions, or report semantics.

## 11 Coding Standards

Project Hunter code must be professional Python.

All production code must use type hints. Names must be meaningful. Modules must be small. Functions must be small enough to be understood, tested, and maintained.

Public APIs must be documented. Internal behavior must be readable without excessive comments.

Unnecessary abstraction must be avoided. Duplicated logic must be consolidated when doing so improves clarity and reduces risk.

Code must prefer explicit data structures, clear contracts, deterministic behavior, and maintainable control flow.

## 12 Testing Standards

Every module must be testable.

Regression tests are required for every bug fix and every architectural behavior that must not regress.

Tests must be deterministic by default. External APIs must be mocked unless a test is explicitly marked as an integration test.

The test suite must include unit tests, repository tests, pipeline tests, report tests, CLI tests, and integration tests where appropriate.

Performance tests must be added where scale, latency, persistence volume, or repeated pipeline execution creates material risk.

## 13 Documentation Standards

Every major component must be documented.

Architecture documentation must evolve together with implementation. A component that changes its contract, responsibility, persistence model, or pipeline role must update the corresponding architectural documentation.

Documentation is part of the product. It must be accurate, current, concise, and suitable for future maintainers, contributors, and automated engineering systems.

Documentation must not invent claims, capabilities, integrations, or guarantees that the implementation does not provide.

## 14 Extensibility Rules

Future additions must require minimal architectural changes.

This includes, but is not limited to:

- Opportunity Timing Engine.
- AI Agents.
- News Intelligence.
- Social Intelligence.
- Macro Intelligence.
- On-chain Intelligence.
- Sentiment Analysis.
- Portfolio Management.
- Dashboards.
- APIs.
- Distributed Workers.

New capabilities must integrate through existing contracts where possible: evidence, metrics, snapshots, repositories, engines, renderers, pipeline stages, configuration, and tests.

No new domain may bypass evidence persistence, deterministic analysis boundaries, or traceability requirements.

## 15 Long-Term Development Principles

Project Hunter must be built for years, not milestones.

Avoid technical debt. Prefer stable architecture. Avoid unnecessary rewrites. Every milestone should improve the platform rather than replace previous work.

Compatibility, migration safety, test coverage, operational clarity, and maintainability must be considered part of implementation quality.

When a feature conflicts with architecture, architecture governs unless the constitution is formally updated.

## 16 Governance

This constitution is the highest architectural authority of Project Hunter.

Future implementations, contributors, AI systems, Codex sessions, maintainers, and automated agents must comply with this document unless the constitution itself is formally updated.

Any change that conflicts with this constitution requires an explicit constitutional update. Implementation convenience, milestone pressure, or local optimization is not sufficient justification for violating constitutional principles.

Project Hunter may evolve, but it must evolve through disciplined architecture, evidence-backed systems, deterministic analysis, and long-term maintainability.
