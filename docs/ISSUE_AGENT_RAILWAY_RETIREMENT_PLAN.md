# Issue Agent Railway Retirement Plan

Status: Accepted with [ADR 0037](ADR/0037-github-native-issue-agent-execution.md), revision 3, and
[ADR 0038](ADR/0038-source-handling-github-native-authority-store.md). Binding since 2026-10-04 (both
accepted, BLK-1 closed). Execution follows the slice order and gates below.

## 1. Rules

1. **One canonical path.** There is never a period in which Railway, a fallback runtime, or n8n and the
   GitHub-native lifecycle all accept, claim, execute or publish Issue-agent work. Railway execution has
   failed closed since #522. The parallel authorities are made fail-closed **before** the GitHub path goes
   live (B-8).
2. **Rollback never re-enables Railway** or a fallback. Rollback means disabling the lifecycle workflow,
   which leaves no execution path until a fix lands.
3. **Deletion only after the retirement proof.** Railway code, config, env, secrets and webhooks, and
   finally the subscription, are deleted only after the #520 canary succeeds with every Railway service
   suspended (§6).
4. **Untouched until their slice.** #520 until S7. #559 and #538/#553/#555/#556 throughout. #558 is closed
   unmerged by the owner (B-6).

## 2. RETAIN / ADAPT / DELETE

### 2.1 From merged `main`

| Component | Decision | Why | Slice |
|---|---|---|---|
| `src/hunter/task_scope.py` | RETAIN | sole scope authority | — |
| ingress / SPM / ECA / routing modules | RETAIN | canonical; called in `authorize` | S3 |
| `GovernedIssueAgentExecutionService` composition | ADAPT | in-job library; fallback dispatch removed | S3 |
| `IssueAgentExecutionLedger` (SQLite), lease/owner-instance, `recover_expired_on_startup`, `PROCESS_RESTART` | DELETE (S8); superseded S2/S5 | relocated to the signed ref chain; no long-lived instance (DFF-020/021) | S2→S8 |
| `derive_execution_target`, failure-code mapping | RETAIN / ADAPT | vocabulary per SM §10 | S2 |
| `scripts/hunter_issue_agent_trigger.py` | ADAPT | minting and eligibility kept; Railway POSTs removed | S5 |
| `scripts/issue_agent_edge_transport.py`, `hunter_issue_agent_issuer.py`, `hunter_issue_agent_ingress.py` | DELETE (S8) | Railway servers and transport | S8 |
| `scripts/hunter_issue_agent_provisioner.py` | ADAPT → library; server DELETE (S8) | SH provisioning inside `authorize` | S3/S8 |
| `provision_source_handling_issue_authority.py`, `bootstrap_source_handling_authority.py` | ADAPT | backend moves to the ref store under the ADR 0036 amendment (OD-5) | S3 |
| `validate_replacement_result`, `CandidateFile`, path helpers (#524) | RETAIN / ADAPT | bind to the state-record binding; harden the corpus | S4 |
| `validation_receipt` / `verify_validation_receipt` | ADAPT | add `execution_id`, `ciphertext_sha256`, `tree_sha`, `unsigned_commit_sha`, `task_scope_sha256`, `toolchain_sha256` | S4 |
| `build_signed_candidate_commit` | ADAPT | fixed identity, dates and identifier-only template (`Hunter-Authorization`/`-Execution`/`-Result` trailers), SSH Ed25519, unsigned-commit equality | S4 |
| `publish_create_only` | ADAPT | **remove the `_run_pre_push_safety` call (PRH-067, live on `main`)**; empty lease; lost-ACK identical-head success | S4 |
| `_run_pre_push_safety` (main version) | DELETE (S4) | replaced by the credential-free validator stage | S4 |
| `publisher_environment_is_safe`, `assert_rehearsal_has_no_publication_authority` | RETAIN | separation proofs | S4 |
| `ReplacementResultLedger` (SQLite) | DELETE (S8); superseded S2 | absorbed into the ExecutionRecord | S2→S8 |
| `hunter-issue-agent-trigger.yml` | ADAPT | the lifecycle workflow (ADR D1) | S5 |
| rehearsal workflow and script | ADAPT | stays structurally non-publishing; no publisher environment | S5 |
| `hunter-issue-agent-candidate-pr.yml` + script | ADAPT | requires PUBLISHED at head; adopt-by-head; records COMPLETED or `CANDIDATE_PREFLIGHT_*` | S5 |
| `hunter-issue-agent-issuer-deploy.yml`, `railway.toml`, `scripts/railway_*startup.py`, `railway_opencode_permission_sandbox.py` | DELETE (S8) | Railway | S8 |
| `opencode_provider_self_check.py` | DELETE (S8) | DFF-027: never authority | S8 |
| `scripts/install_opencode_runtime.py` | ADAPT | pinned OpenCode install on the executor runner | S4 |
| Railway-specific tests | DELETE / ADAPT (S8) | with their modules; non-Railway assertions migrated | S8 |
| `ISSUE_AGENT_EXECUTION_CONTRACT.md`, `ISSUE_AGENT_TRIGGER.md` | ADAPT | amended to ADR 0037 | S1 |
| `ISSUE_AGENT_ISSUER_DEPLOYMENT.md`, `N8N_AGENT_AUTOMATION.md`, `SMART_PROMPT_MACHINE_N8N.md` | ARCHIVE (S8) | historical | S8 |
| `CODE_WRITE_POLICY.json` | ADAPT (governed) | `issue_agent_publisher` code-write path (OD-3); signer per OD-2 | S1 |
| `DEFECT_REGISTRY.json` | ADAPT | §3 carry-forward; #558 P1 class; DFF-020/021 lifecycle (history never deleted) | S1/S8 |

### 2.2 Salvage from #558 (cherry-picked into slices; #558 then closed unmerged)

| #558 component | Decision | Why |
|---|---|---|
| isolation uid launch, `env -i` allowlist, kill-all, private home (PRH-068) | RETAIN | executor and validator boundary |
| trusted git-dir collection, hooks and fsmonitor off | RETAIN | planted-`.git` defence |
| `run_credential_free_candidate_safety`; publisher runs no hook; tree equality (PRH-067) | RETAIN / ADAPT | becomes the validator stage, extended to unsigned-commit equality |
| one active Draft PR per Issue in candidate-PR | RETAIN | F-48 |
| bounded bodies, closed vocabulary, no printing of content | RETAIN | F-55 |
| per-job secret scoping, default-branch checkout | ADAPT | `control_sha` pinning + sealed transport |
| OIDC verifier and single-use OIDC roles (PRH-069/071) | DELETE | no relying party |
| `HANDOFF_IN_FLIGHT` RAM role state (PRH-070) | DELETE | replaced by durable binding |
| Railway fetch/result routes, `GitHubHostedExecutionRuntime` on the issuer | DELETE | Railway channel |
| Railway issuer composition changes | DELETE | B-6: no Railway code preserved for history |

### 2.3 Parallel authorities (B-8 trace and decision)

**Trace** (import graph and entry points on `main`; no consumer found outside the Issue-agent/Railway
graph):

| Path | What it is | Parallel authority? | Decision |
|---|---|---|---|
| `python -m hunter agent-fallback-run` → `agent_fallback_runtime.OperationalAgentFallbackRuntime` → `agent_fallback.GovernedAgentFallbackDispatcher` | runs a fixed provider pool (codex/claude/freebuff/opencode/jules) from `HUNTER_AGENT_*_COMMAND`; success = remote HEAD advance | **yes**: executes models and relies on a co-resident `HUNTER_AGENT_GITHUB_PUSH_TOKEN`; bypasses the isolated validator and publisher | fail-closed guard (S5): the CLI entry and runtime refuse unconditionally (AT-46); DELETE (S8) |
| `opencode_provider_runtime.py` | OpenCode provider that pushes with `HUNTER_AGENT_GITHUB_PUSH_TOKEN` (`_push_trusted`) | **yes**: provider and push credential in one process (DFF-028 class) | fail-closed (S5); DELETE (S8) |
| `issue_agent_workspace.py` | Railway workspace runtime built on `agent_fallback_runtime` | **yes** (Railway execution) | DELETE (S8); already unreachable after S5 |
| `n8n.py` transport, `python -m hunter n8n-canary` | delivers SPM handoffs to an external n8n webhook that orchestrates providers | **yes**: an external always-on orchestrator path | fail-closed (S5); DELETE (S8) |
| `n8n_handoff.PromptAutomationEnvelopeHandoff` | the canonical SPM handoff **data type** | no; it is data, not authority | RETAIN (rename optional, out of scope) |
| External n8n workflows and any host holding `HUNTER_AGENT_GITHUB_PUSH_TOKEN` | outside the repository | **unknown to the repository** | owner revokes the token and disables the workflows before S7 (OD-6); revoking the token kills publication authority regardless of external code |

### 2.4 `CODE_WRITE_POLICY` entries (OD-3, least privilege; **accepted 2026-10-04**, committed in S1)

**Location.** The binding entries are `code_write_paths.issue_agent_publisher` and
`code_write_paths.issue_agent_state_ledger` in `docs/CODE_WRITE_POLICY.json`. They are closed, structured
contracts, validated semantically by `validate_issue_agent_code_write_paths` in the Defect Prevention Guard,
with type-exact comparison and no JSON `1`/`true` coercion. The existing `local_git_push` path and the
prohibition on `github_contents_api` / `github_git_data_api` are unchanged.

**Invariants the guard enforces.**

| Grant | Invariant |
|---|---|
| publisher | allowed; actor = lifecycle workflow `publish` job, named environment, `refs/heads/main`, attempt 1 |
| publisher | writer is an authorized signer with a bound writer identity (`fafa33`, OD-2a) |
| publisher | signing is SSH Ed25519, signing-only, GitHub `verified` |
| publisher | target is a dedicated non-default `refs/heads/` namespace bound to the Issue with 16 hex of the authorization id |
| publisher | operation is exactly create-only: no update, force, delete, tag or PR authority |
| publisher | token is exactly `contents: write` on this repository, with `workflows` absent |
| publisher | exactly one commit; parent = signed base; tree = validated tree; non-signature fields = `unsigned_commit_sha`; no model prose |
| publisher | path authority = TaskScope |
| publisher | required boundary = the `pre-push-safety` stage of the validation stage contract, executed by the credential-free validator, bound to `unsigned_commit_sha`, code identity `control_sha` |
| publisher | no candidate code or hooks with credentials (DFF-028) |
| state ledger | allowed non-code ledger; never admissible; no source paths |
| state ledger | prefix is a dedicated `refs/heads/…/` namespace disjoint from candidate branches |
| state ledger | fast-forward append only, with no force or delete |
| state ledger | anchor = exactly {`deletion`, `non_fast_forward`} with no bypass actors |
| state ledger | writer = the control role's `GITHUB_TOKEN` on `main` |

**Proof.** `tests/test_code_write_policy.py` carries 49 widening fixtures (each must be rejected) and 4
canonically equivalent spellings (each must be accepted).

## 3. Defect registry carry-forward (B-6)

| #558 record | Independently truthful on `main`? | Disposition |
|---|---|---|
| PRH-066 production trigger never cut over | **Yes**: observed on `main` with #520 (label accepted, no execution). The guard text in #558 is Railway/OIDC-specific. | **Carry, re-scoped**: same class and source; guard = lifecycle workflow with no Railway client (AT-42) + the canary; status `recorded` until S5/S7 |
| PRH-067 publisher runs candidate pre-push under credentials | **Yes**: `publish_create_only` → `_run_pre_push_safety` is on `main` today | **Carry**; status `recorded` (a known live defect, no production caller) → `guarded` in S4 with AT-34 |
| PRH-068 same-uid credential recovery | **Yes**: the class applies to any hosted executor or validator design | **Carry**; `recorded` → `guarded` in S4 with AT-19/AT-29 |
| PRH-069, PRH-071 (OIDC claims) | No; there is no OIDC verifier in this architecture | **Not carried**; lessons in FMEA §7; ids left unallocated on `main` |
| PRH-070 (RAM role race) | No; Railway-RAM specific | **Not carried**; invariant covered by AT-09/AT-25 |
| New: #558 P1 class | Yes, as a design defect caught in review | Record in S1 under the existing appropriate family, or the smallest new one per DPM rules. The class: accepted work marked terminal before trusted publication, with continuation state in process RAM. Guard: AT-24 (the COMPLETED-early mutant). |

## 4. Dependency order

```text
OD-1 → S0 live proofs ─┐
OD-2..OD-5 → S1 contracts/policy ─┼─> S2 state lib ─> S3 control ─> S4 role libs ─> S5 workflows (inert) + parallel-authority fail-closed
                                                                                    └─> S6 provision + rehearse + Railway suspend + OD-6
                                                                                         └─> S7 live + #520 canary ─> S8 deletion + retirement proof
```

## 5. Slices

**Forbidden in every slice:** #520 before S7, #559, #538/#553/#555/#556, and merging without owner
approval.

| Slice | Prerequisites | Deliverable | Verification | Exit |
|---|---|---|---|---|
| **S0 live proofs** (**done 2026-10-04**: FMEA §4) | OD-1 | owner sandbox repository (or authorized scratch refs): A-1 race (≥ 30 writers, `GITHUB_TOKEN`), A-2, A-3 (runner-signed commit `verified` for the OD-2 signer), A-4 negative dispatch, A-5 REST digest and retention, A-6 isolation probe, A-12; port the P-*/L-* prototypes | recorded run ids and outputs attached to #560 | every OPEN assumption proven, or the ADR reopened |
| **S1 contracts and policy** | ADR accepted; S0 green; OD-2, OD-3, OD-5 decided | Docs and policy only: ADR 0036 amendment; ADR 0031 disposition text; `CODE_WRITE_POLICY` `issue_agent_publisher` path (+ signer change if OD-2(b)); execution contract and trigger docs; DEFECT_REGISTRY §3 | Artifact, Architecture Index and Defect Prevention guards | amendments accepted |
| **S2 state library** | S1 | schema, canonical JSON, sign and verify, provenance check, chain verify, lease CAS client, pure `advance`, vocabulary | AT-13–17, AT-24 (pure), AT-25, AT-27, AT-44; mutation run | all mutants killed |
| **S3 control** | S2 | `authorize` library + sealed-transport library + prompt-input manifest | AT-01–04, AT-08–12, AT-14, AT-33 | local topology reaches AUTHORIZED with no Railway |
| **S4 role libraries** | S2, S3 | executor, validator (unsigned commit), publisher; PRH-067 fix | AT-19–22, AT-29–32, AT-34–36, AT-45; mutation run | local topology reaches COMPLETED (fake GitHub) |
| **S5 workflows (inert)** | S4 | lifecycle, reconcile, candidate-PR and rehearsal workflows; **trigger without a Railway client**; **parallel-authority fail-closed guard**; environments not provisioned, so a label fails `MISSING_CONFIGURATION` before any write | AT-41, AT-42, AT-46, AT-47; push hook; hosted Pre-PR | merged; nothing can execute |
| **S5b review-knowledge loop** (owner requirement 2026-10-04; [reconciliation](ISSUE_AGENT_REVIEW_KNOWLEDGE_LOOP.md)) | S5a; owner decisions RD-1…RD-6 | reviewer finding → knowledge → prevention → bounded remediation → exact-head proof → thread resolution → feedback, reusing the learning ledger, KnowledgeExtractionAuthority, DEFECT_REGISTRY/RFD, DPM and the S3–S5 lifecycle | the reconciliation §5 acceptance simulation (known + new family, restart between transitions, duplicate delivery, fresh task receives knowledge first); mutation run | **S5 is complete only when S5b is proven** |
| **S6 provision and rehearse** | S5 including S5b; OD-6 done | owner: four environments (branch policy `main`); rotated K_SH/K_SPM; new K_STATE/X_EXEC/X_RESULT; signer per OD-2; SH root; **suspend Railway**; revoke the fallback token; disable n8n; non-publishing rehearsal | rehearsal reaches VALIDATED; AT-33 on real logs; AT-40 live | green; Railway suspended |

| **S7 live + #520** | S6 | §7 | §7 evidence | one Draft PR, no relay |
| **S8 retirement** | S7 proof | delete every S8 row; §6 inventory; AT-43; registry lifecycle; offline owner export of `/data` (never committed); owner deletes the Railway project and ends the subscription | AT-43; reference search shows only archive notes | owner confirms |

S6 bootstraps the GitHub-native Source Handling root with the owner-dispatched `Hunter / Issue Agent Source Handling Bootstrap` workflow on `main` **only after Railway suspension is confirmed**, preserving ADR 0038 §7.3 no-dual-authority ordering. The control-domain job resumes any verified interrupted bootstrap prefix, captures only missing canonical transactions, publishes them to `refs/heads/hunter-state/v1/source-handling` under K_STATE, and requires the canonical bootstrap to be fully idempotent after replay before rehearsal proceeds. It never auto-bootstraps during authorization.

## 6. OD-6 trace (2026-10-04, read-only) and removal inventory (S8)

### 6.1 Trace: does anything outside the retired Issue-agent path depend on the legacy token or n8n?

| Surface | Method | Finding |
|---|---|---|
| Repository code and config | repo-wide reference search | `HUNTER_AGENT_GITHUB_PUSH_TOKEN` / n8n appear only in the retired fallback/n8n/Railway modules, their tests, docs, and `issue_agent_replacement_executor.py` (where it is a *forbidden* environment name). `smart_prompt_transport.py` is a generic SPM transport abstraction whose only concrete transport is n8n; it is retained as data and protocol, with no authority. |
| GitHub repository secrets and variables | `gh secret list`, `gh variable list` | no `HUNTER_AGENT_*` and no n8n secrets or variables. Railway-path secrets present: `HUNTER_ISSUE_AGENT_WEBHOOK_URL`, `HUNTER_ISSUE_AGENT_PROVISIONING_URL` (S8 removal). |
| Repository webhooks | `GET /hooks` | none |
| Deployment environments | `GET /deployments` | `Project-hunter / production`, `creative-mercy / production`, `zestful-embrace / production`, all created by `railway-app[bot]` (last deploy 2026-10-03), plus older `Preview`/`Production` (2026-07-24) |
| Other repositories of `fafa33` | `gh search code` | none reference the token or n8n |
| Railway | `railway list`; variable **names** only, values discarded | `Project-hunter` has 1 service, `hunter-issue-agent-issuer`, which is the **only** holder of `HUNTER_AGENT_GITHUB_PUSH_TOKEN` and the `HUNTER_AGENT_*_COMMAND` pool. It also holds `HUNTER_SOURCE_HANDLING_SIGNING_KEY`, `HUNTER_PROMPT_AUTOMATION_SIGNING_KEY` and **three base64 Issue bodies** (`HUNTER_ISSUE_441/457/461_BODY_B64`). `zestful-embrace` has no services. `creative-mercy` no longer exists. No n8n variables. |
| External n8n | not reachable from the repository or Railway | no n8n endpoint is configured anywhere visible; it is only reachable through the retired transport |
| The PAT behind `HUNTER_AGENT_GITHUB_PUSH_TOKEN` | not enumerable by API | the owner identifies it in GitHub settings (fine-grained or classic tokens, by last use) |

**Conclusion.** No unrelated live use was found, so OD-6's stop condition is not triggered. Per OD-6,
revocation and disabling are **deferred** to the owner-approved migration step: S6 for the token and
external n8n workflows, before go-live; S8 for the rest. Nothing was revoked during this trace.

### 6.2 Removal inventory (S8, after the retirement proof)

| Kind | Items |
|---|---|
| Repository secrets and variables | `HUNTER_ISSUE_AGENT_WEBHOOK_URL`, `HUNTER_ISSUE_AGENT_PROVISIONING_URL`, `HUNTER_ISSUE_AGENT_WEBHOOK_TIMEOUT_SECONDS`, the Railway deploy token, repository-level `HUNTER_ISSUE_AGENT_AUTHORIZATION_SIGNING_KEY` (moved to the control environment in S6) |
| Railway service environment | all `HUNTER_ISSUE_AGENT_*`, `HUNTER_SOURCE_HANDLING_*`, `HUNTER_PROMPT_AUTOMATION_*`, `HUNTER_AGENT_*_COMMAND`, `HUNTER_AGENT_GITHUB_PUSH_TOKEN`, provider keys; every key Railway held is revoked or rotated |
| Fallback and n8n | `HUNTER_AGENT_GITHUB_PUSH_TOKEN` revoked wherever issued; n8n webhook token and workflows disabled (OD-6) |
| Webhooks and apps | any repository webhook or app installation pointing at Railway or n8n hosts |
| Code, config, docs | §2 rows marked DELETE/ARCHIVE |
| Data | `/data` volume: offline owner export, then deletion |
| Railway variables with INTERNAL content | `HUNTER_ISSUE_441/457/461_BODY_B64`: delete with the service; never exported to the repository |
| GitHub App and deployment records | uninstall `railway-app` from the repository; deployment environments `Project-hunter / production`, `creative-mercy / production`, `zestful-embrace / production` (historical records may stay; the app must not) |
| Railway projects | `Project-hunter` (after the proof); the empty `zestful-embrace` |

**Retirement proof** (all must hold):

1. AT-43 and AT-46 are green on `main`.
2. #520 completed with every Railway service suspended and the fallback token revoked.
3. The §6 inventory is empty.
4. No post-S7 run references a Railway or n8n host.

### 6.3 S0 sandbox cleanup (owner, after the evidence is recorded on #560)

- Delete repository `fafa33/hunter-560-s0-sandbox`. This needs the `delete_repo` scope. Its state
  branches cannot be deleted individually under the anchor ruleset; deleting the repository removes them.
- The throwaway A-3 signing key (id 1218014) was already deleted. The public signing-key list shows only
  the pre-existing key 1160850. Nothing else remains on the
  account.
- The temporary local Railway link was removed (`railway unlink`). No Railway state was changed.

## 7. #520 canary (S7)

1. **Refresh.** The owner refreshes #520's TaskScope `base_sha` to current `main`.
2. **The only human act.** The owner removes and re-applies `hunter-agent-execute`. Nobody dispatches,
   copies, or relays anything.
3. **Expected:**
   - one lifecycle run;
   - the chain AUTHORIZED → RESULT_BOUND → VALIDATED → PUBLISHED → COMPLETED;
   - branch `issue-520-<16 hex>` with exactly one verified commit, signed by the OD-2 signer, changing
     only `docs/ISSUE_AGENT_CANARY.md`;
   - Pre-PR green at that exact head;
   - exactly one Draft PR, opened by the candidate-PR workflow.
4. **Negative checks.** Relabel while active → `ISSUE_EXECUTION_ACTIVE`. Re-run → `RERUN_REFUSED`.
   Railway stays suspended.
5. **Evidence recorded on #560:**
   - run id and attempt;
   - `authorization_id`, `execution_id`, `publication_identity`;
   - `control_sha` and `base_sha`;
   - manifest, prompt, result and receipt digests;
   - head, tree and unsigned commit;
   - Pre-PR run at the head;
   - PR number;
   - the state commit of every transition.
6. **No merge** without separate explicit owner approval.

## 8. Rollback

| Situation | Rollback |
|---|---|
| defect after S5 or S7 | disable the lifecycle and reconcile workflows; an owner-dispatched reconcile after the fix records in-flight work as FAILED; **Railway and fallbacks are not resumed** |
| falsified assumption | same; reopen ADR 0037 |
| key compromise | disable; rotate; pin the new id; quarantine chains signed by the compromised K_STATE; revoke and replace the publisher signing key (persistent verification keeps history) |
| after S8 | only disable or fix forward |

## 9. Go / no-go

- **Go S0:** OD-1 granted. **Done**, including A-3.
- **Go S1:**
  - ADR accepted;
  - S0 proves every OPEN assumption;
  - OD-2 to OD-5 decided (done 2026-10-04);
  - **BLK-1 closed** (A-3 GitHub `verified` proven in the sandbox);
  - ADR 0038 accepted with ADR 0037;
  - #558 not merged (owner B-6). The owner closes it before the #560 PR is merged; it does not gate S1.
- **Go S2–S5:**
  - S1 amendments accepted;
  - each slice's ATs exist and kill their named mutants.
- **Go S7:**
  - four environments with branch policy `main`, disjoint secrets, no reviewers;
  - keys rotated;
  - signer commits verify;
  - rehearsal green;
  - AT-33 and AT-40 live green;
  - Railway suspended;
  - OD-6 confirmed;
  - AT-41, AT-42, AT-46 and AT-47 green;
  - required checks green on every merged slice.
- **Go S8:** #520 evidence recorded; one Draft PR; no relay; Railway suspended throughout.
- **No-go (any one stops the work):**
  - a falsified assumption;
  - a surviving mutant;
  - plaintext confidential content on a public surface;
  - COMPLETED before an observed PR;
  - any automatic model re-dispatch;
  - any Railway, fallback or n8n path able to execute or publish.
