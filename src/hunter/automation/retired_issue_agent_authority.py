"""Fail-closed guard for the retired parallel Issue-agent authorities (ADR 0037 D9, AT-46).

The GitHub-native lifecycle is the only Issue-agent execution and publication path. The Railway-era
fallback runtime, the OpenCode provider runtime, the n8n transport and the n8n canary are retired: each
entry point calls :func:`refuse_retired_authority` before it does any work, and they are deleted in S8.
There is deliberately no configuration, environment variable or argument that re-enables them.
"""

from __future__ import annotations

from typing import NoReturn

RETIRED_ISSUE_AGENT_AUTHORITIES = frozenset(
    {
        "agent-fallback-run",
        "n8n-canary",
        "agent_fallback_runtime.OperationalAgentFallbackRuntime",
        "opencode_provider_runtime",
        "n8n.N8nPromptAutomationTransport",
    }
)

RETIRED_AUTHORITY_EXIT_CODE = 2


class RetiredAuthorityError(RuntimeError):
    """A retired Issue-agent authority was invoked."""

    failure_code = "RETIRED_ISSUE_AGENT_AUTHORITY"


def refuse_retired_authority(name: str) -> NoReturn:
    """Refuse a retired authority unconditionally."""
    if name not in RETIRED_ISSUE_AGENT_AUTHORITIES:
        raise ValueError(f"unknown retired authority: {name!r}")
    raise RetiredAuthorityError(
        f"{name} is retired (ADR 0037 D9); the GitHub-native lifecycle is the only Issue-agent path"
    )


__all__ = [
    "RETIRED_AUTHORITY_EXIT_CODE",
    "RETIRED_ISSUE_AGENT_AUTHORITIES",
    "RetiredAuthorityError",
    "refuse_retired_authority",
]
