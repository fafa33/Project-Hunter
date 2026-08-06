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

## Architecture

The gate implements three explicit layers and publishes exactly one required
GitHub status check.

```text
Pull Request Event
        |
        v
Resolve exact source HEAD and target BASE
        |
        v
Deterministic Governance Engine   (scripts/hunter_governance_review/deterministic.py)
        |
        v
LLM Architecture Audit            (scripts/hunter_governance_review/llm_audit.py)
        |
        v
Decision Engine                   (scripts/hunter_governance_review/decision.py)
        |
        v
Re-verify review pair at decision time
        |
        v
GitHub required status check: Hunter Governance Review
```

The LLM is consulted only when every deterministic validator passes **and** the
review pair is still fresh. The LLM never bypasses deterministic failures.

### Layer 1 — Deterministic Governance Engine

Validates the pull request against the repository's canonical governance rules
(`docs/DEVELOPMENT_GOVERNANCE.md`, `docs/MERGE_READINESS_GATE.md`,
`docs/AI_REVIEW_PROTOCOL.md`, `docs/ARCHITECTURE_AUDIT_PROTOCOL.md`) without
invoking an LLM. The validators:

| ID | Check |
|---|---|
| V-010 | PR title is present and not a placeholder |
| V-020 / V-021 | PR body contains the governance evidence package, without template placeholders |
| V-030 | Required template sections present (full set for code changes, minimal set for docs-only changes, per the proportionality rule) |
| V-040 | Acceptance-criteria matrix present with only `PASS` / `FAIL` / `BLOCKED` / `NOT APPLICABLE`; no `FAIL` or `BLOCKED` criterion on a merge-ready PR |
| V-050 | Exactly one implementer readiness declaration is checked; a merge-ready PR must declare `READY FOR REVIEW` |
| V-060 | Verification evidence recorded (code changes) |
| V-070 | Every referenced ADPR/ADR resolves to an existing record (missing references are missing repository evidence) |
| V-080 | PR is not in a conflicting merge state |
| V-090 | Gate self-modification is flagged (informational, hostile audit must scrutinize it) |
| V-100 | Draft state is recorded (informational; GitHub blocks draft merges) |

### Layer 2 — LLM Architecture Audit

An OpenAI-compatible chat-completions call (Groq by default, matching the
repository's existing CI provider convention) performs the independent hostile
architecture audit required by `docs/AI_REVIEW_PROTOCOL.md`. The prompt embeds
a condensed governance brief derived from the canonical documents, the exact
review pair, PR metadata, the changed-file list, the bounded diff, and the
deterministic findings. PR content is treated as untrusted data.

The full assembled prompt (system + user messages) is bounded to a fixed
character budget (`PROMPT_CHAR_BUDGET` in `llm_audit.py`, currently 17,500
characters, using a conservative 3.5 chars/token estimate against a 5,000
token target) so that it stays within the pinned default model's actual
provider rate limit. The completion is separately capped with `max_tokens`
(`MAX_COMPLETION_TOKENS`, 1,000) since the provider's per-minute token limit
covers prompt and completion together. The diff — the most compressible part
of the prompt — absorbs whatever budget remains after the PR body (capped at
`PR_BODY_CHAR_LIMIT`), the changed-file list (capped at
`MAX_CHANGED_FILES_LISTED` entries), the deterministic findings, and the
governance brief are rendered, so the total is bounded regardless of how
large any individual section is. This budget was derived directly from a
real failure: the gate's own live installation run (PR #200, workflow run
31056865509) was rejected outright by Groq — HTTP 413, "Request too large
... tokens per minute (TPM): Limit 12000, Requested 27258" — because the
prior bounds (a 150,000-character diff cap, a 20,000-character PR body cap,
and a 300-file list) had no relationship to that limit.

The model must return strict JSON:

```json
{"verdict": "APPROVED" | "CHANGES_REQUIRED", "summary": "...", "findings": [...], "rationale": "..."}
```

`APPROVED` is valid only when the reviewer can honestly state the canonical
passing outcome: **"No blocking findings were identified."**

### Layer 3 — Decision Engine

| Condition | Outcome |
|---|---|
| Pair is stale (head or base SHA changed during review) | `REVIEW_FAILED` |
| Any blocking deterministic finding | `CHANGES_REQUIRED` |
| LLM audit error / no verdict / malformed / unsupported schema | `REVIEW_FAILED` |
| LLM verdict `CHANGES_REQUIRED` | `CHANGES_REQUIRED` |
| LLM verdict `APPROVED`, deterministic clean, pair fresh | `APPROVED` |

## Supported Outcomes and Check Mapping

The internal system distinguishes exactly three outcomes:

| Outcome | Meaning | Published check |
|---|---|---|
| `APPROVED` | Deterministic validators pass, LLM audit approved, pair still matches | `success` — merge may proceed if all other required checks pass |
| `CHANGES_REQUIRED` | PR violates governance or architecture | `failure` — merge blocked |
| `REVIEW_FAILED` | The review could not produce a trustworthy verdict | `failure` — merge blocked |

`REVIEW_FAILED` includes:

- missing API secret;
- network/API failure;
- timeout;
- malformed model output;
- unsupported response schema;
- stale source or target pair;
- missing required repository evidence;
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

Every approval applies only to that exact pair. Immediately before publishing,
the gate re-resolves the pull request and compares both SHAs. If either
changed, the run publishes `REVIEW_FAILED` (stale pair) and no approval is
ever published for a pair that no longer exists. The status is published to the
**current** PR head SHA so the live head remains fail-closed until a fresh
review completes.

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
  metadata, changed files, and the diff exclusively through the GitHub API.
- **Bootstrap exception**: until the gate engine exists on the default branch
  (i.e., during the installation PR itself, before merge), the workflow
  materializes the engine from the PR head so the required status check can
  publish during installation validation. The fallback is guarded by the
  existence of the engine on the default branch and never activates after the
  installation PR merges. It emits an explicit `::warning::` when used.
- Minimal workflow permissions: `contents: read`, `pull-requests: read`,
  `statuses: write`. The gate never writes PR comments.
- PR titles, bodies, and diffs are untrusted data. The audit prompt marks them
  as such and instructs the model to ignore any embedded instructions.
- A PR that modifies the gate's own files is flagged (V-090) and is subject to
  the hostile audit like any other change; GitHub branch protection still
  requires the check to pass.

## Configuration

### Repository secrets

| Secret | Purpose |
|---|---|
| `HUNTER_LLM_API_KEY` | LLM API key for the audit (preferred) |
| `GROQ_API_KEY` | Fallback (repository's existing provider convention) |
| `OPENAI_API_KEY` | Fallback; when only this is set, the OpenAI endpoint is used |

When no key is configured the gate publishes `REVIEW_FAILED` — it never skips.

### Repository variables (optional)

| Variable | Default | Purpose |
|---|---|---|
| `HUNTER_LLM_MODEL` | `llama-3.3-70b-versatile` (Groq) / `gpt-4o-mini` (OpenAI) | Model for the audit |
| `HUNTER_LLM_BASE_URL` | provider default | OpenAI-compatible base URL |
| `HUNTER_GOVERNANCE_PROTECTED_BRANCHES` | `main` | Comma-separated protected branches the gate guards |

### Branch protection

On each protected branch, add the required status check **Hunter Governance
Review** under *Require status checks to pass before merging*. Until the check
exists on the branch, GitHub will not enforce it — configure this after the
first run of the workflow.

## Behavior Notes

- The workflow step succeeding means only "the gate ran and reported". The
  merge authority is the published status check: `failure` for
  `CHANGES_REQUIRED` and `REVIEW_FAILED`, `success` only for `APPROVED`.
- A PR targeting a non-protected branch is not gated (the check is not
  required for such branches) and the gate reports this explicitly; it does
  not silently fail.
- Draft PRs are evaluated like any other PR. GitHub additionally blocks their
  merge until they leave draft status.

## Files

| File | Purpose |
|---|---|
| `scripts/hunter_governance_review/__main__.py` | CLI orchestrator (resolve pair -> deterministic -> LLM -> decision -> publish) |
| `scripts/hunter_governance_review/contracts.py` | Outcomes, review pair, findings, check mapping |
| `scripts/hunter_governance_review/deterministic.py` | Deterministic Governance Engine |
| `scripts/hunter_governance_review/llm_audit.py` | LLM Architecture Audit |
| `scripts/hunter_governance_review/decision.py` | Decision Engine |
| `scripts/hunter_governance_review/github_api.py` | `gh` CLI / GitHub API interaction |
| `.github/workflows/hunter-governance-review.yml` | PR-event workflow |
| `.github/workflows/hunter-governance-reconcile.yml` | Target-advancement + scheduled reconciliation |
| `tests/test_hunter_governance_review.py` | Unit tests for all layers |

The engine is stdlib-only and runs with the Python interpreter already present
on GitHub-hosted runners; no dependency installation is required.

## Verification

The gate itself follows the repository quality gates (`ruff`, `black`,
`mypy`, `pytest`) and ships unit tests covering every outcome path, including
fail-closed behavior for missing secrets, stale pairs, malformed model output,
and evidence failures.
