# Issue #560: reviewer → knowledge → prevention → remediation loop (reconciliation)

**Status: reconciliation (2026-10-04); owner decisions RD-1…RD-6 taken the same day and recorded as [ADR 0039](ADR/0039-review-knowledge-remediation-loop.md) (accepted). S5b-1…S5b-5 implemented; the §5 acceptance simulation passes, so S5 is complete and the loop is usable. S6 has not started and every lifecycle job still refuses `MISSING_CONFIGURATION`.** Verification is *focused*: the S5b suite (91 tests) and 22/22 mutants pass with Black, Ruff, mypy and the Defect Prevention Guard green. The full-repository `hunter_pr_preflight --mode normal` was stopped by the owner during the repository suite and is **not** claimed as a pass; the hosted exact-head Pre-PR Preflight remains the merge-readiness authority for this branch. Decided: RD-1 hybrid deterministic-first with RED→GREEN proof; RD-2 anchored knowledge ledger; RD-3 promotion inside the remediation commit; RD-4/RD-5 Hunter-agent PRs only with fast-forward-from-bound-head publication; RD-6 reply and resolve only after exact-head proof. It records the owner requirement
of 2026-10-04 against the canonical authorities that already exist, names the smallest missing pieces, and
lists the owner decisions (RD-1…RD-6) that must be taken before any of those pieces is implemented. The
decisions are needed because several pieces require an authority that ADR 0037, ADR 0038, `CODE_WRITE_POLICY`
or the automatic-learning design currently withhold on purpose.

**Binding acceptance criterion (owner, 2026-10-04).** #560 is not usable, and **S5 is not complete**, until the
loop below runs automatically end to end and is proven by the acceptance simulation in §5. HARD STOP before S6
still applies.

```text
review finding → trusted ingestion/normalization → deterministic fingerprint/family classification
→ lookup in DEFECT_REGISTRY / DPM knowledge → dedupe or create the smallest truthful family/evidence record
→ prevention knowledge/guard/test requirements → SPM bounded remediation task (no repository rediscovery)
→ agent remediates → focused validation / adversarial / mutation tests → re-review / reconcile
→ resolve the exact thread, only after exact-head evidence proves the fix
→ result and recurrence evidence fed back to the knowledge loop → merge readiness
```

## 1. What already exists (reused as-is or adapted)

| Loop stage | Existing canonical authority | Fit |
|---|---|---|
| Event trigger | `.github/workflows/hunter-knowledge-learning.yml` (`pull_request_review`, `pull_request_review_comment`) | Exists. Hygiene debt: unpinned actions, pip cache, `cancel-in-progress: true` (AT-41 class). Safe only because the ledger is re-derived from GitHub state on every event. |
| Ingestion and normalization | `scripts/hunter_collect_learning_observations.py` (authenticated reviewer pool, exact head/base, thread state); `incremental_knowledge_learning.build_learning_ledger` | Exists. It is deterministic, content-digest deduplicated, refuses conflicting identities, and records source availability. Unavailable providers are non-blocking. |
| Family classification | `knowledge_extraction_authority.KnowledgeExtractionAuthority` (outcomes: matched / `candidate-new-family` / ambiguous / excluded) | Exists, but a family mapping requires a `claimed_family_id` and an invariant. Today **only an owner disposition reply** supplies those (`split_owner_disposition`, `[family:DFF-nnn]`, `[test:…]`). That reply is the human relay the requirement forbids. |
| Registry integration | `controlled_learning_integration.integrate_learning_ledger` / `materialize_learning_ledger`; `scripts/hunter_canonicalize_learning.py` | Exists. CI only *renders* a candidate. The atomic apply runs only through the local CLI, which is a human step. |
| Validated finding record | `docs/REVIEWER_FINDING_DISPOSITIONS.json` (RFD-*: provenance, validation state, mapped defect, resolution, guard, test), checked by the Defect Prevention Guard | Exists. It is the canonical per-finding evidence store, so no second store is needed. |
| Prevention knowledge before implementation | `EngineeringContextAuthority` reads `docs/DEFECT_REGISTRY.json` at compile time; the S3b `authorize` binds it as `dpm_context_sha256` in the prompt manifest | Exists for knowledge **merged on `main`** (read at `control_sha`). It does **not** see knowledge from an unmerged PR. |
| Exact-head review state | `hunter_review_orchestrator` (blocking threads, remediation generations), collector, merge readiness (counts unresolved threads), reviewer-pool unavailability policy | Exists. Readiness already refuses unresolved threads. |
| Remediation execution | The #560 lifecycle (S2–S5): SPM/DPM-compiled bounded task → isolated executor → credential-free validator → create-only publisher → Draft PR | Exists, but it is triggered only by the owner's Issue label (K_AUTH), and publication is **create-only** on a new `issue-<n>-<digest>` branch. |
| Exact-head proof | Pre-PR Preflight receipts, `VALIDATION_STAGE_CONTRACT`, the validator receipt bound to `unsigned_commit_sha` | Exists. |

## 2. Smallest missing pieces

| # | Missing piece | Why it cannot be built from what exists alone |
|---|---|---|
| M1 | **Disposition without a human relay.** A finding must become `confirmed` or `false-positive` and be mapped to a family automatically. | The learning design makes reviewer prose "evidence only". It refuses to invent an invariant and excludes semantic or LLM classification. Mapping free text to a family deterministically is not possible without (a) structured reviewer output or (b) a model proposal. The truthful mechanical proof of a confirmed finding is **RED→GREEN**: a regression test that fails at the reviewed head and passes at the remediated head. → **RD-1** |
| M2 | **Fast-path knowledge visible before the fix merges.** | DPM reads the registry at `control_sha` on `main`. Knowledge from an open PR reaches later tasks only after merge. A pre-merge fast path needs a durable, anchored knowledge record that DPM overlays, which is a new persistence surface. → **RD-2** |
| M3 | **Automatic canonical registry and disposition promotion** (dedupe into an existing family, or create the smallest truthful new family; add regression evidence; advance the lifecycle). | The design lists "automatic registry commit" and "automatic new-family creation" as **non-goals**. `DEFECT_REGISTRY.json` is a governed artifact, and there is no code-write grant for an automated registry writer. → **RD-3** |
| M4 | **Remediation trigger without the owner label.** | ADR 0037 releases the model only on an owner-minted K_AUTH authorization. A finding-driven run is a new authorization source, with its own scope, budget and signer. → **RD-4** |
| M5 | **Publishing a remediation onto an open PR.** | ADR 0037 D6 and `CODE_WRITE_POLICY.issue_agent_publisher` are create-only (`update: false`), and the target ref must be a new `issue-<n>-*` branch. → **RD-5** |
| M6 | **Automatic reply and thread resolution after exact-head proof.** | No trusted job holds a `pull-requests: write` capability for this today. Merge readiness only reads thread state. → **RD-6** |
| M7 | Recurrence feedback: the fix result and any later recurrence update the family's evidence and lifecycle. | This follows from M3 once decided. |
| M8 | Harden `hunter-knowledge-learning.yml` to the AT-41 rules. | Local hygiene; no new authority. |

## 3. Owner decisions required (no implementation until decided)

- **RD-1 Classification authority.** One of:
  - (a) Deterministic only. The fingerprint is (provider, PR, path, normalized finding title or rule id). A family is matched only when exactly one registry family's applicability covers the paths **and** the reviewer supplied a structured family tag. Everything else becomes `candidate-new-family`.
  - (b) A model *proposes* the family and invariant inside the remediation result (hostile data). It is accepted only when the deterministic checks pass: the family exists, the paths are applicable, and the RED→GREEN regression proves the finding.
  - (c) Both (a) and (b), with (a) taking precedence.
- **RD-2 Fast-path knowledge store.** Either:
  - (a) A new anchored branch `refs/heads/hunter-state/v1/knowledge` (same ADR 0037 D2/D2a anchor, K_STATE-signed, insert-only finding and family-candidate records) that `EngineeringContextAuthority` overlays on the `main` registry at compile; or
  - (b) No fast path, accepting that knowledge reaches later tasks only after the fix PR merges. This does not meet the stated "before implementation" property for tasks that start before that merge.
- **RD-3 Canonical promotion.** Either:
  - (a) Trusted plumbing writes the deterministic registry and RFD delta into the remediation candidate commit, outside model scope, so it merges with the fix under the normal gates; or
  - (b) A separate automated registry PR per finding; or
  - (c) Knowledge stays on the anchored store and is promoted in batches.

  This decision reverses the "automatic registry commit / new-family creation" non-goals, and needs a `CODE_WRITE_POLICY` grant for (b).
- **RD-4 Finding-triggered remediation.** Decide:
  - which PRs are eligible: only Hunter-agent PRs (`issue-*`), or any PR including human or assistant branches;
  - who signs the machine authorization (a new K_FIND key, or K_AUTH via a control job);
  - the per-finding and per-PR attempt budgets;
  - whether the owner label on the governing Issue remains a required standing consent.
- **RD-5 Remediation publication.** Either:
  - (a) An exact-lease fast-forward update of the agent's own `issue-<n>-<digest>` branch, from the bound head only (an OD-3 amendment: `update: fast-forward-from-bound-head`); or
  - (b) Create-only stacked branches `issue-<n>-<digest>-r<k>` with a Draft PR into the PR branch; or
  - (c) Remediation only for Hunter-agent PRs, using (a).
- **RD-6 Thread resolution.** A control job gains `pull-requests: write` only to reply to and resolve the exact thread, once exact-head evidence proves the fix (RED→GREEN at the remediated head, hosted Pre-PR success on that head, and re-review with no recurrence). It never edits or dismisses findings.

## 4. Proposed slice placement (after the decisions)

S5 is **not complete** until S5b is proven. S6 stays owner-gated.

| Slice | Deliverable | Depends on |
|---|---|---|
| **S5a** (done) | lifecycle, reconcile, candidate-PR and rehearsal workflows; AT-41/42/46/47 | — |
| **S5b-1** | M8: harden the learning workflow; fingerprint and finding-record schema on the existing ledger, idempotent and restart-safe | none (local) |
| **S5b-2** | M1 classifier and M2 knowledge store; DPM overlay | RD-1, RD-2 |
| **S5b-3** (done) | M4/M5 finding-triggered bounded remediation through the existing S3–S5 lifecycle, fast-forwarded from the exact bound head | RD-4, RD-5 |
| **S5b-4** (done) | M3/M7 promotion and recurrence feedback; M6 exact-head thread resolution | RD-3, RD-6 |
| **S5b-5** (done) | §5 acceptance simulation; mutation proof | all |

## 5. Acceptance simulation (binding)

On the real stack (local bare remotes, real signed ledger, fake GitHub only for API facts):

1. Inject finding A, which belongs to a known family, and finding B, which is genuinely new. Each is delivered twice, and a process restart is injected between every transition.
2. Prove for each finding: ingest → family mapping (A) or smallest truthful creation (B) → bounded remediation handoff → agent result → validation, including RED→GREEN → re-review/reconcile → exact thread resolution → readiness.
3. Prove exactly one knowledge record, one remediation and one resolution per finding, despite the duplicate deliveries and restarts.
4. Prove fail-closed behavior on: a malformed finding, an ambiguous mapping, a provenance or head mismatch, and a stale or outdated thread.
5. Prove that a subsequent fresh task's compiled DPM context contains A's and B's prevention knowledge **before** its model runs.
6. Prove that reviewer unavailability is non-blocking, while a known valid unresolved finding still blocks readiness.
