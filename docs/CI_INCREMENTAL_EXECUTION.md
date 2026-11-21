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

`python3 scripts/hunter_ci_final_execution.py measure --repository fafa33/Project-Hunter --token ... --limit 30` reads real GitHub Actions run/job timestamps from the workflow-specific Actions API. Durations come only from GitHub timestamps; missing timestamps are reported as `null` rather than invented. `api_requests` counts the exact REST calls made. The shared transport does not expose rate-limit headers, so that field is reported as unavailable rather than guessed. The measurement lane is read-only: it emits JSON to stdout (redirect with `> path` to persist an artifact) and has no `--output` flag, so no caller-supplied argument can select a filesystem write target. Evidence artifacts: `/private/var/folders/4b/tvf7qwyj4fx67pn_rkjn_9wh0000gn/T/opencode/issue606-perf-before.json` (BEFORE) and `/private/var/folders/4b/tvf7qwyj4fx67pn_rkjn_9wh0000gn/T/opencode/issue606-perf-after.json` (AFTER).

### BEFORE — repeated full hosted proof (no equivalence reuse)

30 `Hunter / Pre-PR Preflight` runs measured; 31 REST requests (1 runs listing + 30 job listings); 21 successful. Successful-run job duration: min 617 s, median 828 s, max 1004 s (workflow median 831 s). Recent examples: run 44772017274 @ `6449005c` job 980 s; 44736780949 job 759 s; 44733326827 job 828 s; 44729622493 job 635 s; 44644696452 job 905 s; 44636764725 job 1004 s.

### AFTER — hosted rerun of the pushed candidate

The pushed candidate HEAD reran all required and security workflows successfully: `Hunter Pre-PR Preflight`, `Trusted Candidate Preflight Validation`, `Quality Gates`, `CodeQL`, `dependency-review`, `Hunter Merge Readiness Controller`, `Hunter Governance Review`, `Publish Exact-Head Trusted Upgrade Proof`, `Independently Verify Candidate Module Digests`, and `SonarCloud Code Analysis` (Quality Gate Passed, 0 new issues, 0 security hotspots). `Main Validation` remains skipped by its existing path filter; that skip does not suppress any required context.

`issue606-perf-after.json` is the read-only 30-run measurement taken from the same GitHub Actions timeline: 30 `Hunter / Pre-PR Preflight` runs, 31 REST requests (1 runs listing + 30 job listings), 23 successful. Successful-run job duration: min 617 s, median 803 s, max 1004 s (all-run workflow median 811 s). It includes the candidate's own successful run 44803026391 (job 704 s / workflow 707 s).

These are point-in-time samples, not a controlled A/B experiment: the BEFORE and AFTER windows cover partially different head sets, and job durations vary 617–1004 s run to run. The median shift (828 s → 803 s) is within that variance and is reported only as observed data; no causal improvement, target attainment, or security-gate relaxation is claimed. The behavior that avoids redundant heavy work is enforced by the cross-HEAD equivalence authority and covered by focused regression tests — it is not inferred from these timings.

## Trusted push-lane equivalence (implemented)

`.github/workflows/hunter-pre-pr-preflight.yml` now contains a separate
`Push Proof Equivalence Decision` job that checks out only the trusted default
branch, installs the trusted toolchain, fetches the pushed SHA as untrusted Git
evidence, and calls `hunter_preflight_reuse_decision.py --push` with a read-only
token scoped to that job. It never checks out or executes candidate code; the
candidate `Hunter Pre-PR Preflight` job has no decision token and clears
`GITHUB_TOKEN`/`GH_TOKEN` before the heavy step.

`decide_push` resolves PR identity and base from the exact pushed HEAD,
requires an open same-repository PR, classifies only the source-proof-to-current
commit delta as review-metadata-only, and delegates all immutable proof
validation to the existing canonical authority via
`verify_for_candidate`. Every missing, malformed, stale, conflicting or
unverifiable identity refuses reuse and runs the full suite.

Bootstrap constraint: the decision job runs the adjudicator from the trusted
default branch, so the optimization cannot exercise itself in the PR that
introduces it. An unavailable or incompatible trusted adjudicator emits
`reusable=false` and runs the full suite; the decision job still succeeds and
no required context is left pending or failed for that reason. The reuse path
becomes active only after the adjudicator is present on the trusted default
branch.

The required `Hunter Pre-PR Preflight` check always concludes: the preflight job
uses `always() && !cancelled()` on `needs: proof-equivalence`, and the heavy
suite is suppressed only by a positively reusable decision on a normal-mode
HEAD. Two mutually exclusive named steps record `Hunter proof mode:
verified-reuse` or `Hunter proof mode: full-execution` in the authoritative job
record. `proof_execution_mode` folds that marker into the canonical proof
digest, `resolve` refuses to treat a verified-reuse job as a fresh exact-head
full proof, and `_source_proof_bindings` refuses a verified-reuse run as a
source proof. A successful skipped job is therefore never equivalent to a newly
executed suite; only the original immutable full proof can anchor equivalence.

### Focused regression evidence

`python3 -m pytest tests/test_hunter_ci_impact.py tests/test_preflight_reuse_decision.py tests/test_hunter_ci_final_execution.py tests/test_hma1_trusted_gate.py tests/test_hunter_merge_readiness.py -q`
→ **175 passed**. Ruff, Black and `mypy --follow-imports=skip` passed on the
changed Python files. The new hostile cases cover metadata-only and ordinary
code/dependency/workflow/trust-root deltas, missing and failed source proofs,
newer failed attempts, A→B and A→B→A HEAD movement via Git-backed subtree
digests, concurrent identical decisions, stale callbacks, base changes,
fork/closed-PR self-authorization, proof-mode forgery/ambiguity, and workflow
token/conclusion wiring.

## Required checks and non-weakening

No required context, ruleset, security gate, signature, or trusted boundary is removed or weakened. `paths-ignore` skipping never suppresses a required context. Merge remains owner-gated; this change does not merge or mark the PR ready by itself.
