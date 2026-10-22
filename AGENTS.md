# Project Hunter Agent Rules

All agents must treat repository-local authority and current GitHub state as the source of truth.

Read `.github/instructions/project-hunter.instructions.md` before substantive work, then read the governing Issue and acceptance criteria, relevant ADRs and architecture/roadmap documents, affected tests, and applicable entries in `docs/DEFECT_REGISTRY.json`.

Trace real executable paths before changing architecture. Use tests first for bug fixes and behavioral changes. Run the required Hunter verification gates before completion claims. Do not bypass governance or evidence requirements. Never merge, deploy, or perform irreversible actions without explicit owner approval. Under the owner-approved Issue #616 policy, only the trusted default-branch Candidate Admission controller may automatically mark a same-repository Draft PR Ready after canonical exact-head proof and all pre-Ready security/acceptance checks pass; no per-PR or per-HEAD owner comment, manual Ready action, or additional owner approval is required. Ready is not merge authorization; merging always requires separate explicit owner approval. Fail closed for missing/stale/ambiguous proof, HEAD/base races, forks, or permission errors. Preserve the existing Draft reviewer guard and ready_for_review orchestration; do not give candidate code privileged write authority.

## Portable push toolchain

Before the first push on each execution host, provision the repository-declared developer toolchain with `python3 scripts/hunter_push.py --setup`. Use `python3 scripts/hunter_push.py ...` for subsequent pushes. This preserves the repository-owned pre-push hook in non-interactive Gateway/SSH sessions and fails closed if the persistent toolchain is missing. See `docs/HUNTER_PUSH_TOOLCHAIN.md`; never bypass the hook for missing tools.

## Durable checkpoints

Validated work must not exist only in a temporary agent workspace. At every meaningful checkpoint, after the required checks have the expected outcome, commit the exact state, push the intended branch, and verify that the remote branch HEAD exactly matches the local commit SHA before continuing substantial work.

If push fails, stop substantial new work immediately, preserve and report the exact local commit SHA and blocker, and restore durable persistence before proceeding. An explicit owner instruction not to push takes precedence; in that case preserve the commit locally and report that remote persistence is intentionally withheld.

## Review request finalization after remediation

For every governed PR whose code HEAD changes after a finding fix, the **authorized
writer**, not Reconcile or the reviewer, must finalize the exact-head review
request before the final signed push. After focused regression verification and
before the final signed commit, regenerate `.hunter/pre-ready-hostile-review.json`
using `scripts/hunter_pre_ready_review.py --refresh-request` with a freshly
substantiated judgement for the governing Issue and actual base-to-candidate
HEAD. Recompute applicable defect-family coverage from the current diff; never
reuse previous reviewer authority, assert success without evidence, or copy a
stale request. Commit the regenerated request through the same approved signed
writer channel, then verify the committed final HEAD with the canonical
`verify_review_request` verifier. If judgement evidence is unavailable, stop
review orchestration fail-closed and report the blocker rather than fabricating
review results. Repeat only if content changes; never rerun reviewers solely
because the same valid request is encountered again. Reconcile remains a
read-only consumer and must not receive write or signing privileges.

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
