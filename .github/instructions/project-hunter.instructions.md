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
- Before opening any pull request, push the final candidate head and require `Hunter Pre-PR Preflight` to succeed on that exact head in the mode appropriate to the branch state.
- Normal branches use `python scripts/hunter_pr_preflight.py --mode normal` and require Ruff, Black, Mypy, and Pytest to pass before PR creation.
- Intentional tests-first RED branches must include `tests-first` in the branch name and use `python scripts/hunter_pr_preflight.py --mode tests-first-red`; Ruff, Black, and Mypy must pass before the Draft PR is created, while the intended failing tests remain separately attributable as RED evidence.
- Never classify Ruff, Black, Mypy, workflow/setup, or unrelated test failures as acceptable tests-first RED.
- Build every normal PR body from `.github/pull_request_template.md`; do not rename or omit required section headings, and select exactly one implementer readiness declaration before creation.
- Do not use GitHub PR CI as the first execution of Ruff, Black, Mypy, or Pytest; the shared preflight command is the repository-local source of truth for deterministic gates.
- If the exact-head preflight fails, fix the branch and rerun preflight before creating the PR. GitHub-only checks remain independent and may still run after PR creation.
