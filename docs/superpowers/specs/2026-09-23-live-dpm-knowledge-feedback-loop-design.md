# Live DPM Knowledge Feedback Loop — Design

Issue: #488
Status: implementation-authorizing design
Base: PR #487 merged on main

## Mission

Close the live return path from a validated engineering finding to a bounded,
replayable Knowledge Extraction proposal. The path strengthens the existing DPM
registry and therefore future Smart Prompt Machine prevention context without
creating a second prompt, review, registry, or merge authority.

## Authority graph

validated finding evidence
→ KnowledgeExtractionAuthority (normalize + validate + deduplicate)
→ immutable KnowledgeExtractionProposal
→ canonical-integration decision by existing repository/human authority
→ DEFECT_REGISTRY
→ EngineeringContextAuthority
→ SmartPromptMachine
→ signed implementation handoff

A proposal is evidence. It is never canonical truth and cannot mutate the
registry. Reviewers/models remain evidence producers only.

## Contract

The v1 input carries source kind, stable finding identity, PR, reviewed exact
head/base, reviewer, classification/disposition, normalized invariant, affected
paths, fix reference, regression evidence, and optional claimed family.

The v1 output is immutable and has one outcome:
- existing-family
- candidate-new-family
- excluded
- ambiguous

It includes a deterministic proposal id/digest and enough provenance to replay
the decision.

## Deterministic existing-family matching

No semantic model is allowed to invent equivalence. Existing-family mapping is
accepted only when a claimed family exists and deterministic evidence proves
the recurrence against that family: the normalized invariant must equal the
canonical invariant after bounded normalization and at least one affected path
must intersect the family's declared applicability. If a claimed family is
unknown, invariant/path evidence conflicts, or more than one authoritative
mapping is asserted, fail closed as ambiguous.

This deliberately prefers false-negative proposal-only handling over silently
polluting canonical knowledge.

## Exclusions

False positives, style-only findings, obsolete findings, reviewer/provider
availability, quota/rate-limit state, and infrastructure-only observations are
excluded from defect learning. Exclusion remains auditable and replayable.

## Candidate new family

A confirmed defect with complete provenance that cannot deterministically map
to an existing family may be emitted as candidate-new-family. That outcome has
no registry-write authority and requires canonical approval before integration.

## Idempotency and replay

Proposal identity is a SHA-256 digest over canonical v1 finding evidence.
Replaying identical evidence produces the identical proposal id and payload.
Changing exact-head provenance or substantive evidence changes identity.

## Fail-closed rules

Malformed SHA, missing PR/finding/reviewer, unsupported classification/source,
empty invariant/path/fix/regression evidence for a confirmed defect, unknown
claimed family, incomplete registry family schema, or noncanonical registry
domains cannot become existing-family knowledge.

## Historical convergence

HISTORICAL_DEFECT_BACKFILL remains the historical audit artifact. It is not
rewritten by this PR. Future backfill ingestion must translate each historical
record into this same v1 finding/proposal contract; no second learning schema is
authorized.

## Failure-mode checklist

- duplicate DFF: prevented by deterministic existing-family mapping;
- reviewer prose as truth: proposal-only authority;
- false positive/style/infrastructure pollution: explicit exclusions;
- stale/missing provenance: exact reviewed SHA validation;
- replay duplication: content-addressed proposal id;
- external outage: no provider/network dependency;
- registry mutation: no write API exists in this authority;
- SPM bypass: output only feeds canonical integration, never prompt dispatch;
- historical/live divergence: one future convergence contract.

## Verification

TDD first. Regression must prove the PR #487 noncanonical lifecycle/boundary
finding maps to DFF-004, exclusion classes cannot become defect proposals,
unknown claimed families fail closed/ambiguous, replay is idempotent, and
candidate-new-family cannot claim canonical authority.
