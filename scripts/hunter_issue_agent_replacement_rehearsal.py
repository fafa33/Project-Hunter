"""Non-publishing rehearsal entrypoint for the Issue Agent replacement executor."""

from __future__ import annotations

import argparse
import os
from pathlib import Path

from hunter.automation.issue_agent_control import load_configuration
from hunter.automation.issue_agent_execution import (
    ISSUE_AGENT_VERIFYING_KEY_ENV,
    IssueAgentAuthorizationError,
    IssueAgentAuthorizationVerifier,
    SignedIssueAgentAuthorization,
    require_canonical_rehearsal_identity,
)
from hunter.automation.issue_agent_replacement_executor import (
    assert_rehearsal_has_no_publication_authority,
    validate_replacement_result,
)


def pinned_verifier(checkout: Path, environ: dict[str, str] | os._Environ[str]) -> IssueAgentAuthorizationVerifier:
    """The verifier for the repository-pinned K_AUTH public root.

    A separately configured verifying key is never a second authority: it must equal the pinned root, so a
    divergent or stale secret fails closed instead of silently moving the trust root.
    """

    pinned = load_configuration(checkout).authorization_verifying_key
    configured = environ.get(ISSUE_AGENT_VERIFYING_KEY_ENV, "").strip()
    if configured and configured != pinned:
        raise IssueAgentAuthorizationError("configured verifying key does not match the repository-pinned root")
    return IssueAgentAuthorizationVerifier.from_environment(environ={ISSUE_AGENT_VERIFYING_KEY_ENV: pinned})


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--authorization", type=Path, required=True)
    parser.add_argument("--result", type=Path, required=True)
    parser.add_argument("--trust-roots-checkout", type=Path, default=Path("."))
    args = parser.parse_args()
    assert_rehearsal_has_no_publication_authority(os.environ)
    signed = SignedIssueAgentAuthorization.from_json(args.authorization.read_bytes())
    pinned_verifier(args.trust_roots_checkout, os.environ).verify(signed)
    require_canonical_rehearsal_identity(signed, owner_login=load_configuration(args.trust_roots_checkout).owner_login)
    validate_replacement_result(args.result.read_bytes(), signed_authorization=signed, rehearsal=True)
    print("replacement rehearsal validated; publication authority absent")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
