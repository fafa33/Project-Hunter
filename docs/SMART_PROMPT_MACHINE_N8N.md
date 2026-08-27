# Smart Prompt Machine n8n Integration

Status: Phase D adapter contract. The repository contains the concrete
transport and composition wiring, but no live n8n URL, credential, workflow
activation, provider execution, or production secret.

## Boundary

The operational path is:

```text
SmartPromptMachine
  -> signed PromptAutomationEnvelope
  -> PromptAutomationDispatcher
  -> N8nPromptAutomationTransport
  -> n8n webhook
```

The n8n adapter is a transport implementation. It does not select or change a
task route, prompt profile, context policy, Source Handling authority, provider,
retention policy, or prompt/evidence content.

## Configuration

Configure these values in the deployment environment, secret manager, or
equivalent operational configuration. Never commit their values:

| Variable | Meaning |
|---|---|
| `HUNTER_N8N_WEBHOOK_URL` | HTTPS n8n webhook endpoint; query/fragment credentials are rejected |
| `HUNTER_N8N_WEBHOOK_TOKEN` | Bearer token used only in the outbound `Authorization` header |
| `HUNTER_N8N_WEBHOOK_TIMEOUT_SECONDS` | Optional positive finite request timeout; defaults to `10` |

The transport refuses missing configuration, cleartext endpoints, embedded URL
credentials, query/fragment secrets, malformed timeout values, and redirects.
It never follows a redirect after attaching the bearer token. It never includes
the endpoint or token in the canonical payload, acknowledgement, exception
message, or its representation.

## Webhook request

The request is one JSON object with exactly the Phase C non-content fields:

```json
{
  "build_manifest_id": "...",
  "build_record_id": "...",
  "destination_identity": "...",
  "destination_key": "automation.n8n",
  "destination_registry_identity": "...",
  "dispatch_id": "...",
  "envelope_id": "...",
  "profile_identity": "...",
  "profile_registry_identity": "...",
  "route_identity": "...",
  "route_registry_identity": "...",
  "schema_version": "smart-prompt-automation-payload-v1",
  "task_request_id": "...",
  "transport_name": "n8n"
}
```

The body must not contain task text, prompt text, evidence bytes, trusted
instructions, credentials, bearer tokens, webhook URLs, provider routing,
retention controls, or Source Handling decisions. n8n must treat every received
value as non-content lineage metadata, not as authority.

## Idempotent workflow behavior

The workflow must use `dispatch_id` as its durable deduplication coordinate and
must compute the canonical `payload_id` from the exact received payload using
the repository's Phase C identity contract.

- A repeated `dispatch_id` with the same `payload_id` is the same delivery and
  may return the original receipt.
- A repeated `dispatch_id` with a different `payload_id` is a conflict and must
  be rejected without processing.
- A timeout or connection failure is not an acceptance. The caller must
  reconcile before replaying the same stable dispatch.
- The adapter performs no automatic retry because a failed HTTP exchange may
  have reached n8n.

## Webhook acknowledgement

For a successfully accepted delivery, n8n must return HTTP 2xx,
`Content-Type: application/json`, and exactly this object shape:

```json
{
  "dispatch_id": "...",
  "payload_id": "...",
  "receipt_id": "...",
  "accepted": true,
  "schema_version": "smart-prompt-automation-ack-v1"
}
```

The adapter rejects non-2xx responses (including redirects), non-JSON or
malformed bodies, unknown schema versions, extra fields, `accepted: false`, and
acknowledgements whose dispatch or payload identity does not match the submitted
canonical payload.

## Activation boundary

This contract does not activate a live workflow. Production activation requires
an operator to configure the three environment values, install the n8n workflow
with the request/acknowledgement contract above, and perform a separate
operational verification. No provider/model execution or autonomous prompt
mutation is introduced by this adapter.
