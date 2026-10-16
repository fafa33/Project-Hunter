# Project Hunter Agent Rules

All agents must treat repository-local authority and current GitHub state as the source of truth.

Read `.github/instructions/project-hunter.instructions.md` before substantive work, then read the governing Issue and acceptance criteria, relevant ADRs and architecture/roadmap documents, affected tests, and applicable entries in `docs/DEFECT_REGISTRY.json`.

Trace real executable paths before changing architecture. Use tests first for bug fixes and behavioral changes. Run the required Hunter verification gates before completion claims. Do not bypass governance or evidence requirements. Never merge, mark Ready, deploy, or perform irreversible actions without explicit owner approval.

## Durable checkpoints

Validated work must not exist only in a temporary agent workspace. At every meaningful checkpoint, after the required checks have the expected outcome, commit the exact state, push the intended branch, and verify that the remote branch HEAD exactly matches the local commit SHA before continuing substantial work.

If push fails, stop substantial new work immediately, preserve and report the exact local commit SHA and blocker, and restore durable persistence before proceeding. An explicit owner instruction not to push takes precedence; in that case preserve the commit locally and report that remote persistence is intentionally withheld.
