# Trusted Issuer Edge Deployment for Governed Issue Agent Execution

This document describes the exact operational steps to deploy the trusted issuer HTTP edge required by Issue #423, plus the repository-owned
provisioning boundary (Issue #497) that auto-provisions per-Issue authority records before dispatch. The edge consumes
`hunter-issue-agent-signed-authorization-v1` from the GitHub trigger, verifies the authorization, invokes the production
`GovernedIssueAgentExecutionService` composition root, persists the canonical Smart Prompt build, issues the signed
`PromptAutomationEnvelopeHandoff`, and forwards it unchanged into the existing fallback runtime.

## Prerequisites

The following must be provisioned **before** deployment:

1. **Ed25519 keypair for Issue authorization** (separate from Smart Prompt automation keypair)
   - Private key: `HUNTER_ISSUE_AGENT_AUTHORIZATION_SIGNING_KEY` (repository secret)
   - Public key:  `HUNTER_ISSUE_AGENT_AUTHORIZATION_VERIFYING_KEY` (environment variable for issuer edge)

2. **Smart Prompt automation keypair** (already required by existing infrastructure)
   - Private key: `HUNTER_PROMPT_AUTOMATION_SIGNING_KEY` (repository secret, also used by issuer edge)
   - Public key:  `HUNTER_PROMPT_AUTOMATION_VERIFYING_KEY` (environment variable for issuer edge)

3. **Source Handling authority keypair** (already required by existing infrastructure)
   - Public key: `HUNTER_SOURCE_HANDLING_VERIFICATION_KEY` (environment variable)
   - Public key SHA-256: `HUNTER_SOURCE_HANDLING_VERIFICATION_KEY_SHA256` (environment variable)
   - Genesis rule SHA-256: `HUNTER_SOURCE_HANDLING_GENESIS_RULE_SHA256` (environment variable)

4. **Evidence Intelligence database** with Source Handling authority history **and provisioning provenance**
   - Must be accessible at the path configured by `HUNTER_ISSUE_AGENT_EVIDENCE_DB`
   - Must contain the genesis rule, FACT, POLICY, and FIELD_CATEGORY_REGISTRY records for authorized Issue scopes
   - Must contain a strict-known provenance record per `EVIDENCE` / `VERIFIER` identity those authorizations will name
     (see "Provisioning Source Handling Provenance")

5. **Repository checkout** for provider commands
   - Path configured by `HUNTER_ISSUE_AGENT_REPO_DIR`

6. **Execution branch** that providers must advance
   - Configured by `HUNTER_ISSUE_AGENT_EXECUTION_BRANCH` (e.g., `issue-agent-execution`)

7. **Fallback runtime provider configuration** (already required)
   - `HUNTER_AGENT_CODEX_COMMAND`, `HUNTER_AGENT_CLAUDE_COMMAND`, etc.
   - `HUNTER_AGENT_VALIDATION_COMMAND`
   - Environment allowlists for each provider

## Required Repository Secrets

Configure the following in **GitHub Repository Settings → Secrets and variables → Actions → Repository secrets**:

| Secret Name | Description | Example Value |
|-------------|-------------|---------------|
| `HUNTER_ISSUE_AGENT_WEBHOOK_URL` | HTTPS URL of the deployed issuer edge + `/issue-agent/authorize` | `https://hunter-issuer.example.com/issue-agent/authorize` |
| `HUNTER_ISSUE_AGENT_PROVISIONING_URL` | HTTPS URL of the deployed provisioning boundary + `/issue-agent/provision` | `https://hunter-provisioner.example.com/issue-agent/provision` |
| `HUNTER_ISSUE_AGENT_AUTHORIZATION_SIGNING_KEY` | Hex-encoded Ed25519 private key (32 bytes = 64 hex chars) | `a1b2c3d4...` (64 hex chars) |
| `HUNTER_PROMPT_AUTOMATION_SIGNING_KEY` | Hex-encoded Ed25519 private key (32 bytes = 64 hex chars) | `e5f6a7b8...` (64 hex chars) |
| `HUNTER_SOURCE_HANDLING_VERIFICATION_KEY` | Hex-encoded Ed25519 public key (32 bytes = 64 hex chars) | `12345678...` (64 hex chars) |
| `HUNTER_SOURCE_HANDLING_VERIFICATION_KEY_SHA256` | SHA-256 of the above public key (64 hex chars) | `abcdef12...` (64 hex chars) |
| `HUNTER_SOURCE_HANDLING_GENESIS_RULE_SHA256` | SHA-256 of the genesis authorization rule | `fedcba98...` (64 hex chars) |
| `HUNTER_PROMPT_AUTOMATION_VERIFYING_KEY` | Hex-encoded Ed25519 public key (32 bytes = 64 hex chars) | `99887766...` (64 hex chars) |
| `HUNTER_ISSUE_AGENT_AUTHORIZATION_VERIFYING_KEY` | Hex-encoded Ed25519 public key (32 bytes = 64 hex chars) | `11223344...` (64 hex chars) |

> **Critical**: Never commit any secret value to repository content. All secrets must be configured only through GitHub's secret store or the deployment platform's secret management.

## Required Environment Variables for Issuer Edge

The deployed issuer edge requires these environment variables (set in your deployment platform):

| Variable | Source | Description |
|----------|--------|-------------|
| `HUNTER_ISSUE_AGENT_REPOSITORY` | Repository setting | Exact `owner/name` (e.g., `fafa33/Project-Hunter`) |
| `HUNTER_ISSUE_AGENT_OWNER_LOGIN` | Repository setting | Repository owner login (e.g., `fafa33`) |
| `HUNTER_ISSUE_AGENT_EVIDENCE_DB` | Deployment config | Absolute path to Evidence SQLite database |
| `HUNTER_ISSUE_AGENT_EXECUTION_BRANCH` | Deployment config | Remote branch providers must advance |
| `HUNTER_ISSUE_AGENT_REPO_DIR` | Deployment config | Absolute path to repository checkout |
| `HUNTER_SOURCE_HANDLING_VERIFICATION_KEY` | Secret | Hex Ed25519 public key |
| `HUNTER_SOURCE_HANDLING_VERIFICATION_KEY_SHA256` | Secret | SHA-256 of the above public key |
| `HUNTER_SOURCE_HANDLING_GENESIS_RULE_SHA256` | Secret | Genesis rule digest |
| `HUNTER_PROMPT_AUTOMATION_VERIFYING_KEY` | Secret | Hex Ed25519 public key (prompt automation) |
| `HUNTER_PROMPT_AUTOMATION_SIGNING_KEY` | Secret | Hex Ed25519 private key (prompt automation) |
| `HUNTER_ISSUE_AGENT_AUTHORIZATION_VERIFYING_KEY` | Secret | Hex Ed25519 public key (issue authorization) |
| `HUNTER_AGENT_*` | Secret/Config | Existing fallback runtime provider config |

## The Provenance Resolver (required, repository-owned)

The issuer edge requires `--provenance-resolver <dotted.path.to.callable>` at startup. This is the canonical `ProvenanceResolver` callable
`(provenance_id: str, provenance_kind: str, cutoff: datetime) -> Mapping[str, Any] | None` consulted by the Source Handling read path for
every `EVIDENCE` and `VERIFIER` identity named by a publication authorization. The issuer deliberately has **no default**: an unwired
deployment fails closed at startup rather than resolving provenance as "absent but acceptable".

Issue #426 supplies the canonical production implementation in this repository:

```text
hunter.evidence_intelligence.source_handling_provenance.production_provenance_resolver
```

It is wired to the same four operator-provisioned environment variables the Source Handling read path already uses, in the same Evidence
database (`HUNTER_ISSUE_AGENT_EVIDENCE_DB`):

- `HUNTER_ISSUE_AGENT_EVIDENCE_DB` — canonical evidence + authority (+ provenance) database
- `HUNTER_SOURCE_HANDLING_VERIFICATION_KEY` — hex-encoded Ed25519 public key
- `HUNTER_SOURCE_HANDLING_VERIFICATION_KEY_SHA256` — sha256 of that public key
- `HUNTER_SOURCE_HANDLING_GENESIS_RULE_SHA256` — genesis authorization-rule digest

All four must match the exact values pinned in the `source_handling_operator_root` row of the database. A missing, malformed, operator-root
mismatched, tampered or otherwise unreadable configuration raises `SourceHandlingBlockedError`, and the resolver never falls back to a
latest-record digest. It resolves only records that are strict-known at the cutoff: content that was not yet knowable or not yet admitted
at the cutoff, or whose chain is ambiguous at the cutoff, is not returned.

> Every FACT / POLICY / FIELD_CATEGORY_REGISTRY authorization references `EVIDENCE` and `VERIFIER` provenance records. If the deployed
> resolver does not answer those identities with the exact strength/method the operator provisioned, or answers `None`, the execution path
> raises `SourceHandlingBlockedError` and the request fails closed (HTTP 422).

## The Provisioning Boundary (Issue #497)

A **trusted provisioning boundary** (`scripts/hunter_issue_agent_provisioner.py`) runs beside the issuer as its own deployment service over
the **same** persistent evidence database. It is the repository-owned component that makes per-Issue authority provisioning **automatic** and
removes the manual operator step: the GitHub Actions trigger now POSTs each signed authorization to the boundary (`/issue-agent/provision`)
**before** it ever POSTs to the issuer webhook, and a provisioning failure blocks dispatch.

Contract and invariants:

- **Provisioning precedes and gates dispatch.** The trigger's `_provision_and_dispatch` is fail-closed: a provisioning 400/401/403/422/5xx
  (after bounded retry of only transport-level 502/503/504) stops the run and never contacts the issuer webhook.
- **The boundary holds `HUNTER_SOURCE_HANDLING_SIGNING_KEY` for its whole lifetime.** Unlike the issuer (which scrubs it), the provisioner
  needs the signing key to write authority and provenance records; it stays in memory for the process lifetime and never leaves the
  service. This is the **second and final** holder of the key. The issuer stays read-only as designed.
- **Classification comes from repository-owned defaults only.** `provision_issue_authority` derives the `FACT`, `FIELD_CATEGORY_REGISTRY`,
  and `POLICY` records from the pinned `_REPOSITORY_DEFAULTS` (PUBLIC / FULL_CONTENT_ALLOWED / ALLOW) — never from Issue body, title, or
  caller selection. The document identity is still claim-derived, so Issue content can change identity but never classification.
- **Idempotent and fail-closed on mismatch.** A re-run of identical content is `already-provisioned` (nothing mutated); an existing but
  different authority head for the same document is refused (HTTP 422, "refusing to replace") with **zero writes**, so the boundary can
  never supersede or silently repair provisioned state.
- **The provisioned registry vocabulary matches what the issuer actually persists.** The boundary's contract registry covers the Issue
  Source durable payload fields (`issue_content`, `content_derived_ids`, `locator_urls`, `source_derived_text`, `intake_metadata`) and the
  compiled pre-model bundle (`pre_model_bundle` → `AUDIT_FIELD`), so an auto-provisioned Issue dispatches cleanly.
- **Transport hygiene.** Requests are `hunter-issue-agent-signed-authorization-v1` envelopes from the same issuer key the trigger holds the
  private half of; bodies are capped at 256 KiB; `Content-Length` is mandatory within that cap; a malformed signature is 401, a
  repository/owner mismatch 403, malformed envelope 400, oversized body 413, and missing/partial body 411/400.

## Provisioning Source Handling Provenance (operator step, before deployment)

The evidence database must already hold a provenance record for every `EVIDENCE` and `VERIFIER` identity an authorization will name. Provenance
is a supporting fact about the Source Handling Authority, persisted in append-only tables (`source_handling_provenance_records` and
`source_handling_provenance_heads`) inside **the same** Evidence database, bound to the same pinned operator root and signed with the same
Ed25519 key material as the authority records.

> **Issue #497:** the per-Issue provenance records (below) are now written **automatically** by the provisioning boundary on first
> authorization — no operator CLI run is required for a new Issue. The operator CLI described here remains the tooling for offline
> workflows, recovery of operator-root structure, and one-off records:

```bash
python -m hunter.evidence_intelligence.source_handling_provenance \
  --database /data/evidence.sqlite \
  --signing-key-hex "${HUNTER_SOURCE_HANDLING_SIGNING_KEY}" \
  --verification-key-hex "${HUNTER_SOURCE_HANDLING_VERIFICATION_KEY}" \
  --verification-key-sha256 "${HUNTER_SOURCE_HANDLING_VERIFICATION_KEY_SHA256}" \
  --genesis-rule-sha256 "${HUNTER_SOURCE_HANDLING_GENESIS_RULE_SHA256}" \
  --record '{"provenance_id":"evidence:auth:fact:issue-123","provenance_kind":"EVIDENCE","authority_identity":"<operator id>","effective_from":"2026-09-02T12:00:00Z","recorded_at":"2026-09-02T12:00:00Z","known_at":"2026-09-02T12:00:00Z","evidence_strength":"AUTHORITATIVE_SOURCE_EVIDENCE","evidence_method":"SOURCE_TERMS_VERIFIED"}' \
  --record '{"provenance_id":"verifier:auth:fact:issue-123","provenance_kind":"VERIFIER","authority_identity":"<operator id>","effective_from":"2026-09-02T12:00:00Z","recorded_at":"2026-09-02T12:00:00Z","known_at":"2026-09-02T12:00:00Z","verifier_type":"SOURCE_VERIFIER"}'
```

`HUNTER_SOURCE_HANDLING_SIGNING_KEY` is the hex-encoded Ed25519 **private** key matching the pinned public key. It is **operator-only**: the
issuer runtime never holds a provenance signing key (it only verifies with the public key). Guidance:

- Provision provenance **before** you deploy so it is already present when the first authorization arrives.
- `recorded_at` and `known_at` must be at or before the provisioning instant (`known_at` is the instant the fact became knowable).
- Corrections supersede the current head and must be knowable **strictly later**; re-provisioning the identical current head is an idempotent
  no-op, and re-provisioning already-superseded content fails closed.
- The CLI returns the content-addressed `record_id` (sha256 of the canonical record claims) for each record.

## Generating the Keypairs

```bash
# Generate Issue Authorization keypair (dedicated to this purpose)
python3 -c "
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
import secrets
private = Ed25519PrivateKey.generate()
private_bytes = private.private_bytes_raw()
public_bytes = private.public_key().public_bytes_raw()
print('HUNTER_ISSUE_AGENT_AUTHORIZATION_SIGNING_KEY (private):', private_bytes.hex())
print('HUNTER_ISSUE_AGENT_AUTHORIZATION_VERIFYING_KEY (public):', public_bytes.hex())
"

# Generate Smart Prompt Automation keypair (if not already exists)
python3 -c "
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
private = Ed25519PrivateKey.generate()
private_bytes = private.private_bytes_raw()
public_bytes = private.public_key().public_bytes_raw()
print('HUNTER_PROMPT_AUTOMATION_SIGNING_KEY (private):', private_bytes.hex())
print('HUNTER_PROMPT_AUTOMATION_VERIFYING_KEY (public):', public_bytes.hex())
"

# Generate Source Handling keypair (if not already exists)
python3 -c "
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
private = Ed25519PrivateKey.generate()
private_bytes = private.private_bytes_raw()
public_bytes = private.public_key().public_bytes_raw()
print('HUNTER_SOURCE_HANDLING_VERIFICATION_KEY (public):', public_bytes.hex())
import hashlib
print('HUNTER_SOURCE_HANDLING_VERIFICATION_KEY_SHA256:', hashlib.sha256(public_bytes).hexdigest())
"
```

## Deploying the Issuer Edge

### Option 1: Google Cloud Run (recommended for serverless)

1. **Prepare the service account** with access to:
   - Cloud SQL / Cloud Storage for Evidence database (or mount a volume)
   - Secret Manager for secrets (or use environment variables)
   - VPC connector if database is in private network

2. **Build and deploy**:

```bash
# Set variables
PROJECT_ID="your-gcp-project"
REGION="us-central1"
SERVICE_NAME="hunter-issue-agent-issuer"
REPOSITORY="fafa33/Project-Hunter"
OWNER="fafa33"

# Build container image (Dockerfile below)
gcloud builds submit --tag gcr.io/${PROJECT_ID}/${SERVICE_NAME} .

# Deploy
gcloud run deploy ${SERVICE_NAME} \
  --image gcr.io/${PROJECT_ID}/${SERVICE_NAME} \
  --region ${REGION} \
  --platform managed \
  --allow-unauthenticated \
  --port 8080 \
  --memory 512Mi \
  --cpu 1 \
  --min-instances 0 \
  --max-instances 3 \
  --timeout 300 \
  --concurrency 10 \
  --set-env-vars="HUNTER_ISSUE_AGENT_REPOSITORY=${REPOSITORY},HUNTER_ISSUE_AGENT_OWNER_LOGIN=${OWNER},HUNTER_ISSUE_AGENT_EVIDENCE_DB=/data/evidence.sqlite,HUNTER_ISSUE_AGENT_EXECUTION_BRANCH=issue-agent-execution,HUNTER_ISSUE_AGENT_REPO_DIR=/workspace,HUNTER_SOURCE_HANDLING_VERIFICATION_KEY=${SOURCE_HANDLING_VERIFICATION_KEY},HUNTER_SOURCE_HANDLING_VERIFICATION_KEY_SHA256=${SOURCE_HANDLING_VERIFICATION_KEY_SHA256},HUNTER_SOURCE_HANDLING_GENESIS_RULE_SHA256=${SOURCE_HANDLING_GENESIS_RULE_SHA256},HUNTER_PROMPT_AUTOMATION_VERIFYING_KEY=${PROMPT_AUTOMATION_VERIFYING_KEY},HUNTER_PROMPT_AUTOMATION_SIGNING_KEY=${PROMPT_AUTOMATION_SIGNING_KEY},HUNTER_ISSUE_AGENT_AUTHORIZATION_VERIFYING_KEY=${ISSUE_AGENT_AUTHORIZATION_VERIFYING_KEY},PYTHONPATH=/workspace/src" \
  --service-account=${SERVICE_ACCOUNT_EMAIL} \
  --vpc-connector=projects/${PROJECT_ID}/locations/${REGION}/connectors/hunter-vpc
```

3. **Configure the webhook URL**:
   - Get the deployed URL: `gcloud run services describe ${SERVICE_NAME} --region ${REGION} --format='value(status.url)'`
   - Set `HUNTER_ISSUE_AGENT_WEBHOOK_URL` = `${URL}/issue-agent/authorize`

### Option 2: Fly.io

```bash
# Create fly.toml
cat > fly.toml << 'EOF'
app = "hunter-issue-agent-issuer"
primary_region = "ord"

[build]
  dockerfile = "Dockerfile"

[env]
  HUNTER_ISSUE_AGENT_REPOSITORY = "fafa33/Project-Hunter"
  HUNTER_ISSUE_AGENT_OWNER_LOGIN = "fafa33"
  HUNTER_ISSUE_AGENT_EVIDENCE_DB = "/data/evidence.sqlite"
  HUNTER_ISSUE_AGENT_EXECUTION_BRANCH = "issue-agent-execution"
  HUNTER_ISSUE_AGENT_REPO_DIR = "/workspace"
  PYTHONPATH = "/workspace/src"

[mounts]
  source = "evidence_data"
  destination = "/data"

[processes]
  app = "python scripts/hunter_issue_agent_issuer.py --host 0.0.0.0 --port 8080 --provenance-resolver hunter.evidence_intelligence.source_handling_provenance.production_provenance_resolver"

[http_service]
  internal_port = 8080
  force_https = true
  auto_stop_machines = true
  auto_start_machines = true
  min_machines_running = 0
EOF

# Deploy
fly secrets set \
  HUNTER_SOURCE_HANDLING_VERIFICATION_KEY=... \
  HUNTER_SOURCE_HANDLING_VERIFICATION_KEY_SHA256=... \
  HUNTER_SOURCE_HANDLING_GENESIS_RULE_SHA256=... \
  HUNTER_PROMPT_AUTOMATION_VERIFYING_KEY=... \
  HUNTER_PROMPT_AUTOMATION_SIGNING_KEY=... \
  HUNTER_ISSUE_AGENT_AUTHORIZATION_VERIFYING_KEY=... \
  HUNTER_AGENT_CODEX_COMMAND='["codex", "exec"]' \
  HUNTER_AGENT_CLAUDE_COMMAND='["claude", "--print"]' \
  ...other provider commands...

fly deploy
```

### Option 3: Railway

Railway runs a single Service (one process). The repository ships a repo-owned `railway.toml` that defines the build steps, the HTTP port,
the repository-owned runtime startup seam (`scripts/railway_issuer_startup.py`), persistent storage for the evidence database, and the
operational variables that are safe to commit (assigned via Railway's `$variable` references). Deploy by connecting the `Project-Hunter`
repository and creating a Service from it, then set the remaining variables as a Railway Variable Group / Service variables.

#### Runtime Startup Seam (Issue #442)

Railway volumes are **not** mounted during `preDeploy` commands, so any one-off bootstrap job that runs before the Service starts cannot
access `/data`.  The repository-owned `railway.toml` start command therefore delegates to `scripts/railway_issuer_startup.py`, which
idempotently bootstraps the volume at runtime — *after* the volume is mounted but *before* the long-running issuer process starts.

The startup sequence is:

1. Read `HUNTER_ISSUE_AGENT_EVIDENCE_DB` (typically `/data/evidence.sqlite`).
2. Verify the evidence data directory is the mounted persistent volume — a directory that merely exists (baked into the image, or created by a build step) is not accepted as proof and fails closed rather than bootstrapping into ephemeral storage.
3. Verify `HUNTER_SOURCE_HANDLING_SIGNING_KEY` is present.
4. Invoke the existing canonical bootstrap through its shared public contract (`bootstrap_authority`), which provisions the operator root and genesis rule.
   On a fresh volume this writes the authority; on an already-bootstrapped volume the idempotency check passes through; on a tampered or
   mismatched volume the bootstrap fails closed before the issuer starts.
5. Scrub `HUNTER_SOURCE_HANDLING_SIGNING_KEY` from the process environment.
6. `exec` the canonical issuer with unchanged arguments (`--host 0.0.0.0 --port $PORT --provenance-resolver ...`).

The `railway.toml` start command is therefore:

```toml
[deploy]
startCommand = "python scripts/railway_issuer_startup.py"
```

The bootstrap is the **only** code path that may consume `HUNTER_SOURCE_HANDLING_SIGNING_KEY`; the long-running issuer process starts with
that variable removed from its environment.  No pre-deploy command, no parallel bootstrap mechanism, and no dashboard-only configuration
bypasses this seam.

#### Provisioner Service Startup Seam (Issue #497)

The provisioning boundary is a **second Railway Service** created from the same repository, mounting the **same `/data` volume**. Its
start command delegates to `scripts/railway_provisioner_startup.py`, which idempotently bootstraps the shared volume at runtime (after the
volume is mounted) and then launches the provisioner itself. The sequence is:

1. Verify the evidence data directory is the mounted persistent volume (same fail-closed check as the issuer seam).
2. Verify `HUNTER_SOURCE_HANDLING_SIGNING_KEY` is present and bootstrap the volume through the shared `bootstrap_authority` contract.
3. **Do not scrub the signing key** — the provisioner is the trusted writer and holds it for its lifetime. This is the one deliberate
   asymmetry versus `railway_issuer_startup.py`.
4. `exec` the canonical provisioner (`python scripts/hunter_issue_agent_provisioner.py`), default port 8081, replying to Railway's `PORT`.

The provisioner Service variables mirror the issuer's four operator-provenance variables plus the deployment identity variables, all
pointing at the same `/data/evidence.sqlite` (see steps b′ and the configuration reference below).

#### Railway Setup Steps (a–f)

**a. Attach the persistent Volume** — `railway.toml` provisions a `Volume` at `/data` and sets
`HUNTER_ISSUE_AGENT_EVIDENCE_DB=/data/evidence.sqlite`.  Railway mounts this volume at runtime before the start command executes.
The start command will not run any bootstrap from a `preDeploy` hook or one-off job, because Railway volumes are not mounted during
`preDeploy`; the runtime startup seam is the **only** bootstrap path.

**b. Set issuer runtime variables** — on the issuer Service, set:
- `HUNTER_SOURCE_HANDLING_SIGNING_KEY` (secret — consumed only by the startup seam bootstrap, scrubbed before the issuer starts)
- `HUNTER_SOURCE_HANDLING_VERIFICATION_KEY`
- `HUNTER_SOURCE_HANDLING_VERIFICATION_KEY_SHA256`
- `HUNTER_SOURCE_HANDLING_GENESIS_RULE_SHA256`
- `HUNTER_PROMPT_AUTOMATION_SIGNING_KEY` (secret)
- `HUNTER_PROMPT_AUTOMATION_VERIFYING_KEY`
- `HUNTER_ISSUE_AGENT_AUTHORIZATION_VERIFYING_KEY`
- `HUNTER_ISSUE_AGENT_REPOSITORY`, `HUNTER_ISSUE_AGENT_OWNER_LOGIN`, `HUNTER_ISSUE_AGENT_EXECUTION_BRANCH`, `HUNTER_ISSUE_AGENT_REPO_DIR`
- Fallback runtime provider variables (`HUNTER_AGENT_*`)
- `HUNTER_ISSUE_AGENT_EVIDENCE_DB=/data/evidence.sqlite` (already set by `railway.toml`)

**b′. Set provisioner runtime variables** — on the provisioner Service (Issue #497), set:
- `HUNTER_SOURCE_HANDLING_SIGNING_KEY` (secret — retained for the provisioner's whole lifetime)
- `HUNTER_SOURCE_HANDLING_VERIFICATION_KEY`
- `HUNTER_SOURCE_HANDLING_VERIFICATION_KEY_SHA256`
- `HUNTER_SOURCE_HANDLING_GENESIS_RULE_SHA256`
- `HUNTER_ISSUE_AGENT_AUTHORIZATION_VERIFYING_KEY`
- `HUNTER_ISSUE_AGENT_REPOSITORY`, `HUNTER_ISSUE_AGENT_OWNER_LOGIN`
- `HUNTER_ISSUE_AGENT_EVIDENCE_DB=/data/evidence.sqlite`

Note the provisioner deliberately does **not** need the prompt-automation keys, the execution branch, the repository checkout, or the
fallback runtime variables. Recording the provisioner's Service URL as GitHub secret `HUNTER_ISSUE_AGENT_PROVISIONING_URL`
(`<provisioner-url>/issue-agent/provision`) is required before the trigger can auto-provision (step f below).

**c. Deploy the issuer (bootstraps the mounted volume)** — trigger a Railway deploy. The `railway.toml` start command runs
`python scripts/railway_issuer_startup.py`, which verifies the mounted volume, invokes the canonical bootstrap through the shared public
`bootstrap_authority` contract (provisioning the operator root and genesis rule on this fresh volume), scrubs the signing key, and execs
the canonical issuer. The issuer reads the pinned operator root and genesis rule, and each authorization attempt re-verifies the
provisioned `EVIDENCE` / `VERIFIER` provenance antecedents against the same database with the exact strength/method/verifier type the
operator provisioned.

**c′. Deploy the provisioner (same volume, retains the signing key)** — create the provisioner Service, attach the **same** `/data`
volume, and set its start command to `python scripts/railway_provisioner_startup.py`. Its seam bootstraps the shared volume (idempotent;
the deploy in step c already did), then execs the provisioner which now holds the signing key ready to sign per-Issue authority records.

**d. Verify both Services started** — confirm in the logs that each startup seam reported the bootstrap outcome (`status`, `operator_root`,
`genesis`) and that both processes are serving (`/healthz` on the issuer, `/healthz` on the provisioner). `curl <provisioner-url>/healthz`
should return `{"status":"ok","service":"hunter-issue-agent-provisioner"}`.

**e. No manual per-Issue provisioning** — the operator step that previously had to run
`provision_source_handling_issue_authority.py` for every new authorized Issue is **gone**. The boundary provisions each document
automatically from repository-owned defaults on first authorization, writes the six `EVIDENCE` / `VERIFIER` provenance records the three
issuances require at operator as-of, and is exactly idempotent on re-runs. The CLI remains available for offline/operator workflows but is
no longer part of the admission path.

**f. Live authorized E2E** — apply the `hunter-agent-execute` label to a test Issue. The trigger workflow signs the authorization, retries
only transport-level 502/503/504 (otherwise fail-closed), POSTs first to `HUNTER_ISSUE_AGENT_PROVISIONING_URL`
(`/issue-agent/provision`, expects 200/provisioned), and only if provisioning succeeds POSTs to the issuer webhook
(`/issue-agent/authorize`). Check issuer and provisioner logs for the complete path; a provisioning 422 (e.g. a mismatched pre-existing
head) stops the run before the issuer is ever contacted.

#### Railway Configuration Reference

1. **Set the secrets as variables** on the services:
   `HUNTER_SOURCE_HANDLING_SIGNING_KEY` (issuer *and* provisioner), `HUNTER_PROMPT_AUTOMATION_SIGNING_KEY` (issuer only),
   `HUNTER_ISSUE_AGENT_AUTHORIZATION_VERIFYING_KEY` (issuer *and* provisioner).
   The Source Handling non-secret values are set as regular variables (derived from steps b/b′ above).

2. **Set the deployment variables** on the issuer Service:
   `HUNTER_ISSUE_AGENT_REPOSITORY`, `HUNTER_ISSUE_AGENT_OWNER_LOGIN`, `HUNTER_ISSUE_AGENT_EXECUTION_BRANCH`, `HUNTER_ISSUE_AGENT_REPO_DIR`.
   The Railway-provided `PORT` is consumed by the startup seam as `--port $PORT`. The provisioner Service needs only
   `HUNTER_ISSUE_AGENT_REPOSITORY` and `HUNTER_ISSUE_AGENT_OWNER_LOGIN` (defaults to its own port 8081).

3. **Create the volume and the two Services** — Railway provisions the `/data` Volume from `railway.toml` for the issuer; create the
   provisioner Service from the same repository and attach the **same** volume (start command `python scripts/railway_provisioner_startup.py`).
   On each deploy the respective startup seams idempotently bootstrap the shared volume and launch their process.

4. **Configure the webhook and provisioning URLs** — copy the issuer Service URL (public + 443) and set
   `HUNTER_ISSUE_AGENT_WEBHOOK_URL` to `<issuer-url>/issue-agent/authorize`; copy the provisioner Service URL and set
   `HUNTER_ISSUE_AGENT_PROVISIONING_URL` to `<provisioner-url>/issue-agent/provision`. Both as GitHub repository secrets.

### Option 4: Self-hosted / Docker Compose

```yaml
# docker-compose.yml
version: '3.8'
services:
  hunter-issuer:
    build: .
    ports:
      - "8080:8080"
    environment:
      - HUNTER_ISSUE_AGENT_REPOSITORY=fafa33/Project-Hunter
      - HUNTER_ISSUE_AGENT_OWNER_LOGIN=fafa33
      - HUNTER_ISSUE_AGENT_EVIDENCE_DB=/data/evidence.sqlite
      - HUNTER_ISSUE_AGENT_EXECUTION_BRANCH=issue-agent-execution
      - HUNTER_ISSUE_AGENT_REPO_DIR=/workspace
      - HUNTER_SOURCE_HANDLING_VERIFICATION_KEY=${SOURCE_HANDLING_VERIFICATION_KEY}
      - HUNTER_SOURCE_HANDLING_VERIFICATION_KEY_SHA256=${SOURCE_HANDLING_VERIFICATION_KEY_SHA256}
      - HUNTER_SOURCE_HANDLING_GENESIS_RULE_SHA256=${SOURCE_HANDLING_GENESIS_RULE_SHA256}
      - HUNTER_PROMPT_AUTOMATION_VERIFYING_KEY=${PROMPT_AUTOMATION_VERIFYING_KEY}
      - HUNTER_PROMPT_AUTOMATION_SIGNING_KEY=${PROMPT_AUTOMATION_SIGNING_KEY}
      - HUNTER_ISSUE_AGENT_AUTHORIZATION_VERIFYING_KEY=${ISSUE_AGENT_AUTHORIZATION_VERIFYING_KEY}
      - PYTHONPATH=/workspace/src
      # Fallback runtime provider config
      - HUNTER_AGENT_CODEX_COMMAND=["codex", "exec"]
      - HUNTER_AGENT_CLAUDE_COMMAND=["claude", "--print"]
      # ...other provider commands...
    volumes:
      - evidence_data:/data
      - ./repo:/workspace
    command: python scripts/hunter_issue_agent_issuer.py --host 0.0.0.0 --port 8080 --provenance-resolver hunter.evidence_intelligence.source_handling_provenance.production_provenance_resolver

volumes:
  evidence_data:
```

```bash
# Build and run
docker compose up -d --build

# Configure webhook URL (e.g., via ngrok for testing, or direct DNS)
# HUNTER_ISSUE_AGENT_WEBHOOK_URL=https://your-domain.com/issue-agent/authorize
```

### Dockerfile

```dockerfile
# Dockerfile
FROM python:3.11.13-slim

WORKDIR /workspace

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    sqlite3 \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements and install Python dependencies
COPY requirements/ci-constraints.txt requirements/ci-constraints.txt
COPY pyproject.toml pyproject.toml
RUN pip install --no-cache-dir -c requirements/ci-constraints.txt -e ".[dev]"

# Copy source code
COPY src/ src/
COPY scripts/ scripts/

# Create non-root user
RUN useradd -m -u 1000 hunter && chown -R hunter:hunter /workspace
USER hunter

EXPOSE 8080

CMD ["python", "scripts/hunter_issue_agent_issuer.py", "--host", "0.0.0.0", "--port", "8080", "--provenance-resolver", "hunter.evidence_intelligence.source_handling_provenance.production_provenance_resolver"]
```

## Verifying the Deployment

After deployment, verify the issuer edge is operational:

```bash
# Health check
curl https://your-issuer-url/healthz
# Expected: {"status": "ok", "service": "hunter-issue-agent-issuer"}

# Provisioning boundary health check
curl https://your-provisioner-url/healthz
# Expected: {"status": "ok", "service": "hunter-issue-agent-provisioner"}

# Test with a synthetic authorized payload (requires valid signature)
# A malformed/unsigned body is refused before execution: this one fails on the
# outer signed-envelope schema, so the expected status is 400.
curl -X POST https://your-issuer-url/issue-agent/authorize \
  -H "Content-Type: application/json" \
  -d '{}'
# Expected: 400 Bad Request with error message

# A well-formed `hunter-issue-agent-signed-authorization-v1` envelope signed by
# a key this edge does not trust fails closed with 401 before any execution.
# The complete envelope shape (inner v1 payload + issuer_signature + the
# issuer keypair) is exercised by the repository-owned issuer tests; see
# `tests/test_issue_agent_issuer.py` for a runnable local end-to-end harness.
```

## End-to-End Test Procedure

Once deployed and configured:

1. **Create a test Issue** in the repository with some content
2. **Apply the label** `hunter-agent-execute` as the repository owner
3. **Observe the workflow** `Hunter / Governed Issue Agent Trigger` run
4. **Check the issuer edge logs** for the execution
5. **Verify the fallback runtime** executed and advanced the remote branch
6. **Verify targeted validation** passed

The GitHub Actions workflow `hunter-issue-agent-trigger.yml` will automatically:
- Detect the `hunter-agent-execute` label by the repository owner
- Generate the signed authorization
- POST it first to `HUNTER_ISSUE_AGENT_PROVISIONING_URL` (`/issue-agent/provision`) to auto-provision the per-Issue authority
- Only after provisioning succeeds, POST it to `HUNTER_ISSUE_AGENT_WEBHOOK_URL` (`/issue-agent/authorize`)
- The issuer edge will execute the full governed path

## Troubleshooting

| Symptom | Likely Cause |
|---------|--------------|
| 401 Unauthorized | Invalid or missing `HUNTER_ISSUE_AGENT_AUTHORIZATION_VERIFYING_KEY` |
| 403 Forbidden | Repository/owner mismatch, or non-owner label |
| 409 Conflict | Authorization already executed (replay protection) |
| 422 Unprocessable | Source Handling authority missing for Issue scope, or provisioning boundary refused a mismatched existing head |
| 500 Internal | Missing configuration, database unavailable, or fallback runtime failure |

The trigger may fail a run at **provisioning** (the boundary POST returns 401/403/422); a fail-closed trigger never contacts the issuer
webhook in that case, so a 4xx provisioning rejection is not retried as a transient and no dispatch occurs. Check issuer and provisioner
logs for detailed error messages. All failures are fail-closed by design.

## Security Notes

- The issuer edge **only accepts** `hunter-issue-agent-signed-authorization-v1` envelopes
- The **private signing key never leaves** the GitHub Actions runner (trigger side)
- The **public verifying key** is captured at issuer bootstrap and never re-read
- Issue text **never** reaches the fallback runtime (only non-content handoff does)
- Provider order, destination, branch, and merge behavior are **fixed configuration**
- All secrets remain in secret stores, never in code or logs

## Architecture Summary

```
GitHub Issue (labeled by owner)
        │
        ▼
┌─────────────────────────────────────┐
│  hunter-issue-agent-trigger.yml     │  (GitHub Actions)
│  - Verifies label/owner/event       │
│  - Signs authorization v1           │
│  - POSTs to provisioning URL first  │  (fail-closed; retries only 502/503/504)
│  - Only then POSTs to webhook URL   │
└─────────────────────────────────────┘
        │  hunter-issue-agent-signed-authorization-v1
        ├──────────────────────────────►
        ▼                              │
┌──────────────────┐                   │
│  Provisioner     │ (trusted boundary, shares /data, holds signing key)
│  /issue-agent/   │ - verifies issuer signature (401)
│  provision       │ - repository/owner gate (403)
│                  │ - derives FACT/REGISTRY/POLICY from repository defaults
│                  │ - writes authority + provenance (idempotent; 422 on mismatch)
└──────────────────┘
        │  (only after 200/provisioned)
        ▼
┌─────────────────────────────────────┐
│  hunter_issue_agent_issuer.py       │  (Trusted Issuer Edge, read-only)
│  - Verifies issuer signature        │
│  - Claims execution ownership       │
│  - Ingests via ADR 0036 boundary    │
│  - Compiles via SmartPromptMachine  │
│  - Persists build & signed handoff  │
│  - Dispatches to fallback runtime   │
└─────────────────────────────────────┘
        │  PromptAutomationEnvelopeHandoff (non-content)
        ▼
┌─────────────────────────────────────┐
│  OperationalAgentFallbackRuntime    │  (Existing)
│  - Fixed provider order             │
│  - Remote HEAD advance required     │
│  - Targeted validation required     │
│  - No auto-merge                    │
└─────────────────────────────────────┘
```

Both the provisioner and the issuer run as separate services over the **same persistent `/data` volume**. The provisioner is the trusted
writer (it retains `HUNTER_SOURCE_HANDLING_SIGNING_KEY` for its lifetime); the issuer is read-only and scrubs the key at startup.

The trusted issuer edge is the **only** component that holds both the Issue authorization verifier and the Smart Prompt automation signer. It bridges the two asymmetric trust domains without weakening either.