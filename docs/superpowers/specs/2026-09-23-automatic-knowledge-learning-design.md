# Automatic Incremental Knowledge Learning

> **Amended by [ADR 0039](../../ADR/0039-review-knowledge-remediation-loop.md) (2026-10-04).** The non-goals "automatic registry commit", "semantic LLM classification" and "automatic new-family creation" are replaced by their mechanically proven forms: hybrid classification accepted only behind a RED→GREEN proof, and promotion written by trusted plumbing inside the remediation commit. Raw prose remains evidence only.

## Objective
Connect Hunter's real PR lifecycle to the #491 deterministic learning core without granting any event source canonical authority.

## Model
Observation -> exact-head learning ledger -> governed event admission -> hunter-finding-event-v1 -> KnowledgeExtractionAuthority -> controlled integration.

Raw reviewer, Sonar, CI, or governance prose is evidence only. It never invents an invariant or DFF mapping. Incomplete observations remain auditable as insufficient-evidence. Optional source unavailability never blocks other learning or merge.

The ledger schema is hunter-learning-ledger-v1. It is rebuilt deterministically for an exact PR head/base, deduplicates content-identical delivery, and records source availability. Automation may publish this artifact but has no registry, commit, push, merge, status, reviewer, or DFF-creation authority.

Confirmed historical HBF records translate through the same governed event contract when their existing DFF, fix reference and regression target are present. Historical non-defects remain excluded. No second historical learning architecture is allowed.

Failure modes fail closed at admission: wrong head/base, malformed observation, duplicate/conflicting identity, missing invariant/fix/regression, unavailable optional provider, missing historical DFF/test, and stale evidence.

Non-goals: automatic registry commit, semantic LLM classification, required Sonar/provider check, merge automation, automatic new-family creation.
