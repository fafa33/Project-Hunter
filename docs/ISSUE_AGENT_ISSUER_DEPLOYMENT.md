# Trusted Issuer Edge Deployment for Governed Issue Agent Execution

This document describes the exact operational steps to deploy the trusted issuer HTTP edge required by Issue #423. The edge consumes `hunter-issue-agent-signed-authorization-v1` from the GitHub trigger, verifies the authorization, invokes the production `GovernedIssueAgentExecutionService` composition root, persists the canonical Smart Prompt build, issues the signed `PromptAutomationEnvelopeHandoff`, and forwards it unchanged into the existing fallback runtime.

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

4. **Evidence Intelligence database** with Source Handling authority history
   - Must be accessible at the path configured by `HUNTER_ISSUE_AGENT_EVIDENCE_DB`
   - Must contain the genesis rule, FACT, POLICY, and FIELD_CATEGORY_REGISTRY records for authorized Issue scopes

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

## The Provenance Resolver (required, operator-supplied)

The issuer edge requires `--provenance-resolver <dotted.path.to.callable>` at startup. This is the canonical `ProvenanceResolver` callable
`(provenance_id: str, provenance_kind: str, cutoff: datetime) -> Mapping[str, Any] | None` that the same operator used when the Source
Handling authority history was provisioned. It is an authority callable, not a configuration value, and the issuer deliberately has **no
default**: an unwired deployment fails closed at startup rather than resolving provenance as "absent but acceptable".

> Every FACT / POLICY / FIELD_CATEGORY_REGISTRY authorization references `EVIDENCE` and `VERIFIER` provenance records. If the deployed
> resolver does not answer those identities with the exact strength/method the operator published, or answers `None`, the execution path
> raises `SourceHandlingBlockedError` and the request fails closed (HTTP 422). The same resolver must be used at provisioning time and at
> the issuer edge, so deploy it as a small operator-owned module reachable from the issuer's `PYTHONPATH`, or wire a repository example
> to the same underlying evidence/verifier registry.

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
  app = "python scripts/hunter_issue_agent_issuer.py --host 0.0.0.0 --port 8080 --provenance-resolver my_issuer_ops.provenance_resolver"

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

### Option 3: Self-hosted / Docker Compose

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
    command: python scripts/hunter_issue_agent_issuer.py --host 0.0.0.0 --port 8080 --provenance-resolver my_issuer_ops.provenance_resolver

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

CMD ["python", "scripts/hunter_issue_agent_issuer.py", "--host", "0.0.0.0", "--port", "8080", "--provenance-resolver", "my_issuer_ops.provenance_resolver"]
```

## Verifying the Deployment

After deployment, verify the issuer edge is operational:

```bash
# Health check
curl https://your-issuer-url/healthz
# Expected: {"status": "ok", "service": "hunter-issue-agent-issuer"}

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
- POST it to `HUNTER_ISSUE_AGENT_WEBHOOK_URL`
- The issuer edge will execute the full governed path

## Troubleshooting

| Symptom | Likely Cause |
|---------|--------------|
| 401 Unauthorized | Invalid or missing `HUNTER_ISSUE_AGENT_AUTHORIZATION_VERIFYING_KEY` |
| 403 Forbidden | Repository/owner mismatch, or non-owner label |
| 409 Conflict | Authorization already executed (replay protection) |
| 422 Unprocessable | Source Handling authority missing for Issue scope |
| 500 Internal | Missing configuration, database unavailable, or fallback runtime failure |

Check the issuer edge logs for detailed error messages. All failures are fail-closed by design.

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
│  - POSTs to webhook URL             │
└─────────────────────────────────────┘
        │  hunter-issue-agent-signed-authorization-v1
        ▼
┌─────────────────────────────────────┐
│  hunter_issue_agent_issuer.py       │  (Trusted Issuer Edge)
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

The trusted issuer edge is the **only** component that holds both the Issue authorization verifier and the Smart Prompt automation signer. It bridges the two asymmetric trust domains without weakening either.