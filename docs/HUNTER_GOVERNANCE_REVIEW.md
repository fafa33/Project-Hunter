# Hunter Governance Review

## Status

This document is the implementation specification for the **Hunter Governance
Review** mandatory merge gate. The gate is a repository feature: code,
workflows, tests, and documentation, not a comment-only bot.

## Objective

No pull request may merge into a protected branch unless the required status
check

> **Hunter Governance Review**

completes successfully for the **exact current source HEAD** and **current
target BASE** of that pull request.

Green CI, passing tests, Copilot review, Dependency Review, or any other check
is not sufficient by itself. The gate is **fail-closed**: every failure mode
blocks the merge, and the gate is never skipped silently.

## Architectural decision: deterministic, provider-independent, zero external calls

The gate is **entirely deterministic, repository-native, and CI-native**. It
never calls an external LLM or any other network service besides GitHub
itself, requires no API key or provider secret, and makes zero paid or
external API calls. No external provider's availability, quota, billing
state, or API error can ever block or approve a merge — only a genuine
deterministic governance violation, or required repository evidence that
could not be confirmed to exist at the exact base commit, can.

This repository previously ran an earlier version of this gate that included
an LLM hostile-architecture-audit stage (diff chunking, a generic
provider-failover abstraction, and a durable checkpoint mechanism to survive
provider quota exhaustion across separate workflow runs). That stage was
**removed entirely** as a deliberate, final architectural decision: live
production evidence (Gemini's free-tier request quota and OpenAI's account
credits both exhausting repeatedly, mid-review, on this repository's real
diff sizes) showed that any external-provider dependency in the mandatory
merge path — however well the failover, retry, and resume logic around it
was engineered — meant the required status check's outcome could be governed
by a third party's billing state rather than by this repository's own rules.
No replacement AI/provider service was substituted; the deterministic and
context-resolution layers, which never depended on an LLM, are unchanged and
remain the entire authoritative gate.

## Architecture

```text
Pull Request Event
        |
        v
Resolve exact source HEAD and target BASE
        |
        v
Authoritative Context Resolution   (scripts/hunter_governance_review/context.py)
        |
        v
Deterministic Governance Engine    (scripts/hunter_governance_review/deterministic.py)
        |
        v
Decision Engine                    (scripts/hunter_governance_review/decision.py)
        |
        v
Re-verify review pair, immediately before publishing
        |
        v
GitHub required status check: Hunter Governance Review
```

### Stage 1 — Authoritative Context Resolution

Confirms that the repository's canonical governance documents, and any
ADR/ADPR record referenced by the pull request body, actually **exist** at
the **exact base commit**, via the GitHub Contents API — never from whatever
happens to be checked out locally, which is a single shallow checkout made
once per workflow run and is not guaranteed to be pinned to the exact
recorded base SHA for every validator that might read it.

The document set is not hardcoded: the canonical hierarchy is parsed
directly out of `docs/CANONICAL_ARCHITECTURE_MAP.md`'s own numbered
"Canonical Document Authority Hierarchy" list (itself fetched at the same
exact base commit), so a governance change to that hierarchy is picked up
automatically. Every document from that hierarchy is **mandatory**: if any
cannot be retrieved at the exact base SHA, resolution raises
`ContextResolutionError` and the run publishes `REVIEW_FAILED` — required
repository evidence is missing, and context resolution never fails open.
ADR/ADPR numbers and other `docs/*.md` paths mentioned only in the PR body
are optional context; missing ones are recorded in the manifest but do not
by themselves fail the review (the deterministic V-070 validator, below,
already blocks a PR that references a non-existent ADR/ADPR).

Every document consulted (or attempted) is recorded in a deterministic
**context manifest**: exact path, exact ref, mandatory/referenced,
resolved/missing status, SHA-256 content hash, and byte length. This
manifest is printed to the run log, written to `GITHUB_STEP_SUMMARY`, and is
the auditable evidence for exactly what repository evidence an approval was
actually based on. This module only confirms existence and records
provenance — it never builds prompt text, since nothing downstream consumes
document content anymore.

### Stage 2 — Deterministic Governance Engine

Validates the pull request against the repository's canonical governance
rules (`docs/DEVELOPMENT_GOVERNANCE.md`, `docs/MERGE_READINESS_GATE.md`,
`docs/AI_REVIEW_PROTOCOL.md`, `docs/ARCHITECTURE_AUDIT_PROTOCOL.md`), purely
as a function of PR metadata (title, body, draft state, mergeable state) and
the changed-files list already returned by the GitHub API — no LLM call, no
diff content inspection, no network access beyond what Stage 1 already
performed. The validators:

| ID | Check |
|---|---|
| V-010 | PR title is present and not a placeholder |
| V-020 / V-021 | PR body contains the governance evidence package, without template placeholders |
| V-030 | Required template sections present (full set for code changes, minimal set for docs-only changes, per the proportionality rule) |
| V-040 | Acceptance-criteria matrix present with only `PASS` / `FAIL` / `BLOCKED` / `NOT APPLICABLE`; no `FAIL` or `BLOCKED` criterion on a merge-ready PR |
| V-050 | Exactly one implementer readiness declaration is checked; a merge-ready PR must declare `READY FOR REVIEW` |
| V-060 | Verification evidence recorded (code changes) |
| V-070 | Every referenced ADPR/ADR resolves to an existing record at the exact base commit (Stage 1's resolution result; missing references are missing repository evidence) |
| V-080 | PR is not in a conflicting merge state |
| V-090 | Gate self-modification is flagged (informational) |
| V-100 | Draft state is recorded (informational; GitHub blocks draft merges) |

### Stage 3 — Decision Engine

| Condition | Outcome |
|---|---|
| Required repository evidence could not be retrieved (Stage 1) | `REVIEW_FAILED` |
| Pair is stale (head or base SHA changed since the review pair was resolved) — checked once, immediately before publishing | `REVIEW_FAILED` |
| Any blocking deterministic finding | `CHANGES_REQUIRED` |
| Deterministic clean, pair fresh | `APPROVED` |

## Supported Outcomes and Check Mapping

The internal system distinguishes exactly three outcomes:

| Outcome | Meaning | Published check |
|---|---|---|
| `APPROVED` | Deterministic validators pass, required repository evidence resolved, pair still matches | `success` — merge may proceed if all other required checks pass |
| `CHANGES_REQUIRED` | PR violates deterministic governance rules | `failure` — merge blocked |
| `REVIEW_FAILED` | The review could not produce a trustworthy verdict | `failure` — merge blocked |

`REVIEW_FAILED` includes:

- stale source or target pair;
- authoritative governance context could not be resolved at the exact base commit (a mandatory canonical document is missing);
- missing required repository evidence (e.g. the changed-files list could not be retrieved);
- internal validator exception;
- inability to inspect required files.

`REVIEW_FAILED` is never converted into approval, and the gate is never skipped
silently.

## Exact Review Pair

Every run resolves and records:

- repository;
- pull request number;
- source branch and source HEAD SHA;
- target branch and target BASE SHA;
- workflow run ID;
- review timestamp.

Every approval applies only to that exact pair. The pair is re-verified
immediately before publishing: if either SHA changed at any point while
resolving evidence, context, or deterministic findings, the run publishes
`REVIEW_FAILED` (stale pair) instead of an approval that would apply to a
pair that no longer exists. The status is published to the **current** PR
head SHA (as observed at that re-check), so the live head remains
fail-closed until a fresh review completes.

## Triggers

The gate runs automatically on pull request events affecting merge readiness:

- `opened`
- `reopened`
- `synchronize`
- `ready_for_review`
- `converted_to_draft`
- `edited`
- `review_requested`
- `review_request_removed`

plus `workflow_dispatch` for manual re-runs.

### Target-branch advancement

GitHub Actions does not fire `pull_request` events when a protected target
branch advances while PRs are open. The chosen mechanism is:

1. **Push to protected target branches** — a reconciliation workflow
   (`.github/workflows/hunter-governance-reconcile.yml`) runs on every push to
   a protected branch and re-evaluates every open PR targeting it, and
2. **Scheduled reconciliation** — the same workflow runs on a 30-minute
   schedule as a safety net for any missed event.

Each reconciliation re-evaluates the exact current source HEAD and target BASE
of every open PR. A target-branch advance therefore invalidates prior approvals
(the old pair is stale) until the PR is updated, which re-triggers the primary
workflow.

## Security Hardening

- The gate uses the plain `pull_request` event; `pull_request_target` is not
  used.
- The gate engine is checked out from the **default branch only**. Code from
  the PR under review is never checked out or executed; the engine reads PR
  metadata and changed files exclusively through the GitHub API.
- **Bootstrap exception**: until the gate engine exists on the default branch
  (i.e., during the installation PR itself, before merge), the workflow
  materializes the engine from the PR head so the required status check can
  publish during installation validation. The fallback is guarded by the
  complete engine import closure on the default branch (the
  `hunter_governance_review` package, `hunter_governance_preflight.py`,
  `hunter_governance_revision.py`, and the shared resilient transport
  boundary) and never activates after the installation PR merges. PR-head
  execution is additionally restricted to the one-time installation pull
  request **#277** on the `pull_request` event; any other PR, event, or manual
  dispatch fails closed with an error instead of running PR-controlled code.
  It emits an explicit `::warning::` when used.
- Minimal workflow permissions: `contents: read`, `pull-requests: read`,
  `statuses: write`. The gate never writes PR comments, and requires no
  repository secret of any kind.
- PR titles and bodies are untrusted data as far as the deterministic
  validators are concerned, but are only ever pattern-matched (regex/string
  checks) — never sent to any external service.
- A PR that modifies the gate's own files is flagged (V-090, informational);
  GitHub branch protection still requires the check to pass.

## Configuration

### Repository variables (optional)

| Variable | Default | Purpose |
|---|---|---|
| `HUNTER_GOVERNANCE_PROTECTED_BRANCHES` | `main` | Comma-separated protected branches the gate guards |

No repository secret is required or read by this gate. There is no provider
API key, base URL, or model configuration of any kind — the gate is
provider-independent by construction, not merely by default configuration.

### Branch protection

**Reconciliation (`.github/workflows/hunter-governance-reconcile.yml`) is
recovery, not the primary guarantee.** It re-evaluates open PRs on a push to
a protected branch and on a 30-minute schedule, closing the specific gap that
GitHub Actions does not fire `pull_request` events when the target branch
advances. It cannot, by itself, prevent a merge between the moment this gate
re-verifies the review pair and the moment a human clicks Merge — only
GitHub's own branch protection, enforced natively at merge time, closes that
residual window.

**Verified historical state (checked via `gh api repos/fafa33/Project-Hunter/branches/main/protection` and
`gh api repos/fafa33/Project-Hunter/rulesets`):** as of PR #200's own
installation, `main` had **no branch protection rule and no repository
ruleset at all** — the API returned `404 Branch not protected` and an empty
ruleset list. Nothing prevented a merge into `main` regardless of any status
check's result. Re-verify this current state before relying on it; it is not
re-checked automatically by this document.

**Manual GitHub settings the repository owner must enable** (Settings →
Branches → Add branch protection rule for `main`, or the equivalent Rulesets
UI) — this gate cannot enable these itself; doing so is a repository-setting
change with blast radius across every future PR, which requires an explicit,
separate authorization from a code change:

1. **Require status checks to pass before merging**, with **Hunter
   Governance Review** and **Quality Gates** (the CI workflow's job name)
   added to the required list. Until this is enabled, a failing or missing
   check does not block merge at all.
2. **Require branches to be up to date before merging** (the "strict" flag
   on required status checks). This is the setting that actually closes the
   base-advanced-during-review race natively — it forces GitHub to
   re-evaluate mergeability against the *current* base immediately at merge
   time, which no CI-side polling or reconciliation can fully replicate.
3. Optionally, a **merge queue** (repository Rulesets → "Require merge
   queue") provides the same guarantee under concurrent merge load without
   relying on humans to always rebase before merging.

**Mandatory enforcement is inactive until these settings are configured.**
This repository's code (the gate, the reconcile workflow, the pre-publish
freshness re-check) is complete and correct on its own terms, but none of it
can force a merge decision while `main` has no branch protection rule: a
`failure` status from this gate does not block the GitHub Merge button at
all unless branch protection requires it. Verify with:

```
gh api repos/<owner>/<repo>/branches/main/protection
gh api repos/<owner>/<repo>/rulesets
```

A `404 Branch not protected` response or an empty ruleset list means
enforcement is still inactive. This gate passing is necessary but not
sufficient for merge safety while `main` remains unprotected.

## Behavior Notes

- The workflow step succeeding means only "the gate ran and reported". The
  merge authority is the published status check: `failure` for
  `CHANGES_REQUIRED` and `REVIEW_FAILED`, `success` only for `APPROVED`.
- A PR targeting a non-protected branch is not gated (the check is not
  required for such branches) and the gate reports this explicitly; it does
  not silently fail.
- Draft PRs are evaluated like any other PR. GitHub additionally blocks their
  merge until they leave draft status.
- Because there is no external network dependency besides GitHub itself, a
  review of an ordinary-sized PR completes in a small number of seconds —
  the workflow's `timeout-minutes` reflects this.

## Troubleshooting

- **`REVIEW_FAILED` with `authoritative governance context could not be
  resolved`**: a document in `docs/CANONICAL_ARCHITECTURE_MAP.md`'s hierarchy
  could not be fetched at the exact base commit. Check the run's step summary
  under "Authoritative context coverage manifest" for which document and ref.
- **`REVIEW_FAILED` with `missing required repository evidence`**: the
  GitHub API call to list the PR's changed files failed. Check the run log
  for the underlying `gh` error (network, auth, or rate limit against the
  GitHub API itself).
- **`REVIEW_FAILED` with `stale source or target pair`**: the PR's head or
  base SHA changed while this run was evaluating it. This is expected under
  rapid pushes; the next triggered run evaluates the new pair.
- **`CHANGES_REQUIRED` with no visible reason**: check the workflow run log
  for `[Finding]` lines, or the run's step summary (`GITHUB_STEP_SUMMARY`)
  under "Deterministic findings" — every blocking validator ID and detail is
  listed there.
- **The GitHub Actions job shows green but the PR is still blocked (or vice
  versa)**: the Actions job's own pass/fail is NOT the merge-gate signal —
  the job "succeeds" (exits 0) whenever the gate finished publishing a
  status at all, which happens for `APPROVED`, `CHANGES_REQUIRED`, and
  `REVIEW_FAILED` alike. Always check the published `Hunter Governance
  Review` **commit status** itself (the PR's checks list, or
  `gh api repos/<owner>/<repo>/commits/<sha>/statuses`), never the job's own
  green/red icon.

## Files

| File | Purpose |
|---|---|
| `scripts/hunter_governance_review/__main__.py` | CLI orchestrator (resolve pair -> context -> deterministic -> decide -> re-verify pair -> publish) |
| `scripts/hunter_governance_review/contracts.py` | Outcomes, review pair, findings, context manifest, check mapping |
| `scripts/hunter_governance_review/context.py` | Authoritative Context Resolver (exact-base-SHA governance docs/ADRs existence and provenance) |
| `scripts/hunter_governance_review/deterministic.py` | Deterministic Governance Engine |
| `scripts/hunter_governance_review/decision.py` | Decision Engine |
| `scripts/hunter_governance_review/github_api.py` | `gh` CLI / GitHub API interaction (PR metadata, changed files, document/ADR content, commit status) |
| `.github/workflows/hunter-governance-review.yml` | PR-event workflow |
| `.github/workflows/hunter-governance-reconcile.yml` | Target-advancement + scheduled reconciliation |
| `tests/test_hunter_governance_review.py` | Unit tests for the deterministic engine, decision engine, and orchestration |
| `tests/test_hunter_governance_context.py` | Unit tests for the Authoritative Context Resolver |
| `tests/test_hunter_governance_no_llm_dependency.py` | Architectural regression tests proving zero LLM/external-provider dependency, anywhere in the package or its workflows |

The engine is stdlib-only and runs with the Python interpreter already present
on GitHub-hosted runners; no dependency installation is required.

## Verification

The gate itself follows the repository quality gates (`ruff`, `black`,
`mypy`, `pytest`) and ships unit tests covering every outcome path, including
fail-closed behavior for missing repository evidence, stale pairs, and
internal validator exceptions — plus a dedicated architectural test suite
(`test_hunter_governance_no_llm_dependency.py`) that inspects the actual
source of every module and workflow file in the gate's package to prove no
LLM/external-provider marker, raw HTTP client import, or provider-shaped
module exists anywhere in it.
