# Codex Implementation Guide for Project Hunter

## Purpose

This guide defines how Codex should work on Hunter. It supplements the canonical governance order maintained in `docs/SPRINTS/README.md` and defines Codex-specific implementation expectations within that order.

Before any implementation, Codex must read the documents in the canonical source-of-truth order defined in `docs/SPRINTS/README.md`, then read release-specific user instructions.

Every implementation prompt must be treated as if it begins with:

```text
Verify compliance with HUNTER_IMPLEMENTATION_CONTRACT.md before writing code.
```

Codex must verify `docs/HUNTER_IMPLEMENTATION_CONTRACT.md` before writing code, even when the prompt omits that sentence.

If `docs/SPRINTS/<version>.md` is missing for the requested release, Codex must stop before
implementation and report that the sprint specification has not been created.

## Documentation Governance

The canonical governance hierarchy is defined once in `docs/SPRINTS/README.md`. Sprint specifications define approved release scope, but they do not override higher-governance documents in that order. `docs/PROJECT_CONSTITUTION.md` remains the highest architectural authority. When documents conflict, Codex must stop before implementation and report the conflict.

`docs/HUNTER_IMPLEMENTATION_CONTRACT.md` is the mandatory pre-implementation contract for translating that hierarchy into repository, service, provider, engine, replay, transaction, migration, and test obligations.

## Required Working Mode

Codex must think like a senior production engineer operating under an approved architecture.

It must not reinterpret the mission, add speculative features, or optimize for code volume.

The objective is the smallest production-safe release that creates the greatest immediate investment value while preserving future extensibility.

## Repository Review

Before editing:

- inspect the entire relevant runtime path;
- identify the canonical production boundary;
- inspect models, repositories, migrations, configuration, CLI, automation, documentation, and tests;
- identify characterization tests that must remain unchanged;
- confirm current version, branch, tag, and git status;
- distinguish canonical, derived, experimental, and deprecated modules.

Never create a competing runtime because an existing module is inconvenient.

## Implementation Rules

### Preserve production behavior

Do not weaken:

- deterministic identity;
- evidence provenance;
- validation discipline;
- historical cutoffs;
- explainability;
- idempotency;
- typed boundaries;
- explicit unavailable states.

### Reuse before redesign

Reuse existing contracts and services when technically sound.

Refactor only when required to remove a real blocker.

Do not rewrite a subsystem merely to make it stylistically uniform.

### No fake implementation

Do not add:

- placeholders presented as operational;
- mock data in production paths;
- static dumps presented as live discovery;
- unsupported scores;
- fabricated coverage;
- broad exception swallowing;
- silent candidate rejection;
- implicit time assumptions.

### One source, one adapter

Source-specific payloads remain inside adapters.

All downstream consumers receive normalized typed records.

Every adapter must expose health, availability, error, checkpoint, and provenance semantics.

### Point-in-time truth

All observations require timestamps.

Historical replay must see only evidence known at the replay cutoff.

Do not backfill present identity, market data, or project existence into earlier history without an explicit historical record.

### Persistence

Use durable repositories for authoritative state.

All writes must be idempotent.

Add migrations for schema changes.

Do not use unindexed large JSONL files as authoritative registries.

Raw immutable evidence may remain append-only where appropriate.

### Failure behavior

Failures must be typed and observable.

Use explicit states for:

- unavailable providers;
- rate limiting;
- stale evidence;
- ambiguous identity;
- conflicts;
- incomplete coverage;
- unsupported chains or entities.

Never substitute guessed values.

## Testing Standard

Each release must include:

- model and contract tests;
- repository tests;
- deterministic behavior tests;
- idempotency tests;
- conflict tests;
- restart/checkpoint tests where applicable;
- CLI or service integration tests;
- characterization coverage for unchanged production behavior;
- live validation for every source claimed as operational.

Run the full suite:

- Ruff;
- Black check;
- Mypy;
- Pytest.

Do not report success if any required validation was skipped.

## Live Validation Standard

For every live source, report:

- endpoint/provider used;
- reachability;
- rate limits;
- records observed;
- normalized records;
- merges;
- conflicts;
- persisted results;
- failures;
- actual coverage.

A unit-tested adapter is not operational until live-validated.

## Documentation Standard

Update only documents affected by the release, but ensure:

- version references are consistent;
- architecture status is accurate;
- commands are real;
- limitations are explicit;
- coverage claims match live results;
- migration behavior is documented;
- no roadmap item is described as complete without evidence.

## Release Discipline

Before release:

1. confirm intended files;
2. confirm migrations;
3. run formatting, typing, linting, and tests;
4. run required live validation;
5. inspect git diff;
6. ensure runtime data, secrets, caches, and large generated files are excluded;
7. commit tracked changes;
8. push to `origin/main` when requested;
9. create and push the requested tag;
10. create the release when authentication and tooling permit;
11. report final git status accurately.

## Required Final Report

The final answer must be concise and factual. Include only verified results:

- root cause or architectural blocker addressed;
- architecture summary;
- practical investment-value improvement;
- files added;
- files modified;
- migrations;
- operational sources/adapters;
- live validation counts;
- coverage before and after;
- automation status;
- remaining blockers;
- tests passed;
- commit hash;
- push status;
- release tag and URL;
- final git status.

Do not include promotional language or unsupported claims.

## Stop Conditions

Stop and report rather than guessing when:

- repository state conflicts with the release baseline;
- required credentials are unavailable;
- a provider cannot be legally or technically accessed;
- live evidence cannot validate a claim;
- a migration risks destroying production evidence;
- the requested change would create a competing canonical runtime;
- tests expose an unresolved regression.

## Core Question

Every implementation decision must help Hunter answer:

> Across the entire crypto market, what deserves deeper investment analysis next, and why?

Later releases may answer:

> What is this asset plausibly worth over a defined horizon, what assumptions support that estimate, and what would invalidate it?

Do not implement the later question before the evidence architecture can support it.
