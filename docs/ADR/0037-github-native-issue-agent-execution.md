# ADR 0037: GitHub-Native Issue Agent Execution and Railway Retirement

## Status

**Accepted** (2026-10-04, owner decision on Issue #560: architecture/FMEA gate approved, with revision 3
plus the A-3 live evidence and zero open architecture blockers). Governed by Issue #560.

- **Revision 3.** Incorporates the owner decisions on OD-1 to OD-6 and the Slice-0 live proofs.
- **Implementation authority.** Implementation proceeds only through the Plan's slices, in migration order,
  under one Draft PR for #560. There is no merge without explicit owner approval. Nothing irreversible
  (Railway deletion, revocation) happens before its slice's prerequisites hold.
- **Gate status.** ADR 0037 and [ADR 0038](0038-source-handling-github-native-authority-store.md) are
  accepted, and every item in [Blocker status](#blocker-status) is closed.

Companions (same status; this ADR wins on conflict):

| Document | Owns |
|---|---|
| `docs/ISSUE_AGENT_GITHUB_NATIVE_STATE_MACHINE.md` (SM) | durable state, anchor integrity, every transition, recovery, identities, receipt reuse |
| `docs/ISSUE_AGENT_GITHUB_NATIVE_FMEA.md` (FMEA) | threat model, failure modes, **S0 evidence for A-1 to A-12**, adversarial and mutation proofs |
| `docs/ISSUE_AGENT_RAILWAY_RETIREMENT_PLAN.md` (Plan) | RETAIN/ADAPT/DELETE, `CODE_WRITE_POLICY` entries (OD-3, §2.4), OD-6 trace (§6), slices, canary, go/no-go |
| `docs/ADR/0038-source-handling-github-native-authority-store.md` | the ADR 0036 amendment (OD-5) |

## Context

### Owner decision

Railway is retired with no always-on replacement. The target path:

```text
owner-authorized Issue -> GovernedEngineeringTaskIngress -> SmartPromptMachine
  -> EngineeringContextAuthority / DPM -> signed immutable execution manifest
  -> ephemeral untrusted executor -> trusted credential-free validator
  -> credential-isolated publisher -> exactly one Draft PR
```

### Current state (trusted `main` at `32d72f1`)

- **Railway.** The trigger mints the owner-signed v2 authorization and `TaskScopeContract`, then POSTs to
  the Railway service `Project-hunter/hunter-issue-agent-issuer`. That service runs ingress, SPM and ECA/DPM
  over SQLite on its volume.
- **Railway variables (read-only names trace, 2026-10-04).** The service holds:
  - `HUNTER_AGENT_GITHUB_PUSH_TOKEN` and the whole fallback provider pool `HUNTER_AGENT_*_COMMAND`;
  - `HUNTER_SOURCE_HANDLING_SIGNING_KEY` and `HUNTER_PROMPT_AUTOMATION_SIGNING_KEY`;
  - three base64 Issue bodies (`HUNTER_ISSUE_441/457/461_BODY_B64`).
- **#522.** Railway model execution fails closed.
- **#524.** Merged the hostile-result contract, the receipt, and the plumbing publisher.
- **DFF-028 is live on `main`.** `publish_create_only` → `_run_pre_push_safety` (PRH-067).
- **#558.** Unmerged. Its P1 (`COMPLETED` while the candidate lived only in Railway RAM) is the
  motivating defect.
- **Parallel authorities.** `agent-fallback-run`, `opencode_provider_runtime` and the n8n transport (B-8).
- **`CODE_WRITE_POLICY`.** Admits only verified signatures by `claude` or `fafa33`, and only the
  `local_git_push` code-write path.

## Owner decisions

| ID | Decision (2026-10-04) | Where applied |
|---|---|---|
| B-1 | sealed artifacts are transport only, bound to identity, fail closed, no model rerun | D3 |
| B-2 | GitHub-native append-only signed state with CAS; nothing in RAM or artifacts is authoritative; prove CAS | D2; S0 A-1 proven |
| B-3 | prompt inputs bound before execution; no exact-reconstruction claim | D4 |
| B-4 | replace assumptions with proof; any falsification reopens the ADR | FMEA §4: two falsifications found and **resolved by redesign** in this revision (D2, D3) |
| B-5 / OD-2 | dedicated **signing-only** SSH key for `fafa33`, isolated to the publisher environment; report if provisioning cannot be proven | D6; **A-3 proven live (BLK-1 closed)** |
| B-6 | #558 not merged; carry only independently truthful records | Plan §3 |
| B-7 / OD-4 | integrity from signatures, workflow trust and CAS; **R-1 rollback not acceptable; add anti-rollback** | **D2a**: a ruleset-anchored forward-only branch, live-proven |
| B-8 / OD-6 | one canonical path; trace before revocation | D9; Plan §2.3, §6 |
| OD-1 | S0 live proofs in a sandbox only | done: `fafa33/hunter-560-s0-sandbox`; FMEA §4 |
| OD-3 | least-privilege `CODE_WRITE_POLICY` entry for the publisher | D6; Plan §2.4 (proposed JSON) |
| OD-5 | ADR 0036 amendment direction, conditional on the OD-4 result | ADR 0038 (Accepted 2026-10-04) |

## Decision

### D1. Topology: one lifecycle run, four isolated trust domains

| Domain | Jobs | Secrets (environment, branch policy `main`, no reviewers) | `GITHUB_TOKEN` | Model | Candidate code |
|---|---|---|---|---|---|
| Control | `authorize`, `bind`, `record-validation`, `finalize`, reconcile, Draft-PR record job | K_AUTH, K_SH, K_SPM, K_STATE | `contents: write` (state branches only), `issues: write`, `actions: read/write` | no | no |
| Executor | `execute` | model key, X_EXEC | `{}` | yes, as isolation uid | no |
| Validator | `validate` | X_RESULT (trusted intake step only) | `{}` (`actions: read` on resume) | no | only as isolation uid with `env -i`, no network |
| Publisher | `publish` | signing-only SSH key, push token (`contents: write`, no `workflows`), X_RESULT | `contents: read`, `issues: read`, `pull-requests: read` | no | **never** |

**Live-proven building blocks.**

- An attacker-modified workflow dispatched on a non-`main` ref is refused environment access: "Branch …
  is not allowed to deploy to … due to environment protection rules" (A-4).
- Each job is a fresh VM (A-12).
- The isolation uid can read neither the environment nor the memory of `Runner.Listener`,
  `Runner.Worker` or the step shell. It cannot use sudo or docker, cannot traverse `/home/runner` (mode
  750), and its HTTPS and DNS are blocked by an `iptables` owner match (A-6).

**Rules.**

- No `id-token: write` anywhere, and no Actions cache.
- **Content-processing jobs** run exactly the bound `control_sha`, including their resume runs. These are
  `authorize`, `execute`, `validate` and `publish`.
- Observation and terminal writers may run any trusted `main` descendant of `control_sha` that supports
  the record schema.
- Every fresh-run job refuses `run_attempt != 1`.

### D2. Durable state authority (B-2, B-7)

**Location (revised).** One **forward-only branch per Issue**, `refs/heads/hunter-state/v1/issue-<n>`. It
is the existing execution ledger relocated, with no second ledger. Each transition is one commit whose
first parent is the previous state commit.

**CAS.** `git push --force-with-lease=<ref>:<observed>`; creation uses an empty lease. Live proof on
GitHub with `GITHUB_TOKEN`: 30 concurrent writers against both an unprotected ref and the protected branch
gave **exactly one winner per observed head**. Losers were rejected server-side (`cannot lock ref`) or
client-side (`stale info`) (A-1).

**Record validity.** A record is valid only if **all** of the following hold:

1. its signature by a pinned K_STATE key verifies;
2. the hash chain holds (`prev_record_sha256`, contiguous `record_seq`, first-parent lineage);
3. the transition is legal and carries its required evidence;
4. the immutable bindings are unchanged;
5. trusted workflow provenance holds: the run's workflow path is allowlisted, it ran on `main`, its
   `head_sha` is `control_sha` or a descendant, attempt 1, and the role is allowed this transition;
6. CAS lineage holds;
7. **anchor integrity holds** (D2a).

The ref name itself is not a security boundary.

### D2a. Anti-rollback anchor (OD-4)

**Mechanism.** A repository ruleset ("anchor ruleset") targets `refs/heads/hunter-state/**` with these
properties:

- rules `deletion` and `non_fast_forward`;
- `bypass_actors: []`, so `current_user_can_bypass` is `never`, admin included;
- enforcement `active`.

GitHub then enforces **forward-only** movement server-side. A previously valid prefix cannot become the
current head by moving the ref backward.

**Live S0 proof.**

| Attempt | Result |
|---|---|
| force rollback by `GITHUB_TOKEN` | rejected (rule violations) |
| lease rollback by `GITHUB_TOKEN` | rejected |
| delete by `GITHUB_TOKEN` | rejected |
| force rollback by the **repository admin** over SSH | rejected: `GH013: Repository rule violations … push declined` |
| delete by the repository admin | rejected |
| fast-forward | accepted |
| control case: same rollback on an unprotected custom ref | **accepted** (the revision-2 R-1 was real) |
| control case: tag rulesets | **not enforced** in this repository class; create, force-move and delete were all accepted. **Tags are therefore unusable.** |

**Tamper detection of the anchor itself.** An admin can change the ruleset. Every verifier therefore checks,
with **authenticated** reads (anonymous `GET /rulesets/{id}` was proven CDN-stale):

- the pinned ruleset id still exists, with `enforcement == active`;
- its rules include `deletion` and `non_fast_forward`;
- `GET /rules/branches/<state branch>` attributes both rules to the pinned id;
- `updated_at` equals the pinned value.

Live proof: an admin disable followed by a re-enable moved `updated_at` from `23:01:13.071Z` to
`23:15:23.241Z`, as read by `GITHUB_TOKEN`. While the ruleset was disabled, `rules/branches` returned `[]`.

**Failure behaviour.**

- **Any anchor mismatch freezes every Issue state** (`ANCHOR_INTEGRITY_FAILED`). Re-anchoring is a
  governed trust-root change on `main`: new pin, owner quarantine of non-terminal work.
- **Forward forgery.** A `contents: write` holder can only *append*. An appended commit without a valid
  K_STATE record, provenance and chain fails verification and freezes the Issue (`STATE_CORRUPT`). That is
  a liveness impact only; it is never a rollback and never a false transition.

**Why not the alternatives.**

- Tags: live-falsified.
- Custom refs: unprotectable.
- Sigstore attestations: need `id-token: write` and an external transparency log, and cannot by
  themselves expose "the latest" head.
- A private state repository: excluded by B-7.

### D3. Sealed transport, never state (B-1), revised for A-5

**Envelope.** The handoff and the result travel as X25519 + HKDF + ChaCha20-Poly1305 artifacts with
1-day retention. The associated data binds:

```text
{schema_version, payload_kind, repository_id, issue_number, authorization_id, base_sha,
 task_scope_sha256, execution_id, handoff_sha256 (result), plaintext_sha256, recipient_key_id}
```

**Falsification found in S0.** Artifact names are **not** unique within a run: a second same-name upload
succeeded and produced two artifacts. Downloading by name returned the first.

**Binding (revised).** Binding is by **artifact id** only:

- at bind time, exactly one artifact with the expected name must exist in the bound run. Otherwise the
  result is `TRANSPORT_INTEGRITY_FAILED`;
- every consumer downloads by `artifact-ids`, with the default `digest-mismatch: error`;
- the REST `digest` equals the upload output digest (proven).

**Further S0 facts.**

- `retention-days: 1` gives `expires_at = created + 24 h`.
- Cross-run download works with `actions: read`.
- A missing, expired, corrupt or mismatched transport → `FAILED`; **no model rerun**.

### D4. Pre-execution prompt binding (B-3)

`AUTHORIZED` binds:

- `prompt_input_manifest_sha256` (authorization, Issue title and body digests, TaskScope, base, document
  id, Source Handling record ids, SPM profile and context policy, the DPM/ECA context digest and registry
  blob, the cutoff);
- `compiler_identity_sha256` (`control_sha`, Python version, constraints digest, SPM and ECA blob ids);
- `prompt_artifact_id`, `prompt_sha256` and `handoff_sha256`.

The build records `EXACT_RECONSTRUCTION_UNAVAILABLE` / `NO_CONFIDENTIAL_DURABLE_STORE`. Exact-proof
consumers fail closed.

### D5. Lifecycle

```text
AUTHORIZED -> RESULT_BOUND -> VALIDATED -> PUBLISHED -> COMPLETED
     \______________\______________\___________\__________-> FAILED
```

- `PUBLISHED` is the trusted publication ACK.
- `COMPLETED` is written only after the single Draft PR is observed.
- The freeze codes `STATE_CORRUPT`, `STATE_ROLLBACK_SUSPECTED` and `ANCHOR_INTEGRITY_FAILED` block all
  automated writes.
- Full transition semantics are in SM §5.

### D6. Publication identity, create-only, canonical signing (B-5, OD-2, OD-3)

- **Identities.** `execution_id` and `publication_identity` as defined in SM §2. The commit is deterministic
  (fixed identity, dates and template, SSH Ed25519), so a retry yields the identical head. That is proven
  locally (L-2), and GitHub `verified` for the bound writer is **proven live** (FMEA A-3).
- **Signer (OD-2a).** A dedicated **signing-only** SSH key registered on `fafa33` as a *signing* key (never
  an authentication key), held only in the publisher environment. It never enters the executor, the
  validator candidate step, logs, artifacts, caches, annotations, or untrusted code. The canonical identity
  `Farhad5778 <34549283+fafa33@users.noreply.github.com>` is preserved.
- **Code-write path (OD-3).** The proposed least-privilege policy entries are in Plan §2.4:
  - `issue_agent_publisher` authorizes exactly create-only publication of one deterministic signed commit to
    `issue-<n>-<16 hex>` from validated data, with no hook execution.
  - `issue_agent_state_ledger` classifies state-branch writes as non-code ledger writes.

  Neither is a generic bypass.
- **PR.** Only the existing candidate-PR workflow opens the Draft PR, after the exact-head Pre-PR proof. It
  adopts by head.

### D7. Authority reuse

The canonical composition order is unchanged. Source Handling persistence moves to the anchored store
under ADR 0038, as its own branch `hunter-state/v1/source-handling` with its own CAS. It is not merged
into the Issue ledger, so ADR 0036's sole-publisher boundary is preserved.

### D8. Restart safety, no silent rerun, fresh execution

The same as revision 2: a pure `advance` driven by definitive facts. Only validation and publication may
resume, nonce-bound. The model is never re-dispatched. A fresh execution requires the owner to relabel.

### D9. Exactly one canonical path (B-8, OD-6)

These are retired: the `agent-fallback-run` and `n8n-canary` entries, `agent_fallback*.py`,
`opencode_provider_runtime.py`, the `n8n.py` transport, `n8n_canary.py`, and `issue_agent_workspace.py`.
They are fail-closed in S5 and deleted in S8.

The OD-6 trace (Plan §6) found **no live dependency outside the retired Issue-agent path**:
`HUNTER_AGENT_GITHUB_PUSH_TOKEN` exists only on `Project-hunter/hunter-issue-agent-issuer`, and no n8n
configuration exists in GitHub or Railway. Revocation stays a migration step (S6/S8), per OD-6.

### D10. Confidentiality

The same as revision 2. The retirement inventory additionally covers the Issue bodies stored in Railway
variables.

## Consequences

- Railway, the parallel fallback/n8n authorities, and the SQLite ledgers leave the production graph.
- The state lives in public, ruleset-anchored `hunter-state/**` branches holding non-secret records. They
  appear in the branch list. `GITHUB_TOKEN` pushes to them trigger no workflows (live-proven). Humans must
  never push there; the CAS and the anchor still apply if they do.
- Changing the anchor ruleset is a governed trust-root rotation that freezes in-flight work.
- Keys: K_SH and K_SPM are rotated off Railway (both were present in Railway variables). New: K_STATE,
  X_EXEC, X_RESULT, and the signing-only publisher key.

## Alternatives Considered

| Alternative | Why not selected |
|---|---|
| Railway authority (#558) / other SaaS / private state repo | owner decisions |
| Custom ref `refs/hunter/**` (revision 2) | **live-falsified for anti-rollback**: a force rollback was accepted; no ruleset can target it |
| Ruleset-protected tag log | **live-falsified**: tag rulesets were not enforced (create, force-move, delete accepted for admin and `GITHUB_TOKEN`) |
| Sigstore/attestation monotonic anchor | needs OIDC and an external log; cannot by itself expose the latest head; unnecessary given D2a |
| REST `PATCH /git/refs` fast-forward CAS | documented (409) but `CODE_WRITE_POLICY` forbids Git Data API ref writes for code; the git-protocol lease is proven |
| One-job execute/validate/publish; quarantine-ref publication; publisher-side pre-push hook | revision 2 reasons (credential and candidate co-residency, DFF-028) |

## Blocker status

| ID | Status | Detail |
|---|---|---|
| BLK-1 (OD-2 / A-3) | **closed** (2026-10-04) | sandbox `fafa33/hunter-560-s0-sandbox`, branch `a3-signing-proof-1791072629`, commit `0a8e46496fa0645c6d447605234a12ae137e0330` (SSH signature; author and committer `Farhad5778 <34549283+fafa33@users.noreply.github.com>`, both resolving to login `fafa33`): GitHub `verification.verified=true`, `reason=valid`. Independently re-read on 2026-10-04 via the authenticated owner proof and the public commits API. The throwaway signing-only key (id 1218014) was deleted afterwards: the public `GET /users/fafa33/ssh_signing_keys` lists only the pre-existing key 1160850. Persistent verification keeps the commit Verified. |
| OD-1 | closed | S0 ran in the sandbox only |
| OD-3 | **accepted** (2026-10-04) | Plan §2.4 entries are binding; they are committed to `CODE_WRITE_POLICY.json` in S1 |
| OD-4 / R-1 | **resolved by design + live proof** | D2a. Residual: liveness-only freeze on forward forgery or ruleset edits. |
| OD-5 | **accepted** (2026-10-04) | ADR 0038 accepted together with this ADR |
| OD-6 | trace complete; revocation deferred to the migration step | Plan §6 |
| A-5 falsification | resolved by redesign | D3, artifact-id binding |
| Tag-anchor falsification | resolved by redesign | D2a, branch anchor |
| Admin-scope residual | accepted by construction | Only a repository admin can weaken the anchor, and that is detected and freezes. Owner-level compromise is outside every repository control. |

## Implementation Status

None. Architecture and Slice-0 sandbox evidence only.

## Sources consulted

As in revision 2. Additionally:

- the S0 sandbox `fafa33/hunter-560-s0-sandbox` (runs cited in FMEA §4);
- `CODE_WRITE_POLICY.json`;
- the Railway project, service and variable **names**, read-only;
- GitHub REST and docs pages cited in FMEA §4.
