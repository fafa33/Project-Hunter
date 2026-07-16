# Project Hunter Sprint Specifications

This directory is the canonical home for Project Hunter sprint specifications.

Hunter development is specification-driven. A sprint file defines the approved implementation scope for one release. Codex must read the relevant sprint file before implementation, together with the governance and architecture documents below.

The canonical source-of-truth order is:

1. `docs/PROJECT_CONSTITUTION.md`
2. Accepted ADRs in `docs/ADR/`
3. `docs/CANONICAL_RUNTIME_ARCHITECTURE.md`
4. `docs/DEVELOPMENT_GOVERNANCE.md`
5. `docs/HUNTER_IMPLEMENTATION_CONTRACT.md`
6. `docs/AI_REVIEW_PROTOCOL.md`
7. `docs/SPRINTS/<version>.md`
8. `docs/CODEX_IMPLEMENTATION_GUIDE.md`

`docs/PROJECT_CONSTITUTION.md` remains the highest architectural authority. `docs/HUNTER_IMPLEMENTATION_CONTRACT.md` is the mandatory pre-implementation contract inside this order. This list is the single canonical ordering for governance references in `docs/CODEX_IMPLEMENTATION_GUIDE.md`, `docs/SPRINTS/README.md`, and `docs/AI_REVIEW_PROTOCOL.md`.

## Canonical Workflow

1. Create or update `docs/SPRINTS/vX.Y.Z.md` before implementation begins.
2. Use the exact structure defined in `docs/SPRINTS/TEMPLATE.md`.
3. Define mission, business objective, scope, out-of-scope boundaries, engineering principles, performance, automation, testing, acceptance criteria, definition of done, success metrics, and final report format.
4. Confirm the sprint does not conflict with higher-governance documents in the canonical source-of-truth order.
5. Verify compliance with `docs/HUNTER_IMPLEMENTATION_CONTRACT.md` before writing code.
6. Implement only the approved sprint scope.
7. Validate with sprint-specific commands and the required quality gates.
8. Commit, push, tag, and report the verified final state.

## Governance Rules

- Sprint files specialize release scope; they do not override higher-governance documents in the canonical source-of-truth order.
- A sprint must not authorize fabricated evidence, silent stale fallback, scoring manipulation, coverage inflation, or a competing canonical runtime.
- A sprint must satisfy `docs/HUNTER_IMPLEMENTATION_CONTRACT.md` before implementation begins.
- If a sprint conflicts with higher-order architecture documents, Codex must stop and report the conflict before implementation.
- Each sprint must preserve deterministic execution, evidence provenance, idempotent persistence, point-in-time truth, and explicit unavailable states.
- Every sprint must improve market coverage, market understanding, evidence quality, trust, prioritization, reliability, or real investment-decision usefulness.

## Current Sprint Files

- `TEMPLATE.md`: canonical sprint specification template.
- `v2.7.0.md`: Global Discovery and Candidate Registry.
- `v2.7.1.md`: Discovery Hardening and Production Verification.
- `v2.8.0.md`: Global Market Expansion.
- `v2.9.0.md`: Identity Resolution Foundation.
