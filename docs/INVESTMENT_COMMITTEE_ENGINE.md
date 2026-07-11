# Investment Committee Engine

## Purpose

The Investment Committee Engine is the final persisted analytical decision layer for Project Hunter V1. It consolidates persisted upstream outputs into explainable committee assessments and cycle champion snapshots.

It does not collect data, call intelligence engines, perform trading execution, produce price targets, or make portfolio allocation decisions.

## Boundaries

- Input: persisted intelligence, fusion, opportunity timing, probability, pattern matching, technology necessity, capital rotation, validation, evidence, snapshots, historical committee records, and automation context.
- Output: immutable `InvestmentCommitteeAssessment`, `CommitteeVote`, and `CycleChampionSnapshot` records.
- Execution: optional post-Opportunity Timing stage and CLI-accessible presentation layer.
- Storage: persistence contracts and SQL repositories remain under the existing persistence boundary.

## Eligibility Model

Eligibility is deterministic and configuration-driven:

- `ELIGIBLE`
- `CONDITIONALLY_ELIGIBLE`
- `INELIGIBLE`
- `INSUFFICIENT_EVIDENCE`

Eligibility is based on evidence completeness, available committee inputs, validation health, confidence, critical alerts, and risk limits.

## Voting Model

Committee categories emit normalized votes:

- `STRONG_APPROVE`
- `APPROVE`
- `NEUTRAL`
- `OPPOSE`
- `STRONG_OPPOSE`
- `ABSTAIN_MISSING`
- `ABSTAIN_STALE`
- `ABSTAIN_LOW_CONFIDENCE`

Votes preserve source scores, source confidence, source timestamps, freshness state, supporting references, opposing references, missing fields, and explanations.

## Consensus And Conflict

The engine computes approval, opposition, consensus, conflict, evidence robustness, committee confidence, and thesis fragility from the vote set. Conflict does not get hidden by positive signals; it remains visible in the assessment and report.

## One Project Mode

One Project Mode selects at most one cycle champion. A project must satisfy configured minimums for committee confidence, consensus, evidence robustness, conflict ceiling, lead margin, and candidate decision.

If the winner is not sufficiently evidence-backed, no champion is selected.

## No Qualified Candidate

The engine explicitly supports `NO_QUALIFIED_CANDIDATE`. It never forces a winner when all candidates are weak, conflicted, stale, or insufficiently evidenced.

## Explainability

Every assessment includes:

- committee votes
- positive drivers
- negative drivers
- conflicts
- abstentions
- risks
- invalidation conditions
- source record IDs
- decision-change explanations

## Persistence

The persistence layer stores:

- `InvestmentCommitteeAssessmentRecord`
- `CommitteeVoteRecord`
- `CycleChampionSnapshotRecord`

Operational timestamps are excluded from analytical conflict semantics. Repeated saves of the same analytical records are idempotent.

## CLI

Supported commands:

- `hunter committee evaluate`
- `hunter committee evaluate PROJECT`
- `hunter committee report`
- `hunter committee report PROJECT`
- `hunter committee ranking`
- `hunter committee champion`
- `hunter committee history`
- `hunter committee history PROJECT`

Ranking also supports:

- `hunter rank --sort committee`
- `hunter rank --sort committee-confidence`
- `hunter rank --sort consensus`
- `hunter rank --sort evidence-robustness`
- `hunter rank --sort thesis-fragility`

## Dashboard Foundation

Dashboard Phase 1 displays persisted committee assessments only. It does not calculate committee decisions in the presentation layer.

## Automation

Automation may request the committee stage through typed pipeline options after upstream intelligence stages complete. Scheduler logic remains operational only.

## Backtesting

Backtesting summaries aggregate persisted committee decisions, champion selection outcomes, and false positive/negative counts without introducing predictive models.

## Known Limitations

- This layer is analytical and deterministic; it is not investment advice.
- It does not perform external validation calls.
- It does not implement Dashboard Phase 2 visual workflows.
- It does not replace human review of committee reports.
