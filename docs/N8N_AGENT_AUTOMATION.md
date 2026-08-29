# n8n Agent Automation Activation

This is the operational edge for Issue #384. Smart Prompt Machine remains the authority for the signed handoff; n8n only invokes the fixed fallback runtime.

## n8n workflow

Use a Webhook node to receive the already-issued handoff, then an Execute Command node that writes the exact request body to a temporary handoff file and invokes:

```text
python -m hunter agent-fallback-run HANDOFF.json --repo /path/to/Project-Hunter --branch FEATURE_BRANCH --receipt-out RECEIPT.json
```

Return the receipt JSON from the Respond to Webhook node. The workflow must not edit the handoff JSON, choose a provider, or treat provider text as success.

## Provider adapters

Configure each adapter as a JSON argv array, not a shell command string:

```text
HUNTER_AGENT_CODEX_COMMAND=["/path/to/codex-wrapper"]
HUNTER_AGENT_CLAUDE_COMMAND=["/path/to/claude-wrapper"]
HUNTER_AGENT_FREEBUFF_COMMAND=["/path/to/freebuff-wrapper"]
HUNTER_AGENT_OPENCODE_COMMAND=["/path/to/opencode-wrapper"]
HUNTER_AGENT_JULES_COMMAND=["/path/to/jules-wrapper"]
HUNTER_AGENT_VALIDATION_COMMAND=["/path/to/hunter-targeted-validation"]
```

Every provider wrapper reads the exact canonical handoff from stdin. It receives `HUNTER_AGENT_PROVIDER`, `HUNTER_AGENT_BRANCH`, and `HUNTER_AGENT_REPO_DIR` in its environment. Exit `0` means the provider reports completion, exit `75` means rate-limited, and any other exit code means failure. These reports are not trusted as success: the runtime independently reads `origin/refs/heads/<branch>` and requires a new GitHub-visible HEAD plus the validation command to return zero.

The validation command receives `HUNTER_AGENT_EXPECTED_HEAD` and `HUNTER_AGENT_BRANCH`. It must validate that exact remote candidate.

## Canary completion

Run two real canaries before closing #384:

1. Normal: let the first available provider complete a small Hunter task and verify the receipt records a new remote HEAD with validation success.
2. Forced fallback: make the first provider wrapper exit `75`; verify the identical handoff reaches the next provider and only that provider is credited after a new remote HEAD and successful validation.

No automatic merge is part of this workflow.
