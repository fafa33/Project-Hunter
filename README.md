# Project Hunter

Project Hunter is a deterministic, evidence-first cryptocurrency intelligence platform designed to discover, validate, prioritize, and continuously monitor asymmetric investment opportunities through auditable, explainable, and replayable analytical workflows.

Project Hunter is governed by a specification-first architecture. Every implementation, runtime behavior, engineering decision, and release must comply with the project's canonical governance hierarchy.

Current stable release: `v1.0.0`

Project Hunter V1 is frozen. Maintenance continues on `release/v1`, while all future development is performed on `main`.

---

# Governance

Project Hunter follows a single canonical governance hierarchy.

Every document, implementation, runtime component, ADR, sprint, and engineering decision derives its authority from this hierarchy.

1. `docs/PROJECT_CONSTITUTION.md`
2. `docs/PROJECT_PRINCIPLES.md`
3. `docs/CANONICAL_ARCHITECTURE_MAP.md`
4. `docs/HUNTER_ARCHITECTURE_MANIFEST.md`
5. `docs/HUNTER_ARCHITECTURE_SPEC.md`
6. `docs/CANONICAL_RUNTIME_ARCHITECTURE.md`
7. Accepted ADRs (`docs/ADR/`)
8. `docs/VISION.md`
9. `docs/HUNTER_ROADMAP.md`
10. `docs/DEVELOPMENT_GOVERNANCE.md`
11. `docs/HUNTER_IMPLEMENTATION_CONTRACT.md`
12. `docs/AI_REVIEW_PROTOCOL.md`
13. Sprint Specifications (`docs/SPRINTS/`)
14. `docs/CODEX_IMPLEMENTATION_GUIDE.md`

Nothing lower in the hierarchy may contradict anything above it.

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
- `docs/CANONICAL_ARCHITECTURE_MAP.md`
- `docs/DEVELOPMENT_GOVERNANCE.md`
- `docs/HUNTER_IMPLEMENTATION_CONTRACT.md`

## Architecture

- `docs/HUNTER_ARCHITECTURE_MANIFEST.md`
- `docs/HUNTER_ARCHITECTURE_SPEC.md`
- `docs/CANONICAL_RUNTIME_ARCHITECTURE.md`

## Architecture Decisions

- `docs/ADR/README.md`

## Sprint Specifications

- `docs/SPRINTS/README.md`

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