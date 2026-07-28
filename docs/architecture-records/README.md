# Architecture Decision Preparation Records

## Purpose

This directory stores permanent Architecture Decision Preparation Records (ADPRs).

An ADPR preserves the problem definition, evidence, assumptions, constraints, option set, comparative analysis, falsification, rejected options, risks, readiness determination, and traceability that existed before an ADR or other formal architectural action.

ADPRs do not replace ADRs. An ADPR records how a decision became ready; an ADR records the accepted architectural decision.

## Naming

Use this format:

```text
ADPR-NNNN-short-kebab-case-title.md
```

Examples:

```text
ADPR-0001-global-discovery-boundary.md
ADPR-0002-canonical-valuation-authority.md
```

Numbers are repository-wide, monotonically increasing, never reused, and independent from ADR numbers.

## Lifecycle States

- `PROPOSED`
- `IN_RESEARCH`
- `READY_FOR_REVIEW`
- `APPROVED`
- `IMPLEMENTED`
- `VALIDATED`
- `SUPERSEDED`
- `ARCHIVED`

State meanings are defined in `docs/ARCHITECTURE_DECISION_PREPARATION_GUIDE.md`.

## Required Process

1. Create or identify the governing Issue or Epic.
2. Complete `docs/templates/ARCHITECTURE_DECISION_PREPARATION_TEMPLATE.md`.
3. Perform review using `docs/checklists/ARCHITECTURE_DECISION_PREPARATION_CHECKLIST.md`.
4. Create the permanent ADPR from `docs/templates/ADPR_TEMPLATE.md`.
5. Add the ADPR to `docs/architecture-index.md`.
6. Create an ADR only when the readiness outcome is `READY_FOR_ADR`.
7. Add implementation, PR, commit, validation, supersession, and release traceability as those artifacts become available.

## Immutability

An approved ADPR is a historical reasoning record.

After `APPROVED`:

- substantive reasoning is not rewritten;
- rejected options are not deleted;
- uncertainty known at approval time is not retroactively concealed;
- the record is never removed merely because its decision was superseded;
- a materially changed decision basis requires a new ADPR;
- the new ADPR must identify the record it supersedes;
- the old record must identify its successor.

Typographical corrections and completion of previously unavailable traceability links are allowed, but repository history must preserve the change.

## Relationship to ADRs

The normal relationship is:

```text
Issue or Epic
  -> Preparation working document
  -> Checklist review
  -> ADPR
  -> ADR
  -> Implementation
  -> Pull Request
  -> Validation
  -> Merge and Release
```

One ADPR may produce:

- one ADR;
- multiple narrowly scoped ADRs;
- no ADR, when the result is `NOT_AN_ARCHITECTURE_DECISION` or the decision remains blocked.

Every produced ADR must link back to its governing ADPR. Every ADPR must identify the ADRs it produced when they exist.

## Status Changes

Every material status transition must be recorded in the ADPR decision history and in `docs/architecture-index.md`.

`IMPLEMENTED` does not mean validated. `VALIDATED` requires the applicable post-implementation verification and governance review to have completed.

## Prohibited Practices

Do not:

- create an ADR first and invent its preparation record afterward;
- use an ADPR to override accepted architecture;
- label assumptions as evidence;
- remove rejected options after a recommendation is selected;
- fabricate missing Issue, ADR, PR, commit, or release links;
- reuse an ADPR number;
- silently mutate an approved record into a new decision.
