# CodeRabbit Hostile Review Adapter

## Purpose

Allow Hunter governance to recognize a real independent CodeRabbit review as exact-pair hostile-review evidence without requiring CodeRabbit to emit Hunter-specific markers.

## Acceptance contract

A CodeRabbit review may satisfy the independent hostile-review requirement only when all of the following are true:

1. The review author is CodeRabbit (`coderabbitai` or `coderabbitai[bot]`) and is not the PR author.
2. The review is anchored to the current PR head commit.
3. The review body records the exact base/head pair being reviewed.
4. The review state is `COMMENTED` for a passing review; `CHANGES_REQUESTED` remains adverse.
5. Existing Hunter feedback gates have already confirmed there are no unresolved blocking review threads and no unacknowledged blocking top-level comments.
6. Existing required exact-head checks, including CodeRabbit status and Hunter Governance Review, are successful.
7. Existing explicit `hunter-hostile-review:v1` evidence remains supported and unchanged.

## Security boundary

This adapter must not fabricate review evidence, rewrite CodeRabbit content, accept self-review, accept stale reviews, accept cross-pair reviews, or weaken any existing unresolved-feedback gate. It is only a recognition adapter for live GitHub review evidence that already exists on the exact pair.
