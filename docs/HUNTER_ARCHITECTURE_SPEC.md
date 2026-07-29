# Project Hunter Architecture Specification

## 1. Purpose

This document defines the logical architecture of Project Hunter.

It specifies the major architectural layers, their responsibilities, their relationships, and the flow of information throughout the system.

Implementation details, release planning, engineering procedures, persistence technologies, and runtime-specific behavior are intentionally defined in their respective canonical documents.

---

# 2. Architectural Overview

Hunter is organized as a layered investment intelligence architecture.

Information flows progressively from raw market observations toward increasingly sophisticated analytical intelligence.

Each architectural layer is responsible for improving the quality, trustworthiness, and usefulness of the information produced by the previous layer.

No layer bypasses the responsibilities of an earlier layer.

---

# 3. High-Level Information Flow

```text
Market
    ↓
Discovery
    ↓
Identity
    ↓
Evidence
    ↓
Screening
    ↓
Prioritization
    ↓
Deep Analysis
    ↓
Investment Intelligence
    ↓
Canonical Valuation
    ↓
Comparative Valuation
    ↓
Mispricing
    ↓
Asymmetry
    ↓
Opportunity
    ↓
Prediction
    ↓
Portfolio
    ↓
Decision Support
```

This diagram expresses logical ownership and dependency direction, not the set of services that currently execute in production. Unavailable target layers preserve their intended architectural position without implying runtime execution. The executable production path is defined separately in `docs/CANONICAL_RUNTIME_ARCHITECTURE.md`.

Information moves only in the forward direction. Each implemented layer consumes validated outputs from earlier implemented layers and produces higher-value analytical outputs for authorized downstream consumers.

---

# 4. Discovery Layer

Purpose:

Continuously discover the investable cryptocurrency market.

Responsibilities include:

- discovering assets;
- discovering protocols;
- discovering ecosystems;
- recording market observations;
- preserving discovery provenance;
- expanding market coverage.

Outputs:

- discovered market entities;
- discovery observations;
- discovery metadata.

---

# 5. Identity Layer

Purpose:

Determine whether multiple observations refer to the same economic entity.

Responsibilities include:

- identity resolution;
- duplicate detection;
- ambiguity management;
- canonical identity creation;
- identity confidence.

Outputs:

- canonical entities;
- identity relationships;
- unresolved ambiguities.

---

# 6. Evidence Layer

Purpose:

Acquire, validate, organize, and preserve trustworthy evidence.

Responsibilities include:

- evidence acquisition;
- provenance preservation;
- confidence estimation;
- evidence traceability;
- evidence freshness;
- historical correctness.

Outputs:

- validated evidence;
- evidence relationships;
- evidence availability.

---

# 7. Screening Layer

Purpose:

Efficiently identify which opportunities deserve deeper analysis.

Responsibilities include:

- candidate screening;
- analyzability assessment;
- evidence sufficiency;
- readiness evaluation;
- analytical prioritization.

Outputs:

- screened candidates;
- readiness assessments;
- prioritization inputs.

---

# 8. Prioritization Layer

Purpose:

Determine where analytical effort should be invested.

Responsibilities include:

- opportunity prioritization;
- analytical queue management;
- investigation ordering;
- analytical resource allocation.

Outputs:

- prioritized opportunities;
- analytical work queue.

---

# 9. Deep Analysis Layer

Purpose:

Perform comprehensive evidence-based investment analysis.

Responsibilities include:

- multi-domain analysis;
- evidence integration;
- analytical reasoning;
- explainable conclusions.

Outputs:

- analytical findings;
- investment intelligence;
- confidence assessments.

---

# 10. Canonical Valuation Layer

Purpose:

Produce authoritative, structured fair-value intelligence for a qualifying economic entity under one immutable, versioned methodology.

Responsibilities include:

- fair-value estimation under the accepted methodology;
- confidence and uncertainty decomposition;
- strict-known replay and correction lineage.

Outputs:

- structured, non-scalar fair-value assessment.

Classification: Production implementation complete and independently audited under ADR 0021, ADR 0022, and ADR 0024. The canonical valuation service, persistence boundary, strict-known replay, correction lineage, and accepted production entry point are implemented and the independent final audit is approved. A successful run against real qualifying evidence remains unavailable because no qualifying disclosure satisfying ADR 0022 has yet been identified; that evidence-availability blocker does not make the software implementation incomplete. Per ADR 0024, this output does not become a Market Validation composition input until a separate accepted ADR authorizes that composition.

---

# 11. Comparative Valuation Layer

Purpose:

Compare a target's valuation against economically compatible peers under a declared cohort policy.

Responsibilities include:

- peer-universe eligibility and comparability;
- relative valuation measurement.

Outputs:

- comparative valuation assessment.

Classification: Unavailable target. Its semantic contract is defined by ADR 0021; ADR 0024 confirms comparative valuation remains unavailable pending its own accepted methodology.

---

# 12. Mispricing Layer

Purpose:

Evaluate the divergence between an authorized fair-value estimate and a compatible observed market value.

Responsibilities include:

- fair-value-to-market-value comparison for compatible representations;
- directional divergence measurement.

Outputs:

- mispricing assessment.

Classification: Unavailable target. Its semantic contract is defined by ADR 0021; ADR 0024 confirms mispricing remains unavailable pending its own accepted methodology.

---

# 13. Asymmetry Layer

Purpose:

Evaluate the probability-weighted balance of favorable and adverse payoff across an immutable, predeclared scenario set.

Responsibilities include:

- scenario-based upside/downside evaluation;
- payoff asymmetry measurement.

Outputs:

- asymmetry assessment.

Classification: Unavailable target. Its semantic contract is defined by ADR 0021; ADR 0024 confirms asymmetry remains unavailable pending its own accepted methodology.

---

# 14. Opportunity Layer

Purpose:

Determine investment opportunity quality from authorized analytical factors.

Responsibilities include:

- factor-contract-gated opportunity assessment;
- opportunity ranking.

Outputs:

- opportunity assessment and ranking.

Classification: Experimental/research only (ADR 0016, ADR 0017, ADR 0018). Not a production analytical output and not exposed through Dashboard API, alerts, or other operational projections. Production promotion requires a future accepted ADR satisfying ADR 0017's promotion gate.

---

# 15. Prediction Layer

Purpose:

Estimate and evaluate future outcomes as a logical target capability.

Responsibilities include:

- generating explicitly contracted prediction intelligence under a future authorized production boundary;
- auditing already-made, fully contracted predictions through the distinct canonical prediction-evaluation authority;
- correctness, accuracy, calibration, strict-known replay, and correction lineage for the evaluation portion.

Outputs:

- prediction intelligence;
- prediction evaluation records;
- accuracy and calibration snapshots.

Classification: Prediction generation remains an unavailable logical target; no accepted production authority currently consumes Opportunity outputs to generate canonical predictions. Separately, ADR 0019 establishes the implemented production `PredictionEvaluationService` as the sole audit/evaluation authority for already-made, fully contracted predictions, with a dedicated canonical store and an explicitly versioned read-only Dashboard projection. The audit authority covers only the evaluation portion of this logical layer: it does not generate predictions and cannot become Market Validation, Opportunity, Timing, ranking, or recommendation authority.

---

# 16. Portfolio Layer

Purpose:

Produce portfolio-level decision support from authorized analytical outputs.

Responsibilities include:

- portfolio-level context and aggregation across authorized analytical outputs.

Outputs:

- portfolio-level decision-support intelligence.

Classification: Unavailable target. No accepted ADR yet establishes implementation authority for this domain; ownership is named only in `docs/CANONICAL_ARCHITECTURE_MAP.md`.

---

# 17. Decision Support Layer

Purpose:

Transform analytical intelligence into actionable decision support.

Responsibilities include:

- monitoring;
- watchlists;
- alerts;
- portfolio context;
- review support.

Outputs:

- decision-support intelligence;
- user-facing investment guidance.

---

# 18. Cross-Cutting Architectural Capabilities

The following capabilities span every architectural layer:
- Deterministic execution.
- Evidence traceability.
- Explainability.
- Historical replay.
- Point-in-time correctness.
- Confidence representation.
- Missing evidence representation.
- Failure transparency.
- Operational observability.

No architectural layer is exempt from these requirements.

---

# 19. Architectural Boundaries

Architectural layers remain independent.

Each layer:

- has clearly defined responsibilities;
- consumes validated outputs from earlier layers;
- does not duplicate another layer's responsibilities;
- exposes well-defined outputs;
- remains replaceable without redesigning unrelated layers.

Architectural dependencies always flow forward.

---

# 20. Architectural Evolution

Project Hunter is designed for incremental architectural evolution.

New capabilities should extend existing architectural layers whenever practical instead of introducing parallel architectures or duplicated responsibilities.

Architectural complexity should grow only when it measurably improves investment intelligence.

---

# 21. Relationship to Other Canonical Documents

This document defines the logical architecture of Project Hunter.

Architectural principles, governance, roadmap, implementation contracts, engineering procedures, release planning, and architecture decisions are intentionally maintained in their respective canonical documents.

This document defines the architecture specification only.