"""Canonical S6 rehearsal producer (ADR 0037 S6, Issue #560).

Runs only in the ``hunter-issue-agent-control`` trust domain on exact ``main``. It mints the signed
authorization with the existing Issue Agent primitives (``hunter_issue_agent_trigger``: same schema, same
signature domain, same K_AUTH) over an explicitly bounded rehearsal identity, builds the matching
non-publishing rehearsal result, and proves both against the repository-pinned public root before anything
leaves the job. It holds no model, publisher or GitHub credential, writes no ref, and never emits key material.
It is deterministic, so a repeated dispatch on the same ``main`` head yields byte-identical output.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import subprocess
import sys
from collections.abc import Mapping
from pathlib import Path

import hunter_issue_agent_trigger as trigger
from cryptography.hazmat.primitives import serialization

from hunter.automation.issue_agent_control import ControlRefused, load_configuration
from hunter.automation.issue_agent_execution import (
    ISSUE_AGENT_REHEARSAL_TITLE_PREFIX,
    IssueAgentAuthorizationVerifier,
    IssueAgentExecutionError,
    SignedIssueAgentAuthorization,
    derive_execution_target,
)
from hunter.automation.issue_agent_replacement_executor import (
    REHEARSAL_SCHEMA_VERSION,
    ReplacementExecutorError,
    assert_rehearsal_has_no_publication_authority,
    canonical_bytes,
    model_authority_present,
    validate_replacement_result,
)

REHEARSAL_ISSUE_NUMBER = 560
REHEARSAL_RESULT_PATH = "docs/rehearsal/hunter-s6-rehearsal.md"
#: Fixed so the identity is a pure function of the exact ``main`` head it is bound to.
REHEARSAL_UPDATED_AT = "2026-10-07T00:00:00Z"
REHEARSAL_PROHIBITED_PATHS = (".github/", "scripts/", "src/", "config/", "docs/ADR/", "docs/DEFECT_REGISTRY.json")
_SHA = re.compile(r"[0-9a-f]{40}")


class RehearsalProducerError(RuntimeError):
    pass


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise RehearsalProducerError(message)


def rehearsal_event(*, repository: str, owner_login: str, base_sha: str) -> dict[str, object]:
    """The bounded rehearsal identity as a canonical ``issues:labeled`` event for the existing authorizer."""

    _require(_SHA.fullmatch(base_sha) is not None, "base must be an exact lowercase commit SHA")
    scope = {
        "branch_pattern": f"issue-{REHEARSAL_ISSUE_NUMBER}-*",
        "base_ref": "main",
        "base_sha": base_sha,
        "allowed_paths": [REHEARSAL_RESULT_PATH],
        "prohibited_paths": list(REHEARSAL_PROHIBITED_PATHS),
    }
    body = (
        "Non-publishing S6 rehearsal identity. Never executable: no model run, no branch, no pull request.\n"
        f"<!-- hunter-task-scope-v1\n{json.dumps(scope, sort_keys=True, separators=(',', ':'))}\n-->"
    )
    return {
        "action": "labeled",
        "repository": {"full_name": repository},
        "sender": {"login": owner_login},
        "label": {"name": trigger.DEFAULT_LABEL},
        "issue": {
            "number": REHEARSAL_ISSUE_NUMBER,
            "html_url": f"https://github.com/{repository}/issues/{REHEARSAL_ISSUE_NUMBER}",
            "title": ISSUE_AGENT_REHEARSAL_TITLE_PREFIX + "S6 non-publishing replacement rehearsal",
            "body": body,
            "state": "open",
            "updated_at": REHEARSAL_UPDATED_AT,
        },
    }


def rehearsal_result(signed: SignedIssueAgentAuthorization) -> bytes:
    target = derive_execution_target(signed)
    content = (
        "# Hunter S6 rehearsal\n\n"
        "Non-publishing rehearsal result. It is validated and discarded: no model ran, no branch or\n"
        "pull request is created.\n\n"
        f"authorization: {target.authorization_id}\nbase: {target.base_sha}\n"
    ).encode()
    return canonical_bytes(
        {
            "schema_version": REHEARSAL_SCHEMA_VERSION,
            "authorization_id": target.authorization_id,
            "base_sha": target.base_sha,
            "branch": target.branch,
            "files": [
                {
                    "path": REHEARSAL_RESULT_PATH,
                    "content_b64": base64.b64encode(content).decode("ascii"),
                    "sha256": hashlib.sha256(content).hexdigest(),
                    "mode": "100644",
                }
            ],
        }
    )


def _git_head(checkout: Path) -> str:
    completed = subprocess.run(
        ["git", "-C", str(checkout), "rev-parse", "HEAD"], capture_output=True, text=True, check=False
    )
    return completed.stdout.strip() if completed.returncode == 0 else ""


def produce(environ: Mapping[str, str], *, checkout: Path) -> tuple[bytes, bytes]:
    """Return the verified ``(signed authorization, rehearsal result)`` pair, or raise fail-closed."""

    configuration = load_configuration(checkout)
    _require(environ.get("GITHUB_REPOSITORY") == configuration.repository, "not the pinned repository")
    _require(environ.get("GITHUB_REF") == "refs/heads/main", "the rehearsal is minted on main only")
    _require(environ.get("GITHUB_ACTOR") == configuration.owner_login, "only the repository owner may dispatch")
    _require(environ.get("GITHUB_RUN_ATTEMPT") == "1", "the rehearsal is never minted on a re-run")
    base_sha = environ.get("GITHUB_SHA", "")
    _require(_SHA.fullmatch(base_sha) is not None, "GITHUB_SHA is not an exact commit")
    _require(_git_head(checkout) == base_sha, "the checkout is not the exact main head")
    assert_rehearsal_has_no_publication_authority(environ)
    _require(not model_authority_present(environ), "model authority is absent from the rehearsal producer")

    key = trigger.load_signing_key(environ.get(trigger.SIGNING_KEY_ENV))
    public = key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw).hex()
    _require(public == configuration.authorization_verifying_key, "signing key does not match the pinned K_AUTH root")

    authorization = trigger.authorize_event(
        rehearsal_event(repository=configuration.repository, owner_login=configuration.owner_login, base_sha=base_sha),
        expected_repository=configuration.repository,
        owner_login=configuration.owner_login,
    )
    document = trigger.sign_authorization(authorization, signing_key=key).to_json().encode("utf-8")
    signed = SignedIssueAgentAuthorization.from_json(document)
    IssueAgentAuthorizationVerifier.from_environment(
        environ={"HUNTER_ISSUE_AGENT_AUTHORIZATION_VERIFYING_KEY": configuration.authorization_verifying_key}
    ).verify(signed)
    result = rehearsal_result(signed)
    validate_replacement_result(result, signed_authorization=signed, rehearsal=True)
    return document, result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="hunter_issue_agent_rehearsal_producer")
    parser.add_argument("--checkout", type=Path, default=Path("."))
    parser.add_argument("--out-dir", type=Path, required=True)
    arguments = parser.parse_args(argv)
    try:
        document, result = produce(os.environ, checkout=arguments.checkout)
    except (RehearsalProducerError, ControlRefused, IssueAgentExecutionError, ReplacementExecutorError) as error:
        print(f"rehearsal producer refused: {type(error).__name__}: {error}", file=sys.stderr)
        return 2
    except trigger.IssueAgentTriggerError as error:
        print(f"rehearsal producer refused: {error}", file=sys.stderr)
        return 2
    arguments.out_dir.mkdir(parents=True, exist_ok=True)
    (arguments.out_dir / "authorization.json").write_bytes(document)
    (arguments.out_dir / "result.json").write_bytes(result)
    lines = (
        f"authorization_b64={base64.b64encode(document).decode('ascii')}\n"
        f"result_b64={base64.b64encode(result).decode('ascii')}\n"
    )
    output = os.environ.get("GITHUB_OUTPUT")
    if output:
        with open(output, "a", encoding="utf-8") as handle:
            handle.write(lines)
    print("rehearsal pair minted and verified against the pinned K_AUTH root")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
