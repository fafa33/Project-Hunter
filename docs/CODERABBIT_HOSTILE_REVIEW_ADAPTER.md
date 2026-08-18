# CodeRabbit Hostile Review Adapter

## Purpose

Allow Hunter governance to consume a real independent CodeRabbit exact-pair review without requiring CodeRabbit itself to emit Hunter-specific structured markers.

The adapter is an attestation bridge: it validates live CodeRabbit review evidence, then posts the existing canonical `hunter-hostile-review:v1` marker as `github-actions[bot]`. The canonical governance preflight remains unchanged and continues to validate the marker exactly as before.

## Acceptance contract

A CodeRabbit review may be attested only when all of the following are true:

1. The review author is CodeRabbit (`coderabbitai` or `coderabbitai[bot]`) and is not the PR author.
2. The review is anchored to the current PR head commit.
3. The review body records the exact current base/head pair using CodeRabbit's own review-info text.
4. The qualifying review state is `COMMENTED`; any current-head `CHANGES_REQUESTED` CodeRabbit review blocks attestation.
5. Empty or malformed review bodies do not qualify.
6. Existing Hunter feedback gates remain authoritative for unresolved review threads and top-level comment acknowledgement.
7. Existing required exact-head checks, CodeRabbit status, and Hunter Governance Review remain authoritative in the normal readiness path.
8. Existing explicit `hunter-hostile-review:v1` evidence remains supported and unchanged.

## Attestation output

The bridge posts a normal GitHub PR review on the same exact head with:

- the canonical `hunter-hostile-review:v1` marker;
- structured `Hostile review evidence`, `Scope probed`, and `Limitations` records pointing to the qualifying CodeRabbit review URL;
- `Unresolved blocking findings: 0`.

The bridge does not claim that CodeRabbit emitted Hunter metadata. It records that the independent CodeRabbit review was validated and translated into Hunter's existing evidence format by repository automation.

## Security boundary

The adapter must not fabricate CodeRabbit content, accept self-review, accept stale reviews, accept cross-pair reviews, accept `CHANGES_REQUESTED`, or weaken any existing readiness gate. It does not change Project Hunter runtime code and does not modify the canonical hostile-review parser.
