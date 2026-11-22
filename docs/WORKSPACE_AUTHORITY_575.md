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

## Transactional backend implementation

`src/hunter/automation/workspace_sqlite_backend.py` provides a durable
transactional **single trusted host** SQLite backend. It supports authenticated
grant reads, `BEGIN IMMEDIATE` exclusive issue/workspace insertion, ownership-
bound lease renewal, restart persistence and tamper rejection. Tests cover
reopening the database from a second process-equivalent client, duplicate
issuance, conflicting workspace, expired/wrong-owner lease and tampering.

**This is not yet the shared production authority**: it is not wired into the
issuer/runtime/publisher transport, does not provide a network service or
multi-host consensus, and has no deployed durable volume. Do not mount its
SQLite file on NFS or treat it as a distributed lock. A single trusted service
and authenticated remote admission API (or an approved transactional server DB)
remain required before global cutover.

## Private HTTP transport (no paid API)

`workspace_authority_http.py` exposes a loopback-only standard-library HTTP
service backed by the same SQLite transactional store. Read and write Bearer
credentials are distinct; the reader cannot issue or renew grants. No network
port is publicly exposed. `scripts/hunter_workspace_authority_server.py`
requires `HUNTER_WORKSPACE_SQLITE_PATH` (absolute),
`HUNTER_WORKSPACE_AUTHORITY_KEY_HEX`, `HUNTER_WORKSPACE_READER_TOKEN`, and
`HUNTER_WORKSPACE_WRITER_TOKEN`; optional `HUNTER_WORKSPACE_PORT` defaults to
8765. Supply secrets through the trusted host environment, never GitHub logs.
The API has not been exposed via Tailscale, deployed as a persistent service,
or integrated with the existing runtime and publisher; those remain blocked
until transport identity, checkpoint authority, and hosted tests are approved.

## API client integration (opt-in, not production cutover)

The existing trusted issuer grant issuance and lease renewal, Issue Agent
runtime pre-dispatch admission, and publisher pre-publication admission now
support `HUNTER_WORKSPACE_API_URL=http://127.0.0.1:<port>`. The issuer uses
`HUNTER_WORKSPACE_WRITER_TOKEN`; runtime and publisher use
`HUNTER_WORKSPACE_READER_TOKEN`. All require the existing checkpoint and lease
token. API and legacy file authority configuration must not be mixed. Client
requests fail closed if the loopback service is unavailable. This is an
**optional transport**, not a global cutover: existing paths without authority
configuration are still governed by their prior admission contracts. A remote
runner needs a trusted, authenticated tunnel with its own loopback endpoint;
no direct public HTTP exposure is permitted. A persistent service deployment,
checkpoint signature and issuer/publisher trust-domain hardening remain gates.
