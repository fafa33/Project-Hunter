# Project Hunter Principles

## Purpose

This document defines the enduring engineering and architectural principles that guide the design, implementation, and long-term evolution of Project Hunter.

These principles describe the qualities that every architectural decision, implementation, and future evolution should preserve.

Governance, implementation rules, runtime architecture, development procedures, and release policies are intentionally maintained in their respective canonical documents.

---

## Canonical Purpose

Project Hunter is a market-discovery-first, evidence-driven investment decision-support system.

Its purpose is to continuously discover the cryptocurrency market, identify promising long-term opportunities, validate them through trustworthy evidence, and produce explainable investment intelligence.

Hunter exists to improve investment decisions through better market coverage, better evidence, and better reasoning—not through speculation, opaque models, or unsupported assumptions.

---

# Principle 1 — Discovery First

Hunter must maintain a market-wide perspective before narrowing attention to deep analysis.

The system continuously discovers assets, protocols, ecosystems, and emerging opportunities before determining which candidates deserve further investigation.

Discovery always precedes analysis.

---

# Principle 2 — Evidence First

Every analytical conclusion must be supported by evidence.

Hunter preserves evidence provenance, timestamps, confidence, freshness, conflicts, missingness, and traceability throughout the analytical process.

Evidence is never assumed.

---

# Principle 3 — Trust Before Intelligence

Information must be validated before becoming intelligence.

Source reliability, identity resolution, validation status, conflict handling, freshness, and evidence quality are prerequisites for analytical reasoning.

Untrusted information must never become authoritative intelligence.

---

# Principle 4 — Deterministic by Default

The same evidence must always produce the same analytical outcome.

Deterministic execution enables reproducibility, historical replay, auditing, testing, and explainability.

Hidden state, implicit assumptions, nondeterministic behavior, and silent substitutions are incompatible with production intelligence.

---

# Principle 5 — Identity Before Valuation

Hunter must establish what an economic entity is before evaluating its value.

Canonical identity precedes valuation, comparison, ranking, investment reasoning, and portfolio decisions.

Analytical conclusions built on uncertain identity cannot become authoritative.

---

# Principle 6 — Explainability Is Mandatory

Every meaningful output must be explainable.

Hunter must be able to explain:

- why something was discovered;
- why it was analyzed;
- which evidence supports the conclusion;
- which evidence contradicts it;
- which evidence is missing;
- how the conclusion was produced.

Unsupported conclusions are never acceptable.

---

# Principle 7 — Market Coverage Before Market Understanding

Comprehensive market visibility is the foundation of meaningful investment intelligence.

Hunter must continuously expand market coverage while maintaining evidence quality and trust.

A system cannot identify exceptional opportunities if it cannot observe the market in which those opportunities emerge.

---

# Principle 8 — Investment Value Over Complexity

Architectural complexity is never an objective.

Every architectural decision should improve one or more of the following:

- discovery quality;
- evidence quality;
- analytical quality;
- decision quality;
- reliability;
- maintainability;
- extensibility.

Complexity without measurable value should not be introduced.

---

# Principle 9 — No Fabricated Evidence

Hunter never fabricates evidence.

Unknown information remains unknown.

Missing evidence remains missing.

Conflicting evidence remains conflicting.

Ambiguous identity remains ambiguous until resolved.

Analytical confidence must never be increased by inventing data or silently filling gaps.

---

# Principle 10 — Long-Term Maintainability

Hunter is designed as a long-lived analytical system.

Architectural decisions should favor:

- simplicity;
- extensibility;
- durability;
- testability;
- replayability;
- auditability;
- incremental evolution.

Temporary convenience must never compromise long-term maintainability.

---

## Relationship to Other Canonical Documents

These principles guide every architectural and engineering decision within Project Hunter.

Specific governance rules, implementation requirements, runtime behavior, architectural ownership, engineering procedures, and release policies are defined in their respective canonical documents.

This document defines enduring principles only.