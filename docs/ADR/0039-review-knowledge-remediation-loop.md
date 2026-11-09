# ADR 0039: Review → Knowledge → Prevention → Remediation Loop on the GitHub-Native Lifecycle

## Status

**Accepted** (2026-10-04, owner decisions RD-1…RD-6 on Issue #560). **Amended** 2026-11-09 by L9 (owner decisions
D1–D3, Issue #706). This is a binding amendment of:

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

**DPM overlay.** At compile time, `EngineeringContextAuthority` adds to the `main` registry families every
`family_candidate`, and every ingested-but-unresolved finding, whose paths intersect the task's TaskScope. The
overlay is bound in `dpm_context_sha256`, so prevention knowledge reaches every later task before its model runs.
This is the fast path. Enrichment (classification, proof, promotion) follows without blocking PR progress.

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
