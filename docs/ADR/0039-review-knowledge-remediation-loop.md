# ADR 0039: Review → Knowledge → Prevention → Remediation Loop on the GitHub-Native Lifecycle

## Status

**Accepted** (2026-10-04, owner decisions RD-1…RD-6 on Issue #560). This is a binding amendment of:

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

## Consequences

- The knowledge ledger is a third anchored branch, and the anchor ruleset already covers it.
- Remediation commits change governed registry files. The governance review path sees and gates them.
- A false-positive still needs the owner, by design. A known valid unresolved finding is never silent.

## Implementation Status

Not implemented. Slices S5b-1…S5b-5 (Retirement Plan §5). S5 is complete only when the reconciliation §5
acceptance simulation passes.
