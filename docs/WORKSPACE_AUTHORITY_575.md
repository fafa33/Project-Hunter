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

## GitHub-hosted signed Grant integration (PR #785, not yet activated)

The signed Grant handoff contract binds authorization ID, run/attempt, repository,
issue, branch, exact base/head SHA, checkpoint and expiry. The hosted executor
checks it against the canonical authorization Ledger before invoking the model.
The Ed25519 **public** verification key is accepted only from the pinned
`workspace_grant_verifying_key` in `config/issue_agent_trust_roots.json`, not from
an executor environment variable. When this root is provisioned, a missing
`HUNTER_WORKSPACE_GRANT_FILE` or `HUNTER_WORKSPACE_CHECKPOINT` refuses execution.

**Not a live transport:** the GitHub workflow does not yet deliver this file
from the trusted Mac issuer. Do not provision the root on main until a verified
Mac-side pull issuer, bound artifact delivery, exact-run replay prevention,
and positive/negative hosted canaries exist. A public artifact alone is not
proof of trusted issuer provenance. Never put a signing private key, Writer
Token, registry secret or lease credential in an artifact.

## macOS container isolation adapter (PR #785, not activated)

The existing OpenCode adapter's bwrap-shaped sandbox contract can be carried by
`scripts/hunter_macos_opencode_sandbox` using the fail-closed
`hunter.automation.macos_opencode_container_sandbox` transport. The transport
requires `HUNTER_MACOS_OPENCODE_IMAGE` to name a reviewed Linux OpenCode image
**by sha256 digest**, maps only the per-attempt repository and credential home,
and starts Docker with a read-only root, no capabilities, no-new-privileges,
limited PIDs and memory. Host OpenCode is never bind-mounted into the image.
Unexpected flags, host mounts, unpinned images and executable overrides fail
closed. The existing canonical issuer, handoff, validator and publisher remain
unchanged. This is **not** permission to activate a production issuer: the
image must contain the reviewed/pinned OpenCode version, its tool-capability
and denied-shell self-check must pass inside the container, and real signed
checkpoint/lease and publication credentials must be provisioned. Docker
availability alone does not satisfy those gates. No legacy Railway runtime is
restored.

### Local image build receipt (2026-11-23)

Built on the owner's Mac with `docker build --platform linux/amd64 -f docker/macos-opencode/Dockerfile -t hunter-opencode:1.18.30 .` using the existing verified `scripts/install_opencode_runtime.py` (release archive SHA-256 pinned in that script). The observed **local image ID** is `sha256:b8ed2bcaf8cda594fc63f8cb7709b92e8d8ce631d53623ebca4d45df5f2c959b`; the offline version probe returned `1.18.30`. This is not a registry manifest digest, and does not assert publication or a successful live agent run. The adapter accepts an exact local image ID (`sha256:...`) or a registry digest (`name@sha256:...`), never a mutable tag. Production activation remains blocked on the provider capability/shell self-check and signed Workspace admission and publication grants.

### Live macOS Docker self-check (2026-11-23)

The first live probe exposed a critical false positive: `HUNTER_PROBE_NONCE`
was not forwarded to the container, so the shell created a different filename
and the parent incorrectly reported success. After forwarding the nonce, the
self-check correctly **failed closed** because the model executed `touch`.
The adapter now supplies a deny-all-by-default OpenCode permission configuration,
a non-executable shell policy implemented as an executable refusing shell wrapper,
and `--pure`. A second real model probe returned `Hunter forbids shell execution`,
created no nonce-bearing marker, and the canonical
`opencode_provider_self_check` exited zero. This proves the tested forbidden
shell attempt is denied for the pinned local image. It does **not** prove
production issuer admission, all tool-capability restrictions, network policy,
or GitHub publication readiness; those remain separate gates.

### Exact-run Mac publication adapter (staged, not a live cutover)

The existing issuer's `prepare_authorization` now calls the Mac publisher **only
when** its trusted process environment supplies `HUNTER_WORKSPACE_GITHUB_RUN_ID`
after durable dispatch and successful atomic grant issuance. It passes the exact
issued grant, not a reconstructed candidate. `HUNTER_WORKSPACE_GITHUB_RUN_ATTEMPT`
must be 1. The publisher requires a private `HUNTER_WORKSPACE_GRANT_SIGNING_KEY`
matching the repository-pinned public root, a scoped
`HUNTER_WORKSPACE_GITHUB_COMMENT_TOKEN`, and the existing authority checkpoint.
It verifies the live GitHub workflow run before posting the signed, non-secret
receipt. Missing or inconsistent configuration refuses publication.

**Operational blocker:** no trusted automatic source of the exact GitHub run ID
has yet been connected to the Mac process; no production signing key/root or
comment credential is provisioned. Do **not** set a static run ID in the issuer
service or activate a live trigger. This opt-in adapter is not a proven
GitHub-to-Mac pull transport. Positive/negative hosted canaries, durable
run-to-authorization correlation and owner-approved trust-root provisioning
remain mandatory before closing Issue #575.
