# Issue #606 — bounded incremental validation checkpoint

## Current trusted execution contract

- `Hunter / Pre-PR Preflight` runs on non-main branch push and owns the hosted full proof.
- `Hunter / Trusted Preflight Upgrade` runs on PR opened/reopened/synchronize and may reuse only a verified exact-head hosted receipt when no trusted validation-definition path changed. Missing or contradictory evidence runs fresh gates.
- `Quality Gates` CI runs on PR and main push; its existing reuse adjudicator requires immutable tree, definition, toolchain, and trusted run evidence.
- Governance review, readiness and reconciliation also respond to PR or workflow events, but those events must not be interpreted as new candidate content without verifying the immutable head.

## Advisory classification added in this slice

`hunter_ci_impact.classify_review_metadata_only` identifies a diff consisting solely of `.hunter/pre-ready-hostile-review.json`. It does **not** skip tests, mint a receipt, change required statuses, or authorize review. The existing full-proof gate and independent exact-head review remain mandatory. Mixed changes, empty diffs, changed dependencies, workflows, security definitions, and unavailable evidence remain full-proof cases.

## Remaining acceptance work (not claimed complete)

A review-request-only commit changes the exact HEAD, so the old HEAD's proof cannot simply be reused. Before suppressing any heavy suite, implement a trusted proof equivalence authority binding the *new* HEAD to unchanged validated inputs, base, toolchain, workflow definitions, run/attempt, job and digest. Cover A→B→A, concurrent pushes, cancellation, delayed publication, stale callbacks and terminal required statuses with hostile regression tests. Measure hosted execution before/after; do not create a parallel controller or weaken the canonical outcome authority.
