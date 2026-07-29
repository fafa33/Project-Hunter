# Project Hunter Canonical Architecture Map

## Purpose

This document is the canonical navigation and document-authority map for Project Hunter.

It identifies:

- the canonical document hierarchy;
- the major architectural domains;
- their ownership boundaries;
- their relationships; and
- the direction of information flow throughout the system.

It does not replace the rules owned by the documents it maps. It establishes document precedence and architectural navigation only.

---

# Canonical Document Authority Hierarchy

The authoritative document hierarchy for Project Hunter is:

1. `docs/PROJECT_CONSTITUTION.md`
2. `docs/PROJECT_PRINCIPLES.md`
3. `docs/CANONICAL_ARCHITECTURE_MAP.md`
4. `docs/HUNTER_ARCHITECTURE_MANIFEST.md`
5. `docs/HUNTER_ARCHITECTURE_SPEC.md`
6. `docs/CANONICAL_RUNTIME_ARCHITECTURE.md`
7. Accepted ADRs in `docs/ADR/`
8. `docs/VISION.md`
9. `docs/HUNTER_ROADMAP.md`
10. `docs/DEVELOPMENT_GOVERNANCE.md`
    - `docs/ARCHITECTURE_DECISION_PREPARATION_GUIDE.md` is a mandatory Stage 1 preparation standard for architecturally significant changes. It derives authority from Development Governance and creates no independent governance or architectural authority.
11. `docs/HUNTER_IMPLEMENTATION_CONTRACT.md`
12. `docs/AI_REVIEW_PROTOCOL.md`
13. Versioned sprint specifications in `docs/SPRINTS/`
14. `docs/CODEX_IMPLEMENTATION_GUIDE.md`

The Constitution remains the highest authority.

Accepted ADRs are binding architectural decisions unless explicitly superseded or deprecated by a later accepted ADR.

Architecture Decision Preparation Records in `docs/architecture-records/` are permanent, non-authoritative decision-basis records. They preserve evidence, assumptions, alternatives, falsification, and readiness reasoning but do not approve architecture or implementation.

`docs/architecture-index.md` is a navigation and traceability registry only. It creates no independent authority and does not replace the records or decisions it links.

Sprint specifications authorize implementation scope only. They do not redefine architecture, runtime authority, governance, or engineering principles.

Nothing lower in this hierarchy may contradict anything above it.

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

This overview maps the accepted logical and target architecture. Current production, experimental, unavailable, and deferred classifications remain governed by accepted ADRs and the canonical runtime documents.

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

Each architectural concept must have one canonical owner. Accepted architecture does not imply completed or production-authoritative implementation.

---

# Evidence Assembly Precondition (Canonical Valuation)

ADR 0025 establishes a service-owned Canonical Evidence Assembly Authority immediately upstream of Canonical Valuation's methodology consumption, applicable only where a valuation methodology snapshot explicitly declares acceptance of assembled evidence:

```text
Native Fundamental Valuation Evidence
        ↓
Evidence Assembly Authority
        ↓
Assembled Fundamental Evidence
        ↓
Methodology Contract Evaluation
        ↓
Canonical Valuation
        ↓
Fair-Value Estimate
```

The Evidence Shape Registry is versioned reference data consulted by the Evidence Assembly Authority, not a pipeline stage; it makes no valuation decisions. This precondition is accepted architecture under ADR 0025 only. The Canonical Evidence Assembly Authority and its Assembled Fundamental Evidence record family are not implemented, and the first accepted canonical valuation methodology (ADR 0022) does not declare acceptance of assembled evidence.

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

Higher analytical layers consume authorized outputs from lower layers.

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

# Relationship to Other Canonical Documents and Records

| Document or artifact | Responsibility |
|----------|----------------|
| PROJECT_CONSTITUTION | Constitutional governance |
| PROJECT_PRINCIPLES | Engineering and architectural principles |
| CANONICAL_ARCHITECTURE_MAP | Document precedence and architectural navigation |
| HUNTER_ARCHITECTURE_MANIFEST | Architectural direction |
| HUNTER_ARCHITECTURE_SPEC | Logical architecture |
| CANONICAL_RUNTIME_ARCHITECTURE | Runtime execution architecture |
| Accepted ADRs | Binding architectural decisions |
| VISION | Long-term purpose |
| HUNTER_ROADMAP | Strategic evolution |
| DEVELOPMENT_GOVERNANCE | Development process authority |
| ARCHITECTURE_DECISION_PREPARATION_GUIDE | Mandatory Stage 1 preparation standard under Development Governance; no independent authority |
| Architecture Decision Preparation Records | Permanent evidence and reasoning records; non-authoritative |
| architecture-index.md | Navigation and traceability registry only |
| HUNTER_IMPLEMENTATION_CONTRACT | Production implementation rules |
| AI_REVIEW_PROTOCOL | Independent review protocol |
| Sprint specifications | Versioned implementation scope |

---

# Ownership Boundary

This document owns:

- canonical document precedence;
- architectural navigation;
- high-level domain topology;
- references between canonical owner documents.

This document does not own:

- constitutional rules;
- detailed architectural decisions;
- runtime execution semantics;
- implementation contracts;
- development procedures;
- sprint scope;
- release-specific implementation status.

Those responsibilities remain with their respective canonical documents.

---

# Maintenance Rule

Whenever a canonical document changes document precedence or architectural ownership, this map must be updated first or in the same governed change.

This document maps accepted authority and architecture. It must not invent a new runtime authority, analytical methodology, or implementation claim.
