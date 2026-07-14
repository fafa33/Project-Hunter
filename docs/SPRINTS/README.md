# Project Hunter Sprint Specifications

This directory is the canonical home for Project Hunter sprint specifications.

Each sprint file records the approved implementation scope for one release. Codex must read the relevant sprint file before implementation, together with the architecture manifest, architecture specification, roadmap, and implementation guide.

## Workflow

1. Create or update `docs/SPRINTS/vX.Y.Z.md` before implementation begins.
2. Define the sprint mission, scope, out-of-scope boundaries, acceptance criteria, validation commands, and final reporting requirements.
3. Keep the sprint aligned with the frozen architecture documents.
4. During implementation, preserve deterministic behavior, evidence provenance, idempotent persistence, point-in-time truth, and explicit unavailable states.
5. After release, update the sprint file only when correcting factual release results or documenting verified outcomes.

## Rules

- Sprint files may specialize release scope, but they must not override architecture invariants.
- A sprint may add implementation detail, validation commands, and operational constraints.
- A sprint must not authorize fabricated evidence, silent stale fallback, scoring manipulation, or coverage inflation.
- If a sprint conflicts with the architecture manifest or architecture specification, Codex must stop and report the conflict.

## Files

- `TEMPLATE.md`: canonical sprint specification template.
- `v2.7.0.md`: Global Discovery and Candidate Registry.
- `v2.7.1.md`: Discovery Hardening and Production Verification.
- `v2.8.0.md`: Global Market Expansion.
- `v2.9.0.md`: Identity Resolution Foundation.
