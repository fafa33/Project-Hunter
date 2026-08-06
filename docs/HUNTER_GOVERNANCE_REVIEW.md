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

The gate implements six explicit stages and publishes exactly one required
GitHub status check.

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
Deterministic diff chunking        (scripts/hunter_governance_review/chunking.py)
        |
        v
LLM Architecture Audit, one call per chunk, extracting structured
architectural evidence in addition to a prose summary
                                    (scripts/hunter_governance_review/llm_audit.py)
        |
        v
Aggregation across every chunk, then cross-chunk consistency synthesis
reasoning over the structured evidence
                                    (scripts/hunter_governance_review/aggregate.py)
        |
        v
Authoritative-Document Review: every mandatory document reviewed in full,
in deterministic sections, against the structured evidence
                                    (scripts/hunter_governance_review/llm_audit.py,
                                     scripts/hunter_governance_review/aggregate.py)
        |
        v
Decision Engine                    (scripts/hunter_governance_review/decision.py)
        |
        v
Re-verify review pair AGAIN, immediately before publishing
        |
        v
GitHub required status check: Hunter Governance Review
```

The LLM is consulted only when context resolution succeeded, every
deterministic validator passed, **and** the review pair was still fresh
immediately before the audit started. The LLM never bypasses deterministic
failures. `APPROVED` requires every diff chunk AND every authoritative
document's every section to have been reviewed successfully -- any missing,
failed, or unreviewed chunk or section fails the whole review closed,
regardless of what any individual chunk or section concluded.

### Stage 1 — Authoritative Context Resolution

Resolves the canonical governance documents, referenced ADRs/ADPRs, and any
other document literally referenced in the PR body at the **exact base
commit**, via the GitHub Contents API -- never from whatever happens to be
checked out locally, which is a single shallow checkout made once per
workflow run and is not guaranteed to be pinned to the exact recorded base
SHA for every validator that might read it.

The document set is not hardcoded: the canonical hierarchy is parsed
directly out of `docs/CANONICAL_ARCHITECTURE_MAP.md`'s own numbered
"Canonical Document Authority Hierarchy" list (itself fetched at the same
exact base commit), so a governance change to that hierarchy is picked up
automatically. Every document from that hierarchy is **mandatory**: if any
cannot be retrieved at the exact base SHA, resolution raises
`ContextResolutionError` and the run publishes `REVIEW_FAILED` -- context
resolution never fails open. ADR/ADPR numbers and other `docs/*.md` paths
mentioned only in the PR body are optional context; missing ones are
recorded in the manifest but do not by themselves fail the review (the
deterministic V-070 validator, below, already blocks a PR that references a
non-existent ADR/ADPR, now using this same exact-SHA resolution instead of a
local-filesystem glob).

Every document consulted (or attempted) is recorded in a deterministic
**coverage manifest**: exact path, exact ref, resolved/missing status,
SHA-256 content hash, byte length, and how many characters were included in
the audit prompt. This manifest is printed to the run log, written to
`GITHUB_STEP_SUMMARY`, and is the auditable evidence for what context an
approval was actually based on -- replacing a single hardcoded, hand-written
governance paraphrase that could silently drift from the real documents.

### Stage 2 — Deterministic Governance Engine

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
| V-070 | Every referenced ADPR/ADR resolves to an existing record at the exact base commit (Stage 1's resolution result; missing references are missing repository evidence) |
| V-080 | PR is not in a conflicting merge state |
| V-090 | Gate self-modification is flagged (informational, hostile audit must scrutinize it) |
| V-100 | Draft state is recorded (informational; GitHub blocks draft merges) |

### Stage 3 — Deterministic Diff Chunking and the LLM Architecture Audit

**Complete diff coverage, not truncation.** The full diff is never sent in
one request and never truncated to fit one. Instead, `chunking.py`
deterministically splits the diff into ordered chunks -- on file boundaries
first, and on line boundaries within a single oversized file's own diff if
needed -- such that every character of the original diff appears in exactly
one chunk. Each chunk is reviewed by an independent hostile-audit call; a
chunk's own prompt explicitly tells the model it is seeing only part of the
diff and that a clean chunk should still be marked `APPROVED` for itself,
since full coverage is enforced by aggregating every chunk afterward
(`aggregate.py`), not by any single chunk's verdict.

Each chunk's OpenAI-compatible chat-completions call (Groq by default,
matching the repository's existing CI provider convention) embeds: the exact
review pair, PR metadata, that one chunk's diff text, the deterministic
findings, and Stage 1's resolved governance-context excerpt. PR content is
treated as untrusted data.

The full assembled prompt for **one chunk** (system + user messages) is
bounded to a fixed character budget (`PROMPT_CHAR_BUDGET` in `llm_audit.py`,
29,750 characters, using a conservative 3.5 chars/token estimate against an
8,500-token target) so it stays within the pinned default model's actual
provider rate limit (`PROVIDER_TPM_LIMIT`, 12,000 -- Groq's on-demand tier
for `llama-3.3-70b-versatile`). A chunk's own diff text absorbs whatever
budget remains after the PR body, changed-file list, findings, and context
excerpt (`context.DEFAULT_TOTAL_CHAR_BUDGET`, 12,500 characters -- sized
against the real canonical hierarchy: as of this writing 12 mandatory
documents, whose full uncut excerpts measure 11,758 characters together;
see that constant's comment for the exact `gh api` measurement) are
rendered; `estimate_chunk_diff_budget` sizes chunks *before* they are built
so this should never bind in practice, but if a chunk still does not fit,
`build_chunk_audit_prompt` raises rather than silently truncating -- that
chunk is recorded as failed, which fails the whole review's coverage closed
(see Stage 4). This budget was derived directly from real failures on this
gate's own live installation run: first an HTTP 413 (PR #200, workflow run
31056865509 -- "Request too large ... tokens per minute (TPM): Limit 12000,
Requested 27258" -- because the original pre-chunking bounds had no
relationship to that limit), and later a context budget too small for the
real canonical hierarchy to fit without every mandatory document being
squeezed to zero characters and failing every run closed (workflow run
31094837347).

**Rate limiting across sequential chunk calls is expected, not exceptional,
and is retried, not treated as a hard failure.** `PROVIDER_TPM_LIMIT` bounds
a single request, but it is actually a *rate* -- tokens per rolling 60-second
window -- and a review with many chunks makes many sequential requests that
all draw from that same window. Re-verifying this very fix live against
PR #200's own (much larger, cumulative) diff produced 116 chunks, and 113 of
them failed with HTTP 429 ("Rate limit reached ... Please try again in
21.98s") after only the first 2-3 calls exhausted the window (workflow run
31065137201) -- even though every individual request stayed safely under the
per-request limit above. `run_llm_audit` now retries a 429 using the
provider's own suggested wait time (parsed from its error message, falling
back to a fixed conservative backoff if it cannot be parsed), bounded to
`MAX_RATE_LIMIT_RETRIES` (5) attempts, before giving up on that chunk -- which
correctly fails coverage closed (see Stage 4) rather than retrying forever.
Widening the per-chunk budget (above) and shrinking the context excerpt
directly reduce how often this triggers by reducing the total chunk count for
a given diff size (116 -> ~20 chunks on the same real PR #200 diff).

**A per-day (TPD) 429 is a different failure mode and is never retried.** A
further live re-verification of this exact fix (workflow run 31066459333)
hit Groq's *daily* token quota mid-run ("... on tokens per day (TPD): Limit
100000, Used 95514 ... Please try again in 44m34.944s") -- a wait no retry
budget sized for a 30-minute CI job can absorb, and one the prior
seconds-only duration regex would have mis-parsed as 34.944 seconds anyway
(silently dropping the "44m" prefix). `_parse_retry_after_seconds` now
parses Groq's `<h>h<m>m<s>s` form correctly, and `_is_daily_rate_limit`
detects the `"tokens per day"` marker and fails that chunk immediately --
one wasted request, not `MAX_RATE_LIMIT_RETRIES` of them -- surfacing the
provider's own message (which already names the limit type and suggested
wait) directly to the operator, who needs to wait for the daily quota to
reset or raise it, not a code fix.

The completion is separately capped with an **adaptive** `max_tokens`
(`MIN_COMPLETION_TOKENS` 512 .. `MAX_COMPLETION_TOKENS_CAP` 2,048, scaled to
how much of the TPM budget the prompt did not use, with a safety factor)
since the provider's per-minute token limit covers prompt and completion
together -- a flat cap sized for a small response silently truncates a
larger, entirely legitimate findings list into invalid JSON. The gate also
requests `response_format: {"type": "json_object"}` (preferring the
provider's structured-output control where supported) and explicitly checks
the response's `finish_reason`: a value of `"length"` means the completion
was cut off, and the gate fails that chunk closed rather than attempt to
parse a possibly-invalid truncated response.

The model must return strict JSON, validated field-by-field:

```json
{"verdict": "APPROVED" | "CHANGES_REQUIRED", "summary": "...", "findings": [{"id": "...", "severity": "blocking" | "non-blocking", "location": "...", "description": "...", "decision_impact": "..."}], "rationale": "...", "architectural_evidence": {"entities_introduced": [...], "ownership_declarations": [...], "authority_changes": [...], "dependency_changes": [...], "persistence_contracts": [...], "replay_contracts": [...], "canonical_interfaces": [...], "affected_adrs_or_contracts": [...], "exported_apis": [...], "cross_file_references": [...]}}
```

`validate_audit_payload` in `llm_audit.py` requires every field, the correct
type for each, `verdict` and each finding's `severity` to be one of the
allowed values, every finding id to be unique within the response, and
rejects internally contradictory output outright: `APPROVED` with any
`blocking` finding, or `CHANGES_REQUIRED` with an empty findings list. Any
violation raises `LLMAuditError` -- there is no lenient fallback parse path.
`APPROVED` for a chunk is valid only when the reviewer can honestly state
the canonical passing outcome for that chunk: **"No blocking findings were
identified in this chunk."**

`architectural_evidence` is mandatory on every response (chunk review,
document-section review, and synthesis alike) -- a response that omits it
entirely raises `LLMAuditError`, and each of its ten fixed categories
(`entities_introduced`, `ownership_declarations`, `authority_changes`,
`dependency_changes`, `persistence_contracts`, `replay_contracts`,
`canonical_interfaces`, `affected_adrs_or_contracts`, `exported_apis`,
`cross_file_references`) must be a list of non-empty strings when present,
an empty list when nothing in that category applies, and no other key is
tolerated. This field exists so a downstream reasoning step (the cross-chunk
synthesis in Stage 4, and the authoritative-document review in Stage 5) can
detect a contradiction spanning multiple chunks even when neither chunk's
one-sentence prose `summary` happened to mention the relevant fact -- a
summary is written about its own chunk in isolation, while a cross-chunk
contradiction is, by definition, only visible when the structured evidence
from separate chunks is compared side by side.

### Stage 4 — Aggregation

`aggregate.py` combines every chunk's outcome into one result, deterministically
-- never by trusting any single chunk's self-reported label for the whole PR,
since no chunk ever saw the whole diff. Findings from every chunk are unioned
(with finding ids re-namespaced per chunk, e.g. `C2-F-001`, to keep them
globally unique) and the aggregate verdict is `CHANGES_REQUIRED` if **any**
chunk had a blocking finding, otherwise `APPROVED`.

**Any failed, missing, or unreviewed chunk makes the aggregate result `None`**
(not `CHANGES_REQUIRED`, not `APPROVED` -- no meaningful verdict), which the
Decision Engine maps to `REVIEW_FAILED`. The same applies if any file GitHub's
API lists as changed never appeared in any diff chunk actually reviewed (a
coverage cross-check against the authoritative changed-files list, independent
of the diff-splitting logic itself). A PR whose diff was empty (e.g. a pure
metadata change) trivially produces zero chunks and an `APPROVED` audit
result, deferring entirely to the deterministic findings for anything
substantive.

The resulting **coverage manifest** (total files, total chunks, chunks
reviewed/failed, chunk error messages, files covered, diff bytes covered) is
printed to the run log and written to `GITHUB_STEP_SUMMARY` alongside the
context manifest from Stage 1.

Once every chunk has succeeded, one further, final call performs **cross-chunk
consistency synthesis** (`llm_audit.run_synthesis_review`): it receives each
chunk's already-extracted **structured architectural evidence** (see Stage 3)
alongside its prose summary, findings, and the file-to-chunk coverage map --
never the raw diff again -- and checks whether the evidence is mutually
consistent (e.g. one chunk's `ownership_declarations` conflicting with
another's `authority_changes`, or a `dependency_change` in one chunk breaking
a `canonical_interface`/`exported_api` another chunk relies on). Reasoning
over the structured evidence, not just the prose summaries, is what makes it
possible to catch a contradiction spanning multiple chunks even when neither
chunk's own one-sentence summary happened to mention the relevant fact --
two individually "clean-sounding" chunks can still hide a real contradiction
if their structured evidence disagrees. This is deliberately the one minimal
addition needed to catch that class of contradiction, not a general
reasoning engine: it is a single bounded call reusing the exact same strict
schema, budget, and retry machinery as a per-chunk call
(`llm_audit._call_chat_completion`), so it cannot reintroduce the
token-budget or rate-limit fragility a larger, unbounded synthesis mechanism
would. A `CHANGES_REQUIRED` synthesis verdict means a contradiction was
found; its findings are folded into the aggregate result with a `SYN-` id
prefix. A synthesis failure (network error, malformed output, or an input
too large to fit even this bounded call) is folded in exactly like a failed
chunk -- coverage that cannot be verified consistent is not complete, so the
whole review fails closed (`aggregate.apply_synthesis`).

### Stage 5 — Authoritative-Document Review

**A resolved document is not the same as a reviewed document.** Stage 1
resolves every mandatory document's full text at the exact base commit, but
only exposes a small, bounded excerpt (`ContextManifest.brief`) to the
per-chunk audit prompt in Stage 3 -- reviewing only that excerpt would let a
rule stated later in a long document silently escape review entirely. Stage 5
closes that gap: once diff-chunk coverage and cross-chunk synthesis have both
succeeded, every mandatory document's **full text**
(`ContextManifest.mandatory_document_texts`) is deterministically,
losslessly split into ordered sections (`chunking.split_document_into_chunks`
-- the same file-then-line-boundary splitting algorithm Stage 3 uses for the
diff, generalized to plain text since a governance document has no
`diff --git` headers to split on), and every section is reviewed by an
independent hostile-audit call (`llm_audit.run_document_review`) against the
pull request's structured architectural evidence from Stage 3/4 -- never the
raw diff again. A section's prompt explicitly tells the model it is judging
only whether *this section's* rules are violated by the supplied evidence; a
section whose rules the evidence never touches is `APPROVED` for that
section, since full-document coverage is enforced by aggregating every
section afterward (`aggregate.aggregate_document_chunk_outcomes`), exactly
mirroring Stage 4's diff-chunk aggregation.

**Any failed, missing, or unreviewed section makes the whole
authoritative-document review result `None`** (not `CHANGES_REQUIRED`, not
`APPROVED`) -- there is no partial credit for "most sections were reviewed."
`aggregate.apply_document_review` folds a `CHANGES_REQUIRED` document-section
verdict into the aggregate audit result with a `CTX-` id prefix, and folds an
incomplete document review in exactly like a failed diff chunk or a failed
synthesis call: the whole review fails closed. The resulting **document
review coverage manifest** (`DocumentReviewManifest`: total documents, total
sections, sections reviewed/failed, section error messages, and
`bytes_reviewed`/`bytes_total`) is printed to the run log and written to
`GITHUB_STEP_SUMMARY` alongside the diff coverage and context manifests.
`bytes_reviewed` counts only bytes that passed through an actual,
successful `run_document_review` call -- it is `0`, never a partial fraction
of `bytes_total`, the moment any section fails, so the manifest can never be
misread as "mostly reviewed" when coverage is genuinely incomplete.

Document review is skipped entirely (not attempted) when diff-chunk coverage
or cross-chunk synthesis already failed closed -- there is nothing
trustworthy yet to check any document against in that case.

### Stage 6 — Decision Engine

| Condition | Outcome |
|---|---|
| Authoritative context could not be resolved (Stage 1) | `REVIEW_FAILED` |
| Pair is stale (head or base SHA changed) -- checked once early as a cheap short-circuit, and again immediately before publishing (authoritative) | `REVIEW_FAILED` |
| Any blocking deterministic finding | `CHANGES_REQUIRED` |
| Diff coverage incomplete (any chunk failed/missing, or a changed file never appeared in any chunk) | `REVIEW_FAILED` |
| Cross-chunk consistency synthesis failed (network/parse error, or input too large) | `REVIEW_FAILED` |
| Authoritative-document review incomplete (any mandatory document's section failed/missing) | `REVIEW_FAILED` |
| Aggregated audit verdict `CHANGES_REQUIRED` (per-chunk finding, synthesis-detected contradiction, or document-review violation) | `CHANGES_REQUIRED` |
| Aggregated audit verdict `APPROVED`, synthesis found no contradiction, document review found no violation, deterministic clean, coverage complete, pair fresh | `APPROVED` |

When the aggregated verdict is `CHANGES_REQUIRED`, its summary, the full
unioned findings list, and rationale are surfaced -- not just a generic
sentence. `decide()` folds the summary into `Decision.reason` (visible in
the 140-character GitHub status description), and the workflow prints
`[AuditVerdict]`, `[AuditFinding]`, and `[AuditRationale]` lines to the run
log and writes the full findings list (id, severity, location, description,
decision impact) and rationale to `GITHUB_STEP_SUMMARY`.

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
- truncated completion (`finish_reason=length`) or any `finish_reason` other than `"stop"`;
- malformed, unrecognized-field, or internally contradictory model output (embedded JSON surrounded by
  extra prose is rejected, not extracted);
- unsupported response schema;
- stale source or target pair;
- incomplete diff coverage (any failed/missing chunk, a changed file absent from every chunk, or a
  retrieved diff exceeding the sanity bound -- never silently truncated and treated as complete);
- cross-chunk consistency synthesis failed, or found an unresolved contradiction (folds into
  `CHANGES_REQUIRED` instead when the synthesis call itself succeeded);
- authoritative-document review incomplete (any mandatory document's section failed or was not
  reviewed -- folds into `CHANGES_REQUIRED` instead when every section succeeded but a violation
  was found);
- authoritative governance context could not be resolved at the exact base commit, or a required
  (mandatory) document was retrieved but did not fit the context prompt budget;
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

Every approval applies only to that exact pair, and the pair is verified
**twice**:

1. **Early (optimization only)** -- right after deterministic validation,
   before the (possibly slow, multi-chunk) LLM audit starts, so a PR that is
   already stale doesn't pay for an audit whose result would be discarded
   anyway. This check does not by itself gate the outcome.
2. **Post-audit (authoritative)** -- immediately before publishing, *after*
   every chunk of the LLM audit has completed. This is the check that
   actually gates approval: an approval must apply to the exact pair the
   audit reviewed, and if either SHA changed at any point up to the moment
   of publishing -- including during the audit itself, which can take
   several chunk calls -- the run publishes `REVIEW_FAILED` (stale pair) and
   no approval is ever published for a pair that no longer exists.

The status is published to the **current** PR head SHA (as observed at the
post-audit check) so the live head remains fail-closed until a fresh review
completes. This two-tier design closes a real gap: the previous
implementation re-verified freshness only *before* starting the audit, so a
PR that advanced during the (up to 120-second, per-call) audit window could
have a stale pair's result published to a head that had already moved on.

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

### Repository secrets and provider failover

| Secret | Purpose |
|---|---|
| `HUNTER_LLM_API_KEY` | LLM API key, tried first |
| `GROQ_API_KEY` | LLM API key, tried second |
| `OPENAI_API_KEY` | LLM API key, tried third; uses the OpenAI endpoint/model |

**Every configured secret above is an independently-triable provider, tried in
this exact, fixed priority order for every single LLM call the gate makes**
(`llm_audit._resolve_providers`) — not a first-match-wins exclusive choice.
`_call_chat_completion` tries `HUNTER_LLM_API_KEY` first; if that provider
fails for any reason (network error, any HTTP error including a 429 quota
exhaustion, a truncated or malformed completion, or a schema/validation
failure), it tries `GROQ_API_KEY` next, then `OPENAI_API_KEY`. `REVIEW_FAILED`
occurs only once every configured provider has failed for that call — never
when at least one configured provider can still successfully produce a
trustworthy verdict. This applies independently to every call the gate makes
(every diff chunk, the cross-chunk synthesis call, and every authoritative-
document section), so a provider that fails partway through a review (e.g.
its daily quota exhausts mid-run) does not fail the calls that come after it.

When no secret is configured at all, the gate publishes `REVIEW_FAILED` — it
never skips. `HUNTER_LLM_BASE_URL`/`HUNTER_LLM_MODEL` (below) override
`HUNTER_LLM_API_KEY`'s and `GROQ_API_KEY`'s endpoint/model when set; see
`_resolve_providers`'s docstring for the exact (and 100%-backward-compatible)
scoping rule when `OPENAI_API_KEY` is configured alongside one of them.

**Why this exists.** A single Groq on-demand account's daily quota
(100,000 tokens) is not large enough for this repository's real review sizes:
this gate's own installation PR's diff alone chunks into roughly 50 requests
at up to ~8,500 tokens each (`llm_audit.PROMPT_TOKEN_BUDGET`) — several times
the ENTIRE daily quota in one review pass, before the cross-chunk synthesis
call or the authoritative-document review's own section-by-section calls are
even counted. This is not primarily an inefficiency in how many calls one
review makes: that per-chunk budget is already sized against the pinned
model's hard, provider-imposed per-request limit (`PROVIDER_TPM_LIMIT`,
12,000 tokens/minute for Groq's on-demand tier — see `llm_audit.py`'s
module-level comment for the HTTP 413 failure this budget exists to avoid),
so a materially larger chunk size is not available without either exceeding
that hard per-request ceiling or reducing what governance context each chunk
is judged against. Configuring a second, independent provider secret (a
distinct account/tier, or `OPENAI_API_KEY`) is what actually removes the
single-point-of-failure: that provider has its own, separate quota, so
exhausting one account's daily budget no longer blocks the whole gate.
**Configuring only one secret leaves the gate exactly as exposed to that
one provider's outages/quota as before** — failover only helps once a
second, genuinely independent secret is configured.

### Repository variables (optional)

| Variable | Default | Purpose |
|---|---|---|
| `HUNTER_LLM_MODEL` | `llama-3.3-70b-versatile` (Groq) / `gpt-4o-mini` (OpenAI) | Model for the audit |
| `HUNTER_LLM_BASE_URL` | provider default | OpenAI-compatible base URL |
| `HUNTER_GOVERNANCE_PROTECTED_BRANCHES` | `main` | Comma-separated protected branches the gate guards |

### Branch protection

**Reconciliation (`.github/workflows/hunter-governance-reconcile.yml`) is
recovery, not the primary guarantee.** It re-evaluates open PRs on a push to
a protected branch and on a 30-minute schedule, closing the specific gap that
GitHub Actions does not fire `pull_request` events when the target branch
advances. It cannot, by itself, prevent a merge between the moment this gate
re-verifies the review pair (see "Post-audit freshness verification" above)
and the moment a human clicks Merge -- only GitHub's own branch protection,
enforced natively at merge time, closes that residual window.

**Verified current state (checked via `gh api repos/fafa33/Project-Hunter/branches/main/protection` and
`gh api repos/fafa33/Project-Hunter/rulesets`):** as of this writing, `main` has
**no branch protection rule and no repository ruleset at all** -- the API
returns `404 Branch not protected` and an empty ruleset list. Nothing
currently prevents a merge into `main` regardless of any status check's
result.

**Manual GitHub settings Farhad must enable** (Settings → Branches → Add
branch protection rule for `main`, or the equivalent Rulesets UI) -- this
gate cannot enable these itself; doing so is a repository-setting change
with blast radius across every future PR, which requires an explicit,
separate authorization from a code change:

1. **Require status checks to pass before merging**, with **Hunter
   Governance Review** and **Quality Gates** (the CI workflow's job name)
   added to the required list. Until this is enabled, a failing or missing
   check does not block merge at all.
2. **Require branches to be up to date before merging** (the "strict" flag
   on required status checks). This is the setting that actually closes the
   base-advanced-during-review race natively -- it forces GitHub to
   re-evaluate mergeability against the *current* base immediately at merge
   time, which no CI-side polling or reconciliation can fully replicate.
3. Optionally, a **merge queue** (repository Rulesets → "Require merge
   queue") provides the same guarantee under concurrent merge load without
   relying on humans to always rebase before merging.

**Mandatory enforcement is inactive until these settings are configured.**
This repository's code (the gate, the reconcile workflow, the post-audit
freshness re-check) is complete and correct on its own terms, but none of it
can force a merge decision while `main` has no branch protection rule: a
`failure` status from this gate does not currently block the GitHub Merge
button at all. Do not claim, report, or rely on production enforcement
before Farhad has enabled the settings above **and** verified their effect
(e.g., by confirming a status check failure actually blocks the Merge button
in the GitHub UI) -- verify with:

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

## Troubleshooting

- **`REVIEW_FAILED` with `LLM API returned HTTP 413`**: a single chunk's
  assembled audit prompt exceeded the provider's tokens-per-minute limit.
  `llm_audit.py` bounds each chunk's prompt to `PROMPT_CHAR_BUDGET`; if this
  still occurs, the configured `HUNTER_LLM_MODEL`'s real provider TPM limit
  is lower than `PROVIDER_TPM_LIMIT`/`PROMPT_TOKEN_BUDGET` were derived
  against and both must be lowered to match it.
- **`REVIEW_FAILED` (or per-chunk failure) with `LLM API returned HTTP 429`
  after several retries**: the review has enough chunks that sequential
  calls are exhausting Groq's tokens-per-minute *rate* (not just a single
  request's size) faster than `MAX_RATE_LIMIT_RETRIES` retries can wait it
  out. Occasional 429s that resolve after 1-2 retries are expected and
  normal on a multi-chunk review; if it happens on most chunks, reduce the
  total chunk count (raise `PROMPT_TOKEN_BUDGET`/`PROMPT_CHAR_BUDGET`, or
  shrink `context.DEFAULT_TOTAL_CHAR_BUDGET`) rather than the retry count.
- **`REVIEW_FAILED` (or per-chunk failure) with `LLM API returned HTTP 429`
  naming `tokens per day (TPD)`**: the configured LLM API key's *daily*
  quota is exhausted (Groq's on-demand tier default: 100,000 tokens/day) --
  not something a retry can fix within a bounded CI job. This is not a bug;
  the message names the exact limit and its own suggested reset time. **If
  only one provider secret is configured, this is expected and will recur on
  every large review — configure a second, independent provider secret
  (`GROQ_API_KEY` and/or `OPENAI_API_KEY` on a genuinely different
  account/tier from whichever provider hit the quota) so the gate fails over
  instead of blocking; see "Repository secrets and provider failover"
  above.** If a second provider is already configured and this still occurs,
  every configured provider's quota is exhausted; wait for a reset or
  configure a higher-tier key.
- **`REVIEW_FAILED` with `all N configured provider(s) failed`**: every
  provider secret that IS configured failed for that specific call (see the
  `|`-separated per-provider reasons in the message). This means provider
  failover itself worked as designed -- it tried every configured provider,
  in order -- but none of them could produce a trustworthy verdict. Check
  each named provider's individual failure reason in the message; the fix
  depends on which providers failed and why.
- **`REVIEW_FAILED` with `missing API secret`**: none of `HUNTER_LLM_API_KEY`,
  `GROQ_API_KEY`, or `OPENAI_API_KEY` is configured as a repository secret.
  This is the intended fail-closed bootstrap behavior, not a bug — configure
  at least one of these secrets to let the LLM audit run (configure more
  than one, on independent accounts, for provider failover).
- **`REVIEW_FAILED` with `completion was truncated (finish_reason=length)`**:
  the adaptive completion budget (`MIN_COMPLETION_TOKENS` .. 
  `MAX_COMPLETION_TOKENS_CAP`) was still insufficient for that chunk's
  legitimate findings list. Consider a smaller `max_chunk_chars` (more,
  smaller chunks leave more completion headroom per call) before raising the
  cap, since raising the cap risks the same TPM overflow this budget exists
  to prevent.
- **`REVIEW_FAILED` with `X/Y diff chunk(s) failed or were not reviewed`**:
  check the run's step summary under "Diff coverage manifest" for the exact
  per-chunk error. This is fail-closed by design — coverage is either
  complete or the review does not produce an approval, never a partial one.
- **`REVIEW_FAILED` with `authoritative governance context could not be
  resolved`**: a document in `docs/CANONICAL_ARCHITECTURE_MAP.md`'s hierarchy
  could not be fetched at the exact base commit. Check the run's step summary
  under "Authoritative context coverage manifest" for which document and ref.
- **`CHANGES_REQUIRED` with no visible reason**: check the workflow run log
  for `[AuditVerdict]`, `[AuditFinding]`, and `[AuditRationale]` lines, or the
  run's step summary (`GITHUB_STEP_SUMMARY`) under "Hostile architecture
  audit" — both are populated with the aggregated findings across every
  chunk, not just the bare outcome.

## Files

| File | Purpose |
|---|---|
| `scripts/hunter_governance_review/__main__.py` | CLI orchestrator (resolve pair -> context -> deterministic -> chunk -> audit -> aggregate -> synthesize -> document review -> decide -> re-verify pair -> publish) |
| `scripts/hunter_governance_review/contracts.py` | Outcomes, review pair, findings, coverage/context/document-review manifests, check mapping |
| `scripts/hunter_governance_review/context.py` | Authoritative Context Resolver (exact-base-SHA governance docs/ADRs; resolves both the bounded excerpt and each mandatory document's full text) |
| `scripts/hunter_governance_review/deterministic.py` | Deterministic Governance Engine |
| `scripts/hunter_governance_review/chunking.py` | Deterministic, lossless diff chunking |
| `scripts/hunter_governance_review/llm_audit.py` | LLM Architecture Audit (per diff chunk, cross-chunk synthesis, and per-document-section review), structured architectural evidence schema, deterministic multi-provider failover |
| `scripts/hunter_governance_review/aggregate.py` | Cross-chunk aggregation, synthesis folding, document-review aggregation and coverage manifest |
| `scripts/hunter_governance_review/decision.py` | Decision Engine |
| `scripts/hunter_governance_review/github_api.py` | `gh` CLI / GitHub API interaction |
| `.github/workflows/hunter-governance-review.yml` | PR-event workflow |
| `.github/workflows/hunter-governance-reconcile.yml` | Target-advancement + scheduled reconciliation |
| `tests/test_hunter_governance_review.py` | Unit tests for all stages |

The engine is stdlib-only and runs with the Python interpreter already present
on GitHub-hosted runners; no dependency installation is required.

## Verification

The gate itself follows the repository quality gates (`ruff`, `black`,
`mypy`, `pytest`) and ships unit tests covering every outcome path, including
fail-closed behavior for missing secrets, stale pairs, malformed model output,
and evidence failures.
