# Hunter Reviewer Orchestration Design

**Date:** 2026-09-15
**Authority:** Issue #461 stabilization work
**Status:** Owner-approved; implementation planning authorized. The goal,
constraints, state machine, and timing model below remain current. Individual
provider names in "Reviewer pool" are **superseded** by the shipped pool: the
canonical, machine-checked pool is
`docs/CODE_WRITE_POLICY.json` → `review_progression.review_authority.reviewer_pool`,
and that declaration governs wherever this document and the policy differ.

## Goal

Make pull-request review fast, fail-closed, exact-head-bound, and resilient to unavailable reviewers without turning normal waiting into a red failure or allowing a PR to stall for hours.

The reviewer path must cost the owner nothing beyond already-available services and local compute. A Mac-hosted reviewer may be preferred when healthy, but the PR must continue automatically when that Mac is offline or unresponsive.

## Non-negotiable constraints

- No merge or Ready transition without explicit owner approval.
- Exact-head review authority remains mandatory.
- Waiting for a reviewer is `pending`, never `failure`.
- Reviewer unavailability is a failover event, not a candidate defect.
- A real reviewer finding remains blocking until corrected and verified on a new exact HEAD.
- No reviewer may manufacture structured authority from unstructured prose.
- No candidate-ref workflow may attest to its own trust; orchestration authority runs from trusted default-branch code.
- Root-of-trust workflow/script/policy changes use the clone + pre-push path.
- No paid reviewer subscription is required by this design.

## Reviewer pool

The pool is ordered by availability and cost, not by brand prestige.

**Shipped pool (authoritative, as declared in `docs/CODE_WRITE_POLICY.json`):**

1. **`local-ollama`** — free first-pass reviewer on the Mac self-hosted runner, priority 1, enabled. It is **triage-only**: its required quality gate (`hunter-local-reviewer-v1`) has not passed, so it declares `authority_eligible: false` and cannot terminate the authority search. Its implementation/model may change without changing orchestration semantics.
2. **`hermes`** — Mac-hosted hostile-review support, priority 2, enabled but **triage-only** until its benchmark gate passes; it cannot terminate the authority search.
3. **`codex`** — hosted authority reviewer, priority 3, enabled, `authority_eligible: true`. It is the primary review authority after triage and is never skipped on the way to the last resort.
4. **`copilot`** — authenticated GitHub Copilot code review, priority 4, enabled, `authority_eligible: true`. A review-request trigger is not acknowledgement; only authenticated exact-head review evidence is authoritative.
5. **`gemini`** — server-side API reviewer, priority 5, enabled, `authority_eligible: true`. The trusted default-branch collector calls it with a repository secret; candidate code never receives that secret.
6. **`groq`** — server-side API reviewer, priority 6, enabled, `authority_eligible: true`, invoked on the same trusted terms as `gemini`. Authentication/permission failures are permanent configuration-health failures; transient provider outages remain distinct.
7. **`hunter-guard`** — deterministic non-reviewer last resort, admissible only after every authority-eligible reviewer has authenticated exhaustion evidence and the existing snapshot gates hold.

Authority therefore fails over `codex` → `copilot` → `gemini` → `groq`, one attempt each per exact HEAD with no retry, before `hunter-guard` can close the pool.

**Superseded design intent (historical, not shipped):** an earlier draft of this
section listed **Jules** as a free hosted fallback stage and named the
**OpenCode hostile-review guard** as the last resort. Neither is an active stage:
Jules was never admitted to the pool, and the last resort is the deterministic
`hunter-guard`. They are recorded here only so the history of this design is
readable, and they carry no authority.

CodeRabbit, Copilot Free, and retired consumer Gemini PR review are not part of the dependable pool because their usable automatic-review capacity is not reliably available without additional cost.

The pool is configuration-driven. A provider becomes eligible only after the repository has observed and authenticated its actual integration identity, trigger mechanism, exact-head capability, and result format. No provider is added merely because documentation says it exists.

## State machine

Review orchestration publishes one of these states for the exact candidate HEAD:

- `WAITING_FOR_REVIEWER` — trusted review request exists; orchestration is selecting/triggering a provider. GitHub status: `pending`.
- `REVIEW_IN_PROGRESS` — an authenticated provider acknowledged the exact-head invocation. GitHub status: `pending`.
- `FAILOVER_IN_PROGRESS` — current provider is unavailable/offline/unresponsive and the next provider is being attempted. GitHub status: `pending`.
- `REVIEW_CLEAR` — an authenticated exact-head reviewer emitted admissible structured clear authority and there are no unresolved review threads. GitHub status: `success`.
- `FINDINGS_OPEN` — an authenticated reviewer found blocking defects on the exact HEAD. GitHub status: `failure`/blocked.
- `POOL_EXHAUSTED` — every enabled provider has authenticated exhaustion evidence and no last-resort authority can be admitted. GitHub status: `pending`/action-required, never red solely because reviewers are unavailable; Merge Readiness remains blocked until review capacity returns.

A missing review response before the provider's bounded availability deadline can never be reported as `MISSING_REVIEW_AUTHORITY` failure while orchestration still has a valid provider/failover path. More generally, reviewer delay, queueing, quota exhaustion, provider outage, Mac unavailability, or total pool exhaustion are operational availability states, not candidate defects, and must not paint the review check red.

## Trigger and timing model

The orchestration cycle starts automatically after the final exact HEAD has a valid review request and trusted preflight prerequisites are satisfied. The owner never has to type `@codex review` or manually dispatch the collector for normal operation.

Provider timing has two distinct budgets:

- **Availability/acknowledgement budget:** short. It answers only "did the provider receive and begin this review?" Local reviewer target: 20–30 seconds after a fresh heartbeat. Hosted reviewer target: 60–90 seconds. One bounded retry is permitted only for authenticated transient infrastructure failure.
- **Review execution budget:** separate. Once a provider has acknowledged the exact-head job, the system waits for the substantive review without treating ordinary execution time as unavailability. Progress remains `pending`. A provider-specific hard ceiling still exists to prevent indefinite hangs, but this ceiling is not reused as the acknowledgement timeout.

A single long review timeout must not remain the first signal for provider availability. The shipped pool implements this split: each enabled reviewer declares `ack_timeout_seconds` (30) separately from `review_timeout_seconds` (300). A reviewer that has not acknowledged in the short availability window yields to the next eligible provider instead of parking the PR for 15–30 minutes.

When a new commit changes HEAD, all prior pending/clear review authority becomes stale. The trusted orchestrator cancels or supersedes the previous cycle, creates one new exact-head cycle, and triggers review without manual intervention.

## Local reviewer availability

The local reviewer runs as a persistent macOS service with automatic login-session startup and a repository-controlled adapter. SSH from iPhone is an operational console, not a correctness dependency.

The local reviewer publishes an authenticated heartbeat containing at minimum: service identity, timestamp, reviewer version/config digest, and readiness state. The orchestrator treats it as eligible only when the heartbeat is fresh within a configured 60–90 second freshness boundary.

Failure classes are distinct:

- `offline`: no fresh heartbeat; skip immediately to fallback.
- `unresponsive`: fresh heartbeat but no acknowledgement within the short local acknowledgement budget; fail over immediately.
- `review_failed`: reviewer began work but returned a substantive/tooling failure; record evidence and apply retry/failover policy.

`offline` and `unresponsive` are never candidate failures. If the Mac is shut down, disconnected, asleep, or the service is dead, the hosted reviewer path starts automatically and the PR continues.

## Finding and re-review loop

A valid blocking finding is not patched ad hoc. The finding is classified, tied to the governing defect family when applicable, and receives deterministic regression evidence before production code is changed. RED → minimal GREEN → broader verification remains mandatory.

After a correction creates a new HEAD, the previous review is stale by definition, but the next review cycle is launched automatically. Re-review should be delta-focused where the provider supports it: changed files, corrected findings, and any newly affected dependency surface are prioritized. Exact-head authority still covers the complete candidate and cannot be reduced to a delta-only permission claim.

Repeated identical findings after their deterministic regression exists are treated as evidence of a guard gap or reviewer false positive, not as a reason to repeat the same manual repair loop indefinitely.

## Trusted evidence and failover

Every provider attempt records exact HEAD, provider identity, trigger identity, timestamps, acknowledgement outcome, execution outcome, retry count, and configuration digest. Failover is admissible only from trusted evidence, never candidate prose.

A response, reaction, or authenticated acknowledgement proves provider availability but does not prove the review is complete. Completion requires a provider-specific parser to produce admissible structured review evidence bound to the exact HEAD and claims digest.

The collector/orchestrator itself must be automatically dispatched from a trusted default-branch path. `workflow_dispatch` may remain an implementation primitive, but a human manual dispatch cannot be the normal lifecycle trigger.

## Governance semantics

Governance consumes orchestration state rather than inferring "missing authority" from absence alone. While an exact-head review cycle is active, Governance publishes pending with an explicit reason such as `Waiting for exact-head reviewer acknowledgement`, `Exact-head review in progress`, or `Reviewer failover in progress`.

Governance publishes red/failure only for a confirmed defect or proven invalid condition that belongs to the candidate/evidence itself: an authenticated blocking finding or authenticated malformed/forged authority. Mere absence, lateness, quota exhaustion, provider outage, pool exhaustion, changed HEAD, stale evidence superseded by a new HEAD, or unavailable exhaustion proof remain non-red blocked/pending states. They still prevent Merge Readiness from succeeding, but they do not falsely claim that the candidate failed review.

Merge Readiness remains blocked until review state is `REVIEW_CLEAR`, all required checks are green, structured evidence is complete, unresolved thread count is zero, and the PR is explicitly moved through owner-approved Ready/merge progression.

## Verification and zero-recurrence requirements

The implementation must add deterministic tests before production changes for at least these cases:

- valid exact-head review request automatically starts the trusted reviewer collector/orchestrator;
- waiting for reviewer acknowledgement produces `pending`, not `failure`;
- an active substantive review remains `pending` until a terminal result exists;
- stale/missing local heartbeat skips local review without red status;
- fresh local heartbeat plus missed acknowledgement transitions to failover within the short budget;
- authenticated provider response prevents improper failover;
- authenticated exhaustion of one provider automatically attempts the next enabled provider;
- changed HEAD supersedes the previous cycle and cannot reuse stale authority or exhaustion;
- blocking findings remain blocking until corrected on a new HEAD with regression evidence;
- complete pool exhaustion fails closed;
- the last-resort deterministic guard (`hunter-guard`) remains unavailable unless its existing snapshot gates are satisfied.

The relevant recurrence-prevention evidence is registered under the existing Issue #461 / DFF-022 authority unless implementation uncovers a genuinely distinct defect family. Do not create one issue per symptom.

## Operational model

The Mac reviewer is controllable over the user's existing iPhone-to-Mac SSH path. Normal operation is autonomous; SSH is used only for inspection, restart, logs, and emergency manual invocation.

The service must not depend on a specific LAN. It must recover automatically after process restart/login and expose enough health data that Hunter can bypass it safely whenever the machine is not reachable.

No secret, API token, or reviewer credential is committed to the repository. Provider credentials stay in the appropriate local keychain/environment or GitHub secret store, and evidence records contain identities/digests rather than secret material.

## Success criteria

A normal final push should cause review orchestration to begin within seconds, not minutes. An unavailable local reviewer must add no meaningful delay beyond its health check. A hosted reviewer that fails to acknowledge must be abandoned on a short availability budget rather than the old 15-minute wait. Normal reviewer execution never paints Governance red merely for being slow.

The architecture is successful when a PR cannot silently sleep because the Mac is off, Codex is limited, or one provider is unavailable; Hunter automatically continues through the configured free-first reviewer pool while preserving exact-head, fail-closed merge authority.
