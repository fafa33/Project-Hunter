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

## Follow-on: closing the loop from candidate to canonical registry

`hunter-knowledge-learning.yml` builds the exact-head ledger and renders a
registry candidate on every `pull_request_review`/`pull_request_review_comment`
event -- capture and candidate-rendering already run off the merge-critical
path, since neither step is a required check. But `materialize_learning_ledger`
(the one function with actual write authority over `docs/DEFECT_REGISTRY.json`,
still gated exactly as this design specifies: local-content-only, never
committing, pushing, or opening a PR) had no caller. The workflow only
uploaded the ledger and candidate as a 90-day, non-authoritative CI artifact,
so the "canonical-integration decision by existing repository/human authority"
step in the authority graph above had no ingress: nothing ever turned a
rendered candidate back into a change a human could review and merge through
the normal PR path.

`scripts/hunter_canonicalize_learning.py` closes that gap by composing the
existing `build_learning_ledger` and `materialize_learning_ledger` authorities
behind one CLI, with no new registry, persistence, or replay semantics:

```
python scripts/hunter_canonicalize_learning.py \
  --pr <N> --head <exact-head-sha> --base <exact-base-sha> \
  --observations <path-to-collected-observations.json> [--dry-run]
```

- **Durable backlog, no new persistence layer.** GitHub's own PR review/comment
  history is the backlog: `hunter_collect_learning_observations.py` and
  `hunter_collect_sonar_observations.py` already page through the *complete*
  history for an exact head on every run, not a delta, so a missed CI run, a
  stopped worker, or a GitHub-Actions outage loses nothing -- rerunning later
  reproduces the identical observation set. `docs/DEFECT_REGISTRY.json` itself
  is the second half of the backlog contract: `already_integrated` and the
  registry-digest replay check in `controlled_learning_integration.py` make
  reprocessing any PR, at any later time, safe and idempotent, and a registry
  that has since moved on fails closed with "stale registry snapshot" rather
  than silently mis-integrating -- no separate pending-queue file is needed or
  introduced.
- **Local/Mac execution path.** The script takes no network or provider
  dependency; it requires only a previously-collected `--observations` file
  (the same input contract `hunter_incremental_knowledge.py` already defines).
  It can run identically on a local machine or inside CI.
- **No caller-selected write target.** Matching the existing
  `hunter_materialize_learning_candidate.py`/`hunter_incremental_knowledge.py`
  convention, the script exposes no `--registry`/`--output` flag; the registry
  and ledger paths are read from `controlled_learning_integration`'s own
  module constants, so a caller cannot redirect where the write lands.
- **Still never merge-blocking.** The script is not wired into any required
  check or into `hunter_pr_preflight.py`. Applying its result to
  `docs/DEFECT_REGISTRY.json` is a local file edit a human then carries through
  the unchanged, fully-governed commit/push/PR/review/merge path -- exactly
  the "normal PR + owner-merge path remains the sole route to canonical main"
  guarantee this design already states.
- **Bounded DPM/SPM reuse, already wired.** No further integration step exists
  or is needed: `EngineeringContextAuthority` already reads
  `docs/DEFECT_REGISTRY.json` live and performs the bounded family selection
  that feeds `SmartPromptMachine`. Once a canonicalized update merges through
  the normal path, it is available on the next SPM invocation with no
  additional wiring.

Regression: `tests/test_canonicalize_learning_cli.py` proves dry-run preview
without persisting, atomic apply, idempotent replay, exclusion classes never
integrating, conflicting evidence under one event identity failing closed
without mutating the registry, malformed input failing closed, and the
no-caller-selected-write-target / no-network-dependency shape invariants.
