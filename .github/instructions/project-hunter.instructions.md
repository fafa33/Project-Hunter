---
applyTo: "**"
---

# Project Hunter AI Agent Instructions

Always treat the repository as the source of truth.

Before making changes, identify and follow the applicable repository governance, including:

- `docs/PROJECT_CONSTITUTION.md`
- `docs/PROJECT_PRINCIPLES.md`
- `docs/CANONICAL_ARCHITECTURE_MAP.md`
- applicable accepted ADRs
- `docs/DEVELOPMENT_GOVERNANCE.md`
- `docs/AI_AUTONOMOUS_WORKFLOW_PROTOCOL.md`
- `docs/AI_REVIEW_PROTOCOL.md`
- `docs/MERGE_READINESS_GATE.md`
- `docs/HUNTER_IMPLEMENTATION_CONTRACT.md`

Rules:

- Never bypass repository governance.
- Never invent architecture or requirements.
- Re-derive conclusions from repository authorities rather than previous conversations.
- Keep changes strictly within the authorized scope.
- Continue through mandatory in-scope governance stages until a valid stopping boundary is reached.
- Do not request unnecessary prompts between governed stages.
- Preserve independent-role boundaries.
- Never represent self-review as independent review.
- Quote governing repository authorities for material architectural or governance conclusions.
- Prefer the smallest valid change that satisfies repository governance.
- Before opening a normal pull request, push the final candidate head and require `Hunter Pre-PR Preflight` to succeed on that exact head.
- Before opening a tests-first RED pull request, push the final candidate head and require the governed tests-first hygiene preflight to succeed on that exact head; Ruff, Black, and Mypy failures are never valid expected RED.
- Build every normal PR body from `.github/pull_request_template.md`; do not rename or omit required section headings, and select exactly one implementer readiness declaration before creation.
- Do not use GitHub PR CI as the first execution of Ruff, Black, Mypy, or Pytest; the shared `python scripts/hunter_pr_preflight.py` command is the repository-local source of truth for those gates.
- If the exact-head preflight fails, fix the branch and rerun preflight before creating the PR. GitHub-only checks remain independent and may still run after PR creation.

## Mandatory Recurring-Defect Prevention

This section applies to every agent role, every code change, every test change, every review, and every pull request.

A defect that has been discovered once must not be treated as a one-off patch in later work. Every discovered defect must be promoted into a reusable prevention rule for its entire defect class.

Required workflow for every finding:

1. Identify the concrete finding.
2. Name the underlying defect class rather than only the affected line or file.
3. Search the complete in-scope diff and relevant sibling paths for the same defect class.
4. Correct the root cause and ownership/design error that allowed the class to exist.
5. Add or strengthen deterministic regression coverage that fails if the defect class returns.
6. Where the governing contract requires a blocking invariant, add a non-vacuous counterfactual test proving that disabling/removing the root protection makes the regression test fail.
7. Run the repository-local deterministic preflight before PR creation or PR update as applicable.
8. Perform adversarial implementer self-review against all previously known defect classes relevant to the changed subsystem before requesting independent review.
9. Use independent review to search primarily for genuinely new defects, not to rediscover known classes the implementation process should already prevent.

Coding is subject to the same rule as review. Before implementation is considered complete, the implementer must actively search for previously known defect classes in all changed and sibling paths that share the same authority, persistence, replay, provenance, validation, or execution boundary.

Known defect classes must not be reintroduced under a different field name, helper, adapter, repository method, code path, or representation. A local fix is insufficient when the same root assumption can survive elsewhere.

If the same defect class is discovered a second time during the same implementation/review cycle, stop local patching. Return to the responsible design, ownership boundary, invariant, or shared mechanism and correct the root cause before continuing.

Repeated findings are process failures: a previously known defect class reaching a later PR or independent audit means the prevention mechanism is incomplete and must itself be corrected.

The required development flow is:

`Finding -> Defect Class -> Sibling Search -> Root Correction -> Regression Protection -> Counterfactual Validation (where required) -> Preflight -> Adversarial Self-Review -> PR -> Independent Review`

The prohibited development flow is:

`Code -> PR -> Audit -> Local Patch -> Re-audit -> Local Patch`

A pull request may still expose a genuinely new defect. It must not repeatedly expose a defect class that Project Hunter has already learned how to recognize and prevent.
