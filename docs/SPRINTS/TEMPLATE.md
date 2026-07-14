# Sprint vX.Y.Z: <Sprint Name>

## Status

- Release: `vX.Y.Z`
- Status: approved | in progress | released
- Baseline commit: `<commit>`
- Release tag: `vX.Y.Z`

## Read First

Before implementation, Codex must read:

1. `docs/HUNTER_ARCHITECTURE_MANIFEST.md`
2. `docs/HUNTER_ARCHITECTURE_SPEC.md`
3. `docs/HUNTER_ROADMAP.md`
4. `docs/CODEX_IMPLEMENTATION_GUIDE.md`
5. `docs/SPRINTS/vX.Y.Z.md`

## Mission

Describe the business and architectural objective of the sprint.

## In Scope

- List the specific capabilities authorized for implementation.
- Keep scope focused on the smallest production-safe change that improves real investment decisions.

## Out of Scope

- List explicitly deferred systems and behaviors.
- State any frozen architecture boundaries.

## Architecture Constraints

- Preserve canonical runtime boundaries.
- Preserve deterministic execution identity.
- Preserve evidence provenance and point-in-time truth.
- Preserve idempotent persistence.
- Do not manipulate scoring, weighting, committee logic, replay, historical validation, or calibration unless the sprint explicitly authorizes it.

## Data and Evidence Requirements

- Define acceptable public or persisted sources.
- Define unavailable-state behavior.
- Define provenance, confidence, freshness, and conflict requirements.
- Prohibit mock production data, placeholders, fabricated evidence, and unsupported completeness claims.

## Performance Requirements

- Define expected scale.
- Prefer indexed lookup, batching, checkpoints, and incremental synchronization.
- Avoid full registry scans and avoid O(n²) algorithms where scalable alternatives exist.

## Automation Requirements

- Define scheduled jobs, health checks, retries, checkpointing, idempotency, and failure isolation.
- Scheduler remains operational-only.
- Pipeline ownership remains with canonical pipeline services.

## Reporting Requirements

- Define CLI/reporting outputs.
- Require separate coverage dimensions.
- Require explicit blockers and unavailable states.

## Validation Commands

Run the sprint-specific commands plus:

```bash
ruff check .
black --check .
mypy
pytest
```

## Acceptance Criteria

- Define measurable completion criteria.
- Include live validation requirements for operational providers.
- Include quality gates and regression requirements.

## Final Report Fields

- Architecture summary
- Files added
- Files modified
- Coverage before
- Coverage after
- Validation results
- Tests passed
- Commit hash
- Push status
- Release tag
- Remaining blockers
