# Claude Code Instructions

## Repository Authority

Before performing implementation, architecture analysis, ADR review, ADPR review, governance analysis, or repository review:

1. Load and follow the repository's latest accepted canonical governance documents.
2. Treat accepted canonical documents as the highest authority.
3. Never invent review criteria when canonical governance already defines them.
4. Follow the latest accepted governance rather than previous conversation context.
5. If multiple canonical documents apply, respect the repository's documented authority hierarchy.

## Architecture Reviews

Architecture reviews must:

- follow `docs/ARCHITECTURE_AUDIT_PROTOCOL.md`;
- use `docs/ARCHITECTURE_AUDIT_TEMPLATE.md` where an audit report is required;
- distinguish editorial and documentation-quality findings from decision-blocking and fundamental architecture findings;
- evaluate materiality and decision consequence before assigning a verdict;
- never use raw issue counts or simple PASS/FAIL totals as the basis for readiness;
- apply targeted re-audit rules after revisions unless the architecture scope materially changes.

## Decision Preparation

For architecturally significant work:

- follow `docs/ARCHITECTURE_DECISION_PREPARATION_GUIDE.md`;
- assess quality using `docs/ARCHITECTURE_DECISION_QUALITY_STANDARD.md`;
- keep evidence, assumptions, unresolved conflicts, and missing information explicit;
- do not begin implementation while a material architectural decision remains unresolved.

## Implementation and Review Boundaries

- Implementation must not redefine architecture.
- Review must not invent new architecture or substitute reviewer preference for canonical requirements.
- If requested work conflicts with accepted governance, canonical architecture, or an accepted ADR, stop and report the conflict.
- Do not silently expand scope, weaken evidence requirements, bypass replay or provenance obligations, or treat unavailable evidence as neutral or successful.

## Development Lifecycle

All permanent repository changes must follow `docs/DEVELOPMENT_GOVERNANCE.md` and applicable review and merge-readiness documents.

Before reporting completion, verify the actual repository state and clearly distinguish implemented, tested, reviewed, blocked, and unavailable outcomes.
