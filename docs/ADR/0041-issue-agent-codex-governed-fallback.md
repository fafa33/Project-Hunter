# ADR 0041: Governed Codex Fallback Execution for the GitHub-Native Issue Agent

## Status

**Proposed, revision 4: owner-approved interim implementation direction** (2026-11-06, Issue #390).

**Revision 5 (owner-approved 2026-11-07, Issue #390 comment 6705575255; implemented as P9):** F8 adds an
owner-armed, independently verifiable fallback canary, because P8 cannot otherwise run. No approved mechanism makes
the primary fail recoverably. In the last fifteen lifecycle runs, every primary outcome was terminal under F2
(`UNCLASSIFIED`, `NOT_FOUND` or a timeout). F8 has no effect unless the owner arms it for one authorization.

Revision 4 applies the owner's decision of 2026-11-06:

- the 10-minute approval deadline is removed;
- a fallback request stays pending until the owner explicitly approves or rejects it;
- device-code expiry never cancels the request, and the owner's next approval renews the code;
- no runner is held while the request waits.

See F1 and F6.

Revision 2 rests on verified evidence about the Codex CLI's authentication methods and on the owner's cost
constraint: an existing ChatGPT Pro plan, with no API key, new subscription or other spend. On 2026-11-06 the owner
approved revision 2 as the interim implementation direction, subject to security verification. Revision 3 records
that verification (P0 evidence, below) and the implementation on the Issue #390 branch.

- **Implemented, not operational.** P1–P7 are implemented on the branch. Every path stays inert until the owner:
  - pins `fallback_handoff_recipient` in the trust roots;
  - provisions `config/issue_agent_codex_fallback.json`;
  - installs the fallback host.

  Until then, `bind` can never admit a fallback, and `MacCodexIsolation` refuses to attest.
- **Codex still never runs** through any port but the attested `MacCodexIsolation`, and never as a primary.
- **Not operational until P8.** The fallback may be described as operational only after a real end-to-end run.
- **Not binding until merged.** This ADR becomes binding only when merged with owner approval.
- **Scope.** A proposed amendment of [ADR 0037](0037-github-native-issue-agent-execution.md) **D1** (topology) and
  **D8** (no re-dispatch) only. It extends the state machine
  ([`ISSUE_AGENT_GITHUB_NATIVE_STATE_MACHINE.md`](../ISSUE_AGENT_GITHUB_NATIVE_STATE_MACHINE.md) §2, §4, §5, §6) by
  one state.
- **Reaffirmed unchanged.** Every other decision of ADR 0037, ADR 0038 and ADR 0039, including D9: the retired
  `agent-fallback-run`, n8n and Railway paths stay retired, and there is no parallel orchestrator.

## Context

ADR 0037 runs the model exactly once per authorization, on one fresh GitHub-hosted Ubuntu VM, with Kilo inside
`KiloContainerIsolation`. D8 says "the model is never re-dispatched". Issue #390 asks for a governed Codex
fallback after a **recoverable** Kilo provider failure.

### Facts the design rests on

1. **Kilo's stderr is not independent evidence.** `stderr_category` classifies output of a process that runs
   model-influenced work. The egress gateway is a TLS `CONNECT` proxy and cannot observe HTTP status codes.
2. **The Codex host is the owner's persistent, self-hosted Mac,** not a fresh VM.
3. **The owner's 2026-11-06 sandbox probe passed,** running as `hunteragent` (uid 502) under `sandbox-exec`. It
   denied **all** network access, so it is a preliminary boundary, not a production attestation.
4. **The repository is public.** `gh repo view fafa33/Project-Hunter` reports `PUBLIC`.

### Codex CLI 0.167.0 authentication, verified 2026-11-06

**From the installed CLI** (`codex login --help`, plus strings in the vendored `codex-darwin-x64` binary). The
binary carries these modes:

- `ApiKey`, through `--with-api-key` or the `OPENAI_API_KEY` / `CODEX_API_KEY` environment variables;
- `Chatgpt`, the browser OAuth sign-in, or `--device-auth`;
- `PersonalAccessToken`, through `--with-access-token` or the `CODEX_ACCESS_TOKEN` environment variable;
- `AgentIdentity`;
- workload-identity federation;
- Bedrock.

Credential storage can be `file`, `keyring`, `auto` or `ephemeral`.

**From OpenAI's official documentation** (the Codex [Authentication], [Access tokens] and [CI/CD auth] pages,
retrieved 2026-11-06):

| Method | Plan / cost | Unattended? | Official position |
|---|---|---|---|
| API key | usage-billed | yes | "API keys are still the recommended default for automation" |
| Codex access token (`CODEX_ACCESS_TOKEN`) | "currently supported for ChatGPT **Business and Enterprise** workspaces" | yes | for trusted scripts and private CI runners |
| Persisted ChatGPT `auth.json` on a trusted runner | included plan usage (Pro) | yes | advanced and supported, but: "**Do not use this workflow for public or open-source repositories**" |
| Device-code sign-in (`codex login --device-auth`, beta) | included plan usage (Pro) | **no**: a human approves each sign-in | the preferred sign-in for headless or remote hosts |
| Copying the owner's personal `auth.json` | included plan usage | yes | prohibited by the owner, and the docs say to treat it like a password |

[Authentication]: https://learn.chatgpt.com/docs/auth
[Access tokens]: https://learn.chatgpt.com/docs/enterprise/access-tokens
[CI/CD auth]: https://learn.chatgpt.com/docs/auth/ci-cd-auth.md

**Conclusion.** On this public repository, with no additional cost, there is **no officially supported unattended
ChatGPT Pro credential**:

- the persisted-`auth.json` path is explicitly excluded for public repositories;
- access tokens require a Business or Enterprise plan;
- an API key costs money.

The only supported zero-cost path is a ChatGPT session that the owner approves interactively with a device code.

**One more constraint, which applies to any ChatGPT session.** Codex runs its tool commands as its own uid, inside
the same outer sandbox, so the model can reach the session tokens the CLI holds. A ChatGPT refresh token speaks for
the whole account, which is a broader grant than a scoped API key. Revision 2 therefore never persists such a
token, and bounds its lifetime to one run.

### P0 evidence (2026-11-06, this Mac, macOS 26.6.2, Codex CLI 0.167.0, no sudo)

**Nested sandboxing.** Under a deny-default outer profile, applying a second Seatbelt profile fails:
`sandbox-exec: sandbox_apply: Operation not permitted`, exit 71. It succeeds only under an allow-all outer profile.

- Codex's inner sandbox therefore cannot be relied on.
- The launcher runs Codex with `--sandbox danger-full-access`, always inside the outer profile, which is the sole
  enforced boundary (F5).

**The production profile** (`scripts/issue_agent/codex-fallback.sb`), applied with `sandbox-exec -f … -D …`:

| Case | Result |
|---|---|
| Identity | PASS |
| Workspace write | allowed |
| Read of a file outside the workroot | denied |
| Listing of an outside directory | denied |
| Write outside the workspace | denied |
| Read of the owner's `~/.codex/auth.json` | denied |
| Direct TCP 443 | denied |

**Codex startup under the profile.**

- Codex 0.167.0 starts under the profile and reports `Not logged in` with a fresh per-run `CODEX_HOME`. It cannot
  see the owner's session.
- Path canonicalization needs `file-read-metadata` on ancestor directories, so the profile allows stat()
  metadata only. File contents and directory listings stay denied.

**Device-code sign-in.** `codex login --device-auth` reached `auth.openai.com` through the loopback Codex gateway
profile, under the production profile, with zero gateway denials, and a verification URL was issued.

- The code text was never logged.
- The unused code was left to expire.

**Workspace ACLs.** An owner-set inherited ACL for `hunteragent` (`chmod +a`) works without root, and children
inherit it.

**launchd.** `launchd.plist(5)` on this host documents `SockPathOwner`, `SockPathGroup` and `SockPathMode`, and
`AbandonProcessGroup`.

**Not yet verified.** These need the provisioned host or the owner's approval:

- whether `codex logout` revokes the session server-side;
- the launcher running under launchd as `hunteragent`;
- the attestation as `hunterrunner`;
- a real Codex `exec` turn.

**Accepted residual risk.** Model tool commands run as the same uid, inside the same sandbox, as Codex, so they can
read the per-run session tokens. The F6 mitigations bound that exposure:

- one session per run;
- logout and deletion on every path;
- token-value result scan;
- gateway allowlist.

## Decision

### F1. At most one governed fallback dispatch (amends ADR 0037 D8)

D8 becomes: *the model is never re-dispatched, except for **at most one** launched Codex fallback execution per
authorization. That execution happens only after the control domain has written a signed `FALLBACK_AUTHORIZED`
ledger record (F4), and only in an owner-approved resume run that claimed its nonce (`run_attempt == 1`).*

**Admission is automatic; execution waits for the owner.** `bind` writes `FALLBACK_AUTHORIZED`, and the label run
then ends. No fallback job runs and no runner is held. The request stays pending with **no deadline**:

- `reconcile` and `bind` treat it as a permanent no-op;
- only the owner's explicit decision moves it.

**The owner's decision.** It is a label on the open Issue, `hunter-codex-fallback-approve` or
`hunter-codex-fallback-reject`, by the repository owner. The `fallback-decision` control job re-verifies the event
payload (action, label, sender, open Issue).

- **Approval** records a nonce-bound `resume_requested(stage="fallback")` on the ledger, then dispatches one run.
  This is the existing resume protocol: `resume-bind` claims the nonce by CAS and returns the bound `control_sha`.
- **Rejection** writes `FAILED / OWNER_WITHDREW` from `FALLBACK_AUTHORIZED`.
- **One run at a time.** A decision while an approved run is pending or in flight does nothing. That run's own
  outcome decides, because Codex may already be running in it.

**Back to pending, or final.**

- **Back to pending.** A run in which Codex was never launched reports `FALLBACK_SESSION_NOT_STARTED`. That covers
  a run where the owner's device sign-in did not complete, the code expired, attestation was refused, or the job
  never started. The resume is abandoned and the request returns to pending, so the next approval issues a fresh
  device code. This stage has no attempt cap: every attempt is an explicit owner approval.
- **Final.** Once Codex has been launched, the outcome is final: a bound result, or a terminal failure.

**Rules that still hold.**

- **No chaining.** The fallback never falls back again.
- **Resumes.** No automatic resume dispatches a model. The only model resume is the owner-approved fallback run.
- **Fixed order.** Kilo stays the primary. The provider order is fixed in trusted code at `control_sha`. Issue
  text, prompts, repository content and workflow inputs cannot select or reorder providers.

### F2. Admissible cause: probe-verified recoverable provider failure only (implemented, inert)

A fallback is admissible only when **all** of the following hold:

- **Closed set of causes:** `RATE_LIMIT`, `QUOTA_EXCEEDED`, or `NETWORK` (the provider is unreachable).
- **Independent agreement:** the untrusted `stderr_category` **equals** the category from a **trusted provider
  probe**.
  - The probe is model-free trusted code in the executor's trusted step, never the isolation uid.
  - It runs after the isolated run has stopped.
  - It sends one bounded request with the model key, carrying no prompt, repository content or model output.
  - It classifies only the HTTP status or the transport error.
- **No primary result:** zero primary result artifacts.
- **Primary single attempt:** exactly one primary attempt was started, with isolation and authorization verified.

**Never a fallback cause.** These are terminal exactly as today:

- authorization, transport-integrity, provenance or schema failures;
- isolation or attestation failures;
- `SECRET_IN_RESULT`, `EXECUTOR_RESULT_REJECTED`, `NO_CHANGES`;
- `MODEL_TIMEOUT` and `MODEL_NOT_FOUND`;
- generic `PROVIDER_UNAVAILABLE`;
- authentication or permission errors;
- `UNCLASSIFIED`;
- any disagreement between the two observations;
- every validation, publication or acceptance failure.

`src/hunter/automation/issue_agent_fallback_policy.py` implements this predicate. No lifecycle code calls it.

### F3. Authenticated Ubuntu-to-macOS execution transport (reuses ADR 0037 D3)

No new channel is introduced.

- **Two seals.** `authorize` seals the identical handoff plaintext to two recipients:
  - the primary's `X_EXEC`, as today;
  - a **distinct** fallback key `X_FB`.

  `AUTHORIZED` binds both ciphertext digests (`handoff_artifact`, and an optional `fallback_handoff_artifact`).
- **Where `X_FB` lives.** `X_FB` is held only by a new environment, `hunter-issue-agent-fallback-executor`, with
  branch policy `main`.
- **Download checks.** The fallback job downloads only the artifact whose id and digest match
  (`digest-mismatch: error`). `run_executor` then applies its existing checks unchanged:
  - the ciphertext digest;
  - the `TransportBinding`;
  - the issuer signature (`PromptAutomationVerifier`);
  - the lineage and prompt digests.
- **Authority.** The fallback job verifies `FALLBACK_AUTHORIZED` on the anchored ledger itself. It never takes the
  Ubuntu job's outputs as authority.

### F4. Ledger: one new state, attempt-bound identity (extends SM §2, §5)

**New state and transitions.**

- `AUTHORIZED → {RESULT_BOUND, FALLBACK_AUTHORIZED, FAILED}`
- `FALLBACK_AUTHORIZED → {RESULT_BOUND, FAILED}`

**Writers.** `bind` writes the new state in one CAS. `bind` and `reconcile` may fail it.

**`FALLBACK_AUTHORIZED` evidence (closed schema).**

| Field | Content |
|---|---|
| `primary_executor_job_id`, `primary_executor_conclusion` | the primary attempt's job facts |
| `primary_cause` | one of the F2 causes |
| `stderr_category`, `probe_category` | must be equal |
| `primary_result_artifacts` | `0` |
| `fallback_execution_id` | the attempt-2 identity (see below) |
| `fallback_handoff_artifact` | the second sealed handoff |
| `fallback_runner_label` | pinned |
| `attestation_policy_sha256` | the pinned attestation policy |
| `fallback_deadline` | the latest time the fallback may finish |

**Attempt-bound identity.**

- `fallback_execution_id = execution_identity(…, execution_attempt=2)`. This is a **ledger** field. GitHub's
  `run_attempt` stays 1. The attempt-1 derivation is unchanged, so existing `execution_id`s stay valid.
- The fallback's result is sealed under that identity, with a distinct artifact name, `hunter-ia-result-fb-<digest>`.
  A late attempt-1 result therefore cannot be bound as attempt 2, and the reverse is also impossible.

**Binding the fallback result.**

- From `FALLBACK_AUTHORIZED`, exactly one fallback artifact moves the record to `RESULT_BOUND`.
- A duplicate artifact, or any attempt-1 artifact, fails with `TRANSPORT_INTEGRITY_FAILED`.
- `RESULT_BOUND` gains three fields:
  - `execution_attempt`;
  - `executor`;
  - `attestation_sha256` (attempt 2 only).
- If the run has finished and the state is still `FALLBACK_AUTHORIZED`, `reconcile` fails it with
  `EXECUTION_NOT_COMPLETED`.

**Compatibility.**

- The record schema version is **not** bumped: a bump would orphan every existing v1 ledger.
- Readers at an older `control_sha` reject the unknown state through the closed state enum (`STATE_CORRUPT`), so
  they fail closed without one.
- Old ledgers never contain the new state, so no migration is needed.

### F5. macOS fallback executor: least privilege with no sudoers grant (amends ADR 0037 D1)

**What revision 1 got wrong.** Revision 1 proposed a standing `NOPASSWD` sudoers drop-in. It is withdrawn, because
it had five weaknesses:

1. It was a permanent privilege grant, contrary to the owner's constraint.
2. `exec *` let the caller choose any Codex arguments. A compromised runner could pass `-c` overrides,
   `--add-dir`, a different provider `base_url`, or `--dangerously-bypass-approvals-and-sandbox`.
3. Secrets could only pass `env_reset` with a `SETENV` tag, or on the command line.
4. `kill -KILL -1` was a second standing entry.
5. The isolation principal shared the owner's `staff` primary group.

**New trust domain.** D1 gains a fifth domain, **Fallback executor**:

| Property | Value |
|---|---|
| Job | `execute-fallback` |
| Runner | a dedicated self-hosted macOS runner, label `hunter-codex-fallback`, separate from `hunter-reviewer` |
| Secrets | `X_FB` only |
| `GITHUB_TOKEN` | `{}` |
| Model | yes, as `hunteragent` |
| Candidate code | never run by trusted code |

**Principals.** Both are non-admin, and they share no primary group:

- `hunterrunner` runs the Actions runner. It is never the owner's account, so the owner's `~/.codex` and keychain
  are out of reach.
- `hunteragent` runs the model.

**Launcher, with no sudo at runtime.**

- A root-installed LaunchDaemon, `com.hunter.codex-fallback`, runs as `hunteragent` (`UserName`). launchd starts it
  on demand through a socket-activated Unix socket that only `hunterrunner` can open.
- The trusted launcher (`/Library/Hunter/codex-fallback-launcher`, pinned by hash) accepts **one fixed-schema
  request**: a workroot identifier and a model name, both validated against closed patterns. It takes no argv, no
  environment and no configuration from the caller.
- The launcher itself builds the Codex argv (`codex_invocation`) and runs
  `sandbox-exec -f /Library/Hunter/codex-fallback.sb …`, then reports only an exit category.
- When the job ends, launchd kills the whole process group (`AbandonProcessGroup` false).

**One-time owner provisioning.** This is interactive and done once. No standing privilege remains afterwards. The
owner:

- creates `hunterrunner`;
- moves `hunteragent` to its own primary group;
- installs the pinned Codex binary (`/opt/hunter/codex/<version>/`);
- installs the profile, the launcher and the plist;
- registers the runner.

**Workspace.** The workroot is `0700` and owned by `hunterrunner`. `hunteragent` receives inherited ACLs on
`workspace/` and `home/` only (`chmod +a`, which the file's owner may set, so no root is needed). The trusted step
revokes the ACLs after the run, then collects with the unchanged `collect_result`.

**Sandbox profile.** It denies by default.

- **Reads:** system paths, the pinned Codex install, `workspace/`, and the per-run `home/`.
- **Writes:** `workspace/`, and the per-run `home/` and tmp.
- **Network:** `network-outbound` **only** to `localhost:<gateway-port>`. The existing `model_egress_gateway` runs as
  `hunterrunner`, with a `CONNECT` allowlist limited to the OpenAI auth and Codex hosts.

Codex's inner sandbox is defense in depth only. If P0 shows that it cannot nest inside the outer profile, the
launcher disables it, and the outer profile is the sole boundary. Codex is never run without the outer profile.

**Per-run attestation.** It runs in a trusted step before `X_FB` is used. Any failure gives `EXECUTION_NOT_STARTED`.

- **Pinned hashes:** the profile, the launcher, the plist, the Codex binary and the gateway script.
- **Principals:** `hunteragent` is not an admin; it shares no group with `hunterrunner`; and no `hunteragent`
  process is running.
- **Live cases under the production profile** (a setup error counts as FAIL):
  - identity is `hunteragent`;
  - a write inside the workspace succeeds;
  - a read of the runner's home is denied;
  - a write outside the workspace is denied;
  - a direct connection to TCP 443 is denied;
  - a gateway `CONNECT` to a host that is not allowlisted is denied.
- **Recording:** `attestation_sha256` is recorded in `RESULT_BOUND`.

**Residual risk** (accepted only on acceptance of this ADR):

- **Persistent host.** It is mitigated by a fresh workroot, ACL revocation, the process-group kill, pinned hashes,
  the attestation, and a single-job runner (concurrency `hunter-codex-fallback`).
- **Host compromise.** It exposes `X_FB` and at most one live session (F6). It never exposes `K_STATE`, the
  publisher key or `X_RESULT`.

### F6. Codex credential: owner-attended, per-run ChatGPT Pro session

**Rules.**

- **No personal credentials.** The owner's personal `~/.codex`, keyring entries and browser session are never read,
  copied or referenced.
- **No persistence.** No ChatGPT credential is persisted between runs, stored as a GitHub secret, or placed in a
  public log, and no API key is used.

**Per-run session.**

1. **Login.** Inside the launcher, as `hunteragent` under the profile, with a fresh per-run
   `CODEX_HOME` (`cli_auth_credentials_store = "file"`, mode `0700` / `0600`), the launcher starts
   `codex login --device-auth`.
2. **Code delivery.** The verification URL and the one-time user code go **only** to an owner-only local channel on
   the Mac. They never go to the Actions log, which is public. P0 selects the channel.
3. **Sign-in.** The owner, who has just approved this run (F1), signs in with the code in their own browser. Only
   the device code's own validity bounds this sign-in, with a hard host cap of 15 minutes so that a run never holds
   the host indefinitely. This is not an approval deadline. Expiry launches no model, the run reports
   `FALLBACK_SESSION_NOT_STARTED`, and the request returns to pending. The owner's next approval issues a new code.
4. **Run.** `codex exec` runs once.
5. **Teardown.** Then, in all cases, `codex logout` (P0 verifies that it revokes the session) and deletion of
   `CODEX_HOME`.
6. **Secret scan.** The trusted step reads the session's token values before teardown and adds them to the
   `result_carries_secret` scan. A result that carries any of them is never sealed.

**Exposure and usage.**

- **Exposure window:** one fallback run.
- **Exfiltration channels:** only the allowlisted OpenAI hosts and the scanned result.
- **Usage:** the owner's included Pro quota.

**This is not unattended.** The fallback is admitted automatically, but it executes only with a human approval per
run. A fully unattended fallback becomes possible only through a separate owner decision (see A7 and A8), and it
needs an amendment to this ADR.

### F7. Exact-head proof: no new authority

A fallback result enters the **identical** chain:

1. T2 `RESULT_BOUND`
2. T3 validator pre-push safety
3. T4 create-only signed publication
4. T5 candidate Draft PR
5. hosted exact-head `Hunter / Pre-PR Preflight`, followed by Hunter Merge Readiness

The validator and publisher domains, the exact `base_sha` binding, `collect_result`, the signing identity and the
Validation Stage Contract are unchanged. The executor identity is evidence only. It never relaxes a stage.

### F5a. Pinned fallback model and served-model guard (owner decision, Issue #390 comment 6706666538)

The requested model name is configuration, never evidence of the model that served the run.

- **Pin.** `config/issue_agent_codex_fallback.json` pins `codex_model` (`gpt-5.6`) at `control_sha`. A missing or
  malformed pin leaves the fallback inert. `MacCodexIsolation` refuses any other requested model before the
  launcher is contacted, whatever `HUNTER_ISSUE_AGENT_FALLBACK_MODEL` says.
- **Before the prompt.** Inside the owner's signed-in session and the outer Seatbelt profile, the launcher reads the
  session's own catalog (`codex debug models`). It must list the pinned slug exactly once, or the launcher answers
  `MODEL_UNAVAILABLE` and no model runs.
- **After the run.** Codex runs with `--json`. The launcher's verdict comes only from the pinned CLI's event
  structure and its own warning/error items. It returns `REROUTED` for a reroute event, a "routed to" CLI notice
  or a mismatching model field; `AMBIGUOUS` for an unknown-model warning, or a malformed or oversized stream;
  `UNVERIFIED` for a turn that did not complete; `VERIFIED` otherwise. Model-generated text (agent messages,
  command output) is never consulted, so it can neither prove nor forge an identity.
- **Fail closed.** Only `VERIFIED` with a clean exit lets the runner collect a result. Anything else is a failed
  fallback: nothing is sealed, uploaded or published.
- **Observed limits (pinned CLI 0.167.0, unauthenticated).**
  - The built-in catalog has no plain `gpt-5.6` entry, only `gpt-5.6-sol`, `-terra` and `-luna`.
  - `exec --model gpt-5.6` warns `Model metadata for 'gpt-5.6' not found`.

  The guard therefore rejects `gpt-5.6` unless the authenticated session's catalog offers it. Whether it does can
  only be established on the fallback Mac after the owner's device sign-in.
- **Installation.** The launcher is attested by hash, so the owner must reinstall it (`provision_codex_fallback_host.py
  --apply`) from the merged commit before the fallback can attest again.

### F8. Owner-armed fallback canary (revision 5, implemented as P9)

**Problem.** P8 requires a primary that fails for an F2 cause. A real `RATE_LIMIT`, `QUOTA_EXCEEDED` or `NETWORK`
failure cannot be scheduled, and F2's two-observation rule must not be weakened to stage one. F8 adds one bounded
way to *induce* a real, observable provider unreachability. It records that failure under its own cause, so a
staged failure can never be mistaken for, or relabelled as, a real one.

**Arming (owner only, before authorization).**

- The repository owner labels the open Issue `hunter-codex-fallback-canary` **before** the `hunter-agent-execute`
  label event.
- `authorize` re-verifies the label from the Issue timeline (actor is the owner, applied before the authorizing
  event) and records `fallback_canary: true` in the signed `AUTHORIZED` evidence. Otherwise the field is `false`.
- A canary cannot be armed after `AUTHORIZED`, cannot be selected from Issue text, prompts or workflow inputs, and
  never applies to another authorization.

**Inducement (trusted step, primary run only).**

- With `fallback_canary: true`, the trusted executor step starts the existing `model_egress_gateway.py` with a
  `canary` profile. It equals the Kilo profile except that the pinned provider host is removed from the allowlist,
  so the gateway refuses `CONNECT` to it and counts each refusal.
- The isolated Kilo run is unchanged: same container, argv, network and handoff. It fails only because its sole
  external route refuses the provider.

**Two observations (F2 unchanged in kind).**

- The untrusted `stderr_category` must be `NETWORK`.
- For a canary authorization only, the trusted probe sends its unchanged bodiless `GET` through the **same gateway
  instance** with `CONNECT`, instead of directly. It must observe the refusal, which `classify_probe` maps to `NETWORK`.
- The gateway must report at least one refusal for the pinned provider host, and none for any other host.

**Admission.**

- `bind` writes `FALLBACK_AUTHORIZED` with `primary_cause = CANARY_NETWORK`, a new closed value, only when all of
  these hold:
  - `AUTHORIZED.fallback_canary` is `true`;
  - both categories are `NETWORK`;
  - the gateway refusal evidence (count and digest) is present;
  - every other F2 condition holds.
- `CANARY_NETWORK` is never admissible without the signed flag.
- A canary authorization never admits `RATE_LIMIT`, `QUOTA_EXCEEDED` or `NETWORK`, so a canary is never
  reported as a real provider failure, and a real failure is never relabelled as a canary.

**Unchanged.**

- F1: one fallback per authorization, an owner approval label per run, and no chaining.
- F3: the handoff, `X_FB` and the download checks.
- F5 and F6: Mac isolation, the Codex gateway profile (the fallback never uses the canary profile) and the
  owner-attended device sign-in.
- F7: the exact-head proof.

**Independent verification.** A reviewer can confirm a canary from public facts alone:

- the label event's actor and time, from the GitHub timeline;
- the signed ledger: `AUTHORIZED.fallback_canary`, `FALLBACK_AUTHORIZED.primary_cause = CANARY_NETWORK`, the
  gateway refusal digest, then `RESULT_BOUND` (attempt 2);
- the exact-head Pre-PR run.

**Bounds.**

- No production authorization can carry the flag without the owner's own pre-authorization label.
- The induced failure is limited to one gateway instance in one primary run.
- No credential, key or evidence is synthesised: both categories are real observations of a real refusal.

**Implementation notes (P9).**

- *Arming.* `authorize` reads the Issue's label events to their end. The authorizing event is the owner's latest
  `hunter-agent-execute` `labeled` event at or before the signed `issue_updated_at`. The canary is armed only when
  the last canary label event strictly before it is the owner's `labeled` event, and that event is later than the
  owner's previous authorizing label event. So one arming never carries over to a later authorization. An
  indefinite read refuses the authorization rather than silently disarming it. A remediation is never armed.
- *Probe target.* The canary probe sends its `GET` to the pinned provider host (`ai-gateway.kilo.ai`) instead of the
  real probe's listing host (`api.kilo.ai`). The listing host is outside both gateway profiles, so its refusal would
  prove nothing about the canary, and would itself be a refusal "for another host". The probe sends an HTTP/1.1
  `CONNECT` the gateway can attribute to that host, and never a credential.
- *Refusal evidence.* The gateway logs one content-free `GATEWAY_REFUSED_HOST <host>` line per well-formed
  `CONNECT` it refuses for its host. The trusted step reads that instance's log before and after the probe. Evidence
  exists only when:
  - the primary was refused the provider host at least once;
  - after the agent exited, the only new refusal is the probe's single refusal of the provider host;
  - no malformed request was denied.

  "None for any other host" is enforced in this narrower form, because the pinned Kilo 7.8.18 also sends telemetry
  to `us.i.posthog.com`. Both Kilo profiles refuse that host on every run, canary or not, so its refusal says
  nothing about the canary. The canary profile differs from the Kilo profile only by the provider host, so any
  other refused host is one the Kilo profile refuses too. Such hosts are bound into the digest but never counted.
  This was observed by running the pinned CLI through this gateway in the canary profile.
- *Stderr hint.* The pinned Kilo CLI reports a refused tunnel as `Proxy refused to open a tunnel: 403 Forbidden`.
  The closed stderr vocabulary maps that text to `NETWORK`: the run's only egress route refused the connection.
  Before this mapping, the text classified as `UNCLASSIFIED`, so the canary could never be admitted. For a run
  that is not a canary, this changes nothing: the direct trusted probe still has to observe `NETWORK` itself.

  `FALLBACK_AUTHORIZED.canary_refusal` records `primary_refusals`, `probe_refusals` (always 1) and
  `refusal_digest`. The digest is the SHA-256 of the canonical JSON of the gateway instance name, the provider host
  and the ordered refused hosts. All three inputs are printed in the public executor job log, so anyone can
  recompute it.
- *Order.* The probe runs after the agent container has exited and before the isolation is torn down. That is the
  only window in which the same gateway instance still exists.

**Schema.**

- `AUTHORIZED` gains the optional boolean `fallback_canary`, and the cause vocabulary gains `CANARY_NETWORK`.
- As in F4, the schema version is not bumped. A reader at an older `control_sha` rejects the unknown field or cause
  through the closed schema, so it fails closed.

## Consequences

- **Inert until prerequisites hold.** The fallback does nothing until:
  - the owner accepts this ADR;
  - provisioning is done;
  - `X_FB` exists;
  - the runner is online.

  If any of these is missing, `bind` cannot write `FALLBACK_AUTHORIZED`, and behavior is identical to today.
- **Recovery waits for the owner, with no deadline.** No runner or host is held while the request waits.
- **The waiting limit is GitHub's.** Artifact retention bounds how long a request can wait.
  - The fallback handoff is the only copy of the sealed task (there is no confidential durable store), so it is
    uploaded with the longest retention GitHub allows, 90 days.
  - After it expires, the approved run's download step fails before the executor starts, so no model runs. The
    authorization then ends terminally (`EXECUTION_NOT_COMPLETED`), because a failed job without a not-started report
    is never assumed to have launched nothing.
- **Publish deadline.** It restarts at the fallback's own bind (`RESULT_BOUND.fallback.deadline_published_at`, 6
  hours), so a late approval is not failed by the original authorization's window.
- **The Issue stays busy.** A pending fallback keeps its authorization active, so the Issue takes no new
  authorization until the owner approves and the fallback completes, or rejects.
- **Guards and documents to extend:**
  - the state machine document;
  - the defect registry;
  - the static workflow guard (AT-41/42/47), which must cover the new job, the new environment and the
    self-hosted label (permitted only on `execute-fallback`).
- **Cost.** At most one launched extra model run per authorization, on the included plan quota.

## Alternatives Considered

- **A1. Fall back inside the Ubuntu executor.** Rejected. There is no attested Codex isolation there, and it would
  put a second credential beside `X_EXEC`.
- **A2. Trust Kilo's stderr or the advisory alone.** Rejected, because both can be influenced by the model.
- **A3. Reuse `X_EXEC` on the Mac.** Rejected. A compromise of the persistent host would expose the primary key.
- **A4. Revision 1's `NOPASSWD` sudoers rules.** Withdrawn for the five weaknesses listed in F5.
- **A5. Run the runner as `hunteragent`.** Rejected, because the runner, the secrets and the model would share
  one uid.
- **A6. Keep ADR 0037 as is.** Rejected by the owner (Option 1).
- **A7. A persisted ChatGPT `auth.json` on the self-hosted runner** (unattended, zero cost). Not selected. OpenAI's
  CI/CD guide excludes public repositories. A long-lived, account-wide refresh token would also sit where the model
  principal can read it. It would be viable only if the repository became private, and even then an amendment
  would have to address the model's access to the token.
- **A8. A dedicated API key, or a Business/Enterprise access token** (unattended, officially supported). Not
  selected, because it costs money, which the owner excluded. It is the smallest change to F6 if that constraint
  changes.
- **A9. Copy the owner's personal credentials.** Prohibited by the owner.

## Implementation Plan (gated on acceptance unless marked done)

Everything stays on the single Issue #390 branch, tests first, with focused verification. Nothing is pushed
without owner approval.

| Step | Change | Focused tests | State |
|---|---|---|---|
| **P0** host checks | Nested sandbox, production profile denials, Codex startup, device-code sign-in through the gateway, ACLs, launchd keys | see "P0 evidence" | **done** (logout revocation and launchd run pending provisioning) |
| **P1** policy | F2 predicate | `test_issue_agent_fallback_policy.py` | **done** |
| **P1b** refusal regression | Behavioral Codex refusal before workspace or launch | mutation-checked | **done** |
| **P2** provider probe | `issue_agent_provider_probe.py`: one bodiless, no-redirect, authenticated `GET`; closed status table; healthy → `UNCLASSIFIED` | `test_issue_agent_mac_fallback.py` (probe cases) | **done** |
| **P3** ledger | `FALLBACK_AUTHORIZED`, attempt-2 identity, cross-binding checks, `advance` rules, `AuthorizationView.execution_id` for every later reader | `test_issue_agent_fallback_ledger.py`, `test_issue_agent_fallback_control.py` | **done** |
| **P4** handoff | Dual seal in `authorize`; `fallback_handoff_recipient` trust root; `bound` exposes the second artifact id from the ledger | `test_issue_agent_authorize_fallback.py`, `test_issue_agent_fallback_executor.py` | **done** |
| **P5** Mac isolation | `codex_fallback_launcher.py` (closed schema, peer-uid check, launcher-built argv); `codex-fallback.sb`; plist template; `MacCodexIsolation` (installation hashes, principals, live cases, ACL workroot); loopback gateway profile | `test_issue_agent_mac_fallback.py`, `test_issue_agent_fallback_executor.py` | **done** |
| **P6** session | Device-code session with owner-only inbox, window, logout and delete on every path, token scan | launcher and port tests | **done** |
| **P7** workflow, docs | `execute-fallback` (self-hosted, `X_FB` only), `bind-fallback`, validate gating; static guard extended; state machine document | `test_issue_agent_workflows.py` | **done** (defect registry entry deferred to independent review) |
| **P8** end to end | After push and merge approval: an owner-labelled Issue with the primary failing recoverably (F8 canary, P9), observed through `FALLBACK_AUTHORIZED`, then `RESULT_BOUND` (attempt 2), validated, published, Draft PR, and hosted Pre-PR Preflight on the exact head | exact-head SHA equality; ledger chain verified | gated |
| **P9** canary (F8, revision 5) | Owner label verified in `authorize`; `fallback_canary` in `AUTHORIZED`; gateway `canary` profile with refusal counter; canary-only probe via the gateway; `CANARY_NETWORK` admission | positive canary admission; flag absent → terminal; real `NETWORK` on a canary authorization → terminal; label after authorization → unarmed; non-owner label → unarmed; refusal for another host → terminal | implemented (`tests/test_issue_agent_fallback_canary.py`) |
