# Issue #575 — mandatory Issue Agent workspace admission

The existing Issue Agent workspace runtime now **fails closed** before workspace
materialization and provider dispatch unless the trusted issuer supplies all of:

- `HUNTER_WORKSPACE_REGISTRY_PATH`: absolute trusted registry file path;
- `HUNTER_WORKSPACE_AUTHORITY_KEY_HEX`: 32+ byte secret HMAC key, hex encoded,
  provisioned only in trusted issuer runtime (never in a PR, log or candidate);
- `HUNTER_WORKSPACE_CHECKPOINT`: approved exact checkpoint identity;
- `HUNTER_WORKSPACE_LEASE_TOKEN`: secret active lease token, not stored in plaintext
  in the registry.

The registry must contain a matching, authenticated grant for the signed
Issue's repository, issue number, derived workspace path, execution branch,
base/head and checkpoint, with unexpired lease and matching token digest.
Any missing/partial configuration, invalid signature, stale checkpoint,
expired lease or mismatched lineage yields `WORKSPACE_AUTHORITY_MISMATCH`
**before** cloning or calling the provider. No opt-out or fallback exists in
`IssueAgentWorkspaceRuntime.dispatch`.

This code change is *not* permission to turn on a live issuer with fabricated
registry entries. The trusted owner-controlled registry writer, durable signed
checkpoint handoff, external launchers, publisher and CI enforcement must be
validated before production rollout. Until then, hosted Issue Agent execution
will deliberately refuse dispatch if the required authority is absent. The
current isolated worktree and older worktrees must not be deleted or imported.

## Shared-registry deployment gate (PR #785)

The existing `WorkspaceRegistry` uses an HMAC-authenticated JSON file and a
**host-local** `fcntl.flock`. It is safe for competing issuer processes on one
trusted host and local durable storage, but is **not** a multi-host consensus
backend. An ordinary per-runner path, ephemeral container volume, or shared
network filesystem without a proven distributed lock/fencing contract MUST NOT
be represented as globally authoritative.

The issuer deployment workflow was previously a green no-op template. It now
fails closed until the owner-approved self-hosted service and persistent shared
authority are actually provisioned and a hosted canary proves exact-head,
restart survival, duplicate admission rejection, and cross-worker fencing.
Do not put HMAC keys, lease tokens, checkpoint contents, or registry files in
public CI artifacts. Never use Railway for the replacement.

**Current boundary:** one trusted host can safely share one registry path
between processes, but no distributed backend or operational endpoint is
provisioned. Issue #575 remains open; production-wide cutover is blocked.
