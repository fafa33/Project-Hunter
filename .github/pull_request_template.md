## Summary

Describe the exact issue/milestone scope implemented by this PR.

## Scope and architecture

- [ ] The governing Issue and acceptance criteria are linked.
- [ ] Architecture Impact Check is recorded.
- [ ] Evidence Impact is recorded.
- [ ] No unapproved scope expansion is present.
- [ ] Relevant ADR authority, provenance, missingness, replay, and persistence boundaries remain intact.

## Acceptance-criteria matrix

Every criterion must be listed explicitly. Use only `PASS`, `FAIL`, `BLOCKED`, or `NOT APPLICABLE`.

| Acceptance criterion | Status | Evidence |
|---|---|---|
| Replace with criterion | BLOCKED | Replace with test, runtime record, query result, or explanation |

- [ ] No criterion is omitted or inferred from green CI.
- [ ] No `FAIL` or `BLOCKED` criterion remains for a PR presented as merge-ready.

## Verification

- [ ] `ruff check .`
- [ ] `black --check .` or the repository-approved equivalent
- [ ] `mypy`
- [ ] Full `pytest` suite
- [ ] Required migrations/configuration checks

Record exact commands and results:

```text
Replace with verification output summary.
```

## Operational validation

- [ ] All required runbooks were executed in a suitable environment.
- [ ] Required live providers/APIs were exercised.
- [ ] Required records were persisted and independently queried/replayed.
- [ ] Evidence identifiers, provenance, and logical history were verified.
- [ ] No fixture, fabricated response, or current-state substitution was used where live or point-in-time evidence was required.
- [ ] Environment/network limitations are disclosed below.

Operational evidence:

```text
Replace with commands, record IDs, query/replay results, and environment details.
```

## Remaining limitations and risks

List every known incomplete item, environmental blocker, deferred requirement, and residual risk. Write `None` only after explicit verification.

## Implementer readiness declaration

Select exactly one. This is the implementer's self-assessment, not an approval — per `docs/AI_REVIEW_PROTOCOL.md`, the implementer does not approve the implementation. `APPROVED` is not a valid selection here; it is recorded only by an independent reviewer, in a separate review, after required review and verification have completed.

- [ ] `READY FOR REVIEW` — all acceptance criteria and required operational validations pass from the implementer's own assessment.
- [ ] `CHANGES REQUIRED` — implementation or evidence remains incomplete.
- [ ] `BLOCKED` — completion depends on an unavailable environment, provider, credential, or external condition.

> Green CI and the absence of unresolved blocking review comments are necessary signals, not proof of completion. Non-blocking recommendations and already-resolved review comments do not prevent merge. A PR with any unsatisfied required acceptance criterion or operational validation, or any unresolved blocking review comment, must not be merged.
