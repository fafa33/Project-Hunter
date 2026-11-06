# ADR 0041: Governed Codex Fallback Execution for the GitHub-Native Issue Agent

## Status

**Proposed** (2026-11-06, Issue #390, owner selection of Option 1: amend ADR 0037 before implementation).

- This ADR is **not binding** and authorizes **no production change** until the owner accepts it.
- Until acceptance, the Issue #390 branch keeps the fail-closed refusal in `run_executor`: Codex is never launched.
- **Scope.** A proposed amendment of [ADR 0037](0037-github-native-issue-agent-execution.md) **D1** (topology) and **D8**
  (no re-dispatch) only. It extends the state machine
  ([`ISSUE_AGENT_GITHUB_NATIVE_STATE_MACHINE.md`](../ISSUE_AGENT_GITHUB_NATIVE_STATE_MACHINE.md) §2, §4, §5, §6) by
  one state. Every other decision of ADR 0037, ADR 0038 and ADR 0039 is reaffirmed unchanged, including D9: the
  retired `agent-fallback-run`, n8n and Railway paths stay retired. This ADR adds no parallel orchestrator.

## Context

ADR 0037 runs the model exactly once per authorization: the executor is one fresh GitHub-hosted Ubuntu VM, the
primary is Kilo inside `KiloContainerIsolation`, and D8 states that "the model is never re-dispatched". A
transient provider failure of Kilo therefore ends the authorization as `FAILED`, and only an owner relabel starts
a fresh execution.

Issue #390 asks for an automatic, governed fallback to Codex after a **recoverable** Kilo provider failure. Four
facts constrain the design:

1. **Kilo stderr is not independent evidence.** `stderr_category` classifies output of a process that runs
   model-influenced work, and the egress gateway is a TLS `CONNECT` proxy that cannot observe HTTP status. A model
   could print `rate limit` to steer execution onto a different executor.
2. **The available Codex host is a persistent, self-hosted macOS machine.** It is not a fresh VM, and ADR 0037
   D1's isolation proofs (A-6, A-12) were made on Ubuntu.
3. **The owner's sandbox probe passed** (2026-11-06: identity, workspace-write, private-read, outside-write all
   PASS, as `hunteragent`, uid 502, under `sandbox-exec`). That probe denied **all** network access. Production
   Codex needs provider egress, so the probe is a preliminary boundary, not a production attestation.
4. **Owner constraints:**
   - no permanent root/sudo privilege for the agent or the runner;
   - no copy of the owner's personal Codex credentials;
   - no push, PR or merge without owner approval.

## Decision

### F1. Exactly one governed fallback dispatch (amends ADR 0037 D8)

D8 becomes: *the model is never re-dispatched, except for **at most one** Codex fallback dispatch per
authorization. That dispatch happens in the **same** lifecycle run (`run_attempt == 1`), and only after the
control domain has written a signed `FALLBACK_AUTHORIZED` ledger record (F4).*

- The fallback never falls back again.
- Resumes never dispatch a model; `RESUME_STAGES` is unchanged.
- The only other fresh execution is still an owner relabel.
- **Kilo stays the primary.** The primary/secondary order is fixed in trusted code at `control_sha`. Issue text,
  the prompt, repository content and workflow inputs cannot select or reorder providers.

### F2. Admissible cause: verified recoverable provider failure only

The executor job may claim a fallback cause only when **all** of these hold:

- **Closed set of causes:**
  - `RATE_LIMIT`
  - `QUOTA_EXCEEDED`
  - `NETWORK` (provider unreachable)
  - `PROVIDER_5XX`
- **Two independent observations agree:**
  - Kilo's `stderr_category`, which is untrusted and only a hint.
  - A **trusted provider probe**: model-free trusted code in the executor's trusted step (never the isolation uid)
    sends one bounded request with the model key, after the isolated run has stopped. It classifies only the HTTP
    status or transport error into the same closed set. The probe sends no prompt, repository content or model
    output.
- **No primary result was produced.** The executor sealed no result and uploaded no result artifact.
- **The primary run was not refused.** Kilo started, was not refused by isolation, and exited non-zero or timed out
  on a provider call.

**Never a fallback cause.** These are terminal exactly as today:

- authorization, transport-integrity, provenance or schema failures;
- isolation or attestation failures (`EXECUTION_NOT_STARTED`);
- `SECRET_IN_RESULT`, `EXECUTOR_RESULT_REJECTED`, `NO_CHANGES`, `MODEL_TIMEOUT` without a probe-verified cause;
- authentication or permission errors;
- `UNCLASSIFIED` failures, or any disagreement between the two observations;
- any validation, publication or acceptance failure (these occur after `RESULT_BOUND` and never reach F4).

`MODEL_NOT_FOUND`, which the current branch's draft policy admits, is **removed**. It is a primary configuration
defect, and falling back would hide it.

### F3. Authenticated Ubuntu-to-macOS execution transport (reuses ADR 0037 D3)

No new channel is introduced. The fallback consumes the **same** sealed handoff over GitHub artifact transport:

- **Sealing.** `authorize` seals the identical handoff plaintext twice:
  - to the primary's `X_EXEC`, as today;
  - to a **distinct** fallback recipient key `X_FB`.

  `AUTHORIZED` evidence binds both ciphertext digests: `handoff_artifact` (existing) and an optional
  `fallback_handoff_artifact`.
- **Key separation.** `X_EXEC` never leaves the Ubuntu executor environment. `X_FB` is held only by a new
  environment `hunter-issue-agent-fallback-executor`, branch policy `main`, scoped to the fallback job. If the Mac
  is compromised, only `X_FB` is exposed: no primary key and no ledger key.
- **Download checks.** The fallback job downloads only the artifact whose id and digest match
  `fallback_handoff_artifact` (`digest-mismatch: error`). `run_executor` then applies its existing checks unchanged:
  - the ciphertext digest equals the ledger binding;
  - the envelope opens under the expected `TransportBinding`;
  - the issuer signature verifies (`PromptAutomationVerifier`);
  - the lineage and the prompt digest equal the ledger.
- **Precondition.** The fallback job also requires a `FALLBACK_AUTHORIZED` record on the anchored ledger, which it
  reads and verifies itself. It never reads the Ubuntu job's outputs as authority.

### F4. Ledger: one new state, attempt-bound identity (extends SM §2, §5)

**New state.** `FALLBACK_AUTHORIZED` changes the transition table as follows:

- `AUTHORIZED → {RESULT_BOUND, FALLBACK_AUTHORIZED, FAILED}`
- `FALLBACK_AUTHORIZED → {RESULT_BOUND, FAILED}`

**Writers.** `bind`, in the control domain, writes `FALLBACK_AUTHORIZED` in one CAS. `bind` and `reconcile` may
fail it. No other role writes it.

**`FALLBACK_AUTHORIZED` evidence (closed schema):**

- `primary_executor_job_id`, `primary_executor_conclusion`;
- `primary_cause`, from the F2 closed set;
- `stderr_category` and `probe_category`, which must be equal;
- `primary_result_artifacts: 0`;
- `fallback_execution_id`, defined below;
- `fallback_handoff_artifact`, the same digest as in `AUTHORIZED`;
- `fallback_runner_label`, pinned;
- `attestation_policy_sha256`, the digest of the pinned attestation policy (F5);
- `fallback_deadline`.

**Attempt-bound identity.** `fallback_execution_id = execution_identity(…, execution_attempt=2)`. This is a new
**ledger** attempt field, distinct from GitHub `run_attempt`, which stays 1. For attempt 1 the existing derivation
is unchanged, so every existing `execution_id` is preserved.

- The fallback result is sealed under a `TransportBinding` that carries `fallback_execution_id`.
- The fallback uploads a distinct artifact name: `hunter-ia-result-fb-<digest>`.
- A late attempt-1 result can therefore never be bound as the fallback result, and the reverse is also impossible.

**Bind rules from `FALLBACK_AUTHORIZED`:**

- exactly one fallback artifact: transition to `RESULT_BOUND`;
- duplicate fallback artifacts, or any attempt-1 artifact appearing late: `TRANSPORT_INTEGRITY_FAILED`;
- no artifact: the existing terminal mapping.

**Additional `RESULT_BOUND` evidence:**

- `execution_attempt`: 1 or 2;
- `executor`: `kilo` or `codex`;
- `attestation_sha256`: present only when the attempt is 2.

**Reconcile.** If the run has finished and `FALLBACK_AUTHORIZED` is still open, `reconcile` fails it with
`EXECUTION_NOT_COMPLETED`.

**Compatibility.** The record schema version is bumped. Readers that do not know `FALLBACK_AUTHORIZED` fail closed
(`STATE_CORRUPT`). Old ledgers never contain the new state. No migration is needed.

### F5. macOS fallback executor isolation and per-run attestation (amends ADR 0037 D1)

D1 gains a fifth trust domain, **Fallback executor**:

- **Job:** `execute-fallback`.
- **Runner:** a dedicated self-hosted macOS runner with label `hunter-codex-fallback`, separate from
  `hunter-reviewer`.
- **Secrets:** `X_FB` and a dedicated Codex provider key.
- **`GITHUB_TOKEN`:** `{}`.
- **Model:** yes, as the isolation uid.
- **Candidate code:** never executed by trusted code.

**Principals.** Both principals are non-admin:

- `hunterrunner` runs the Actions runner service. It is never the owner's account, so the owner's `~/.codex` and
  keychain are out of reach.
- `hunteragent` is the model uid.

**One-time owner provisioning.** This is an interactive administrator action performed once. No standing root
grant exists.

- Create `hunterrunner`.
- Install a pinned Codex CLI at a root-owned path (`/opt/hunter/codex/<version>/`).
- Install the root-owned sandbox profile `/Library/Hunter/codex-fallback.sb`.
- Install one sudoers drop-in. Its rules may only switch to `hunteragent`, never to root:
  ```
  hunterrunner ALL=(hunteragent) NOPASSWD: /usr/bin/sandbox-exec -f /Library/Hunter/codex-fallback.sb /opt/hunter/codex/*/codex exec *
  hunterrunner ALL=(hunteragent) NOPASSWD: /bin/kill -KILL -1
  ```
  `hunterrunner` gains no root privilege, and the agent never holds sudo.

If the owner rejects even this run-as-`hunteragent` rule, the alternative in A4 applies. It costs one more trusted
component.

**Workspace ownership without `chown`.** The trusted step creates a fresh `0700` workroot owned by `hunterrunner`.
It grants `hunteragent` an inherited ACL on `workspace/` and `home/` only (`chmod +a`, which the owner of a file
may set). After the run, the step removes the ACLs and kills every `hunteragent` process before collection.
Collection reuses `collect_result` through the trusted git directory unchanged.

**Sandbox profile.** The profile denies by default. It allows:

- reads of system paths, the pinned Codex install, `workspace/` and the per-run `home/`;
- writes to `workspace/` and the per-run `home/` and tmp;
- `network-outbound` **only** to `localhost:<gateway-port>`.

The existing trusted `model_egress_gateway` runs as `hunterrunner` on localhost, with a `CONNECT` allowlist
limited to the Codex provider hosts. This gives the same egress model as Kilo on Ubuntu.

**Codex's own sandbox.** Codex's inner Seatbelt sandbox is defense in depth only. P0 must establish whether it
nests under the outer profile. If it does not, the adapter launches Codex with its inner sandbox disabled, and the
**outer** profile is the sole enforced boundary. The adapter must never fall back to running Codex unsandboxed.

**Per-run attestation.** This runs in a trusted step, **before** `X_FB` or the provider key is used. Any failure
returns `EXECUTION_NOT_STARTED` with no fallback.

- **Pinned SHA-256 values** must equal the policy pinned at `control_sha`, for:
  - the profile file;
  - the Codex binary;
  - the sudoers drop-in;
  - the gateway script.
- **Principal checks:**
  - `hunteragent` is not an admin;
  - `hunteragent` does not share a primary group with `hunterrunner`;
  - no `hunteragent` process is running.
- **Live probe cases** under the production profile. Each must hold, and setup errors count as FAIL:
  - identity is `hunteragent`;
  - a workspace write is allowed;
  - a read of the `hunterrunner` home is denied;
  - a write outside the workspace is denied;
  - a direct TCP/443 connection to a non-allowlisted host is denied;
  - a `CONNECT` through the gateway to a non-allowlisted host is denied.
- **Result.** `attestation_sha256` is the digest of the canonical attestation result. It is recorded in
  `RESULT_BOUND`.

**Residual risk, accepted only on acceptance of this ADR.** The fallback host is persistent, not a fresh VM.
Per-run mitigations:

- a fresh workroot;
- ACL revocation;
- kill-all of `hunteragent` processes;
- removal of the workroot;
- pinned hashes;
- the attestation;
- a single-job runner with concurrency `hunter-codex-fallback`, `queue: max`.

A host compromise exposes `X_FB` and the Codex key, never `K_STATE`, the publisher key or `X_RESULT`.

### F6. Codex credential provisioning

- **Dedicated key.** A **dedicated** provider API key in a dedicated provider project with a hard spend cap, stored
  only as an environment secret of `hunter-issue-agent-fallback-executor`.
- **No personal credentials.** The owner's personal `~/.codex/auth.json` and ChatGPT login are never read, copied
  or referenced.
- **Delivery.** The key reaches Codex only through `secret_env`, using the existing secret launch (no argv, no
  logs). P0 confirms the exact environment variable that the pinned Codex CLI version reads in `exec` mode.
- **Per-run `CODEX_HOME`.** `CODEX_HOME` is a per-run directory under `home/`, removed after the run.
- **Secret scan.** `result_carries_secret` covers the key exactly as it covers the Kilo key.

### F7. Exact-head proof: no new authority

A fallback result enters the **identical** chain:

1. T2 `RESULT_BOUND`
2. T3 validator pre-push safety
3. T4 create-only signed publication
4. T5 candidate Draft PR
5. Hosted exact-head `Hunter / Pre-PR Preflight`, followed by Hunter Merge Readiness

The validator and publisher domains, the exact `base_sha` binding, `collect_result`, the signing identity and the
Validation Stage Contract are unchanged.

The executor identity is recorded as evidence only. It never relaxes or adds a proof stage.

## Consequences

**Configuration and compatibility**

- The fallback is inert until all of the following hold:
  - the owner accepts this ADR;
  - provisioning is complete;
  - `X_FB` and the Codex key exist;
  - the fallback runner is online.
- If any of these is missing, `bind` cannot write `FALLBACK_AUTHORIZED`, and the existing terminal mapping applies.
  Behavior is then identical to today.
- The state machine document, the defect registry and the static workflow guard (AT-41/42/47) must cover:
  - the new job;
  - the new environment;
  - the self-hosted label, permitted only for `execute-fallback`.

**Cost and timing**

- At most one extra model run per authorization.
- The lifecycle deadline accounts for the fallback job's timeout (20 minutes).

## Alternatives Considered

- **A1. Fall back inside the Ubuntu executor job.** Rejected: there is no attested Codex isolation on Ubuntu in
  scope, and it would put a second provider key in the same job as `X_EXEC`.
- **A2. Trust Kilo's stderr or the executor's advisory output alone.** Rejected: both are model-influenceable
  (Context 1).
- **A3. Reuse `X_EXEC` on the Mac.** Rejected: a compromise of the persistent host would expose the primary's
  handoff key.
- **A4. No sudoers rule at all.** The alternative is a root-installed LaunchDaemon running as `hunteragent` that
  accepts only fixed, schema-validated launch requests over a socket that only `hunterrunner` can open. It is
  viable, but it adds a trusted component and more code. It is held in reserve if the owner rejects F5's
  run-as-`hunteragent` rule.
- **A5. Run the self-hosted runner as `hunteragent`.** Rejected: the runner, the secrets and the model would share
  one uid.
- **A6. Keep ADR 0037 as is (owner relabel only).** Rejected by the owner (Issue #390, Option 1).

## Implementation Plan (proposed, gated on acceptance)

There is one Issue #390 branch and no PR chain. Each step is tests-first with focused verification. After
acceptance, nothing is pushed without owner approval.

| Step | Change | Focused regression tests |
|---|---|---|
| **P0** spike, owner-run, no repository change | On the Mac, with one interactive `sudo` session: confirm whether Codex's sandbox nests under the outer profile, the API-key environment variable Codex reads, and that the gateway `CONNECT` path works under the profile | Recorded as evidence in the Issue, not as a test |
| **P1** policy | Revise `issue_agent_fallback_policy`: F2 closed cause set (drop `MODEL_NOT_FOUND`, add `PROVIDER_5XX`), require `stderr_category == probe_category`, require zero primary artifacts | Forged stderr without probe agreement is refused; every non-recoverable code is refused; a second fallback is refused |
| **P2** provider probe | A trusted, model-free probe in the executor's trusted step, with a closed HTTP-status-to-category table, emitted as `probe_category` | No prompt or content in the probe request; unknown status maps to `UNCLASSIFIED`; key never logged |
| **P3** ledger | Add `FALLBACK_AUTHORIZED`, an `execution_attempt` field, the evidence schema, `advance` rules, the reconcile rule and a schema bump | Legal and illegal transitions; attempt-1 artifact after admission → `TRANSPORT_INTEGRITY_FAILED`; duplicate fallback artifact; existing attempt-1 `execution_id` unchanged |
| **P4** handoff | `authorize` dual-seals to `X_EXEC` and `X_FB`, and binds `fallback_handoff_artifact` | Wrong recipient or digest is refused; the binding carries `fallback_execution_id`; the primary path is byte-identical |
| **P5** Mac isolation | A `MacSandboxIsolation` port: ACL prepare, sudo run-as through the fixed command, gateway env, kill-all stop, the attestation routine; replaces the current hard refusal with "refuse unless `MacSandboxIsolation` and a passing attestation" | Codex refused under `SudoIsolation` or a generic port; argv/flag injection refused; attestation hash mismatch, admin uid or setup error → `EXECUTION_NOT_STARTED`; secret never in argv |
| **P6** workflow | Add the `execute-fallback` job (runner label, new environment, `needs: bind`, gated on the ledger state), a second bind pass, validate `needs` both; extend the static workflow guard | Guard admits the self-hosted label only on `execute-fallback`; no `id-token`; `run_attempt == 1` enforced |
| **P7** registry and docs | `DEFECT_REGISTRY.json` entry for the "untrusted-signal provider downgrade" class; state machine §2/§4/§5/§6; ADR 0037 cross-reference | Registry guard |
| **P8** end-to-end | After owner approval to push and merge to `main`: one owner-labelled Issue with the primary model configured to a deliberately rate-limited key, observed through `FALLBACK_AUTHORIZED` → `RESULT_BOUND` (attempt 2) → validated → published → Draft PR → hosted Pre-PR Preflight on the exact candidate head | Exact-head SHA equality checked against the run; the ledger chain is verified |

**Prerequisites only the owner can supply:**

- the P0 session;
- one-time provisioning (the `hunterrunner` user, the pinned Codex install, the profile, the sudoers drop-in, and
  runner registration);
- a dedicated provider project and key;
- the `X_FB` keypair and the new environment;
- approval to push, open the PR and merge before P8.
