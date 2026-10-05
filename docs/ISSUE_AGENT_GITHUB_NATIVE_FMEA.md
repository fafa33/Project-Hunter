# Issue Agent GitHub-Native Threat Model and FMEA

Status: Proposed with [ADR 0037](ADR/0037-github-native-issue-agent-execution.md), revision 3. **Accepted** 2026-10-04 with ADR 0037; binding.
Not implemented.

Terms are defined in ADR 0037 and `docs/ISSUE_AGENT_GITHUB_NATIVE_STATE_MACHINE.md` (SM). Each
security-critical guard maps to an adversarial proof `AT-*` (§6). A guard without a proof that fails under
its named mutant is not "prevented".

## 1. Assets

| Asset | Why it matters |
|---|---|
| K_AUTH, K_SH, K_SPM, K_STATE | forge an owner authorization, Source Handling authority, prompt envelope, or lifecycle state |
| Publisher SSH signing key and push token | produce *verified* commits under a canonical signer identity (OD-2) and write branches |
| Model API key | spend and abuse |
| X_EXEC, X_RESULT | decrypt sealed transport within its 1-day window |
| State-chain integrity | no false success, no rerun, exactly one branch and one PR |
| Confidential payloads | prompt/handoff, pre-publication result, INTERNAL-classified Issue body |
| `main` + workflow definitions | the trust root of all job code and of K_STATE provenance |

## 2. Trust boundaries

```text
[Owner] --label--> issues event --> control VM (trusted @control_sha; K_AUTH/K_SH/K_SPM/K_STATE)
      sealed handoff (X_EXEC, AAD-bound) │                 ▲ signed+provenance-checked CAS state
                                         ▼                 │
                   executor VM (hostile model as isolation uid; model key + X_EXEC only; token {})
                                         │ sealed result (X_RESULT, AAD-bound) + GitHub artifact metadata
                                         ▼
                   validator VM (trusted intake → credential-free isolation uid; token {})
                                         │ receipt (digests incl. unsigned_commit_sha)
                                         ▼
                   publisher VM (trusted code only; signing key + push token + X_RESULT; no candidate code)
                                         │ deterministic signed head, create-only
                                         ▼
     Pre-PR Preflight → Candidate PR workflow → one Draft PR → governance → owner merge
```

| Topic | Analysis and control |
|---|---|
| **Workflow tampering** | `issues` and `schedule` always run the default-branch file. **`workflow_dispatch` can run any branch's file** (GitHub docs), so environment branch policy `main` is load-bearing (A-4) and is backed by an in-code `github.ref` assertion. The publisher token lacks `workflows`, so candidates cannot change workflows. Actions are SHA-pinned. Every job checks out `control_sha`, which must be an ancestor of `main`. |
| **Untrusted model output** | Closed schema, strict UTF-8, canonical paths, TaskScope; never executed under credentials; never placed in metadata; never logged. |
| **Compromised runner** | Scope is one job's VM (A-12). Executor: model key + X_EXEC + `ACTIONS_RUNTIME_TOKEN` (artifacts/cache in this run). **No lifecycle job uses the cache**, which closes cache poisoning. Its artifacts are hostile by design and bound by GitHub-reported identity. Validator: X_RESULT; it may forge a receipt, but the publisher re-derives the tree and unsigned commit independently, and hosted Pre-PR plus Candidate Admission re-establish everything. |
| **`GITHUB_TOKEN`** | Minimum per job. Executor and validator have `{}`. Write scopes only in control jobs, which run no untrusted code. **Other repository workflows with `contents: write`** (and every PAT/connector of `fafa33`) can also move `refs/hunter/**`. They cannot forge K_STATE signatures or run provenance. Under the anchor ruleset they cannot roll back or delete state either (AR-1, live, admin included). The worst case is appending garbage, which freezes the Issue (liveness only). |
| **Environment secrets** | Disjoint across four environments, branch policy `main`, no reviewers, step-scoped `env:` only. The isolation uid cannot read runner `/proc/*/environ` (A-6). Masking is never relied on. |
| **OIDC** | Unused; `id-token: write` is forbidden by a static guard. Optional Sigstore hardening for R-1 is in ADR alternatives. |
| **Publisher identity** | Canonical policy: verified signature, `authorized_signers` = {`claude`, `fafa33`}, single bound writer for author and committer (DFF-010). Only `fafa33` can register the signing key for its bound email, so the key's custody on hosted runners is OD-2. The commit is deterministic, so retries are idempotent. Persistent verification means a later key rotation does not un-verify published commits (GitHub docs). |
| **Code-write path** | The pre-existing paths are unchanged. `local_git_push` stays behind `.githooks/pre-push`, and direct `github_contents_api` / `github_git_data_api` code writes stay forbidden. OD-3 added two separate, least-privilege grants, validated by `validate_issue_agent_code_write_paths` against exact canonical values. **`issue_agent_publisher`**: only the lifecycle `publish` job, in the publisher environment, on `main`, at attempt 1, signed by the OD-2a writer; create-only publication of one commit to `refs/heads/issue-<n>-<16 hex>`, bound to the validator's `unsigned_commit_sha`; no hooks or candidate code with credentials. **`issue_agent_state_ledger`**: non-code, append-only `refs/heads/hunter-state/v1/` records under the anchor ruleset. |
| **Parallel authority (B-8)** | `agent-fallback-run`, `opencode_provider_runtime` and n8n co-locate execution with `HUNTER_AGENT_GITHUB_PUSH_TOKEN`. Each is retired: guarded fail-closed before go-live, deleted after proof, and the token revoked (OD-6). |

## 3. Failure-mode matrix

Proofs map to §6. Residual risks are in §3.15.

### 3.1 Triggers, replay, edits, concurrency

| ID | Failure mode | Guard | Outcome | Proof |
|---|---|---|---|---|
| F-01 | duplicate `issues:labeled` | single-commit AUTHORIZED CAS + replay set | `DUPLICATE_AUTHORIZATION` / `ISSUE_EXECUTION_ACTIVE` | AT-01 |
| F-02 | re-run of any job | `run_attempt == 1` bound in state and in provenance | `RERUN_REFUSED`; a record from a re-run is invalid | AT-02, P-A9 |
| F-03 | relabel while active or with open Draft PR | active pointer + PR list | refused | AT-03 |
| F-04 | non-owner, wrong label, PR-as-Issue, closed | `if:` + code check | `NOT_ELIGIBLE` | AT-04 |
| F-05 | Issue edited, closed, or label removed before publish | publish-time live gate | `ISSUE_CHANGED_AFTER_AUTHORIZATION` / `ISSUE_CLOSED` / `OWNER_WITHDREW` | AT-05 |
| F-06 | concurrent dispatch across Issues | soft cap | `ADMISSION_CAP_REACHED` | AT-06 |
| F-07 | concurrency group drops a pending run | `queue: max`; nothing durable before T1 anyway | refused by CAS, never lost silently | AT-07 |
| F-08 | stale base, incomplete TaskScope | before any write | `BASE_NOT_ON_MAIN` / `SCOPE_INCOMPLETE` | AT-08 |

### 3.2 SPM/DPM lineage (B-3)

| ID | Failure mode | Guard | Outcome | Proof |
|---|---|---|---|---|
| F-09 | executor runs without a valid handoff, or provider-direct | executor checks AAD, then `handoff_sha256`, then the K_SPM envelope against AUTHORIZED | model not launched | AT-09 |
| F-10 | DPM/ECA context absent | `dpm_context_sha256` required in the manifest | `COMPILATION_REFUSED` | AT-10 |
| F-11 | SH preflight after claim (DFF-023) | SH evaluated in memory and committed in the same AUTHORIZED commit | no write on refusal | AT-11 |
| F-12 | exact reconstruction falsely claimed | `EXACT_RECONSTRUCTION_UNAVAILABLE`; exact-proof consumers fail closed | explicit | AT-12 |
| F-13 | prompt inputs drift between compile and execution | prompt-input manifest + compiler identity + prompt digest bound before release; immutable across the chain | binding change invalid | AT-12, P-A8 |

### 3.3 Durable state (B-2, B-7)

| ID | Failure mode | Guard | Outcome | Proof |
|---|---|---|---|---|
| F-14 | concurrent writers | lease CAS | one wins | AT-13, P-A5, L-1 |
| F-15 | lost ACK after a state push | idempotency read-back | success, no duplicate | AT-14 |
| F-16 | unauthorized signer writes a transition | pinned K_STATE key ids | invalid → freeze | AT-15, P-A1 |
| F-17 | tampered record | signature | invalid | AT-15, P-A2 |
| F-18 | replayed record (other Issue or other position) | bindings + chain + seq | invalid | AT-15, P-A3, P-A4 |
| F-19 | keyed but illegal transition (skip, re-apply, early COMPLETED) | transition table + required evidence | invalid | AT-24, P-A5b, P-A6, P-A7 |
| F-20 | valid key used from an untrusted workflow or a re-run | run provenance check | invalid | AT-44, P-A9 |
| F-21 | rollback to an older valid head | **anchor ruleset: forward-only, no bypass** (AR-1 live) + anchor-integrity check (AR-3) + forward-fact contradiction freeze | rollback rejected by GitHub; any anchor weakening → `ANCHOR_INTEGRITY_FAILED` | AT-16, AT-48, AT-49 |
| F-21a | admin weakens the anchor (disable, evaluate mode, edit, delete or recreate), then rolls back | pinned ruleset id + `updated_at` + enforcement + rules + branch attribution, authenticated reads | global freeze; never silent | AT-48 (anchor prototype, 9 cases, 5/5 mutants killed) |
| F-21b | anonymous or cached read hides anchor tampering | authenticated reads only (AR-3: anonymous was stale) | — | AT-48 |
| F-21c | forward forgery: a garbage commit appended by any `contents: write` holder | chain + K_STATE + provenance verification | freeze `STATE_CORRUPT` (liveness only); quarantine = new generation branch | AT-15 |
| F-21d | relying on tag rulesets | not used (AR-2 falsified) | — | AT-41 (static: state namespace is `refs/heads/hunter-state/**` only) |
| F-22 | secret or content written to state | closed-type schema | refused | AT-17 |
| F-23 | artifact or cache treated as state | static guard; `advance` reads only the ref + API facts | — | AT-18 |

### 3.4 Executor

| ID | Failure mode | Guard | Outcome | Proof |
|---|---|---|---|---|
| F-24 | `/proc` credential theft | isolation uid + `env -i` (PRH-068) | — | AT-19 |
| F-25 | planted `.git`/hooks; lingering processes | trusted git dir; kill-all | — | AT-19 |
| F-26 | model key exfiltrated into the result | exact and encoded scan | `SECRET_IN_RESULT` | AT-20 |
| F-27 | timeout, cancellation | wall clock; `finalize` `if: always()` (documented to run on cancel) | `EXECUTOR_RESULT_TIMEOUT` / `EXECUTION_NOT_COMPLETED` | AT-21, AT-23 |
| F-28 | provider outage, quota, missing key | advisory only; no re-dispatch | FAILED; owner relabels | AT-22 |
| F-29 | executor cache poisoning | no cache anywhere | — | AT-18 |

### 3.5 Transport (B-1)

| ID | Failure mode | Guard | Outcome | Proof |
|---|---|---|---|---|
| F-30 | envelope from another authorization, Issue, base, TaskScope, execution, or schema | AAD equality before decrypt | `TRANSPORT_INTEGRITY_FAILED` | P-T1 |
| F-31 | AAD rewritten to look legitimate | AEAD tag over the AAD | refused | P-T2 |
| F-32 | corrupt or truncated ciphertext; wrong recipient | AEAD; GitHub `digest-mismatch: error` | refused | P-T3, P-T4 |
| F-33 | executor misdeclares the plaintext digest | recompute after decrypt | refused | P-T5 |
| F-34 | missing or expired artifact | definitive 404 / expiry | `RESULT_TRANSPORT_EXPIRED` / `EXECUTION_NOT_COMPLETED`; **no model rerun** | AT-24 |
| F-34a | duplicate same-name artifacts (A-5 falsified live) | exactly-one-at-bind; bind and download by artifact id; `digest-mismatch: error` | `TRANSPORT_INTEGRITY_FAILED` | AT-50 |

### 3.6 Lost ACK, callbacks, reruns, outages

| ID | Failure mode | Guard | Outcome | Proof |
|---|---|---|---|---|
| F-35 | crash before or after each of the 10 transition writes (T1–T5) and each push, upload, or PR create | SM §5 + §6 | deterministic; never COMPLETED without an observed PR | AT-24 |
| F-36 | stale, mismatched, or replayed actor | CAS + binding + provenance + nonce | refused | AT-25, AT-26 |
| F-37 | API 5xx/429/timeout | definitive facts only | no-op | AT-27 |
| F-38 | Actions outage or queue starvation | deadlines; reconcile after recovery | FAILED with deadline codes | AT-28 |

### 3.7 Validator

| ID | Failure mode | Guard | Outcome | Proof |
|---|---|---|---|---|
| F-39 | candidate tool executes with a secret (DFF-028) | isolation uid, `env -i`, no network, token `{}`, X_RESULT intake-only | — | AT-29 |
| F-40 | wrong Issue, base, TaskScope, result, tree, or commit binding | receipt vs record | refused | AT-30 |
| F-41 | stale receipt reuse | contract invalidation table | refused | AT-31 |
| F-42 | path and encoding attacks | `validate_replacement_result` hardened | `EXECUTOR_RESULT_REJECTED` | AT-32 |
| F-43 | candidate content in logs | codes and counts only | — | AT-33 |

### 3.8 Publisher (B-5)

| ID | Failure mode | Guard | Outcome | Proof |
|---|---|---|---|---|
| F-44 | candidate code under credentials (PRH-067, **live on main**) | plumbing only; no hook; static + runtime guard | — | AT-34 |
| F-45 | validated ≠ published | tree + `unsigned_commit_sha` byte equality | refused | AT-35 |
| F-46 | lost push ACK | deterministic head (L-2) | success | AT-36 |
| F-47 | foreign branch head | empty lease, never force | `REMOTE_BRANCH_CONFLICT` | AT-36 |
| F-48 | duplicate PR / lost PR ACK | adopt-by-head; GitHub uniqueness | one PR | AT-37 |
| F-49 | partial publication | PUBLISHED non-terminal | COMPLETED or `CANDIDATE_PREFLIGHT_*` | AT-38 |
| F-50 | workflow-file candidate | token without `workflows` | `PUBLICATION_REJECTED_BY_PLATFORM` | AT-39 |
| F-51 | weaker signing identity substituted | signer ∈ `authorized_signers`, `verification.verified`, signer = `writer_login` | refused at T4 and at admission | AT-45 |
| F-52 | signing-key compromise | publisher environment only; signing-only key; revocable; persistent verification keeps history | residual (OD-2) | — |

### 3.9 GitHub trust surface

| ID | Failure mode | Guard | Outcome | Proof |
|---|---|---|---|---|
| F-53 | `workflow_dispatch` on a non-main ref with a modified file reaches secrets | environment branch policy `main` (A-4) + code assertion | job fails; no secrets | AT-40 |
| F-54 | permission or `control_sha` drift in later edits | static workflow guard | merge blocked | AT-41 |

### 3.10 Confidentiality

| ID | Failure mode | Guard | Outcome | Proof |
|---|---|---|---|---|
| F-55 | prompt, result, Issue body, or authorization on any public surface | sealed transport; closed vocabulary; leak canary | — | AT-33 |
| F-56 | X_EXEC/X_RESULT compromise | 1-day retention, deletion on terminal, rotation | residual | — |

### 3.11 to 3.14 Migration, canary, retirement, parallel authority

| ID | Failure mode | Guard | Outcome | Proof |
|---|---|---|---|---|
| F-57 | dual authority (Railway + GitHub) | Railway suspended before live; trigger without a Railway client | single authority | AT-42 |
| F-58 | rollback re-enables Railway | rollback = disable workflows | no execution path | AT-42 |
| F-59 | hidden Railway dependency | retirement graph guard + canary with Railway suspended | proof | AT-43 |
| F-60 | parallel Issue-agent authority (`agent-fallback-run`, `opencode_provider_runtime`, n8n transport/canary) | fail-closed guard before live; deletion after proof; token revoked (OD-6) | exactly one path | AT-46 |
| F-61 | Issue-agent publication outside `CODE_WRITE_POLICY` | governed path entry (OD-3) | governance-consistent | AT-47 |

### 3.15 Residual risks (owner acknowledgement required)

| ID | Residual risk |
|---|---|
| R-1 | **Resolved (OD-4).** Rollback and deletion are rejected by GitHub (AR-1). Anchor weakening is detected (AR-3). What remains is liveness only: a forward-forgery freeze, or a governed re-anchor after a ruleset change. |
| R-2 | Kernel escape on the executor or validator VM, bounded to that job's secrets. |
| R-3 | Label removed after the publish-time check. The Draft PR grants nothing. |
| R-4 | Best-effort cron. Latency only. |
| R-5 | Custody of the signing-only publisher key on hosted runners (OD-2a accepted; GitHub `verified` proven, A-3). Mitigations: publisher environment only, signing-only key, revocable, persistent verification keeps history. |
| R-6 | Railway variables hold INTERNAL-classified Issue bodies (`HUNTER_ISSUE_441/457/461_BODY_B64`) and both signing keys. Remove and rotate them in the migration (Plan §6). |

## 4. Platform assumptions: S0 evidence (B-4, OD-1)

Sandbox: public, disposable `fafa33/hunter-560-s0-sandbox` (no production authority), 2026-10-03/04 UTC.

Legend:

- **PROVEN-LIVE** — mechanically proven on GitHub-hosted infrastructure.
- **DOC** — primary documentation.
- **LOCAL** — proven on reference Git or OpenSSH only.
- **FALSIFIED** — an assumption shown false; the ADR was reopened and the design changed in revision 3.
- **OPEN** — not yet proven.

| ID | Assumption | Status | Exact evidence |
|---|---|---|---|
| A-1 | lease push is an atomic server-side CAS; the empty lease is create-only | **PROVEN-LIVE** | Run 37160408479 (`refs/hunter/…/issues/1`, 30 `GITHUB_TOKEN` writers): per observed head `none`/`5d1445d1…`/`ec67d3f2…` exactly **1 WIN** each; losers: 20 server-side `cannot lock ref`, 7 client-side `stale info`. Run 37161095475 (protected branch `hunter-state/issue-1`, chosen namespace): per observed head `none`/`cc27ff38…`/`a2b99531…` exactly **1 WIN** each; losers: 23 `cannot lock ref`, 4 `stale info`. Local L-1 agrees. DOC: REST create-ref returns 409 if the ref exists, and `force=false` must fast-forward. |
| A-2 | state pushes trigger no workflows; the store is not a branch; anonymous read | **PROVEN-LIVE** (re-scoped) | Witness workflow `on: [push, create, delete]`: **zero** runs for any of the dozens of `GITHUB_TOKEN` pushes to `refs/hunter/**`, `refs/heads/hunter-state/**` and tags. An owner SSH push to custom ref `refs/hunter/issue-agent/v1/issues/77` (23:17:31Z) → zero runs. Owner pushes to a **branch or tag** do trigger, which is why only `GITHUB_TOKEN` may write state. Unauthenticated `git ls-remote` lists both namespaces. Custom refs are absent from `GET /branches`. In revision 3 the chosen store **is** a branch namespace (D2a), visible in the branch list, which is an accepted consequence. |
| A-3 | identical bytes give an identical SSH Ed25519 signature; GitHub `verified` for the bound writer | **PROVEN-LIVE** (GitHub) + LOCAL (determinism) | sandbox `fafa33/hunter-560-s0-sandbox`, branch `a3-signing-proof-1791072629`, commit `0a8e46496fa0645c6d447605234a12ae137e0330` (SSH signature; author and committer `Farhad5778 <34549283+fafa33@users.noreply.github.com>`, both resolving to login `fafa33`): GitHub `verification.verified=true`, `reason=valid`. Independently re-read on 2026-10-04 via the authenticated owner proof and the public commits API. The throwaway signing-only key (id 1218014) was deleted afterwards: the public `GET /users/fafa33/ssh_signing_keys` lists only the pre-existing key 1160850. Persistent verification keeps the commit Verified. Determinism: L-2 (two independent repositories → identical signed commit `605fa508…`). |
| A-4 | environment branch policy denies secrets on other refs, including `workflow_dispatch` | **PROVEN-LIVE** | Run 37160405977 (`main`): `with_env` secret=present sha12=`58430d29…`; `without_env` absent. Run 37160415098 (`untrusted-dispatch`, **attacker-modified workflow file**): `with_env` **failed**: "Branch "untrusted-dispatch" is not allowed to deploy to s0-protected due to environment protection rules"; `without_env` secret=absent. DOC: `workflow_dispatch` can target any branch's workflow file. |
| A-5 | unique immutable artifact names; API id and digest; cross-run with `actions: read`; digest check; 1-day retention | **PARTLY FALSIFIED → redesigned (D3)** | **Falsified:** run 37160404361 `up2` same-name upload **succeeded**, so two artifacts named `s0-transport` (ids 11287177370, 11286819451) existed in one run; name download returned the first (sha `b6ac9844…` = `up1`). Repeated in run 37161093881. **Proven:** REST `digest` = upload output `artifact-digest` (`sha256:8b634e31…`); `expires_at` = created + 24 h; cross-run download by run-id returned sha `b6ac9844…` (= source payload). Redesign: bind by **artifact id**, exactly-one-at-bind, `artifact-ids` download, `digest-mismatch: error`. |
| A-6 | isolation uid: no sudo, cannot read runner `/proc/*/environ`, network deniable | **PROVEN-LIVE** | Run 37161092362 (ubuntu 24.04, kernel 6.17.0-1022-azure, yama ptrace_scope=1): `environ` and `mem` of `Runner.Listener` (pid 2266), `Runner.Worker` (2284) and the step `bash` (2328) all **denied**; `sudo -n` denied; `docker ps` denied; canary secret not found in any file readable by the uid (`/home/runner` is mode 750); `iptables`/`ip6tables` owner REJECT → uid HTTPS **blocked** and DNS **blocked**, runner HTTPS allowed. Caveat recorded: the runner uid has passwordless sudo and the `docker` group, so any escape to the runner uid is root on that single-job VM (bounded by A-12). |
| A-7 | `GITHUB_TOKEN` `workflow_dispatch` creates a run | DOC | `GITHUB_TOKEN` doc: "workflow_dispatch and repository_dispatch events always create workflow runs" |
| A-8 | `always()` runs on cancel | DOC | expressions doc: "returns true, even when canceled" |
| A-9 | limits / volume | DOC | 20/40 concurrent jobs (Free/Pro); `GITHUB_TOKEN` 1,000 req/h/repo; 50 re-runs per run; `queue: max` ≤ 100 pending. S0 itself queued 30-job matrices without issue. |
| A-10 | free hosted runners for public repositories | DOC | billing doc |
| A-11 | job 6 h, token 6 h, cron best-effort and disabled after 60 days | DOC (hosted queue limit undocumented; not load-bearing: bounded by the lifecycle deadlines) | limits, `GITHUB_TOKEN` and events docs |
| A-12 | fresh VM per job | **PROVEN-LIVE** | Run 37160401830: `boot_id` j1 `6b482390…` / j2 `9329de44…` / j3 `1045b6df…` (all distinct); SSH host keys distinct (`268254c8…` vs `53d039b6…`); markers in `/tmp` and `/root` written by j1 were **absent** in j2. (The `machine_id` and hostname are image-constant and are not evidence of reuse.) |
| **AR-1** (OD-4) | the anchor ruleset makes state branches forward-only for everyone | **PROVEN-LIVE** | Runs 37160407257 and 37161196276 (`GITHUB_TOKEN`): branch force rollback, lease rollback and delete → `rule violations`; fast-forward accepted; head unchanged. Admin SSH force rollback → `GH013: Repository rule violations … push declined`; admin delete → rejected. `current_user_can_bypass: never`. **Control:** the same rollback on custom ref `refs/hunter/…/9` was **accepted**. |
| **AR-2** | tag rulesets as an anchor | **FALSIFIED** | Tag ruleset 24433841 (`active`, deletion+update+non_fast_forward, no bypass): `GITHUB_TOKEN` tag force-move and delete **accepted**; admin tag create, force-move and delete **accepted**. Not used. |
| **AR-3** | anchor tampering is detectable by trusted jobs | **PROVEN-LIVE** (authenticated reads only) | Admin `enforcement` disabled → active: `GITHUB_TOKEN` read `updated_at` moved `2026-10-03T23:01:13.071Z` → `23:15:23.241Z` (run 37161196276); while disabled, `rules/branches/hunter-state/issue-9` returned `[]`. **Anonymous** `GET /rulesets/{id}` stayed stale (cached), so anonymous reads are forbidden for this check. Rule-suites are admin-only (anonymous 401) and not used. |

**Conclusion.**

- **Falsified, and the design changed (ADR reopened, revision 3):** A-5 name uniqueness, AR-2 tags, and
  revision-2 custom-ref anti-rollback.
- **Proven live:** A-1, A-2, A-3, A-4, A-6, A-12, AR-1, AR-3.
- **Documented:** A-7 to A-11.
- **Open:** none that are load-bearing. The undocumented hosted queue limit in A-11 is bounded by the
  lifecycle deadlines.

## 5. Local mechanical evidence (this session; design proof, not implementation)

The scripts are throwaway, under the session scratchpad. They were not committed and are not part of the
repository.

### 5.1 State-integrity adversarial proof (B-7)

A prototype verifier implements ADR D2 checks 1–5 over a **real local bare Git ref** with lease CAS. All
26 cases behaved as designed:

| Case | Attack | Result |
|---|---|---|
| P-legit | AUTHORIZED → RESULT_BOUND by K_STATE | verifies |
| P-A1 | unauthorized key writes VALIDATED with a correct lease | **rejected**: signer key not pinned |
| P-A2 | field changed after signing | **rejected**: bad signature |
| P-A3 | valid record from Issue 521 spliced into Issue 520 | **rejected**: binding changed |
| P-A4 | earlier valid record re-appended | **rejected**: non-monotonic seq |
| P-A5 | two keyed writers decide from the same stale head | **exactly one** CAS winner |
| P-A5b | keyed stale writer re-applies VALIDATED with another tree | **rejected**: illegal transition |
| P-A6 | RESULT_BOUND → PUBLISHED skip | **rejected** |
| P-A7 | COMPLETED without Draft-PR evidence | **rejected**: missing evidence |
| P-A7b | COMPLETED right after RESULT_BOUND (**#558 P1 mutant**) | **rejected**: illegal transition |
| P-A8 | `base_sha` changed mid-chain | **rejected**: binding changed |
| P-A9 / A9b | valid key, record from a re-run attempt or an untrusted workflow | **rejected**: provenance |
| P-A10 | force-push to an older valid head (revision-2 custom-ref model) | accepted as a valid prefix. **Superseded:** revision 3 moves state to the anchored branch, where the same attack is rejected live (AR-1). |
| P-T0–T5 | sealed transport: bound open; AAD bound to another authorization, Issue, base, TaskScope, execution or schema; AAD rewritten; corrupt ciphertext; wrong recipient; lying plaintext digest | open succeeds only for T0; **all others rejected** |

**Mutation run.** Each guard was disabled in turn, and each mutant turned at least one case red:

| Mutant | Cases turned red |
|---|---|
| accept an unpinned signer | 1 |
| drop the transition table | 3 |
| drop provenance | 2 |
| drop immutable bindings | 2 |
| drop AAD equality | 6 |
| blind push without a lease | 1 |

### 5.2 Platform-adjacent local proofs

| Proof | Result |
|---|---|
| L-1 | Git CAS semantics on `refs/hunter/issue-agent/v1/issues/1` (Git 2.50.1): see A-1 |
| L-2 | Deterministic SSH-signed commit (OpenSSH 10.3): see A-3 |

### 5.3 Anti-rollback anchor proofs (OD-4)

- **Live (GitHub).** AR-1, AR-2 and AR-3 in §4: rollback and delete rejected for `GITHUB_TOKEN` and for the
  admin; tag anchor falsified; tamper visible through authenticated `updated_at` and `rules/branches`.
- **Anchor-integrity verifier (local prototype).** 9 cases, all PASS:
  - intact anchor;
  - disabled;
  - toggled (`updated_at` moved);
  - deleted and recreated identically;
  - `non_fast_forward` removed;
  - branch outside the pattern;
  - evaluate (audit-only) mode with rules still listed;
  - ruleset object and branch rules citing different ids;
  - ruleset object lost a rule while a stale branch-rules read still lists it.

  **Mutation run:** each of the 5 checks (id, enforcement, `updated_at`, ruleset rules, branch attribution)
  disabled in turn → each turned exactly one isolating case red. The first two mutants initially
  **survived**, and isolating cases were added until every mutant was killed.

## 6. Adversarial and mutation proof map (implementation slices)

Harness:

- a local bare remote for refs and CAS (extending `tests/test_issue_agent_candidate_topology.py`);
- a fake GitHub API: run metadata, job conclusions, artifacts with digests, PRs, Issue; 5xx/429/timeout
  injection; dropped ACKs;
- crash injection before and after every durable write, upload, push and PR create.

Every guard names a **mutant** that must turn at least one test red. A surviving mutant is blocking.
Parser and validator guards need paired negative and positive fixtures (CLAUDE.md adversarial bypass).
The P-* and L-* scripts above become the seeds for AT-13 to AT-17, AT-44 and the transport tests.

| Proof | Covers | Mutant that must fail it |
|---|---|---|
| AT-01 | duplicate event → one AUTHORIZED | drop replay-set check |
| AT-02 | rerun refused in every job | accept attempt > 1 |
| AT-03 | relabel while active / open PR | drop active or PR check |
| AT-04 | eligibility (both layers) | remove code check |
| AT-05 | Issue edit, close or label removal before publish | skip live gate |
| AT-06 | admission cap | ignore cap |
| AT-07 | queued duplicates refused, none silently lost | claim before eligibility |
| AT-08 | stale base and incomplete scope (paired valid accepted) | skip check |
| AT-09 | no valid handoff → no model; provider-direct refused | skip AAD, digest or envelope check |
| AT-10 | DPM consumed before implementation | allow empty DPM digest |
| AT-11 | SH preflight before any write | write first |
| AT-12 | manifest, compiler identity and prompt digest bound; exact-proof consumer fails closed | omit any field |
| AT-13 | N-writer CAS race → one winner | push without lease |
| AT-14 | lost ACK on every state write | re-apply after read-back |
| AT-15 | forged, tampered or replayed records → freeze | skip any verification |
| AT-16 | rollback cannot cause rerun, a second branch or a second PR; a contradiction with forward facts → freeze, never skip | trust the ref for the PR rule, or advance over missing evidence |
| AT-17 | content, secrets or free text refused | widen a field type |
| AT-18 | no cache; artifacts never state | allow `actions/cache` |
| AT-19 | isolation uid and planted `.git` (salvaged #558) | run model as the runner uid |
| AT-20 | model key exact or encoded in the result | raw-only scan |
| AT-21 | model timeout | remove wall clock |
| AT-22 | provider outage, quota or missing secrets per role; never re-dispatch; no value printed | auto-retry |
| AT-23 | cancellation → `finalize` | remove `always()` |
| AT-24 | crash at every injection point → SM §6; never COMPLETED without an observed PR | write COMPLETED at T2 (#558 P1) |
| AT-25 | stale actor (old run, wrong nonce, wrong artifact or receipt) | drop a comparison |
| AT-26 | duplicate resume dispatch | skip nonce CAS |
| AT-27 | API errors never produce transitions | treat error as absence |
| AT-28 | deadlines | remove deadlines |
| AT-29 | candidate step has no secret, token or network | run candidate as runner uid |
| AT-30 | receipt binding mismatch (each field) | skip a field |
| AT-31 | receipt reuse invalidation (each row) | ignore toolchain or definition |
| AT-32 | path and encoding corpus (paired) | drop each canonicalization rule |
| AT-33 | leak canary across stdout, stderr, summary, outputs, annotations, state and artifact names | print an exception message |
| AT-34 | publisher never executes candidate content (includes the PRH-067 regression on `main`) | invoke a hook |
| AT-35 | tree / unsigned-commit equality | drop equality |
| AT-36 | lost push ACK idempotent; foreign head conflict | force push or non-fixed dates |
| AT-37 | duplicate PR race → adopt | create without listing |
| AT-38 | branch without PR → T5 or deadline | PUBLISHED terminal |
| AT-39 | workflow-file candidate → platform refusal mapped | — (platform, S0/S6) |
| AT-40 | non-main dispatch gets no secrets | — (platform, S0) |
| AT-41 | static workflow guard (permissions, environments, SHA pins, `control_sha`, no `id-token`, no cache, `queue: max`) | loosen a rule |
| AT-42 | no Railway client in the trigger; rollback leaves no path | keep a Railway POST |
| AT-43 | retirement graph: nothing Railway reachable | re-add an import |
| AT-44 | provenance: a record from a non-allowlisted workflow, non-main branch, wrong head SHA, or attempt > 1 is invalid | drop provenance |
| AT-45 | signer ∉ `authorized_signers`, unverified, or signer ≠ writer → refused | accept unverified |
| AT-46 | `agent-fallback-run`, `n8n-canary`, `opencode_provider_runtime` and the n8n transport fail closed (and are later absent) | re-enable an entry point |
| AT-47 | `CODE_WRITE_POLICY` has the Issue-agent path and the artifact guard validates it | remove the entry |
| AT-48 | anchor integrity: intact passes; disabled, evaluate-mode, edited (`updated_at`), deleted/recreated, rules weakened, branch not covered, object/branch-rules mismatch → freeze | drop any single check (each killed in the S0 prototype) |
| AT-49 | live: rollback and delete of a state branch by `GITHUB_TOKEN` and by admin rejected; fast-forward accepted | — (platform; re-run in S6 against production with a scratch branch) |
| AT-50 | duplicate same-name artifacts at bind → refuse; post-bind duplicates ignored; download by id | bind by name |

## 7. #558 lessons not carried as registry records

These are recorded here as design lessons only (B-6):

- **PRH-069** (malformed JWT `aud`/`nbf`) and **PRH-071** (`job_workflow_ref` on direct workflows). They
  apply to an OIDC verifier that this design does not have. If OIDC is ever added (the R-1 hardening), they
  become mandatory acceptance tests for it.
- **PRH-070** (result admitted before handoff delivery). This was a race in Railway RAM role state. The
  invariant is preserved structurally: the handoff is bound in AUTHORIZED before model release, and T2
  binds only an AAD that names `handoff_sha256`. It is covered by AT-09 and AT-25.
