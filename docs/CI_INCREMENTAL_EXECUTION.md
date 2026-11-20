# Issue #606 — bounded incremental validation checkpoint

## Current trusted execution contract

- `Hunter / Pre-PR Preflight` runs on non-main branch push and owns the hosted full proof.
- `Hunter / Trusted Preflight Upgrade` runs on PR opened/reopened/synchronize and may reuse only a verified exact-head hosted receipt when no trusted validation-definition path changed. Missing or contradictory evidence runs fresh gates.
- `Quality Gates` CI runs on PR and main push; its existing reuse adjudicator requires immutable tree, definition, toolchain, and trusted run evidence.
- Governance review, readiness and reconciliation also respond to PR or workflow events, but those events must not be interpreted as new candidate content without verifying the immutable head.

## Advisory classification added in this slice

`hunter_ci_impact.classify_review_metadata_only` identifies a diff consisting solely of `.hunter/pre-ready-hostile-review.json`. It does **not** skip tests, mint a receipt, change required statuses, or authorize review. The existing full-proof gate and independent exact-head review remain mandatory. Mixed changes, empty diffs, changed dependencies, workflows, security definitions, and unavailable evidence remain full-proof cases.

## Cross-HEAD proof equivalence (implemented)

`scripts/hunter_validation_reuse.py` is the trusted equivalence authority; `scripts/hunter_ci_final_execution.py` is only its standalone operator CLI/consumer. The core lives in the already-root-of-trust canonical module so the trusted reuse decision never depends on a module outside the connector write-ingress root of trust (the push guard enforces this). Equivalence lets a metadata-only candidate HEAD bind to the immutable proof of a *previous* HEAD only when every validated input is proven identical. It never copies an old HEAD's success into a new HEAD's proof: a successful equivalence produces a **new current-HEAD-bound decision that references the original immutable source proof**.

### Tested-subtree digest

`compute_tested_tree_digest` hashes Git blob object IDs from `git ls-tree -r -z <head>` (paths + modes + blob SHAs). It excludes exactly one path, `.hunter/pre-ready-hostile-review.json`, because that file is lifecycle metadata rather than a validated input. Every other path — application code, tests, dependency lockfiles, workflows, trust roots and security definitions — is included, so any change to a tested input changes the digest and equivalence fails closed. Missing/ambiguous Git evidence fails closed.

### Equivalence bindings

`EQUIVALENCE_IDENTITIES` declares all 15 required bindings: repository, PR, current exact HEAD, immutable source proof HEAD, base SHA, tested-subtree digest, dependency identity, workflow-definition identity, toolchain identity, environment identity, trusted workflow, run attempt, trusted job identity, canonical proof digest, and provenance. `verify_equivalence` checks each binding and returns a decision; any missing, inconsistent, ambiguous or unverifiable binding refuses reuse.

### Canonical proof authority (no second authority)

`_source_proof_bindings` lives in `hunter_validation_reuse.py` and derives the source proof's run, attempt, job and digest through that module's own canonical authorities (`_canonical_job_proof` and `trusted_branch_preflight_run`). No parallel proof minting is introduced. `verify_for_candidate` is the enforcement entry point called by `hunter_preflight_reuse_decision.decide()` for metadata-only diffs; it returns `cross-HEAD proof reuse: ...` on success and refuses into a fresh full run on any failure. Independent exact-head hostile review remains mandatory.

## Concurrency hardening (implemented)

`tests/test_preflight_reuse_decision.py` and `tests/test_hunter_ci_final_execution.py` cover the Issue #606 race matrix with hostile regression tests against real production selectors/decision functions where practical: HEAD A→B while A runs; A→B→A; two simultaneous controllers producing identical canonical outcomes; stale `workflow_run` callbacks; delayed/over-deadline publication; cancelled runs; malformed/unknown proof and proof-bindings failures; replayed digests for a different head; base changes during adjudication; conflicting API snapshots for the same run/attempt; and newer-run-attempt precedence. An old HEAD can never authorize a newer HEAD.

## Hosted CI performance evidence

### Measurement method

`python3 scripts/hunter_ci_final_execution.py measure --repository fafa33/Project-Hunter --token ... --limit 30` reads real GitHub Actions run/job timestamps from the workflow-specific Actions API. Durations come only from GitHub timestamps; missing timestamps are reported as `null` rather than invented. `api_requests` counts the exact REST calls made. The shared transport does not expose rate-limit headers, so that field is reported as unavailable rather than guessed. Evidence artifacts: `issue606-perf-before.json` (this head) and `issue606-perf-after.json` (pushed head).

### BEFORE — repeated full hosted proof (no equivalence reuse)

30 `Hunter / Pre-PR Preflight` runs measured; 31 REST requests (1 runs listing + 30 job listings); 21 successful. Successful-run job duration: min 617 s, median 828 s, max 1004 s (workflow median 831 s). Recent examples: run 44772017274 @ `6449005c` job 980 s; 44736780949 job 759 s; 44733326827 job 828 s; 44729622493 job 635 s; 44644696452 job 905 s; 44636764725 job 1004 s.

### AFTER

Pending hosted rerun of the pushed HEAD and the controlled metadata-only scenario. No improvement or target claim is made until that evidence exists.

## Required checks and non-weakening

No required context, ruleset, security gate, signature, or trusted boundary is removed or weakened. `paths-ignore` skipping never suppresses a required context. Merge remains owner-gated; this change does not merge or mark the PR ready by itself.
