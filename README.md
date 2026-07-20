# Project Hunter

Project Hunter is a deterministic, evidence-first cryptocurrency intelligence platform designed to discover, validate, prioritize, and continuously monitor asymmetric investment opportunities through auditable, explainable, and replayable analytical workflows.

Project Hunter is governed by a specification-first governance model. Every implementation, runtime behavior, engineering decision, and release must comply with the project's canonical governance hierarchy.

Current stable release: `v1.0.0`

Project Hunter V1 is frozen. Maintenance continues on `release/v1`, while all future development is performed on `main`.

---

# Governance

Project Hunter follows a single canonical governance model.

The canonical authority hierarchy for the project is defined exclusively in:

`docs/SPRINTS/README.md`

Every implementation, runtime behavior, engineering decision, architecture document, release, review, and automated engineering agent must comply with that hierarchy.

No other document may define, duplicate, reinterpret, or compete with the canonical authority hierarchy.

Each architectural fact has exactly one owner document. Other documents may summarize or reference an architectural fact, but they must not redefine, reinterpret, or duplicate it.

When an architectural fact changes, its owner document must be updated first before any dependent document is updated.

If a conflict exists between documents, the conflict must be resolved according to the canonical governance hierarchy before implementation continues.

---

# Major Analytical Engines

Project Hunter currently contains the following analytical engines:

- Discovery
- Evidence
- Validation
- Developer Intelligence
- Tokenomics Intelligence
- Governance Intelligence
- Security Intelligence
- On-chain Intelligence
- Protocol Intelligence
- Whale Intelligence
- Macro Intelligence
- News Intelligence
- Narrative Intelligence
- Social Intelligence
- Intelligence Fusion
- Valuation
- Comparative Valuation
- Mispricing
- Asymmetry
- Opportunity Timing
- Probability
- Pattern Matching
- Technology Necessity
- Capital Rotation
- Investment Committee

---

# Platform Components

Core platform components include:

- Pipeline Orchestrator
- Plugin Architecture
- Deterministic Execution
- Canonical Runtime
- SQL Persistence Layer
- Repository Contracts
- Evidence Layer
- Intelligence Layer
- Automation & Scheduler
- Dashboard
- Reporting
- Ranking
- Historical Replay
- Backtesting
- Validation
- Operational Monitoring

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

---

# Repository Structure

```
docs/               Governance, architecture and documentation
configs/            Runtime configuration
src/hunter/         Production source code
tests/              Automated verification
alembic/            Database migrations
```

Major packages:

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

---

# Documentation

## Governance

- `docs/PROJECT_CONSTITUTION.md`
- `docs/PROJECT_PRINCIPLES.md`
- `docs/DEVELOPMENT_GOVERNANCE.md`
- `docs/HUNTER_IMPLEMENTATION_CONTRACT.md`

## Architecture

- `docs/CANONICAL_ARCHITECTURE_MAP.md`
- `docs/HUNTER_ARCHITECTURE_MANIFEST.md`
- `docs/HUNTER_ARCHITECTURE_SPEC.md`
- `docs/CANONICAL_RUNTIME_ARCHITECTURE.md`

## Vision & Roadmap

- `docs/VISION.md`
- `docs/HUNTER_ROADMAP.md`

## Architecture Decisions

- `docs/ADR/README.md` (accepted architecture decisions)

## Sprint Specifications

- `docs/SPRINTS/README.md` (canonical sprint governance)

## Component Documentation

Documentation for individual subsystems is located throughout the `docs/` directory.

---

# Release Information

- Stable Release: `v1.0.0`
- Stable Branch: `release/v1`
- Active Development Branch: `main`

---

# Verification

Project Hunter requires deterministic verification before every release.

Required quality gates include:

- Ruff
- Black
- mypy
- pytest
- Replay validation
- Runtime validation
- Architecture compliance
- Governance compliance

---

# Project Principles

Project Hunter is built around the following engineering principles:

- Evidence before conclusions
- Deterministic execution
- Explainable analysis
- Immutable evidence provenance
- Replay correctness
- Point-in-time truth
- Explicit unavailable states
- Idempotent persistence
- Architecture before implementation
- Governance before engineering

---

# Scope

Project Hunter provides analytical decision support.

Project Hunter does **not**:

- Execute trades
- Manage portfolios
- Guarantee investment outcomes
- Produce fabricated evidence
- Hide missing data
- Override governance rules

---

# Roadmap

The canonical roadmap is maintained in:

`docs/HUNTER_ROADMAP.md`

Future work is planned and approved exclusively through the governance process defined by the project's canonical document hierarchy.