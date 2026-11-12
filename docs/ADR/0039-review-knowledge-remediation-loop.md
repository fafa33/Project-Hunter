# ADR 0039: Review → Knowledge → Prevention → Remediation Loop on the GitHub-Native Lifecycle

## Status

**Accepted** (2026-10-04, owner decisions RD-1…RD-6 on Issue #560). **Amended** 2026-11-09 by L9 (owner decisions
D1–D3, Issue #706). **Amended** 2026-11-12 by L11 (owner decisions, Issue #527): every confirmed-and-corrected
finding must carry a fast-path Knowledge Receipt before it can be treated as complete; the policy itself is this
amendment plus the mandatory lifecycle it encodes below. This is a binding amendment of:

- ADR 0037 D6 (publication becomes create-only **or fast-forward from the bound head** for a remediation);
- ADR 0037 D9/D1, which add a knowledge ledger and a remediation authorization next to the Issue ledger;
- the automatic-learning design (`docs/superpowers/specs/2026-09-23-automatic-knowledge-learning-design.md`).
  Its non-goals "automatic registry commit", "semantic classification" and "automatic new-family creation" are
  replaced by the mechanically proven forms below.

Everything else in ADR 0036, 0037 and 0038 is reaffirmed. Reconciliation and gap analysis:
[`docs/ISSUE_AGENT_REVIEW_KNOWLEDGE_LOOP.md`](../ISSUE_AGENT_REVIEW_KNOWLEDGE_LOOP.md).

## Context

The owner requires the reviewer → knowledge → prevention → remediation loop to run without a human relay before
#560 is usable. The existing canonical pieces each stop short of that:

- the learning workflow and ledger;
- `KnowledgeExtractionAuthority`;
- `DEFECT_REGISTRY` and `REVIEWER_FINDING_DISPOSITIONS`;
- DPM (`EngineeringContextAuthority`);
- the review orchestrator and merge readiness;
- the S2–S5 lifecycle.

Where they stop short:

- **Classification** needs an owner disposition reply.
- **Registry promotion** is a local human step.
- **DPM** sees only knowledge already on `main`.
- **Remediation** starts only from an owner label and publishes only new branches.
- **Thread resolution** is manual.

## Owner decisions

| Id | Decision |
|---|---|
| RD-1 | **Hybrid classification, deterministic first.** A model proposal is hostile data, accepted only behind a mechanical RED→GREEN proof. |
| RD-2 | **Anchored knowledge ledger** `refs/heads/hunter-state/v1/knowledge`, overlaid by DPM at compile time. |
| RD-3 | **Promotion inside the remediation commit**, written by trusted plumbing and never by the model. |
| RD-4/RD-5 | **Hunter-agent PRs only.** The machine authorization is signed by a control job, with the governing Issue's owner label as standing consent. Publication is an exact-lease **fast-forward from the bound head**. Attempt budgets apply. |
| RD-6 | A control job may **reply to and resolve the exact thread**, and only after exact-head proof. It never edits, dismisses, approves or merges. |

## Decision

### L1. Finding identity (fast path, idempotent)

A finding is one authenticated reviewer **thread** on an eligible PR:

```text
finding_id  = sha256(canon{repository_id, pull_request_number, thread_node_id, first_comment_database_id})
fingerprint = sha256(canon{path, normalized_claim})   # normalized_claim: first sentence, markup and numbers removed, lowercased
```

Ingestion reads review threads with the existing authenticated collector logic, for exact `head`/`base`, using
the reviewer pool from `pre_ready.authority_pool_reviewers`. It appends one `finding_ingested` record per
`finding_id`. The record carries:

- provenance: PR, reviewed head, provider, reviewer login, thread id, comment id, path, line;
- `fingerprint` and `claim` (the normalized first sentence, at most 280 characters, already public on the PR).

The store is insert-only and keyed by `finding_id`, so duplicate delivery and restarts are idempotent. A
malformed thread, an unauthenticated reviewer, or a head or base mismatch writes nothing and is reported.

### L2. Knowledge ledger (RD-2)

`refs/heads/hunter-state/v1/knowledge` is one chain: one `record.json` per commit, K_STATE-signed under the
domain `hunter-knowledge-ledger-v1`. It is protected by the same anchor (ADR 0037 D2a), uses the same lease CAS
and the same run provenance (ADR 0037 D2 check 5). Record kinds are insert-only, and each kind is unique per
key:

| Kind | Key | Meaning |
|---|---|---|
| `finding_ingested` | `finding_id` | L1 |
| `finding_classified` | `finding_id` | `matched(family_id)`, `candidate-new-family(candidate_id)`, `false-positive-claimed`, or `ambiguous`; `basis` is `deterministic` or `proven` |
| `family_candidate` | `candidate_id` | the smallest truthful new family: `invariant` (from a proven proposal), applicability paths, `source_finding_ids` |
| `remediation_requested` | `(finding_id, attempt)` | bounded remediation authorization issued (L4) |
| `finding_proven` | `finding_id` | RED→GREEN evidence at the remediated head (L5) |
| `thread_resolved` | `finding_id` | reply id and exact head (L7) |
| `recurrence` | `(family_id, finding_id)` | a proven finding of an already-promoted family |
| `verified_e2e_outcome` | `(issue_number, pull_request_number)` | a positive lifecycle completion, never a finding (L2a) |

**DPM overlay.** At compile time, `EngineeringContextAuthority` adds to the `main` registry families every
`family_candidate`, and every ingested-but-unresolved finding, whose paths intersect the task's TaskScope. The
overlay is bound in `dpm_context_sha256`, so prevention knowledge reaches every later task before its model runs.
This is the fast path. Enrichment (classification, proof, promotion) follows without blocking PR progress.

### L2a. Positive outcome evidence (owner decision D3, Issue #705, 2026-11-09/11)

**Amendment, accepted 2026-11-11.** A successful end-to-end lifecycle completion is prevention-relevant
knowledge too, but it is never a finding and can never authorize proof by itself. `verified_e2e_outcome`
adds exactly one more insert-only kind to the existing anchored chain (L2); it adds no new ledger, writer,
workflow or orchestrator.

- **Writer.** The existing `knowledge-ingest` role (`scripts/hunter_issue_agent_lifecycle.py
  verified-e2e-outcome`), dispatched manually through the existing `hunter-issue-agent-knowledge.yml`
  `workflow_dispatch` input, the same bounded, no-payload-relay path already used for historical backfill.
- **Evidence.** Every field is read back from three independent sources and never supplied by a caller:
  the live GitHub pull request (merged state, exact head/base, merge commit), the signed Issue ledger's
  own `COMPLETED` authorization for that issue/PR (`pull_request_head_sha`, `preflight_conclusion`, and the
  `VALIDATED` transition's `result_sha256`/`validator_run_id`, bound as `proof_digest`/`lifecycle_run_id`),
  and a live hosted admission run (`admission_run_id`). A mismatch on any of these fails closed.
- **Scope.** The eligible issue/PR pair is a closed, owner-approved allowlist (currently #577/#703 alone);
  this is a one-shot backfill, not a general ingestion mechanism.
- **DPM/SPM consumption.** `overlay_families` (the function the SPM/DPM compile step reads) only ever
  iterates registry-shaped family/finding entries; a `verified_e2e_outcome` record is readable on the same
  consumed `KnowledgeView` but never enters that overlay, so it can never be misclassified as a defect
  family and never duplicates or perturbs existing prevention entries.

### L3. Classification (RD-1)

1. **Deterministic, at ingestion.** The finding is `matched(F)` when the thread carries a structured tag
   `[family:DFF-nnn]` naming an existing family whose applicability covers the path, **or** when an earlier
   finding with the same `fingerprint` is already `matched(F)`. It is `ambiguous` when the tag names an unknown
   or inapplicable family. Otherwise it stays unclassified.
2. **Proven proposal, at remediation.** The remediation result may carry a hostile `finding_disposition`: either
   `{family_id}` or `{new_family: {title, invariant}}`, plus the `regression_tests` it adds. The validator accepts
   it only if:
   - the family exists and its applicability covers the paths, or the new-family fields are well-formed and
     bounded; **and**
   - every named regression test **fails** on the reviewed head plus the result's test files only (RED), and
     **passes** on the full result (GREEN). Both runs execute as the isolation uid with no network.

   Without that proof there is no classification. A `false-positive-claimed` disposition is never proof: the
   thread stays a known unresolved finding, and readiness stays blocked until the owner disposes of it.

### L4. Finding-driven remediation (RD-4)

The scheduled reconcile treats a PR as eligible only when **all** of these hold:

- its head branch is an `issue-<n>-<digest>` branch whose Issue ledger shows the authorization `COMPLETED` for
  that branch (PUBLISHED is still active and is finished by T5 first);
- the PR is open;
- the governing Issue is still open and still carries `hunter-agent-execute` (standing consent);
- there is an ingested, unresolved, non-ambiguous finding at the PR's current head;
- no authorization for that Issue is active. A remediation authorization is exempt from
  `ISSUE_HAS_ACTIVE_DRAFT_PR`, because its target *is* that PR; every other T1 refusal applies.

The control job then mints a **remediation authorization**
(`hunter-issue-agent-remediation-authorization-v1`), signed with K_AUTH and bound to:

```text
{issue, parent_authorization_id, pull_request_number, branch, bound_head_sha = PR head, finding_ids, attempt}
```

`authorization_id` is derived from those claims. It runs through the **same** S3–S5 lifecycle:

- `authorize` compiles a bounded SPM/DPM task. Its input is the finding claims, the TaskScope of the parent
  authorization, and the DPM overlay. There is no repository rediscovery.
- The AUTHORIZED evidence gains a `remediation` group. `execution_branch` equals the parent branch, and
  `base_sha` equals `bound_head_sha`.

Budgets: at most **2** remediation attempts per finding and **5** per PR. An exhausted budget leaves the finding
known and unresolved, and readiness stays blocked.

### L5. Validation and proof (RD-1)

The validator runs the RED→GREEN proof (L3.2) and then the unchanged pre-push safety over the final unsigned
commit. The final tree is the result files plus the L6 promotion delta. The receipt gains `proven_finding_ids`,
`regression_tests` and `disposition`, bound to `unsigned_commit_sha`.

### L6. Promotion inside the remediation commit (RD-3)

Trusted plumbing (validator and publisher, deterministically and identically) derives the registry and RFD delta
from the knowledge ledger plus the receipt's proof. It appends to `docs/REVIEWER_FINDING_DISPOSITIONS.json` one
RFD record per proven finding (provenance, mapped family, guard and test). It then applies one of:

- `matched(F)`: appends the regression tests to `F.regression_evidence`, and records a `recurrence` when `F` was
  already proven before;
- a new family: appends the smallest truthful `DFF-<next>` family with `lifecycle: regression-tested`, the
  proven invariant, the applicability paths and the sources.

The model can never write either file. A result that touches them is refused. The delta is part of the validated
tree, so the normal gates (Artifact Guard, Defect Prevention Guard, Pre-PR, governance) review it with the fix.

### L7. Publication and thread resolution (RD-5, RD-6)

The publisher fast-forwards the PR branch: `--force-with-lease=<branch>:<bound_head_sha>`, using a new commit
whose single parent is `bound_head_sha`. A moved head is `REMOTE_BRANCH_CONFLICT`, and nothing is overwritten.
The `CODE_WRITE_POLICY` `issue_agent_publisher.operation.update` changes from `false` to
`"fast-forward-from-bound-head"`; every other field is unchanged.

A control job writes `finding_proven` and then replies to and resolves the exact thread. It does so only when
**all** of these hold:

- the PR head equals the remediated head;
- a hosted Pre-PR Preflight succeeded at exactly that head;
- the receipt proves the finding;
- the review orchestrator's exact-head cycle at that head shows no unresolved thread with the same fingerprint.
  Reviewer unavailability is non-blocking per the existing pool policy.

An outdated thread, or a thread resolved by anyone else, is never proof by itself. Merge readiness keeps counting
unresolved threads.

### L8. Failure and restart

Every transition is a CAS append keyed as in L2. A crash or lost acknowledgement is resolved by read-back, and a
duplicate dispatch loses the CAS. Malformed, ambiguous or provenance-mismatched input fails closed with no write.
Everything stays inert until S6 (`MISSING_CONFIGURATION`).

### L9. Recovery from a verified failed exact-head Pre-PR Preflight (owner decisions D1–D3, Issue #706)

**Amendment, accepted 2026-11-09.** Before this amendment a candidate whose exact-head Pre-PR Preflight failed
went `PUBLISHED → FAILED(CANDIDATE_PREFLIGHT_FAILED)` and stayed stranded: L4 only remediates a reviewer finding
on an open PR of a `COMPLETED` authorization. L9 adds exactly one more closed authorization shape to L4. It adds no
new lifecycle, workflow, ledger, writer or orchestrator.

- **Trigger and eligibility.** The scheduled reconcile `remediate` job admits an Issue when **all** of these hold:
  - its newest authorization is `FAILED` with code `CANDIDATE_PREFLIGHT_FAILED`, written from `PUBLISHED`, and it
    is not a finding remediation;
  - its lineage branch is still exactly at the head that authorization published;
  - no pull request is open for that branch;
  - the Issue is still open and carries `hunter-agent-execute` (standing consent, as in L4).
- **Trusted classification.** Only a *deterministic test failure* is recoverable. All of these must hold:
  - the authoritative run is the first-attempt `push` run of `hunter-pre-pr-preflight.yml` at the exact failed
    head, the same selection the lifecycle uses for T5, and it concluded `failure`;
  - exactly one job, `Hunter Pre-PR Preflight`, failed, with exactly one failed step,
    `Run shared deterministic preflight`;
  - the job log shows `PASS` for Architecture Index Guard, Artifact Guard, Defect Prevention Guard, Ruff, Black
    and Mypy, and its only `FAIL` is `Pytest exited 1`;
  - the pytest summary reports failures and no errors, and its count equals the parsed `FAILED` node ids (one to
    sixteen, in the ledger's test-id vocabulary).

  Everything else stays terminal: a non-test gate (never retried), an infrastructure step, a cancellation or
  timeout, an inconsistent or incomplete log, or an indefinite read. The log is untrusted. It can only narrow what
  is recoverable, and the extracted node ids and one-line printable reasons reach the model only as untrusted
  task text.
- **Authorization.** The control job signs, with K_AUTH, a remediation authorization whose group is the closed
  preflight-recovery shape. It binds:
  - the parent authorization and the lineage branch;
  - the exact failed head;
  - the preflight run id, run attempt and job id;
  - the failed tests;
  - the attempt number.

  Its identity is a pure function of those facts and the live Issue claims. `authorize` re-applies the eligibility
  rule against the ledger under CAS and refuses while any authorization is active or a pull request is open.
  The AUTHORIZED evidence carries a `preflight_recovery` group, never mixed with `remediation`. The ledger fold
  applies the identical rule on every read.
- **Budget.** At most **2** recoveries per lineage branch. Attempt *n + 1* names attempt *n*'s failed
  authorization as its parent, so the lineage stays on the same branch. Once the budget is exhausted, the last
  `FAILED(CANDIDATE_PREFLIGHT_FAILED)` record is the durable, evidence-backed BLOCKED state. The reconcile log
  reports `RECOVERY_BUDGET_EXHAUSTED`, and the owner decides.
- **Unstarted recoveries (owner decision, Issue #716).** A recovery that provably never reached execution does not
  spend the execution budget of 2. It spends its own finite budget of **3** instead, so the total stays at most 5
  authorizations per lineage, and its signed records stay on the ledger unchanged. Two facts are needed:
  - **Ledger (necessary):** the record is `FAILED(EXECUTION_NOT_STARTED)` from `AUTHORIZED`, with no other
    lifecycle evidence (no fallback admission, bound result, validation or publication).
  - **Run (sufficient):** the controller reads the lifecycle run that the signed AUTHORIZED record binds
    (`authorize_run_id`). In every run attempt, the complete job listing holds exactly one `execute` job, and every
    `execute` / `execute-fallback` job is completed and `skipped`.

  `EXECUTION_NOT_STARTED` is also raised inside a started executor, so a code alone is never proof. Any job that
  started, or any partial, foreign or contradictory evidence, counts as an execution. An unreadable run is a no-op.
  Such a recovery may itself be recovered at the same exact head (`base_sha` = `bound_head_sha`).
- **Execution, validation and publication.**
  - **Unchanged:** the SPM/DPM compile (the parent's TaskScope rebased onto the failed head, never wider), the
    isolated executor on the lineage branch at the exact bound head (`PR_HEAD_MOVED` otherwise), and the
    credential-free validator.
  - **No proof group:** a recovery carries no finding proof and promotes nothing.
  - **Publication:** the publisher refuses while an Issue-Agent pull request is open. Otherwise it fast-forwards
    with `--force-with-lease=<branch>:<failed head>` (L7). A moved head is `REMOTE_BRANCH_CONFLICT`, and nothing
    is ever force-pushed.
  - **Observer:** before the fast-forward, a lineage branch at its bound head means "not yet published" (a
    bounded publication resume), never a conflict.
- **Completion.** The new head runs the ordinary exact-head Pre-PR Preflight. Success lets the unchanged
  Candidate PR workflow open exactly one Draft PR and record `COMPLETED` (T5). Failure records
  `CANDIDATE_PREFLIGHT_FAILED` again and the budget decides. Nothing merges automatically.

### L10. Repair of a signed pre-publication Ruff failure (owner requirement, Issue #706, 2026-11-10)

**Amendment, accepted 2026-11-10.** L9 starts only from `FAILED` written from `PUBLISHED`. Hosted run 42816584643
showed the other stranded case: the credential-free validator refused a candidate because of Ruff alone, so nothing
was published. L10 adds one more closed authorization shape. It adds no lifecycle, workflow, ledger, writer or
orchestrator, and it does not change Ruff, the sealed result, or the parent authorization.

- **Narrow class.** The validator reports `PRE_PUSH_RUFF_FAILED` only when all of these hold. Anything else stays
  the generic, terminal `PRE_PUSH_SAFETY_FAILED`:
  - the candidate-safety lane named Ruff and no other gate;
  - a trusted rerun reproduced findings, using the pinned Ruff binary in a fresh clone with the base's
    `pyproject.toml` passed explicitly, so a candidate configuration cannot shape it;
  - there are 1 to 32 findings, each with a rule code, on a file the result wrote.

  The lane is candidate-controlled and only names Ruff; the trusted rerun is the proof. A finding carries only its
  path, rule, row and column, because a message can quote source.
- **Signed failure.** `record-validation` re-checks the refusal receipt as hostile input and binds it to the
  validator run, run attempt and job, and to the result digest and diagnostic digest, in
  `FAILED.ruff_failure`. If the job identity cannot be proven, it records the generic code instead. The ledger
  fold refuses this code without that group, and the group on any other code.
- **Eligibility (reconcile `remediate`).** The signed ledger alone names the failure. Live facts can only narrow it:
  the never-published branch must not exist, no pull request may be open, the base must still be on main, and the
  Issue title and body must be unchanged. An indefinite read stops the pass.
- **Authorization.** The closed `validator_failure` group binds the parent, the same branch, the exact parent base
  and the failure facts above. `authorize` and the ledger fold apply one rule, `validator_recovery_refusal`.
- **Budget.** L9 and L10 share one budget per lineage: at most **2** executed repairs, plus the L9 unstarted budget.
- **Execution.** The result of the failed attempt stays sealed and is not given to the model. The repair runs the
  task again from the same base, through the unchanged SPM/DPM and isolated executor, with the closed findings as
  untrusted task text. It then needs the full credential-free validation and create-only signed publication.
- **Boundary.** A finding remediation or an L9 recovery runs on a published lineage branch, so its Ruff failure
  stays generic and terminal. A terminal failure never starts the same-run publisher: only a signed `VALIDATED`
  transition sets `publication_eligible` (PR #722).

### L11. Mandatory finding-to-knowledge lifecycle: the fast-path Knowledge Receipt (owner decisions, Issue #527)

**Amendment, accepted 2026-11-12.** Every authenticated/validated review finding confirmed as a real defect must
enter this knowledge lifecycle after correction; a finding is not fully resolved merely because code was fixed
and the thread was resolved. This is the permanent policy itself (AC1): the mandatory lifecycle is

```text
finding -> validate -> root cause -> minimal fix -> regression test -> fast knowledge receipt
        -> focused verification -> resolve -> asynchronous deep knowledge processing
```

L11 adds exactly one more insert-only ledger kind, one more deterministic writer pass, and one transport commit
status. It adds no new ledger, authority, workflow, or privileged execution path, and it does not change L1–L10.

- **Obligation (the receipt-required lifecycle).** A finding *enters* the obligation when its GitHub review
  thread is resolved and the owner's structured disposition reply on that exact thread classifies it
  `confirmed` (`split_owner_disposition`, reused unchanged from the historical-evidence collector). A resolved
  thread alone is never sufficient: only this authoritative, authenticated owner evidence creates the
  obligation, independent of whether a receipt yet exists for it.
- **The receipt (L2 kind `knowledge_receipt`, key `finding_id`, insert-only).** Deterministic, bounded,
  repository-local, no external-provider dependency (AC4): it is minted the moment the SAME confirmed
  disposition reply additionally carries a correction commit, non-empty regression evidence and an invariant
  summary -- fields the collector already parses, never invented. Deliberately independent of thread
  resolution timing, because the mandated lifecycle writes the receipt *before* resolve, not after: a finding
  ingested on an earlier pass becomes receipt-eligible on a later pass once that reply arrives
  (`receipt_writes`, a pass over observations independent of `ingestion_writes`'s own dedup). The receipt's
  outcome is `matched-family` (an applicable `[family:DFF-nnn]` tag on the same reply) or, explicitly,
  `pending-adjudication` (AC2/AC3) -- never an absence. A `pending-adjudication` receipt is a complete, valid
  receipt: deep family/recurrence adjudication is the unchanged L2/L3 slow path and never blocks on it.
- **Fail-closed boundary (AC7).** An obligation without evidence complete enough to mint a receipt (for
  example a bare "confirmed" with no cited commit) stays unsatisfiable until a follow-up reply supplies it --
  this is the intended discipline, not a defect. A finding with no obligation (no disposition, or a
  `false-positive`/`style`/`obsolete` disposition) needs no receipt and is never retroactively blocked (AC5 for
  legacy PRs).
- **Transport, never a second proof authority (AC2/AC6, owner decision on Issue #527).** The trusted
  `knowledge-ingest` role, in the same signed batch as ingestion, derives a verdict (`receipt_verdict`) from
  the ledger view it just appended to: `success` when every obligation on the PR's observations has a receipt
  (including the legacy/no-obligation case), `failure` otherwise. It publishes that verdict, and nothing else,
  as the `Hunter Knowledge Receipt` commit status on the PR's exact head -- idempotently, never republishing an
  unchanged verdict. `hunter_merge_readiness_v2.evaluate()` consumes it as one more ordered, lazy
  `ReadinessObservation` property, exactly like `candidate_admission`/`issue_acceptance`: it reads the status at
  the exact head and trusted context only, never re-derives it, and absence or a read failure is `pending`,
  never a silently satisfied obligation. This is how DPM/SPM's existing `overlay_families` (already including
  every open finding, unchanged) and this merge-blocking check compose without a parallel knowledge or review
  system (AC5/AC6/AC10): one signed ledger, one existing writer role, one existing consumer.
- **Receipt family link feeds classification too (Issue #586 requirement 3).** A family an owner names only in
  the disposition reply -- not in the original finding comment, the realistic case since the reviewing bot
  rarely knows the family -- was invisible to `finding_classified`/DPM-overlay family enrichment entirely:
  `ingestion_writes` only ever reads a tag from the *original* comment. `receipt_writes` now mints the same
  `finding_classified` record that path already would whenever its own `matched-family` outcome applies, so
  the match reaches DPM/SPM the same way either source names it. Skipped whenever a deterministic
  classification already exists or the original message already carries its own tag -- first classification
  always wins, and no two writes can ever conflict under the same `(finding_id, basis)` key.

## Consequences

- The knowledge ledger is a third anchored branch, and the anchor ruleset already covers it.
- Remediation commits change governed registry files. The governance review path sees and gates them.
- A false-positive still needs the owner, by design. A known valid unresolved finding is never silent.

## Implementation Status

**S5b-1…S5b-5 implemented (2026-10-04). S5 is complete: the §5 acceptance simulation passes.** S6 has not
started; `config/issue_agent_trust_roots.json` remains unprovisioned, so every lifecycle job still refuses
`MISSING_CONFIGURATION` before it touches a secret, a ledger or the network.

| Slice | Delivered |
|---|---|
| S5b-1/2 | `issue_agent_knowledge` (insert-only anchored ledger), idempotent ingestion with deterministic first classification, the DPM overlay, `hunter-issue-agent-knowledge.yml` |
| S5b-3 | the executor base is the exact bound PR head (`PR_HEAD_MOVED` otherwise); an optional bounded defect-family proposal in the result contract, with the canonical registry files refused; `red_green_regression_proof` (RED over the reviewed head plus the result's test files only, GREEN over the full result, both as the isolation uid with no credential); `remediation.promote`/`promotion_delta` as one deterministic deriver used by validator and publisher; `publish_bound_fast_forward` under `--force-with-lease=<ref>:<bound head>`; `CODE_WRITE_POLICY.issue_agent_publisher.operation.update = "fast-forward-from-bound-head"`; `control.eligible_remediation` + `dispatch_remediation` through the unchanged trigger workflow |
| S5b-4 | the receipt's proof group carried into the anchored Issue ledger; `proof_writes` (permanent classification), `proven_writes` (`finding_proven` + `recurrence` at an exactly preflighted head), `resolution_writes` (`thread_resolved` only after the proof and the reply); `control.review_threads`, `exact_head_proof`, `resolve_thread` (one reply, one `resolveReviewThread`, nothing else) |
| S5b-5 | the §5 acceptance simulation: one known-family and one genuinely new finding, each delivered twice, with a restart at every transition; fail-closed on a malformed, ambiguous, moved-head, preflight-less and recurring claim; prevention knowledge in a later task's DPM context before its model runs; 22 targeted mutants, all observed |

**Verification actually performed.** The S5b suite is proven: `test_issue_agent_remediation_{roles,promotion,reconcile,resolution,acceptance}`
— 91 passed — plus 22/22 mutants killed, and Black, Ruff, mypy and the Defect Prevention Guard green. The
full-repository `hunter_pr_preflight --mode normal` run was **stopped by the owner** partway through the whole
repository suite; it is **not** a pass and is not claimed as one. The hosted `Hunter / Pre-PR Preflight` on this
branch is still the authority for the exact head, and merge readiness continues to require it.

One governance consequence is recorded rather than hidden: the ADR 0039 L7 resolve job holds
`pull-requests: write`, which makes the reconcile workflow merge-gating authority, so the Issue-agent authority
closure is now part of the connector write-ingress root-of-trust floor. That narrows what that capability may
modify; it grants nothing.

A pre-existing, unrelated condition is recorded for the owner: `hunter_governance_preflight.py self-check` fails
because `docs/DEVELOPMENT_GOVERNANCE.md`, `docs/AI_REVIEW_PROTOCOL.md` and `.github/pull_request_template.md`
lack their canonical sentinels at this branch's head. None of those files is touched by ADR 0039, and the
pre-push safety lane does not run that command.

### L11 implementation status (Issue #527)

Delivered: the `knowledge_receipt` ledger kind and its `_fold` invariants; `receipt_writes` (the fast-path
writer, independent of `ingestion_writes`'s own dedup); `receipt_obligations`/`receipt_verdict` (the pure
obligation and transport-verdict derivation); the collector's `obligation`/`receipt_eligible` observation
fields (`hunter_collect_learning_observations.collect`); the `Hunter Knowledge Receipt` status publish, wired
into `cmd_knowledge_ingest` as a best-effort step after the signed ledger append (a transient publish failure
never aborts ingestion for the remaining PRs in the pass, since the ledger write it reports on already
succeeded); the `knowledge_receipt` `ReadinessObservation` property and its ordered gate in
`hunter_merge_readiness_v2.evaluate()`; the `statuses: write` permission this adds to the existing
`hunter-issue-agent-knowledge.yml` job (no new workflow).

**Verification actually performed.** 89 passed in `test_issue_agent_knowledge.py` (ledger schema, `_fold`
invariants, insert-only/idempotency, the later-pass receipt-arrival case, forged/mismatched-fingerprint and
inconsistent-outcome adversarial cases, writer-role enforcement, DPM-overlay non-interference, and the
transport verdict's success/failure/legacy-no-obligation counting); 29 passed in
`test_hunter_knowledge_learning_workflow.py` (the collector's `obligation`/`receipt_eligible` signal across
resolved/unresolved/non-confirmed/incomplete-evidence dispositions); 69 passed in
`test_hunter_merge_readiness_v2.py` (the new gate's ordering, fail-closed-on-missing/absent/producer-failure,
pending-adjudication and legacy-no-obligation pass-through, and exact-head/trusted-context status binding);
36 passed in `test_issue_agent_lifecycle_entry.py` (idempotent and changed-verdict status publication, the
140-character GitHub description bound). Ruff, Black and mypy (full configured file list, 1033 source files)
all green. This is local, focused evidence; the hosted exact-head `Hunter / Pre-PR Preflight` run remains the
authoritative full-repository proof, and merge readiness continues to require it.

**Hardening round (Copilot review, PR #737, 2026-11-12).** Eight High findings against the first version,
all closed on the same branch:

- `receipt_schema_problems`, the single source of truth for `knowledge_receipt` schema conformance, reused
  by both the collector's eligibility gate and `receipt_writes`'s own last-line-of-defense check -- an
  abbreviated SHA or a too-short invariant is refused, never silently receipt-eligible and never able to
  abort an otherwise-valid batch.
- `receipt_obligations` returns an unresolvable count separately from its finding-id list, so a
  confirmed-and-resolved finding `finding_from_observation` refuses (malformed path/provenance) fails the
  verdict closed instead of silently reading as "no obligation."
- The transport status is PR-scoped (`Hunter Knowledge Receipt / PR #<N>`, never a bare SHA-scoped context),
  authenticated (`creator.login == "github-actions[bot]"`, the same pattern `hunter_review_orchestrator.py`
  already uses for its own trusted statuses), and freshness-bounded (`KNOWLEDGE_RECEIPT_MAX_AGE_SECONDS`,
  comfortably wider than the producer's own ~30-minute cadence -- GitHub Actions has no "thread resolved"
  trigger, so this bounds, without eliminating, how long a cached verdict may be trusted before a fresher
  one is required; staleness degrades to `pending` in either direction, never upgraded to a fabricated
  `failure` either).
- `StaticReadinessObservation.knowledge_receipt` no longer defaults to `success`; every production
  constructor (`hunter_candidate_admission.ready_checks`, `hunter_workflow_state.observe_pull_request`,
  `hunter_governance_shadow.project`) supplies it explicitly, reusing an already-fetched statuses list where
  one exists rather than a duplicate network call.

Verification: 860 passed across the fourteen affected test files (ledger, collector, merge-readiness,
candidate-admission, workflow-state, governance-shadow, and the review-orchestrator guard this PR's own
`statuses: write` grant exercised). Ruff, Black and mypy green again on the full configured file list.
