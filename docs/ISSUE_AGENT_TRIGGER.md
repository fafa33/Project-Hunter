# Governed GitHub Issue Agent Trigger

Issue #390 adds the first repository-owned execution edge for GitHub Issues. It is intentionally narrower than the existing n8n fallback worker and must not be used to bypass Smart Prompt Machine authority.

## Authorization

Execution is eligible only when all of the following are true:

- the event is `issues:labeled`;
- the label is exactly `hunter-agent-execute`;
- the actor is exactly the repository owner;
- the target is an open Issue, not a pull request.

The trigger serializes a deterministic `hunter-issue-agent-authorization-v1` document containing the exact Issue identity/content observed at authorization time and a SHA-256 authorization identity. Issue text cannot choose a provider, branch policy, reviewer, or merge behavior.

## Dispatch boundary

The workflow requires the repository secret `HUNTER_ISSUE_AGENT_WEBHOOK_URL`. The URL must be a credential-free HTTPS endpoint. Missing or malformed configuration fails closed.

This endpoint is a **trusted issuer edge**, not the existing fallback-runtime webhook that expects an already-signed `PromptAutomationEnvelopeHandoff`. The receiver must:

1. validate/idempotently consume the exact authorization document;
2. compile the Issue task through the canonical Smart Prompt Machine route/profile authority;
3. create/persist the canonical build record;
4. issue and serialize the signed automation handoff;
5. pass that unchanged handoff to `hunter agent-fallback-run` / the existing n8n fallback worker;
6. preserve provider order, remote-HEAD proof, targeted validation, and the no-auto-merge rule.

Until that production Smart Prompt Machine composition root exists, this trigger must remain fail-closed in production and must not be pointed directly at the fallback worker.

## Replay

The authorization identity is deterministic over repository, Issue number/URL/title/body, owner, governed label, Issue `updated_at`, and schema version. Replaying the exact same authorization therefore produces the same identity. The trusted receiver must store/recognize this identity and reject or idempotently return an already-recorded dispatch result rather than executing it twice.

## Security invariants

- No automatic execution on Issue creation/edit/comment.
- No authorization by non-owner actors.
- No provider selection from Issue text.
- No merge instruction from Issue text.
- No signing key or provider credential is stored in repository content.
- The GitHub workflow has read-only repository/Issue permissions.
- Existing signed-handoff verification remains mandatory downstream.
