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
- Do not use GitHub PR CI as the first execution of Ruff, Black, Mypy, or Pytest; the shared `python scripts/hunter_pr_preflight.py` command is the repository-local source of truth for those gates.
- If the exact-head preflight fails, fix the branch and rerun preflight before creating the PR. GitHub-only checks remain independent and may still run after PR creation.
