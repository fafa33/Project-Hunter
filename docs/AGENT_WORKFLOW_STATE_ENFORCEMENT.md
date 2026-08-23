# Agent Workflow State Enforcement

## Status

Implementation reference for `scripts/hunter_workflow_state.py`.

It defines no merge-readiness policy. `docs/DEVELOPMENT_GOVERNANCE.md` owns merge-readiness semantics, and `docs/MERGE_READINESS_GATE.md` states the rule this evaluator consumes.

## Problem

An agent reports its own progress. "Tests pass", "review is done", "this is merge ready" are assertions, and an agent that is wrong — or that simply has not looked recently — reports the same words as one that is right. Process narration is not evidence.

## Core rule

```text
GITHUB STATE > AGENT CLAIMS
```

A claim is never an input to the derivation. The evaluator derives the state current evidence supports, then compares the claim with it. A claim above the derived state is **demoted**, and the report names the stage that stopped it.

## States

```text
IMPLEMENTED -> TESTED -> PREFLIGHT_PASSED -> PR_OPEN ->
    REVIEWED -> ZERO_OPEN_FINDINGS -> ALL_CHECKS_GREEN -> MERGE_READY
```

The states are ordered. The derived state is the furthest stage whose predecessors are *all* established, so a later stage holding in isolation does not advance the contribution. A green PR nobody has reviewed is `PR_OPEN`, not `ALL_CHECKS_GREEN` — unless review is not required for it, which is a declaration, not a derivation (see **Proportional review**).

## Evidence per state

| State | Authority |
| --- | --- |
| `IMPLEMENTED` | the open PR changes at least one file |
| `TESTED` | exact-head `Quality Gates`, which runs the canonical preflight and contains the Pytest gate |
| `PREFLIGHT_PASSED` | exact-head `Quality Gates`, which *is* `scripts/hunter_pr_preflight.py --mode normal` |
| `PR_OPEN` | an open PR targeting `main` |
| `REVIEWED` | a submitted review by someone other than the PR author, or a declared proportional-review waiver |
| `ZERO_OPEN_FINDINGS` | no unresolved review thread and no current `CHANGES_REQUESTED` |
| `ALL_CHECKS_GREEN` | the canonical decision over the check signals alone |
| `MERGE_READY` | the canonical decision, unmodified |

`MERGE_READY` is exactly `scripts/hunter_merge_readiness_v2.evaluate()` returning `success`. `ALL_CHECKS_GREEN` evaluates that same function against an observation with every non-check blocker neutralised, so it inherits the required-check set and the stale-governance-pending allowance instead of restating them. There is one merge-readiness definition in this repository, and it is not here.

## Proportional review

`docs/DEVELOPMENT_GOVERNANCE.md` requires independent review for substantive production-code, architecture, security, authority, persistence/replay, evidence-integrity, and migration changes, and states that governance must not force a small cleanup through the same review volume. That is a judgement about changed behaviour, not something GitHub reports, so it is the one input this evaluator does not derive.

`--review-not-required` declares it. The declaration:

- defaults to *required*, so the fail-closed direction is the default;
- can only excuse a **missing** review — a review that actually happened always establishes `REVIEWED` from GitHub, and the report names the reviewer;
- reaches no other state. Unresolved threads and current `CHANGES_REQUESTED` still block at `ZERO_OPEN_FINDINGS`, red checks still block at `TESTED`, and Draft still blocks at `MERGE_READY`.

The report marks a waived `REVIEWED` with authority `declared` rather than `github`, so a reader can always see that this leg rests on an assertion.

Without it, every green low-risk cleanup would stop at `PR_OPEN` and a valid `MERGE_READY` claim would be rejected — a guard that rejects canonically valid state is itself a defect (`docs/DEFECT_REGISTRY.json`, `PRH-009`).

## Local evidence

Before any PR exists, GitHub has nothing to say, so `IMPLEMENTED`, `TESTED`, and `PREFLIGHT_PASSED` may be established from locally reported evidence. That evidence is itself an agent claim, so:

- it can never reach `PR_OPEN` or beyond;
- it is ignored entirely once an open PR exists.

Otherwise a claim could be laundered into evidence by restating it as a local result — an agent whose local run passed before a change would keep `TESTED` while the hosted gate is red.

## Why `Quality Gates` and not `Hunter Pre-PR Preflight`

Both run the same script. On a branch head carrying `.hunter-preflight-mode`, `Hunter Pre-PR Preflight` passes in tests-first-red mode with a genuinely failing suite, so accepting it would let a red suite establish `TESTED`. `Quality Gates` runs `--mode normal` and is a required check.

## Not a merge gate

The evaluator publishes no commit status and adds no required check. It reports; `Hunter Merge Readiness` remains the final current-state controller, and human merge approval remains required.

This is deliberate. A new merge-blocking check needs a demonstrated merge risk the existing current-state signals cannot express, and this evaluator by construction expresses nothing they do not already carry.

## Usage

```text
python scripts/hunter_workflow_state.py --pr <number> --claim MERGE_READY
```

Exit codes: `0` when the claim is upheld or absent, `1` when it is demoted, `2` when GitHub state could not be read. Infrastructure failure is never converted into a verdict about the claim. `--json` emits the same report as a machine-readable object.

Before a PR exists there is no PR number to supply, so `--pr` is omitted and no GitHub read happens at all:

```text
python scripts/hunter_workflow_state.py --changed-files 3 --local-tests-passed --local-preflight-passed
```

Such a run can reach `PREFLIGHT_PASSED` and no further.

## Non-authority

The following are not inputs, in keeping with `docs/GOVERNANCE_ENFORCEMENT.md`:

- PR title, body, or template completeness;
- Issue identity, branch naming, commit-message formatting;
- top-level comments, reactions, owner acknowledgements;
- an agent's own progress report, completion claim, or execution state — the workflow-state claim itself is compared with the derivation, never fed into it;
- superseded historical workflow runs.

## Namespace separation

These workflow states are distinct from the execution states in `docs/AI_AUTONOMOUS_WORKFLOW_PROTOCOL.md`. Those coordinate a session; these describe what current repository evidence supports about a contribution. Neither substitutes for the other, and neither is a merge authority.
