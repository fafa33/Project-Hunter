# Project Hunter Canonical Runtime Architecture

## Purpose

This document defines the canonical runtime architecture of Project Hunter.

It describes how the major runtime components collaborate to transform market observations into investment intelligence during system execution.

Implementation details, release-specific classifications, engineering policies, and migration decisions are intentionally maintained in their respective canonical documents.

---

# Runtime Overview

Project Hunter executes as a deterministic evidence-driven processing pipeline.

Each implemented runtime stage consumes validated outputs from previous implemented stages and produces structured outputs for subsequent implemented stages.

No implemented runtime stage bypasses earlier implemented stages.

Unavailable targets are documented separately from the executable production path and do not run merely because their dependency position is defined.

---

# Executable Production Runtime Flow

The accepted executable production-authority path is:

```text
CLI / authorized runtime entry point
        ↓
Acquisition
        ↓
Validation
        ↓
Repositories
        ↓
EngineValidationSource
        ↓
EvidenceBackedProjectExecutor
        ↓
Weight Engine
        ↓
Production Timing
        ↓
Canonical Committee Fields
        ↓
Explainability
        ↓
Reports
```

This flow reproduces the accepted production boundary defined by ADR 0007 and ADR 0016. Discovery, identity resolution, screening, prioritization, broader investment-intelligence composition, decision support, and unavailable valuation-family targets are not promoted into this executable path merely because they exist as logical, operational, experimental, or target architecture.

Every production stage executes deterministically using only the authorized evidence available at that point in time.

---

# Analytical Dependency Topology

The following topology preserves the ownership and dependency direction defined by `docs/CANONICAL_ARCHITECTURE_MAP.md` without implying that every node currently executes in production:

```text
Investment Intelligence
        ↓
Canonical Valuation
        ↓
Comparative Valuation
        ↓
Mispricing Intelligence
        ↓
Asymmetry Intelligence
        ↓
Opportunity Intelligence
        ↓
Prediction Intelligence
        ↓
Portfolio Intelligence
```

Only the valuation-family and adjacent analytical stages below carry explicit release classifications here. Those classifications come from the accepted ADRs and canonical authority documents named for each stage, not from this diagram. An unavailable target represents an authorized or named dependency position only; it is not an executable runtime stage.

---

# Runtime and Architectural Components

The sections below describe production runtime components together with logical, operational, experimental, and target components. Inclusion in this section does not by itself establish production execution or production analytical authority; the executable production boundary above and the governing ADRs remain authoritative.

## Acquisition

Collects observations from external sources.

Responsibilities include:

- acquiring observations;
- preserving provenance;
- recording acquisition metadata;
- handling unavailable sources.

Outputs:

- acquired observations.

---

## Validation

Determines whether acquired observations satisfy trust requirements.

Responsibilities include:

- validation;
- normalization;
- quality verification;
- conflict detection.

Outputs:

- validated observations.

---

## Persistence

Preserves validated information for future analytical use.

Responsibilities include:

- durable storage;
- historical preservation;
- point-in-time correctness;
- replay support.

Outputs:

- persistent evidence.

---

## Discovery

Continuously expands market coverage.

Responsibilities include:

- discovering new entities;
- updating existing entities;
- preserving discovery history.

Outputs:

- discovered candidates.

---

## Identity Resolution

Determines canonical economic entities.

Responsibilities include:

- identity reconciliation;
- ambiguity preservation;
- duplicate handling.

Outputs:

- canonical entities.

---

## Evidence Processing

Transforms validated observations into structured analytical evidence.

Responsibilities include:

- evidence organization;
- evidence relationships;
- evidence sufficiency.

Outputs:

- analytical evidence.

---

## Screening

Determines analytical readiness.

Responsibilities include:

- candidate screening;
- readiness assessment;
- analytical eligibility.

Outputs:

- screened candidates.

---

## Prioritization

Determines analytical order.

Responsibilities include:

- prioritization;
- analytical queue management.

Outputs:

- prioritized opportunities.

---

## Deep Analysis

Produces comprehensive investment analysis.

Responsibilities include:

- evidence integration;
- analytical reasoning;
- explainable conclusions.

Outputs:

- investment intelligence.

---

## Canonical Valuation

Produces authoritative, structured fair-value intelligence for a qualifying economic entity under one immutable, versioned methodology.

Responsibilities include:

- fair-value estimation under the accepted methodology;
- confidence and uncertainty decomposition;
- strict-known replay and correction lineage.

Outputs:

- structured, non-scalar fair-value assessment.

**Classification:** Architecture accepted (ADR 0021, ADR 0022, ADR 0024). Implementation is in progress under the governing issue and is not yet independently validated as operationally complete. Per ADR 0024, this output does not become a Market Validation composition input until a separate accepted ADR authorizes that composition.

---

## Comparative Valuation

Compares a target's valuation against economically compatible peers under a declared cohort policy.

Responsibilities include:

- peer-universe eligibility and comparability;
- relative valuation measurement.

Outputs:

- comparative valuation assessment.

**Classification:** Unavailable target. Its semantic contract is defined by ADR 0021; ADR 0024 confirms comparative valuation remains unavailable pending its own accepted methodology.

---

## Mispricing Intelligence

Evaluates the divergence between an authorized fair-value estimate and a compatible observed market value.

Responsibilities include:

- fair-value-to-market-value comparison for compatible representations;
- directional divergence measurement.

Outputs:

- mispricing assessment.

**Classification:** Unavailable target. Its semantic contract is defined by ADR 0021; ADR 0024 confirms mispricing remains unavailable pending its own accepted methodology.

---

## Asymmetry Intelligence

Evaluates the probability-weighted balance of favorable and adverse payoff across an immutable, predeclared scenario set.

Responsibilities include:

- scenario-based upside/downside evaluation;
- payoff asymmetry measurement.

Outputs:

- asymmetry assessment.

**Classification:** Unavailable target. Its semantic contract is defined by ADR 0021; ADR 0024 confirms asymmetry remains unavailable pending its own accepted methodology.

---

## Opportunity Intelligence

Determines investment opportunity quality from authorized analytical factors.

Responsibilities include:

- factor-contract-gated opportunity assessment;
- opportunity ranking.

Outputs:

- opportunity assessment and ranking.

**Classification:** Experimental/research only (ADR 0016, ADR 0017, ADR 0018). Not a production analytical output and not exposed through Dashboard API, alerts, or other operational projections. Production promotion requires a future accepted ADR satisfying ADR 0017's promotion gate.

---

## Prediction Intelligence

Represents the logical target capability for estimating and evaluating future outcomes.

Target responsibilities include:

- generating explicitly contracted prediction intelligence under a future authorized production boundary;
- evaluating prediction correctness, accuracy, and calibration through a distinct audit authority.

Target outputs include:

- prediction intelligence;
- prediction evaluation records;
- accuracy and calibration snapshots.

**Classification:** Prediction generation remains an unavailable logical target; no accepted production authority currently generates predictions or consumes Opportunity outputs to produce them. Separately, ADR 0019 establishes the implemented production `PredictionEvaluationService` as the sole audit/evaluation authority for already-made, fully contracted predictions, with a dedicated canonical store and an explicitly versioned read-only Dashboard projection. The evaluation authority does not generate predictions and does not become Market Validation, Opportunity, Timing, ranking, or recommendation authority.

---

## Portfolio Intelligence

Produces portfolio-level decision support from authorized analytical outputs.

Responsibilities include:

- portfolio-level context and aggregation across authorized analytical outputs.

Outputs:

- portfolio-level decision-support intelligence.

**Classification:** Unavailable target. No accepted ADR yet establishes implementation authority for this domain; ownership is named only in `docs/CANONICAL_ARCHITECTURE_MAP.md`.

---

## Decision Support

Transforms analytical intelligence into practical user guidance.

Responsibilities include:

- monitoring;
- alerts;
- watchlists;
- decision context.

Outputs:

- decision-support intelligence.

---

## Reporting

Presents runtime outputs.

Responsibilities include:

- explainability;
- reporting;
- operational visibility.

Outputs:

- user-facing reports.

---

# Runtime Characteristics

The runtime preserves:

- deterministic execution;
- evidence traceability;
- point-in-time correctness;
- historical replay;
- explainability;
- explicit uncertainty;
- explicit missing evidence;
- explicit failures.

Every runtime execution must remain reproducible from the available evidence.

---

# Runtime Evolution

The runtime may evolve by extending existing stages or introducing new stages when justified by architectural requirements.

Evolution must preserve deterministic execution, historical correctness, and evidence integrity.

---

# Relationship to Other Canonical Documents

This document defines the canonical runtime architecture of Project Hunter.

Logical architecture, architectural principles, governance, implementation details, engineering procedures, release planning, runtime inventories, and architecture decisions are intentionally maintained in their respective canonical documents.

This document defines the runtime architecture only.
