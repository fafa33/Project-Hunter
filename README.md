# Project Hunter

Project Hunter is a deterministic, evidence-first cryptocurrency intelligence platform designed to discover, validate, prioritize, and continuously monitor asymmetric investment opportunities through auditable, explainable, and replayable analytical workflows.

Project Hunter is governed by a specification-first governance model. Every implementation, runtime behavior, engineering decision, and release must comply with the project's canonical governance hierarchy.

Current stable release: `v1.0.0`

Project Hunter V1 is frozen. Maintenance continues on `release/v1`, while future development is performed on `main`.

---

# Governance

Project Hunter follows one canonical governance model.

The canonical document-authority hierarchy is owned and defined by:

`docs/CANONICAL_ARCHITECTURE_MAP.md`

Every implementation, runtime behavior, engineering decision, architecture document, release, review, and automated engineering agent must comply with that hierarchy.

Each architectural fact has one canonical owner. Supporting documents may summarize or reference that fact, but must not create a competing definition.

When an architectural fact changes, its owner document must be updated first or in the same governed change as dependent documents.

Conflicts must be resolved according to the canonical hierarchy before implementation continues.

---

# Current Authority Classification

Implementation existence does not by itself establish production analytical authority. Current classifications are governed by accepted ADRs, especially ADR 0007 and ADR 0016–0021.

## Canonical production analytical authority

- Evidence-backed Market Validation runtime.
- `EvidenceBackedProjectExecutor` as the production deep-analysis composition and scoring boundary.
- Market Validation `hunter_score` and project ranking.
- Canonical production Timing through `hunter.timing.OpportunityTimingEvidenceEngine` as consumed by Market Validation.
- Canonical committee fields contained in Market Validation project results.
- Explainability and reports emitted by the canonical Market Validation path.

## Production descriptive intelligence

The following service-owned engines may produce evidence-backed descriptive findings under their accepted ADR contracts. They do not independently own scoring, ranking, timing, recommendation, valuation, or cross-engine composition:

- Developer Intelligence.
- Tokenomics Intelligence.
- Governance Intelligence.
- Security Intelligence.
- On-chain Intelligence.

Other evidence and domain packages may exist in the repository, but their production authority depends on an explicit accepted contract rather than package presence.

## Experimental or research capabilities

The following remain experimental or research infrastructure unless a later accepted ADR explicitly promotes a defined output:

- `PipelineOrchestrator` and plugin analytical orchestration.
- Intelligence Fusion.
- Opportunity scoring and Opportunity ranking.
- Fusion-backed Opportunity Timing.
- Probability.
- Pattern Matching.
- Technology Necessity.
- Standalone Investment Committee.
- General ranking helpers.

Experimental outputs must remain clearly classified and cannot substitute for canonical production outputs.

## Accepted target or currently unavailable capabilities

- Canonical Valuation.
- Comparative Valuation.
- Mispricing.
- Asymmetry.
- Portfolio Intelligence.

ADR 0021 defines future valuation-family evidence and authority contracts, but all four Market Validation valuation-family scalar inputs remain explicitly unavailable until the required records, methodologies, calibration, normalization, and service-owned persistence paths are implemented and accepted.

## Operational and presentation components

The following are supporting or downstream components, not analytical authorities:

- Automation and Scheduler.
- Operational Corpus.
- Persistence and repository infrastructure.
- Dashboard API.
- Desktop console and Hunter Terminal.
- Operational monitoring and health projections.

---

# Platform Components

Core platform components include:

- Deterministic execution.
- Canonical runtime.
- SQL persistence and repository contracts.
- Evidence acquisition and evidence intelligence.
- Plugin architecture.
- Automation and Scheduler.
- Dashboard API and presentation surfaces.
- Reporting.
- Historical replay and backtesting.
- Validation and operational monitoring.

---

# Installation

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e ".[dev]"
```

---

# Quick Start

Run the complete quality gates:

```bash
.venv/bin/ruff check .
.venv/bin/black --check src tests config alembic
.venv/bin/mypy
.venv/bin/pytest
```

Example commands:

```bash
hunter discover
hunter analyze bitcoin
hunter validate ethereum
hunter whales bitcoin
hunter committee champion
hunter rank --sort committee
hunter dashboard build --sqlite-path hunter.sqlite
hunter automation status
```

Command availability does not change the authority classification of the output it invokes.

---

# Repository Structure

```text
docs/               Governance, architecture, and documentation
configs/            Runtime configuration
src/hunter/         Production and explicitly classified source packages
tests/              Automated verification
alembic/            Database migrations
```

Major packages include:

- `automation`
- `committee`
- `dashboard`
- `discovery`
- `evidence`
- `execution`
- `historical`
- `intelligence`
- `macro`
- `necessity`
- `onchain`
- `opportunity`
- `patterns`
- `persistence`
- `probability`
- `providers`
- `ranking`
- `reports`
- `security`
- `sufficiency`
- `tokenomics`
- `validation`

A package name indicates repository capability, not production authority.

---

# Documentation

## Canonical hierarchy and architecture

- `docs/CANONICAL_ARCHITECTURE_MAP.md`
- `docs/PROJECT_CONSTITUTION.md`
- `docs/PROJECT_PRINCIPLES.md`
- `docs/HUNTER_ARCHITECTURE_MANIFEST.md`
- `docs/HUNTER_ARCHITECTURE_SPEC.md`
- `docs/CANONICAL_RUNTIME_ARCHITECTURE.md`

## Governance and implementation

- `docs/DEVELOPMENT_GOVERNANCE.md`
- `docs/HUNTER_IMPLEMENTATION_CONTRACT.md`
- `docs/AI_REVIEW_PROTOCOL.md`

## Vision and roadmap

- `docs/VISION.md`
- `docs/HUNTER_ROADMAP.md`

## Architecture decisions

- `docs/ADR/README.md`

## Sprint specifications

- `docs/SPRINTS/README.md`

Component-specific documentation is maintained throughout `docs/`.

---

# Release Information

- Stable Release: `v1.0.0`
- Stable Branch: `release/v1`
- Active Development Branch: `main`

---

# Verification

Project Hunter requires deterministic verification before every release.

Required quality gates include:

- Ruff.
- Black.
- mypy.
- pytest.
- Replay validation.
- Runtime validation.
- Architecture compliance.
- Governance compliance.

---

# Project Principles

Project Hunter is built around:

- Evidence before conclusions.
- Deterministic execution.
- Explainable analysis.
- Immutable evidence provenance.
- Replay correctness.
- Point-in-time truth.
- Explicit unavailable states.
- Idempotent persistence.
- Architecture before implementation.
- Governance before engineering.

---

# Scope

Project Hunter provides analytical decision support.

Project Hunter does not:

- execute trades;
- guarantee investment outcomes;
- fabricate evidence;
- hide missing data;
- convert missing information into optimistic defaults;
- allow operational or presentation components to override analytical authority;
- override governance rules.

---

# Roadmap

The canonical roadmap is maintained in:

`docs/HUNTER_ROADMAP.md`

Future work is planned and approved exclusively through the governance process defined by the canonical document hierarchy.