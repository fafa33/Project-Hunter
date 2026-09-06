# Governed GitHub Issue Agent Trigger

Issue #390 adds the first repository-owned execution edge for GitHub Issues. It is intentionally narrower than the existing n8n fallback worker and must not be used to bypass Smart Prompt Machine authority.

The edge has two halves. `scripts/hunter_issue_agent_trigger.py` runs inside GitHub Actions and only *authorizes*: it turns one exact `issues:labeled` event into a deterministic `hunter-issue-agent-authorization-v1` payload and wraps it in an issuer-signed `hunter-issue-agent-signed-authorization-v1` envelope. `hunter.automation.issue_agent_execution.GovernedIssueAgentExecutionService` is the production composition root that *consumes* that document behind the trusted issuer endpoint. Neither half can execute anything on its own.

## Authorization

Execution is eligible only when all of the following are true:

- the event is `issues:labeled`;
- the label is exactly `hunter-agent-execute`;
- the actor is exactly the repository owner;
- the target is an open Issue, not a pull request.

The trigger serializes a deterministic `hunter-issue-agent-authorization-v1` payload containing the exact Issue identity/content observed at authorization time and a SHA-256 authorization identity. Issue text cannot choose a provider, branch policy, reviewer, or merge behavior.

## Dispatch boundary

The workflow requires two repository secrets: `HUNTER_ISSUE_AGENT_WEBHOOK_URL` and `HUNTER_ISSUE_AGENT_AUTHORIZATION_SIGNING_KEY` (the hex Ed25519 issuer private key, whose public half the consumer holds as `HUNTER_ISSUE_AGENT_AUTHORIZATION_VERIFYING_KEY`). This keypair is dedicated to Issue authorization and is separate from the Smart Prompt automation envelope keypair. A missing or malformed signing key fails the authorization closed before any dispatch. The URL must be a credential-free HTTPS endpoint. Missing or malformed configuration fails closed. Redirects are refused rather than followed: a 3xx response fails the dispatch closed, so a redirected POST can never be downgraded to a GET and reported as a delivered authorization.

This endpoint is a **trusted issuer edge**, not the existing fallback-runtime webhook that expects an already-signed `PromptAutomationEnvelopeHandoff`. Behind it runs `GovernedIssueAgentExecutionService.execute()`, which composes existing authorities in this fixed order and adds no new one:

1. parse the signed envelope, **verify the issuer signature** over the exact canonical payload against the bootstrap-captured public key, re-derive the payload's `authorization_id` from its own claims, and recheck repository and owner against captured configuration;
2. map the authorization deterministically onto exactly one `PromptTaskRequest` (governed task key, document identity, execution owner, task text);
3. take durable execution ownership in the `issue_agent_execution_ledger` table before anything can run;
4. ingest the Issue content through the ADR 0036 `IssueSourceTransientIntakeBoundary`, which resolves the production read-only Source Handling authority and fails closed when retention is not permitted;
5. compile through the canonical `SmartPromptMachine`, which persists the build and issues the signed `PromptAutomationEnvelope`;
6. verify that envelope against the bootstrap-captured issuer verifier and record the exact serialized `PromptAutomationEnvelopeHandoff` durably;
7. hand those exact bytes, unchanged, to the existing fallback runtime, which keeps its own fixed provider order, remote-HEAD proof, targeted validation, and the no-auto-merge rule.

### Trusted origin

Authentication is added as a **separate outer schema** so the canonical inner payload keeps exactly the meaning accepted ADR 0036 §7 gave it. Two documents, two jobs:

| Schema | Role |
| --- | --- |
| `hunter-issue-agent-authorization-v1` | The canonical authorization payload the ADR names. Unchanged: same fields, same `authorization_id` derivation. It answers *what was authorized*. |
| `hunter-issue-agent-signed-authorization-v1` | The transport. Carries that payload **verbatim** plus the Ed25519 issuer proof. It answers *who minted it*. |

Only the envelope is executable. A bare `hunter-issue-agent-authorization-v1` document is refused at the composition root on the outer field set — it has no execution path at all, so an unsigned payload cannot reach mapping, the ledger, intake, compilation or dispatch.

The `authorization_id` digest proves only that the payload is internally consistent, and every field it covers — repository, Issue number, URL, title, body, owner login, governed label, `updated_at` — is public on an owner-authored Issue, so a caller reaching the issuer endpoint could recompute it. It is therefore **never** evidence of who minted the document.

The workflow that actually observes the owner's `issues:labeled` event signs the exact canonical payload — every claim, its `authorization_id` and its `schema_version` — under a domain separator, with a key held only as the repository secret `HUNTER_ISSUE_AGENT_AUTHORIZATION_SIGNING_KEY`. The composition root verifies that signature with a public key captured once at trusted bootstrap, before the ledger claim, before durable intake, and before any dispatch. Without a valid proof nothing durable or external happens at all.

The split is asymmetric deliberately: the execution side holds only the public half, so it can verify an owner authorization and can never mint one, and a later environment mutation cannot move the trust root.

Source Handling is a second, independent gate, not a substitute for this one. It governs whether an exact document scope may be processed, so for an Issue whose exact content already carries published authority it would admit a forged payload as readily as a genuine one. Proving the owner performed the authorization event is the issuer signature's job.

The composition root never signs, never chooses a provider, never selects a destination or branch, and has no merge path. It only reads Source Handling authority: it is built from `SqliteSourceHandlingAuthorityReadView`, never from `SourceHandlingAuthorityService`, so the execution path cannot publish the authority that governs it.

## Operational configuration

All of the following are required; any missing or malformed value fails closed before execution begins.

| Variable | Meaning |
| --- | --- |
| `HUNTER_ISSUE_AGENT_REPOSITORY` | Exact `owner/name` this deployment executes for. |
| `HUNTER_ISSUE_AGENT_OWNER_LOGIN` | The only login whose authorization is accepted. |
| `HUNTER_ISSUE_AGENT_EVIDENCE_DB` | Evidence Intelligence database, which must also hold the Source Handling authority history. |
| `HUNTER_ISSUE_AGENT_EXECUTION_BRANCH` | Remote branch providers must advance. Never taken from Issue text. |
| `HUNTER_ISSUE_AGENT_REPO_DIR` | Repository checkout provider commands run in. |
| `HUNTER_SOURCE_HANDLING_VERIFICATION_KEY` | Hex Ed25519 public key for the authority history. |
| `HUNTER_SOURCE_HANDLING_VERIFICATION_KEY_SHA256` | Operator-provisioned fingerprint of that key. |
| `HUNTER_SOURCE_HANDLING_GENESIS_RULE_SHA256` | Operator-provisioned genesis authorization-rule digest. |
| `HUNTER_PROMPT_AUTOMATION_VERIFYING_KEY` | Issuer verifier, captured once at bootstrap. |
| `HUNTER_PROMPT_AUTOMATION_SIGNING_KEY` | Smart Prompt issuer-only signing key. Machine-only; never repository content. |
| `HUNTER_ISSUE_AGENT_AUTHORIZATION_VERIFYING_KEY` | Public half of the dedicated Issue authorization issuer key, captured once at bootstrap. |

The existing `HUNTER_AGENT_*` provider, validation and timeout variables continue to configure the fallback runtime unchanged.

The canonical provenance resolver is an authority callable, not a configuration value, so `GovernedIssueAgentExecutionService.from_environment()` takes it as an explicit argument and has no default. An unwired deployment therefore cannot silently resolve provenance as "absent but acceptable".

## Replay

The authorization identity is deterministic over repository, Issue number/URL/title/body, owner, governed label, Issue `updated_at`, and schema version. Replaying the exact same authorization therefore produces the same identity, and the composition root recomputes that identity from the document's own claims rather than believing the value it carries: a single mutated claim no longer matches and is refused.

`IssueAgentExecutionLedger` stores that identity together with a digest of the complete document. Ownership is claimed before any execution work and advances `CLAIMED -> DISPATCHED -> COMPLETED` only forwards; the exact handoff bytes are recorded durably before they are dispatched. Ownership is never released on failure, so a crash or an uncertain network outcome leaves a row that refuses the next attempt. That is deliberate: a duplicate execution can push commits, a refused retry cannot. Recovering from a genuinely failed run is an explicit operator action, not an automatic retry.

## Security invariants

- No automatic execution on Issue creation/edit/comment.
- No authorization by non-owner actors, and no execution from an unsigned payload: a raw `hunter-issue-agent-authorization-v1` document, or one wrapped by a signer that is not the trusted issuer, cannot dispatch even when Source Handling authority exists for its exact content.
- No provider selection from Issue text.
- No merge instruction from Issue text.
- No signing key or provider credential is stored in repository content.
- The GitHub workflow has read-only repository/Issue permissions.
- Existing signed-handoff verification remains mandatory downstream, and the composition root additionally verifies the envelope it just issued against the bootstrap-captured verifier before dispatch.
- Issue text is caller data at every step: it selects no route, provider, destination, branch, reviewer or merge behaviour, and it never reaches the fallback runtime, whose handoff carries non-content lineage only.
- Fallback success is unchanged: a provider's own report is not success; a GitHub-visible remote HEAD advance plus the configured targeted validation are still required.
