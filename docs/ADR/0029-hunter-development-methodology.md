# ADR 0029: Hunter Development Methodology

## Status

Proposed.

## Context

Project Hunter increasingly relies on AI agents to plan, implement, review, and verify significant repository changes. Existing governance defines authority boundaries, review independence, evidence requirements, and development controls, but the repository does not yet define one canonical lifecycle for major AI-assisted changes.

PR #200 exposed the need for such a lifecycle. The governance capability being introduced was itself rejected by the governance process it introduced, and independent hostile review identified defects that were not caught by implementation tests alone. This demonstrated that implementation, automated review, independent review, and architectural knowledge extraction must be distinct responsibilities.

The repository also needs a durable rule that no implementation is exempt from the governance it introduces.

## Decision

Project Hunter adopts the Hunter Development Methodology (HDM) for significant architectural, governance, persistence, replay, authority, or AI-runtime changes.

The canonical lifecycle is:

1. Architecture — establish the problem, authority boundary, scope, risks, and whether an ADR is required.
2. Design — define contracts, interfaces, persistence, replay, provenance, migration, and non-goals.
3. Implementation — implement only the approved scope with tests and documentation parity.
4. Verification — run deterministic checks, full quality gates, integration tests, and live verification where operational behavior is claimed.
5. Technical Defense — the implementer explains and evidences architectural choices, rejected alternatives, remaining debt, and known limitations. Technical Defense must be preserved as a durable repository or pull-request artifact, not only in a private chat.
6. Independent Review — an independent reviewer must challenge both implementation and Technical Defense. The implementer must not approve its own work.
7. Architecture Review — classify findings as merge blockers, bounded follow-up work, or future architecture; prevent both unsafe merge and uncontrolled scope expansion.
8. Knowledge Extraction — capture reusable patterns, principles, failure modes, debt, and follow-up architecture discovered during the change.
9. Canonical Integration — update applicable ADRs, canonical maps, implementation contracts, governance documents, and roadmap artifacts.
10. Merge — merge only after required evidence is complete, blockers are resolved, independent review is satisfied, and repository enforcement is correctly configured for the claimed operating mode.

The following principles are binding for this methodology:

- No implementation is exempt from the governance it introduces.
- A governance capability must successfully govern its own introduction before governing subsequent changes.
- The implementer does not approve its own implementation.
- Passing tests and CI are necessary but not sufficient evidence of architectural readiness.
- Automated governance decisions may not be overridden merely by opinion. The underlying issue must be fixed, or the governing rule must be formally changed and documented.
- Architecture may evolve through independent review, but review findings must be separated into current merge blockers and explicitly deferred future architecture to prevent scope creep.
- Merge is the end of a verified learning cycle, not merely the end of coding.

PR #200 is the first historical application of this methodology: the governance gate evaluated and rejected the pull request introducing that gate. This event establishes the project principle that Hunter applies its rules to itself before applying them to later changes.

## Consequences

- Significant PRs require explicit role separation among implementer, automated governance, independent reviewer, and final repository authority.
- Technical Defense and independent review become durable evidence.
- Major PRs may produce follow-up ADRs, issues, or runtime proposals through Knowledge Extraction.
- The methodology adds process cost, but concentrates that cost on high-risk changes and reduces false confidence from green CI alone.
- Future automation may make HDM partially executable by verifying required artifacts and lifecycle stages before merge.
- Minor, low-risk maintenance may use a proportionate subset of HDM, but must not bypass binding governance or authority rules.

## Alternatives Considered

- Continue with informal AI-agent workflows. Rejected because role boundaries and architectural lessons remain dependent on transient chat context.
- Require only tests, CI, and one AI review. Rejected because PR #200 demonstrated that operational success and local tests can coexist with material architectural defects.
- Require every change to execute the full methodology. Rejected as disproportionate for low-risk maintenance and contrary to Project Hunter's simplicity principle.
- Allow repository owners or reviewers to override a failed governance decision without changing code or policy. Rejected because it would make enforcement optional and undermine trust.
