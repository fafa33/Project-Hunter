# Project Hunter Agent Rules

All agents must treat repository-local authority and current GitHub state as the source of truth.

Read `.github/instructions/project-hunter.instructions.md` before substantive work, then read the governing Issue and acceptance criteria, relevant ADRs and architecture/roadmap documents, affected tests, and applicable entries in `docs/DEFECT_REGISTRY.json`.

Trace real executable paths before changing architecture. Use tests first for bug fixes and behavioral changes. Run the required Hunter verification gates before completion claims. Do not bypass governance or evidence requirements. Never merge, mark Ready, deploy, or perform irreversible actions without explicit owner approval.

## Durable checkpoints

Validated work must not exist only in a temporary agent workspace. At every meaningful checkpoint, after the required checks have the expected outcome, commit the exact state, push the intended branch, and verify that the remote branch HEAD exactly matches the local commit SHA before continuing substantial work.

If push fails, stop substantial new work immediately, preserve and report the exact local commit SHA and blocker, and restore durable persistence before proceeding. An explicit owner instruction not to push takes precedence; in that case preserve the commit locally and report that remote persistence is intentionally withheld.

## HGCC-1: GitHub contract compliance (Issue #603)

Every agent (Jules, Hermes, OpenCode, and future providers) must use the existing
repository-owned `.githooks/pre-push` boundary. It invokes the read-only
`scripts/hunter_github_contract.py` observer to fail closed when live GitHub
rulesets cannot be retrieved. This is **not** a merge-ready verdict: the existing
trusted controller alone checks exact PR HEAD, required checks and sources,
review threads, and publishes the canonical readiness decision. Do not bypass
this boundary, invent a parallel status publisher, or claim `PR_READY` from a
local test or contract snapshot. Any changed workflow needs focused review for
`pull_request`/`pull_request_target` trust and privileged candidate execution.
