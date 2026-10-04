# Issue Agent GitHub-Native Durable State Machine

Status: Proposed with [ADR 0037](ADR/0037-github-native-issue-agent-execution.md), revision 3. **Accepted** 2026-10-04 with ADR 0037; binding.
Not implemented.

Mandatory semantics (owner, 2026-10-04):

```text
AUTHORIZED -> RESULT_BOUND -> VALIDATED -> PUBLISHED -> COMPLETED
     \______________\______________\___________\__________-> FAILED
```

- `PUBLISHED` is the trusted publication ACK. `COMPLETED` follows only after the single Draft PR is
  observed.
- `FAILED` is the explicit terminal failure.
- A lost ACK reconciles from durable identity. It never reruns the model, and it never creates a second
  branch or PR.

## 1. Location

| Item | Value |
|---|---|
| Per-Issue state branch | `refs/heads/hunter-state/v1/issue-<issue_number>`: forward-only, protected by the anchor ruleset (ADR D2a) |
| Source Handling store (ADR 0038) | `refs/heads/hunter-state/v1/source-handling`: same anchor, its own CAS, sole publisher per ADR 0036 |
| Quarantine | new generation branch `refs/heads/hunter-state/v1/issue-<n>-g<k>`; the frozen branch is kept forever (deletion is impossible by design) |
| Anchor | repository ruleset `hunter-state/**`: `deletion` + `non_fast_forward`, no bypass actors, `active`; its id and `updated_at` are pinned at `control_sha` |
| Writers | control-role jobs only (`GITHUB_TOKEN` `contents: write` + K_STATE). The branch name grants nothing (ADR D2). |
| Readers | anyone; anonymous fetch of a public repository |
| Pinned trust roots at `control_sha` | public keys: K_STATE, K_AUTH, K_SPM, K_SH, X_EXEC, X_RESULT; workflow-path allowlist and role→transition map |

Tree of each state commit:

```text
index.json
authorizations/<authorization_id>.json
```

Source Handling records live on their own anchored branch (ADR 0038). The Issue ledger references their
ids; it does not embed them.

The commit message is `<from>-><to> <authorization_id> <run_id>/<attempt>`. It is built with plumbing in a
trusted git dir, with hooks and fsmonitor disabled.

## 2. Records

All records are canonical JSON: UTF-8, sorted keys, no floats, size and depth caps, unknown fields refused.

**Common fields:**

- `schema_version` (`hunter-issue-agent-execution-record-v1`);
- `record_seq`;
- `prev_record_sha256`;
- `recorded_at`;
- `recorded_by` (`{workflow_path, job, role, run_id, run_attempt, head_sha}`);
- `signature` (`{alg: ed25519, key_id, domain: hunter-issue-agent-state-v1, value}`).

The signature covers the record without `signature`.

**IssueIndex** (`index.json`):

- `repository_id`, `issue_number`, `issue_node_id`;
- `claimed_authorization_ids` (append-only replay set);
- `active_authorization_id` (or `null`);
- `pending_resume` (`{stage, nonce, attempt, dispatched_at, bound_run_id}` or `null`).

**ExecutionRecord** (`authorizations/<id>.json`):

| Group | Fields |
|---|---|
| state | `state`, `failure` = `{code, failed_from_state}` |
| immutable bindings | `repository_id`, `issue_number`, `authorization_id`, `authorization_envelope_sha256`, `base_sha`, `task_scope_sha256`, `execution_id`, `prompt_input_manifest_sha256`, `compiler_identity_sha256`, `control_sha` |
| authorization | claims `{owner_login, label, issue_updated_at, schema_version, title_sha256, body_sha256}`; full `task_scope`; `execution_branch` |
| control | `authorize_run_id`, `authorize_run_attempt` (=1), `deadline_published_at` (+6 h), `deadline_completed_at` (set at PUBLISHED, +24 h) |
| lineage (B-3) | `document_id`, `build_record_id`, `envelope_id`, `prompt_artifact_id`, `prompt_sha256`, `handoff_sha256`, `dpm_context_sha256`, `source_handling_record_ids[]`, `reconstruction` = `EXACT_RECONSTRUCTION_UNAVAILABLE`, `reconstruction_reason` = `NO_CONFIDENTIAL_DURABLE_STORE` |
| transport (B-1) | `handoff_artifact` and `result_artifact`: `{run_id, artifact_id, artifact_digest, ciphertext_sha256, aad_sha256, recipient_key_id}` |
| result | `result_plaintext_sha256` (as declared in the authenticated associated data; verified at T3), `executor_job_id`, `executor_conclusion`, `executor_advisory_code` |
| validation | `receipt_sha256`, `result_sha256`, `tree_sha`, `unsigned_commit_sha`, `validation_definition`, `toolchain_sha256`, `verdict`, `validator_run_id`, `validation_attempts` |
| publication | `writer_login`, `publication_identity`, `head_sha`, `commit_verified`, `publish_attempts` |
| completion | `pull_request_number`, `pull_request_node_id`, `pull_request_head_sha`, `draft` = `true` |

**Identity derivations** (ADR D6):

```text
execution_id         = sha256(canon{authorization_id, authorize_run_id, run_attempt=1, control_sha, handoff_sha256})
publication_identity = sha256(canon{repository_id, issue_number, authorization_id, base_sha, task_scope_sha256,
                                    execution_id, result_sha256, tree_sha, unsigned_commit_sha, control_sha, writer_login})
```

**Forbidden content (schema-enforced).**

- **Never stored:**
  - Issue title or body;
  - the prompt, handoff, or envelope bytes;
  - the signed authorization document;
  - model output, file contents, or patches;
  - gate or tool output;
  - provider responses;
  - free text;
  - tokens, keys, or environment dumps.
- **Allowed string types:** SHA, id, enum, bounded login, ISO-8601, canonical TaskScope path.

## 3. Validity, CAS, idempotency, facts

**Validity.** A record is valid only if all seven ADR D2 checks pass for the whole chain: signature,
chain, legal transition plus required evidence, immutable bindings, trusted workflow provenance, CAS lineage,
and **anchor integrity**.

**Anchor integrity.** Authenticated reads only; anonymous reads were proven CDN-stale. The pinned ruleset
id exists with `enforcement == active`; its rules include `deletion` and `non_fast_forward`; `GET
/rules/branches/<branch>` attributes both rules to that id; `updated_at` equals the pin. Any mismatch →
global freeze `ANCHOR_INTEGRITY_FAILED`.

**Legal transitions and required evidence:**

| From | To | Writer role | Required evidence |
|---|---|---|---|
| (none) | AUTHORIZED | control:`authorize` | immutable bindings, lineage, handoff transport, deadlines |
| AUTHORIZED | RESULT_BOUND | control:`bind`/reconcile | `result_artifact`, executor job id and conclusion |
| RESULT_BOUND | VALIDATED | control:`record-validation`/reconcile | receipt fields |
| VALIDATED | PUBLISHED | control:`finalize`/reconcile | `head_sha`, `publication_identity`, `commit_verified` = true |
| PUBLISHED | COMPLETED | control:candidate-PR record step/reconcile | PR number, node id, head, `draft` |
| any non-terminal | FAILED | any control role | `failure.code` from §10 |

**CAS.** The writer reads head `H0`, verifies the chain, decides, appends one commit with parent `H0`, and
pushes with `--force-with-lease=<ref>:<H0>`. Creating a ref uses the empty lease. A lease rejection means a
re-read and a re-decide. The writer never overwrites.

**Idempotency key.** `(authorization_id, to_state, writer run_id, run_attempt, evidence-id)`. The evidence
id is the artifact id, the `receipt_sha256`, the `publication_identity`, or the PR number. After any push
outcome, including an error, the writer re-reads:

- its exact key is present → success;
- another record holds that slot → it refuses.

**Definitive facts only.** A transition driven by observation needs one of these:

- a 200 with content;
- a documented absence: 404 for a ref, PR or artifact, or a job conclusion of `skipped`.

A 5xx, 429, timeout, or partial response makes `advance` a no-op.

**Concurrency groups.** These are hygiene, not correctness:

- label runs use `hunter-issue-agent-<n>` with `queue: max`, so duplicates are queued and refused by CAS
  rather than silently cancelled;
- resume runs use `hunter-issue-agent-resume-<n>` with `queue: max`.

## 4. Job graph (fresh lifecycle run; all jobs on `control_sha = github.sha`, attempt 1)

```text
authorize[control] -> execute[executor] -> bind[control] -> validate[validator]
  -> record-validation[control] -> publish[publisher] -> finalize[control, if: always()]
Pre-PR Preflight (existing, on push) -> Issue Agent Candidate PR (existing, adapted) -> record COMPLETED
```

## 5. Transitions

### Pre-authorization refusals (nothing durable is written)

| Code | Cause |
|---|---|
| `NOT_ELIGIBLE` | not owner, wrong label, PR-as-Issue, Issue closed, or ref ≠ `refs/heads/main` |
| `RERUN_REFUSED` | `run_attempt` > 1 |
| `SCOPE_INCOMPLETE` | `incompleteness()`, or the target cannot be derived |
| `BASE_NOT_ON_MAIN` | the base is not a commit reachable from `main` |
| `SOURCE_HANDLING_BLOCKED` | DFF-023 ordering: refused before any write |
| `DUPLICATE_AUTHORIZATION` | the id is already in the replay set |
| `ISSUE_EXECUTION_ACTIVE` | another authorization for this Issue is active |
| `ISSUE_HAS_ACTIVE_DRAFT_PR` | an open PR exists whose head is `issue-<n>-*` |
| `ADMISSION_CAP_REACHED` | global soft cap (default 2) |
| `MISSING_CONFIGURATION` | a control-environment secret or variable is absent |
| `COMPILATION_REFUSED` | SPM or DPM refusal |

Any orphan handoff artifact left behind by a refusal is never bound and expires after one day.

### T1: (none) → AUTHORIZED: claim and model release, one atomic CAS

| Aspect | Specification |
|---|---|
| Actor | `authorize` job, control environment, `control_sha = github.sha`, attempt 1 |
| Signed inputs and bindings | v2 authorization minted with K_AUTH and **verified** with the pinned public key, `authorization_id` re-derived; `TaskScopeContract` complete; `derive_execution_target`; base reachable from `main`; Source Handling records provisioned (idempotent CAS on the SH branch; an existing mismatch is refused, never replaced) and preflight passed; `GovernedEngineeringTaskIngress` → `SmartPromptMachine` → ECA/DPM, compiled ephemerally; envelope verified with pinned K_SPM; prompt-input manifest, compiler identity and prompt digest computed (D4); handoff sealed with the D3 associated data and uploaded; artifact id and digest **read back from the API** |
| Durable evidence | new ExecutionRecord (state AUTHORIZED) + index update (id appended, active set) in **one commit** on the Issue branch. The Source Handling records were written *before*, by idempotent CAS on the Source Handling branch (ADR 0038). DFF-023 ordering holds: SH is validated before the claim. A crash between the two leaves only idempotent SH records and no claim. |
| CAS / idempotency key | create the Issue ref (empty lease), or lease on the observed head; precondition: active = null, id ∉ replay set, no open Issue-Agent PR. Key `(authorization_id, AUTHORIZED, run_id, 1, handoff_artifact_id)`. |
| Crash before write | no durable state; the model cannot start (the executor requires `needs: authorize` success **and** a verified AUTHORIZED record bound to its run); an orphan artifact expires. The owner relabels, and the failed run is visible. |
| Crash after write, before ACK | in-job: read-back finds the key → success. If the job dies, the executor is `skipped` → `finalize`/reconcile → TF `EXECUTION_NOT_STARTED`. The model is not auto-started (ADR alternatives). |
| Duplicate / out-of-order replay | duplicate delivery in a new run → same id → `DUPLICATE_AUTHORIZATION`; concurrent label → lease serializes, and the loser refuses with `ISSUE_EXECUTION_ACTIVE`; workflow re-run → `RERUN_REFUSED`; an old event can be replayed only through a re-run, which is refused |
| Timeout | job ≤ 20 min; nothing durable before the write |
| GitHub outage | API or push failure before the write → no state, and the job fails; ambiguous push → read-back decides (bounded retry within the job deadline, DFF-034) |
| Provider outage | not applicable; no model call |
| Terminal / recovery | non-terminal; recovery via `advance` |

### Executor (writes nothing durable)

The trusted parent on the executor VM, at `control_sha`, does the following in order:

1. Anonymously reads and verifies the chain: AUTHORIZED, bound `run_id`, attempt 1, no result yet.
2. Downloads the handoff. Refuses unless the associated data equals the expectation derived from state.
   Then decrypts with X_EXEC, checks `handoff_sha256`, and verifies the K_SPM envelope.
3. Materializes `base_sha` from the public repository.
4. Runs the model only as the isolation uid, with `env -i` and an allowlist, and a wall clock of 50 min.
5. Kills every isolation-uid process, then collects the result through a trusted git dir.
6. Checks the closed schema, and scans for the exact model key and its encodings. A hit is sealed as the
   non-candidate outcome `SECRET_IN_RESULT`.
7. Seals the result to X_RESULT with associated data that binds `execution_id`, `handoff_sha256` and
   `plaintext_sha256`, and uploads `hunter-ia-result-<authorization_id>`.

Its job outputs are advisory and hostile.

**Provider outage or quota, or a missing model key.** The executor seals an advisory non-candidate
outcome, or uploads nothing. Either way the authorization ends in `FAILED`, and the model is **never**
re-dispatched.

### T2: AUTHORIZED → RESULT_BOUND

| Aspect | Specification |
|---|---|
| Actor | `bind` job (control); in recovery, reconcile using the same `advance` |
| Signed inputs and bindings | the verified AUTHORIZED record; **GitHub API facts only**: the executor job of `authorize_run_id` (id, conclusion, attempt 1) and the artifacts in that run named `hunter-ia-result-<authorization_id>`. **Exactly one** must exist at bind time (S0: names are *not* unique, so zero or several → `TRANSPORT_INTEGRITY_FAILED`), with size ≤ cap and digest. The sealed header's associated data equals the state-derived expectation. The binding is by **artifact id**; every consumer downloads by `artifact-ids` with `digest-mismatch: error`. |
| Durable evidence | `result_artifact` binding (including `aad_sha256`), declared `result_plaintext_sha256`, executor job and conclusion, advisory code |
| CAS / idempotency key | lease from the AUTHORIZED head; key `(authorization_id, RESULT_BOUND, artifact_id)` |
| Crash before write | reconcile repeats; deterministic from API facts |
| Crash after write, before ACK | read-back → success |
| Duplicate / out-of-order replay | a duplicate same-name artifact uploaded before the bind (only the hostile executor can do that) → `TRANSPORT_INTEGRITY_FAILED`; one uploaded after the bind (for example by a compromised validator) is ignored because consumers use the bound id; a `bind` from another run fails provenance and binding; first writer wins |
| Timeout | executor ≤ 60 min (model 50 min); lifecycle deadline AUTHORIZED + 6 h |
| GitHub outage | facts ambiguous → no-op; the artifact persists for one day; after expiry → TF `RESULT_TRANSPORT_EXPIRED` |
| Provider outage | executor concluded with no artifact → TF `EXECUTION_NOT_COMPLETED`; `skipped` → TF `EXECUTION_NOT_STARTED`. **Never re-dispatched.** |
| Terminal / recovery | non-terminal |

### T3: RESULT_BOUND → VALIDATED (`pre-push-safety` stage)

| Aspect | Specification |
|---|---|
| Actor | `validate` job (credential-free; writes nothing) produces plain receipt artifact `hunter-ia-receipt-<authorization_id>-<attempt>` (digests only); `record-validation` (control) or reconcile records it |
| Validator steps | **Trusted intake step:** check the associated data and artifact digest against state; decrypt with X_RESULT; check `plaintext_sha256`; run `validate_replacement_result` against the **state-bound** authorization, TaskScope and target (strict UTF-8, canonical paths, no symlink, submodule or `.git`, no Unicode or case collision, modes, sizes); build the tree; build the exact **unsigned commit** (D6); X_RESULT is dropped when the step ends. **Candidate step:** the trusted `control_sha` pre-push-safety gate chain over that commit, as isolation uid with `env -i`, no network, and a toolchain pre-installed by the trusted step; no candidate hook. Emits codes and counts only. |
| Signed inputs and bindings | receipt `{authorization_id, execution_id, ciphertext_sha256, result_sha256, tree_sha, unsigned_commit_sha, base_sha, task_scope_sha256, validation_definition, toolchain_sha256, verdict}` read through the API from the bound validator run; must equal the record's bindings; `result_sha256` must equal `result_plaintext_sha256` |
| Durable evidence | the validation group |
| CAS / idempotency key | lease from RESULT_BOUND; key `(authorization_id, VALIDATED, receipt_sha256)` |
| Outcomes | PASS → VALIDATED; schema or binding rejection → TF `EXECUTOR_RESULT_REJECTED`; gate failure → TF `PRE_PUSH_SAFETY_FAILED`; secret pattern or executor `SECRET_IN_RESULT` → TF `SECRET_IN_RESULT`; transport failure (associated data, AEAD, digest) → TF `TRANSPORT_INTEGRITY_FAILED` |
| Crash before write | no receipt → nonce-bound **validation resume** (model-free, same `control_sha`, `actions: read` for the cross-run download), at most 2 attempts → TF `VALIDATION_UNAVAILABLE` |
| Crash after write, before ACK | read-back → success |
| Duplicate / out-of-order replay | a resume run binds `pending_resume.nonce` by CAS, so a duplicate dispatch exits; a receipt from an unbound run fails provenance; a stale receipt of attempt n−1 does not match `validation_attempts` |
| Timeout | validate ≤ 30 min; artifact expiry → TF `RESULT_TRANSPORT_EXPIRED` |
| GitHub outage | no-op until definitive; the resume budget is consumed only by runs that actually concluded |
| Provider outage | not applicable |
| Terminal / recovery | non-terminal |

### T4: VALIDATED → PUBLISHED (trusted publication ACK)

| Aspect | Specification |
|---|---|
| Actor | `publish` job (publisher environment) pushes and writes nothing durable; `finalize` (control) or reconcile observes and records |
| Publisher steps | 1. Verify the chain, and that the state is VALIDATED. 2. Live Issue gate: open, not a PR, label present, title and body digests equal to the claims; failures are `ISSUE_CLOSED`, `OWNER_WITHDREW` and `ISSUE_CHANGED_AFTER_AUTHORIZATION`. 3. No other open Issue-Agent PR for this Issue. 4. Download and check the associated data, then decrypt with X_RESULT. 5. Re-run the pure `validate_replacement_result` and the plumbing tree build. This is the publication precondition, not a re-proof. 6. Rebuild the unsigned commit, which must be byte-equal to `unsigned_commit_sha`. 7. Sign with SSH Ed25519; the result is deterministic. 8. `git push --force-with-lease=refs/heads/<branch>: <head>:refs/heads/<branch>`. The publisher runs no hook and no candidate code (DFF-028). |
| Signed inputs and bindings | for the transition: the remote branch head and its commit through the API — exactly one commit over `base_sha`; tree = `tree_sha`; non-signature fields recompute to `unsigned_commit_sha`; `verification.verified` with signer = `writer_login`; the `Hunter-Publication-Identity` trailer equals `publication_identity` |
| Durable evidence | the publication group |
| CAS / idempotency key | lease from VALIDATED; key `(authorization_id, PUBLISHED, publication_identity)` |
| Crash before push | publication resume (deterministic, same head), at most 3 attempts → TF `PUBLICATION_UNAVAILABLE` |
| Crash after push, before record | reconcile observes the branch at the conforming head → PUBLISHED. It **discovers** the existing branch and never creates a second one. |
| Lost push ACK | re-push of the identical deterministic head; the branch is already at that head → success |
| Duplicate / out-of-order replay | the empty lease makes the branch create-only; a duplicate publisher produces the identical head (no-op); any **foreign** head → TF `REMOTE_BRANCH_CONFLICT`, and nothing is overwritten; a stale publisher of an old authorization targets a different branch name |
| Timeout | publish ≤ 10 min; lifecycle deadline → TF `LIFECYCLE_DEADLINE_EXCEEDED` |
| GitHub outage | push or API failure → resume within budget and deadline; ambiguous → read the remote ref before re-pushing |
| Provider outage | not applicable |
| Platform refusal | for example a workflow file without the `workflows` scope → TF `PUBLICATION_REJECTED_BY_PLATFORM` (terminal; a deterministic retry would fail the same way) |
| Terminal / recovery | non-terminal; `deadline_completed_at` is set |

### T5: PUBLISHED → COMPLETED (single Draft PR observed)

| Aspect | Specification |
|---|---|
| Actor | existing `Hunter / Issue Agent Candidate PR` workflow (adapted): its PR-token job creates the PR; a separate control-environment **job** records, because environments are job-scoped; reconcile is the fallback observer |
| Signed inputs and bindings | `workflow_run` of `Hunter / Pre-PR Preflight`, conclusion `success`, event `push`, `head_sha` = record `head_sha` = current remote head; existing checks (agent branch shape, Issue binding, signed range, one active PR per Issue); PR head ref = branch, head SHA = `head_sha`, base `main`, `draft` = true |
| Durable evidence | the completion group; terminal success |
| CAS / idempotency key | lease from PUBLISHED; key `(authorization_id, COMPLETED, pull_request_number)` |
| Crash before PR create | the next Pre-PR completion re-triggers, or reconcile observes no PR and stays non-terminal until the deadline |
| Crash after PR, before record | reconcile lists PRs by head, finds the PR, and records COMPLETED (**discovered**, not recreated) |
| Duplicate / out-of-order replay | list-by-head before create, then adopt; per-Issue concurrency (`queue: max`); GitHub refuses a second open PR for the same head and base, and that refusal is adopted; a Pre-PR run for an older head does not match `head_sha` and is ignored |
| Timeout | `deadline_completed_at` → TF `CANDIDATE_PREFLIGHT_TIMEOUT` |
| GitHub outage | no-op until definitive |
| Pre-PR failed | conclusion other than success for `head_sha` → TF `CANDIDATE_PREFLIGHT_FAILED`; the branch is kept and no PR is created |
| Terminal / recovery | COMPLETED is absorbing. Side effects (best effort, non-authoritative): clear the active pointer in the same commit; status comment; label removal; delete transport artifacts. New claims stay refused while the PR is open. |

### TF: any non-terminal → FAILED

- **Actor.** Any control role running `advance`.
- **CAS.** From the observed state. The first terminal write wins.
- **Evidence.** `failure` from §10.
- **Duplicate.** A second terminal write loses the CAS and refuses.
- **Side effects.** Same as COMPLETED. The id stays in the replay set forever.

## 6. `advance` (reconcile)

- **Triggers.** `finalize` (`if: always()`), every control job, the scheduled reconcile (best effort,
  every 30 min), and owner `workflow_dispatch` (no payload relay).
- **Active run.** If the run bound to the current stage is queued or in progress, `advance` does nothing.
- **Resume protocol.** CAS `pending_resume = {stage, nonce, attempt}` → `workflow_dispatch(issue,
  authorization_id, nonce)` (documented to create a run even with `GITHUB_TOKEN`) → the resume run CASes
  `bound_run_id` and must check out `control_sha` (else `CONTROL_SHA_NOT_ON_MAIN`).
- **What may resume.** Validation and publication only. Resume runs execute `control_sha` code.
- **Writers.** Observation and terminal transitions may be written by newer trusted `main` code that supports
  the record's `schema_version` (ADR D1).
- **Provenance cost.** Run provenance is verified once per record per job, using public run metadata (about
  1 request per record). A verified prefix may be memoized within a job only, never persisted as authority.

| State (owning run concluded) | Definitive facts | Action |
|---|---|---|
| AUTHORIZED | result artifact present | T2 |
| AUTHORIZED | executor `skipped` | TF `EXECUTION_NOT_STARTED` |
| AUTHORIZED | executor concluded, no artifact | TF `EXECUTION_NOT_COMPLETED` |
| RESULT_BOUND | receipt present | T3 |
| RESULT_BOUND | no receipt, attempts < 2, unexpired | validation resume |
| RESULT_BOUND | otherwise | TF `VALIDATION_UNAVAILABLE` / `RESULT_TRANSPORT_EXPIRED` |
| VALIDATED | conforming remote head | T4 |
| VALIDATED | foreign head | TF `REMOTE_BRANCH_CONFLICT` |
| VALIDATED | absent, attempts < 3, unexpired, before deadline | publication resume |
| VALIDATED | otherwise | TF `PUBLICATION_UNAVAILABLE` / `RESULT_TRANSPORT_EXPIRED` / `LIFECYCLE_DEADLINE_EXCEEDED` |
| PUBLISHED | open Draft PR at head | T5 |
| PUBLISHED | Pre-PR non-success at head | TF `CANDIDATE_PREFLIGHT_FAILED` |
| PUBLISHED | past deadline | TF `CANDIDATE_PREFLIGHT_TIMEOUT` |
| any | verification fails | **freeze** `STATE_CORRUPT` (§7) |
| any | anchor integrity fails | **global freeze** `ANCHOR_INTEGRITY_FAILED` (§7) |
| state before VALIDATED but the authorization's branch exists, or state before PUBLISHED but its PR exists | contradiction | **freeze** `STATE_ROLLBACK_SUSPECTED`: no automated write; owner quarantine. Forward facts never skip a state whose evidence is missing. The adjacent cases (VALIDATED + conforming branch, PUBLISHED + PR) are the normal crash windows handled by T4 and T5. |

## 7. Corruption, rollback, quarantine, retention

- **Freeze.** Any failed verification, or `STATE_ROLLBACK_SUSPECTED`, freezes the Issue. No automated write follows. The owner recovers by
  dispatching `quarantine`. A new generation branch `hunter-state/v1/issue-<n>-g<k+1>` starts, and its root
  record (K_STATE-signed) names the frozen branch's head commit and every authorization id readable from it,
  so they stay in the replay set. The frozen branch cannot be deleted under the anchor and remains as
  evidence. Readers start at generation 1 and follow successors. A successor is a generation branch whose root record
  verifies (K_STATE, provenance, anchor) **and** names the predecessor's frozen head. Branches with forged
  roots are ignored, so squatting a generation name costs at most a different suffix. Two valid successors
  of the same predecessor → freeze.
- **Rollback (OD-4).** It is impossible while the anchor is intact. GitHub rejects every non-fast-forward
  and every delete, for `GITHUB_TOKEN` and for the repository admin (S0 live). If the anchor is weakened
  (ruleset edited, disabled, evaluate-mode, deleted or replaced), the change is detected through the pinned
  id and `updated_at` and freezes everything. Defence in depth: safety still does not depend on the ref:
  - the model starts only in the original run, at attempt 1;
  - publication is deterministic and create-only;
  - the PR rule reads GitHub's PR list;
  - replay needs a re-run, which is refused.

  A rollback that contradicts forward facts is detected and frozen (§6); one that does not is
  indistinguishable from a crash window, and is handled as one.
- **Retention.** State refs are kept indefinitely; they are small and non-secret. Transport artifacts last
  1 day and are deleted on terminal.
- **Key rotation.** A new key id is added to the pinned roots. Old records stay verifiable under their old
  ids and are never re-signed.

## 8. Exact-head receipt reuse (`docs/VALIDATION_STAGE_CONTRACT.md`)

| Proof | Stage | Identity | Reuse |
|---|---|---|---|
| Validator receipt | `pre-push-safety` (credential-free) | `(authorization_id, execution_id, ciphertext_sha256, result_sha256, tree_sha, unsigned_commit_sha, base_sha, task_scope_sha256, validation_definition, toolchain_sha256)` | Reused by every publication attempt of the same authorization; the publisher never re-runs gates. Refused on any identity mismatch, per the contract's invalidation table. Never reused across authorizations. |
| Hosted full proof | `hosted-full-exact-head-proof` | exact head SHA | A publication retry yields the identical head, so the existing run applies. Any other head needs its own run. Never synthesized, and never re-run for reassurance. |

The publisher's structural re-check and its unsigned-commit equality check are publication preconditions.
They are not a second validation proof.

## 9. Owner-authorized fresh execution

- **When allowed.** After FAILED, or after COMPLETED once its PR is closed.
- **How.** The owner removes and re-applies `hunter-agent-execute`. That produces a new `updated_at`, which
  gives a new id, a new branch, new execution and publication identities, and a new handoff and result.
- **What does not create one.** Nothing is reused from earlier attempts. Re-runs, reconcile, edits,
  comments, and non-owner labels never create a fresh execution.

## 10. Closed failure vocabulary

**Durable failure codes:**

- `BASE_NOT_ON_MAIN`
- `REMOTE_BRANCH_CONFLICT`
- `EXECUTOR_RESULT_TIMEOUT`
- `EXECUTOR_RESULT_REJECTED`
- `EXECUTION_NOT_STARTED`
- `EXECUTION_NOT_COMPLETED`
- `SECRET_IN_RESULT`
- `TRANSPORT_INTEGRITY_FAILED`
- `PRE_PUSH_SAFETY_FAILED`
- `VALIDATION_UNAVAILABLE`
- `RESULT_TRANSPORT_EXPIRED`
- `PUBLICATION_UNAVAILABLE`
- `PUBLICATION_REJECTED_BY_PLATFORM`
- `OWNER_WITHDREW`
- `ISSUE_CLOSED`
- `ISSUE_CHANGED_AFTER_AUTHORIZATION`
- `LIFECYCLE_DEADLINE_EXCEEDED`
- `CANDIDATE_PREFLIGHT_FAILED`
- `CANDIDATE_PREFLIGHT_TIMEOUT`
- `CONTROL_SHA_NOT_ON_MAIN`

**Summary-only codes** (no durable write; the actor writes no state or refuses before any write):

- the pre-authorization refusals in §5;
- `MISSING_PUBLICATION_CREDENTIAL` (publisher). A persistently unconfigured publisher ends durably as
  `PUBLICATION_UNAVAILABLE`.

**Executor advisory codes:** `PROVIDER_UNAVAILABLE`, `PROVIDER_QUOTA`, `MODEL_TIMEOUT`, `NO_CHANGES`,
`MISSING_CONFIGURATION`. These map to `EXECUTION_NOT_COMPLETED`, `EXECUTOR_RESULT_TIMEOUT` or
`EXECUTOR_RESULT_REJECTED`.

**Freeze codes** (not FAILED; no automated write; owner quarantine, §7): `STATE_CORRUPT`, `STATE_ROLLBACK_SUSPECTED`, `ANCHOR_INTEGRITY_FAILED`.

**Retired:** `PROCESS_RESTART` and the revision-1 `AUTHORIZATION_INCOMPLETE` (CLAIMED is removed).
