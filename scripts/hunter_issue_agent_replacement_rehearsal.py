"""Non-publishing rehearsal entrypoint for the Issue Agent replacement executor."""

from __future__ import annotations

import argparse
import os
from pathlib import Path

from hunter.automation.issue_agent_execution import IssueAgentAuthorizationVerifier, SignedIssueAgentAuthorization
from hunter.automation.issue_agent_replacement_executor import (
    assert_rehearsal_has_no_publication_authority,
    validate_replacement_result,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--authorization", type=Path, required=True)
    parser.add_argument("--result", type=Path, required=True)
    args = parser.parse_args()
    assert_rehearsal_has_no_publication_authority(os.environ)
    signed = SignedIssueAgentAuthorization.from_json(args.authorization.read_bytes())
    IssueAgentAuthorizationVerifier.from_environment().verify(signed)
    validate_replacement_result(args.result.read_bytes(), signed_authorization=signed, rehearsal=True)
    print("replacement rehearsal validated; publication authority absent")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
