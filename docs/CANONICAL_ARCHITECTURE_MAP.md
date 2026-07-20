# Project Hunter Canonical Architecture Map

## Purpose

This document provides the canonical navigation map for Project Hunter.

It identifies the major architectural domains, their ownership boundaries, their relationships, and the direction of information flow throughout the system.

It does not define governance, architecture, implementation rules, runtime behavior, engineering procedures, or release planning. Those responsibilities belong to their respective canonical documents.

---

# Canonical Architecture Overview

```text
Market
    │
    ▼
Market Discovery
    │
    ▼
Canonical Identity & Trust
    │
    ▼
Evidence Acquisition
    │
    ▼
Evidence Intelligence
    │
    ▼
Evidence Fusion
    │
    ▼
Canonical Valuation
    │
    ▼
Comparative Valuation
    │
    ▼
Mispricing Intelligence
    │
    ▼
Asymmetry Intelligence
    │
    ▼
Opportunity Intelligence
    │
    ▼
Prediction Intelligence
    │
    ▼
Portfolio Intelligence
    │
    ▼
Dashboard API
    │
    ▼
Hunter Terminal
```

Operational Execution supports the runtime.

Historical Validation evaluates analytical quality without modifying historical truth.

---

# Architectural Ownership

| Architectural Domain | Primary Responsibility |
|----------------------|------------------------|
| Market Discovery | Discover the investable market |
| Canonical Identity & Trust | Determine canonical economic entities |
| Evidence Acquisition | Acquire and preserve trustworthy observations |
| Evidence Intelligence | Interpret evidence within individual analytical domains |
| Evidence Fusion | Integrate authoritative domain intelligence |
| Canonical Valuation | Produce authoritative valuation intelligence |
| Comparative Valuation | Compare valuation across valid peers |
| Mispricing Intelligence | Evaluate valuation gaps |
| Asymmetry Intelligence | Evaluate long-term investment asymmetry |
| Opportunity Intelligence | Determine investment opportunity quality |
| Prediction Intelligence | Estimate and evaluate future outcomes |
| Portfolio Intelligence | Produce portfolio-level decision support |
| Operational Execution | Execute workflows and maintain runtime health |
| Dashboard API | Expose read-only authoritative outputs |
| Hunter Terminal | Present information to users |

Each architectural domain owns its responsibility exclusively.

---

# Dependency Direction

Information flows only in the following direction:

```text
Discovery
    ↓
Identity
    ↓
Evidence
    ↓
Domain Intelligence
    ↓
Evidence Fusion
    ↓
Valuation
    ↓
Opportunity
    ↓
Prediction
    ↓
Portfolio
    ↓
Presentation
```

Higher analytical layers consume authoritative outputs from lower layers.

No layer bypasses earlier responsibilities.

---

# Cross-Cutting Capabilities

The following capabilities apply across every architectural domain:

- Deterministic execution.
- Evidence provenance.
- Historical replay.
- Explainability.
- Point-in-time correctness.
- Confidence representation.
- Missing evidence representation.
- Operational observability.

These capabilities are architectural qualities rather than independent layers.

---

# Supporting Runtime Components

The following runtime components support the analytical architecture without becoming analytical authorities:

- Operational Execution.
- Scheduler.
- Orchestrator.
- Persistence.
- Dashboard API.
- Hunter Terminal.

These components coordinate, store, expose, or visualize analytical outputs but do not create analytical intelligence.

---

# Relationship to Other Canonical Documents

| Document | Responsibility |
|----------|----------------|
| PROJECT_CONSTITUTION | Constitutional governance |
| PROJECT_PRINCIPLES | Engineering and architectural principles |
| ADRs | Architectural decisions |
| VISION | Long-term purpose |
| HUNTER_ROADMAP | Strategic evolution |
| HUNTER_ARCHITECTURE_MANIFEST | Architectural direction |
| HUNTER_ARCHITECTURE_SPEC | Logical architecture |
| CANONICAL_RUNTIME_ARCHITECTURE | Runtime execution architecture |
| DEVELOPMENT_GOVERNANCE | Development process |
| HUNTER_IMPLEMENTATION_CONTRACT | Production implementation rules |

This document serves only as the canonical navigation map connecting these documents.

---

# Maintenance Rule

Whenever a canonical document changes architectural ownership, this map should be updated to reflect the new relationships.

This document never establishes new architectural rules. It only maps the existing canonical architecture.