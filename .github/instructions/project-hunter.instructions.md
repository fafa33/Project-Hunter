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
- `docs/GOVERNANCE_ENFORCEMENT.md`

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
- Before a governed branch, commit, push, PR creation/body update, Ready-for-Review promotion, blocking-finding resolution, or merge-readiness assertion, run the matching `python scripts/hunter_governance_preflight.py ...` action. Do not rely on remembered Issue identity or hand-reconstructed governance state.
- Use `python scripts/hunter_governance_preflight.py generate-pr-body ...` for normal governed PR bodies. The governing Issue, canonical template, exact changed scope, exact head/base pair, and criterion-specific evidence must drive generated metadata.
- Never infer `PASS` from green CI. Unproven Issue acceptance criteria remain `BLOCKED`/incomplete until explicit evidence exists.
- Before opening a normal pull request, push the final candidate head and require `Hunter Pre-PR Preflight` to succeed on that exact head.
- Do not use GitHub PR CI as the first execution of Ruff, Black, Mypy, or Pytest; the shared `python scripts/hunter_pr_preflight.py` command is the repository-local source of truth for those quality gates.
- If the exact-head quality preflight or governance preflight fails, correct the branch and rerun the applicable preflight before the governed mutation. GitHub-only checks remain independent and may still run after PR creation.
- Passing preflight is never review approval and never merge authority. Independent review and human merge approval remain governed by `docs/AI_REVIEW_PROTOCOL.md` and `docs/DEVELOPMENT_GOVERNANCE.md`.
