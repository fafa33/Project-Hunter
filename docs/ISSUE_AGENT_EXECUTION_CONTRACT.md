# Issue Agent Execution Contract

This document is the architecture and failure-state contract for the governed
Issue Agent path, from an owner-signed Issue authorization to a Draft pull
request that enters the existing Candidate Admission and governance chain. It
names one execution contract, and every component listed here is implemented
and proven against it together.

It adds no new authority. Candidate Admission, Hunter Governance Review,
Pre-Ready review, Merge Readiness and owner merge approval keep exactly their
existing definitions (`docs/HUNTER_GOVERNANCE_REVIEW.md`,
`docs/MERGE_READINESS_GATE.md`, `docs/CODE_WRITE_POLICY.json`). This contract
defines how a Railway execution produces a candidate those authorities can
evaluate.

## Why this contract exists

Before this contract, the Railway runtime advanced one static branch named by
the deployment variable `HUNTER_ISSUE_AGENT_EXECUTION_BRANCH`, cloned at
startup from that branch's own head. Three results followed from that:

- the signed implementation scope (`branch_pattern`, `base_ref`, `base_sha`)
  was carried to the prompt but never used by the runtime;
- the agent worked on whatever commit the shared branch happened to hold, not
  on the owner-pinned `base_sha`. In production the branch was 30 commits
  behind the pinned base of Issue #496;
- the shared branch name binds no Issue (`issue_for_branch` in
  `scripts/hunter_governance_review_v2.py`), and no step opened a pull
  request, so a successful execution could never become an admissible
  candidate.

## Target execution chain

```text
Issue authorization (owner-signed, exact implementation scope)
  -> execution target: exact base_sha + deterministic issue branch
  -> isolated per-authorization clone-capable workspace forked at base_sha
  -> governed agent provider
  -> targeted validation (exact head, fork point, linear history, preflight)
  -> guarded candidate push (create-only lease; signed, pre-push bound)
  -> Hunter / Pre-PR Preflight (existing, on push)
  -> Hunter / Issue Agent Candidate PR workflow -> Draft PR
  -> Candidate Admission / Hunter Governance Review (existing, clone-capable)
  -> Pre-Ready review (existing)
  -> Hunter Merge Readiness (existing)
  -> owner approval and merge (human; never automated)
```

Railway never opens the pull request. A GitHub workflow opens it after the
candidate branch is pushed. Connector admission is not used on this path; the
Issue Agent is an ordinary clone-capable writer.

## Invariants

### I1. The execution target is derived only from the signed authorization

`derive_execution_target` (`src/hunter/automation/issue_agent_workspace.py`)
is a pure function of the verified signed authorization:

| Field | Derivation | Refusal |
|---|---|---|
| `branch` | `issue-{issue_number}-{first 16 hex of the authorization identity digest}` | must match the signed `branch_pattern` and must bind the same Issue under the governance branch binding |
| `base_ref` | signed `implementation_scope.base_ref` | must be `main`, the only base the governance chain admits |
| `base_sha` | signed `implementation_scope.base_sha` | must be a full 40-character commit SHA |

It runs after the issuer signature is verified and **before** the ledger
claim. A document whose target cannot be derived is refused with HTTP 403
without consuming its authorization identity. Nothing in Issue text,
deployment configuration or provider output can choose or change the branch
or base. `HUNTER_ISSUE_AGENT_EXECUTION_BRANCH` is retired: it is ignored, and
startup logs a warning when it is still set.

### I2. The base is immutable per authorization

The workspace is forked at exactly `base_sha`. A changed base is a changed
authorization: the owner edits the Issue scope, which produces a new signed
authorization, a new authorization identity, and so a new branch. An existing
authorization branch is never rebased and `main` is never merged into it.
Targeted validation and the Draft PR workflow both refuse a candidate whose
history from `base_sha` to the head is not linear.

### I3. One isolated workspace per authorization

Each execution materializes a fresh workspace beneath the configured
workspace root (`HUNTER_ISSUE_AGENT_REPO_DIR`, which must lie under
`/app/.hunter-runtime-checkouts` on Railway). The workspace is named by the
authorization digest. Its `origin` is the canonical credential-free
`https://github.com/<owner>/<name>.git`. It fetches `main` and `base_sha`,
verifies that `base_sha` is a commit reachable from `main`, and checks out the
target branch at `base_sha`. The workspace is removed after the execution
reaches a terminal outcome. No workspace is shared between authorizations,
and none is prepared at startup.

### I4. The remote branch is created, never taken over

Before any provider runs, the remote target branch must be absent or already
point at `base_sha`. Any other remote head is a foreign write and fails the
execution closed. The trusted OpenCode publication pushes with a create-only
lease (`--force-with-lease=refs/heads/<branch>:`) when the branch is absent,
or with an exact-head lease otherwise. If validation fails after a push it
created, it deletes the branch it created; otherwise it restores the previous
head. Head advancement is measured against `base_sha` when the branch does not
exist yet.

### I5. The candidate is clone-capable and signed

Publication keeps the existing clone-capable discipline: a signed commit under
the authorization-bound writer identity, pushed through the repository
`.githooks/pre-push` boundary. Candidate Admission then requires a verified
signature from an authorized signer over the whole range, plus the trusted
hosted exact-head preflight proof. This contract changes neither requirement.

### I6. The Draft PR is opened by GitHub, conservatively

`.github/workflows/hunter-issue-agent-candidate-pr.yml` runs when
`Hunter / Pre-PR Preflight` completes successfully for a `push` whose head
repository is this repository.

`workflow_run` runs with this repository's permissions and secrets, so the job
never checks out, installs or executes candidate content. Its only checkout is
the workflow's own trusted default-branch commit (no ref is taken from event
data). Candidate identifiers reach the trusted script only as environment
values, and the dedicated token exists only in that one step.

The trusted default-branch decision module
(`scripts/hunter_issue_agent_candidate_pr.py`) opens a Draft PR only when all
of the following hold:

- the candidate head repository is this repository, never a fork;
- the head branch has the exact agent-branch shape
  `issue-<n>-<16 lowercase hex>` and binds Issue `<n>`;
- the workflow-run head SHA is still the branch head;
- Issue `<n>` exists and is an Issue, not a pull request;
- no pull request is open for that branch;
- the commit range from `main` is complete and non-empty, ends at the head,
  and contains no merge commit;
- every commit in that range carries a verified signature from an authorized
  signer in `docs/CODE_WRITE_POLICY.json`.

The PR is created with a dedicated token (`HUNTER_ISSUE_AGENT_PR_TOKEN`), not
the workflow `GITHUB_TOKEN`. GitHub does not start `pull_request` workflows for
events created by `GITHUB_TOKEN`, so a PR opened with it would never receive
`Quality Gates`, `dependency-review`, `CodeQL` or `Hunter Governance Review`.
When the secret is missing, the workflow fails closed with an explicit error
and opens nothing. The workflow never marks a PR ready, never approves and
never merges. A Draft PR creates no authority, so opening one grants the
candidate nothing that Candidate Admission has not independently verified.

### I7. Post-ACK outcomes are observable without Railway logs

The execution ledger records the execution target (`execution_branch`,
`base_sha`) at dispatch, and on success the provider and `head_after`. Every
terminal failure records a bounded `failure_code` from a fixed vocabulary,
alongside the existing `failure_type`. The issuer serves
`GET /issue-agent/status/<authorization_id>` through the public ingress. It
returns only non-secret fields:

- `state`, `claimed_at`, `dispatched_at`, `completed_at`, `failed_at`;
- `execution_branch`, `base_sha`, `head_after`, `provider`;
- `failure_type`, `failure_code`;
- `failure_attempts`, the per-provider attempt outcomes of an exhausted
  provider pool. These are runtime-owned fixed strings, never provider output.

It never returns the handoff document, the prompt or free-form failure text.
Malformed identities are refused by the ingress before anything is forwarded.

### I8. The provider is self-checked at startup

When the OpenCode provider is configured, Railway startup runs
`hunter.automation.opencode_provider_self_check` before launching any child.
It uses the issuer's environment, never the Source Handling signing key. The
probe runs the configured provider exactly as a real execution does:

- the same sandbox launcher and permission contract;
- the same pinned runtime and the same model;
- a disposable attempt workspace.

The probe instructs the provider to run one shell command, which creates a
marker named by a random nonce. The nonce exists only in the provider's
process environment, never in the prompt, so a file-writing tool cannot forge
the marker; only an executed shell command can. Startup fails closed, and the
issuer never starts, if:

- the pinned runtime's resolved tool set offers `bash`, `webfetch`,
  `websearch`, `task`, `skill` or `question`. The sandbox shim checks this
  against `opencode debug agent build` before every run, not only at startup;
- the nonce marker exists after the run, meaning a shell command executed;
- the provider cannot complete the probe (unreachable, rejected by its
  service, or rate-limited). An execution path that cannot be proven safe is
  not started.

Evidence behind this check, from OpenCode 1.18.30 driven by a local stand-in
model that always requests a `bash` tool call:

- under Hunter's contract the model is offered only `edit`, `glob`, `grep`,
  `read` and `write`, and the `bash` call is rejected as an unavailable tool;
- with `bash: allow` the same call executes.

`tests/test_opencode_provider_self_check.py` repeats that experiment when
`HUNTER_TEST_OPENCODE_EXECUTABLE` names the pinned runtime.

### I9. OpenCode runs in the directory it is given

OpenCode 1.18.30 resolves its project directory from the inherited `PWD`
variable, not from the process working directory. Before this contract, the
Railway sandbox shim launched OpenCode with the workspace as its working
directory but with the issuer's `PWD`, so every read and edit targeted the
issuer's directory instead of the isolated workspace. Every OpenCode process
now runs with `PWD` bound to its own working directory (`/workspace` inside
the bubblewrap sandbox), and `OLDPWD` removed.

## Failure states

| # | State | Where detected | Outcome | Authorization consumed |
|---|---|---|---|---|
| F1 | issuer signature invalid / foreign repository / non-owner | `prepare_authorization` | 401/403 | no |
| F2 | scope has no `base_sha`, `base_ref != main`, or the derived branch does not match `branch_pattern` | `derive_execution_target`, before claim | 403 | no |
| F3 | authorization already claimed (replay, including A33/A34/A35) | ledger claim | 409 | already |
| F4 | Source Handling / pre-model rejection | before or after claim, as today | 422 | as today |
| F5 | `base_sha` not a commit reachable from `main` | workspace materialization | ledger `FAILED`, `failure_code=BASE_NOT_ON_MAIN` | yes |
| F6 | remote branch exists at a foreign head | workspace materialization | `FAILED`, `REMOTE_BRANCH_CONFLICT`; nothing pushed | yes |
| F7 | git transport failure while materializing | workspace materialization | `FAILED`, `WORKSPACE_UNAVAILABLE` | yes |
| F8 | every provider fails, is rate-limited, or does not advance the head | fallback dispatcher | `FAILED`, `PROVIDER_POOL_EXHAUSTED`, per-provider `failure_attempts` | yes |
| F9 | candidate not linear from `base_sha`, head mismatch, or preflight red | targeted validation | provider attempt failed; a branch the trusted publication created is deleted | yes |
| F10 | provider environment unsuitable | fallback dispatcher | `FAILED`, `ENVIRONMENT_UNSUITABLE` | yes |
| F11 | issuer restarted mid-execution (lease lapsed) | startup recovery | `FAILED`, `PROCESS_RESTART` | yes |
| F12 | pushed branch not agent-shaped, merge commit present, unsigned head, PR already open, Issue missing | Draft PR workflow | no PR; the workflow reports the refusal | n/a |
| F13 | `HUNTER_ISSUE_AGENT_PR_TOKEN` missing | Draft PR workflow | workflow fails closed; no PR | n/a |
| F14 | provider self-check fails: forbidden tool offered, shell executed, or probe cannot complete | Railway startup | no child starts; the service is not ready | n/a |

A consumed authorization is never retried. To try again, the owner creates a
new Issue event, which produces a new authorization, identity and branch.
Re-running an old GitHub event can only reproduce F3.

## Operator prerequisites

These are deployment facts that this repository cannot establish by itself:

1. **Repository secret `HUNTER_ISSUE_AGENT_PR_TOKEN`**: a fine-grained token
   of the repository owner with `pull_requests: write` and `contents: read` on
   this repository.
2. **Commit signing on Railway**: the publication signing key must be
   registered on the owner's GitHub account, so that GitHub reports the
   agent's commits as verified. Unverified commits fail Candidate Admission
   by design.
3. **`HUNTER_ISSUE_AGENT_REPO_DIR`** is now the workspace root, for example
   `/app/.hunter-runtime-checkouts/issue-agent`. Remove
   `HUNTER_ISSUE_AGENT_EXECUTION_BRANCH`; it is ignored.
4. **Provider**: at least one provider must pass the startup self-check.

## Proof

`tests/test_issue_agent_candidate_topology.py` proves the chain end to end
against a local bare Git repository standing in for GitHub:

- the real trigger signs the authorization;
- the real issuer edge admits it over HTTP;
- the real workspace runtime and fallback dispatcher materialize the
  workspace and run a deterministic stand-in provider;
- the real targeted validation checks the result;
- the real Draft PR decision module and governance branch binding evaluate the
  pushed candidate;
- the status endpoint reports the outcome.

The same file carries the negative cases for F2, F3, F5, F6 and F9, and for
F12 at the PR decision. The hosted pieces that a local test cannot exercise
are the GitHub workflow runtime, the token permissions and the real provider.
They are verified by the first authorized low-risk canary, not by Issue #496.
