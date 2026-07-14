# Sprint Specification Template

Use this structure for every Project Hunter sprint specification. A release sprint is not approved for implementation until each section contains concrete release-specific content.

## Status

- Sprint name.
- Release version.
- Approval or release status.
- Baseline commit when required.
- Release tag.

## Read First

Before implementation, Codex must read:

1. `docs/PROJECT_PRINCIPLES.md`
2. `docs/VISION.md`
3. `docs/HUNTER_ARCHITECTURE_MANIFEST.md`
4. `docs/HUNTER_ARCHITECTURE_SPEC.md`
5. `docs/HUNTER_ROADMAP.md`
6. `docs/CODEX_IMPLEMENTATION_GUIDE.md`
7. the sprint specification for the release being implemented

## Mission

State the architectural and product mission of the sprint.

## Business Objective

State the real investment-decision value the sprint must improve.

## Scope

List the implementation work authorized for the sprint.

## Out of Scope

List systems, behaviors, and architectural changes that are explicitly deferred or prohibited.

## Engineering Principles

State the release-specific engineering principles. Include determinism, evidence provenance, idempotency, simplicity, reuse, and production stability when applicable.

## Performance

State scale assumptions, indexed lookup requirements, batching requirements, incremental update requirements, and algorithms to avoid.

## Automation

State automation jobs, health checks, retries, checkpoints, idempotency, failure isolation, and operational boundaries.

## Testing

List sprint-specific validation commands and the required quality gates:

```bash
ruff check .
black --check .
mypy
pytest
```

## Acceptance Criteria

List measurable conditions that must be true before the sprint is complete.

## Definition of Done

Define the final release state, including documentation, validation, git, tag, and release requirements.

## Success Metrics

List the metrics that determine whether the sprint improved market coverage, evidence quality, trust, prioritization, reliability, or investment usefulness.

## Final Report Format

List the exact final report fields required for the sprint.
