#!/usr/bin/env python3
"""GitHub-hosted jobs of the governed Issue Agent production path (Issue #557).

One trusted default-branch script, three separate GitHub-hosted jobs, three
separate trust domains:

``execute``
    Model authority only. Fetches the exact SPM/DPM handoff and prompt for one
    opaque authorization identity over the GitHub-OIDC-authenticated issuer
    channel, runs the pinned OpenCode runtime in a credential-free workspace at
    the exact signed base, and returns the closed-schema hostile file-data
    result. It refuses to start if any publication credential is present.
``validate``
    No credential at all. Fetches the validated result, re-verifies the signed
    authorization and the validation receipt, and runs the trusted pre-push
    safety boundary on an unsigned commit with the exact candidate tree. This
    is the only job that executes candidate-controlled repository content.
``publish``
    Publication authority only. Fetches the same validated result, binds a
    signed commit to the exact tree ``validate`` proved, and pushes it
    create-only through the #524 publisher. It never runs candidate content,
    holds no model authority, and opens no pull request: the existing
    ``Hunter / Issue Agent Candidate PR`` workflow remains the only PR creator.

The repository is public, so nothing sensitive is ever printed: no prompt, no
handoff, no result content and no issuer error text. Output is a fixed status
code plus non-secret identities and digests.
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
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from hunter.automation.issue_agent_execution import IssueAgentAuthorizationVerifier, SignedIssueAgentAuthorization
from hunter.automation.issue_agent_github_execution import (
    EXECUTION_CANDIDATE_SCHEMA_VERSION,
    EXECUTION_FETCH_PATH,
    EXECUTION_HANDOFF_SCHEMA_VERSION,
    EXECUTION_REQUEST_SCHEMA_VERSION,
    EXECUTION_RESULT_ACK_SCHEMA_VERSION,
    EXECUTION_RESULT_PATH,
    ROLE_EXECUTOR,
    ROLE_PUBLISHER,
    ROLE_VALIDATOR,
    VALIDATION_DEFINITION,
    oidc_audience,
)
from hunter.automation.issue_agent_replacement_executor import (
    OIDC_REQUEST_ENV,
    PUBLICATION_CREDENTIAL_ENV,
    RESULT_SCHEMA_VERSION,
    ReplacementExecutorError,
    ValidatedReplacementResult,
    publication_credentials_present,
    publish_create_only,
    publisher_environment_is_safe,
    run_credential_free_candidate_safety,
    validate_replacement_result,
    verify_validation_receipt,
)

WEBHOOK_URL_ENV = "HUNTER_ISSUE_AGENT_WEBHOOK_URL"
AUTHORIZE_PATH = "/issue-agent/authorize"
OPENCODE_EXECUTABLE_ENV = "HUNTER_ISSUE_AGENT_EXECUTOR_OPENCODE"
MODEL_ENV = "HUNTER_OPENCODE_MODEL"
MODEL_API_KEY_ENV = "HUNTER_ISSUE_AGENT_EXECUTOR_MODEL_API_KEY"
MODEL_API_KEY_NAME_ENV = "HUNTER_ISSUE_AGENT_EXECUTOR_MODEL_KEY_ENV"
PUBLISHER_SIGNING_KEY_ENV = "HUNTER_ISSUE_AGENT_PUBLISHER_SIGNING_KEY"
PUBLISHER_PUSH_TOKEN_ENV = "HUNTER_ISSUE_AGENT_PUBLISHER_PUSH_TOKEN"
PUBLISHER_WRITER_ENV = "HUNTER_ISSUE_AGENT_PUBLISHER_WRITER"
CODE_WRITE_POLICY = Path(__file__).resolve().parents[1] / "docs" / "CODE_WRITE_POLICY.json"

#: The issuer edge bounds bodies at 256 KiB; leave room for the OIDC token.
MAX_RESULT_REQUEST_BYTES = 240 * 1024
MAX_RESPONSE_BYTES = 256 * 1024
MODEL_TIMEOUT_SECONDS = 45 * 60
_AUTHORIZATION_ID_RE = re.compile(r"hunter-issue-agent-authorization:[0-9a-f]{64}")
_SHA_RE = re.compile(r"[0-9a-f]{40}")
_ENV_NAME_RE = re.compile(r"[A-Z][A-Z0-9_]{0,63}")


class ExecutionJobError(RuntimeError):
    """A fixed, non-secret failure code for one job."""

    def __init__(self, code: str, detail: str = "") -> None:
        self.code = code
        self.detail = detail
        super().__init__(code)


class _RejectRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[no-untyped-def]
        raise ExecutionJobError("TRANSPORT_REDIRECT_REFUSED")


_OPENER = urllib.request.build_opener(_RejectRedirects)


def _required(environ: Mapping[str, str], name: str) -> str:
    value = environ.get(name, "").strip()
    if not value:
        raise ExecutionJobError("MISSING_CONFIGURATION", name)
    return value


def execution_base_url(environ: Mapping[str, str]) -> str:
    """Derive the issuer base from the existing trigger webhook URL; no new endpoint secret."""
    parsed = urllib.parse.urlsplit(_required(environ, WEBHOOK_URL_ENV))
    if (
        parsed.scheme != "https"
        or not parsed.netloc
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or parsed.path != AUTHORIZE_PATH
    ):
        raise ExecutionJobError("ISSUER_URL_INVALID")
    return f"https://{parsed.netloc}"


def request_oidc_token(environ: Mapping[str, str], audience: str) -> str:
    """Mint this job's GitHub Actions OIDC token for the execution audience."""
    url = environ.get("ACTIONS_ID_TOKEN_REQUEST_URL", "").strip()
    bearer = environ.get("ACTIONS_ID_TOKEN_REQUEST_TOKEN", "").strip()
    if not url or not bearer:
        raise ExecutionJobError("OIDC_UNAVAILABLE")
    separator = "&" if "?" in url else "?"
    request = urllib.request.Request(
        f"{url}{separator}audience={urllib.parse.quote(audience, safe='')}",
        headers={"Authorization": f"bearer {bearer}"},
    )
    try:
        with _OPENER.open(request, timeout=30) as response:
            payload = json.loads(response.read(64 * 1024).decode("utf-8"))
    except (urllib.error.URLError, OSError, ValueError):
        raise ExecutionJobError("OIDC_UNAVAILABLE") from None
    token = payload.get("value") if isinstance(payload, dict) else None
    if not isinstance(token, str) or not token:
        raise ExecutionJobError("OIDC_UNAVAILABLE")
    return token


def post_execution(base_url: str, path: str, payload: Mapping[str, Any]) -> dict[str, Any]:
    """One authenticated issuer call. Never retried: every operation is single-use."""
    body = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    request = urllib.request.Request(
        f"{base_url}{path}", data=body, headers={"Content-Type": "application/json"}, method="POST"
    )
    try:
        with _OPENER.open(request, timeout=120) as response:
            raw = response.read(MAX_RESPONSE_BYTES + 1)
    except urllib.error.HTTPError as error:
        # The refusal text may name candidate content; only the status is reported.
        raise ExecutionJobError("ISSUER_REFUSED", f"HTTP {error.code}") from None
    except (urllib.error.URLError, OSError, TimeoutError):
        raise ExecutionJobError("ISSUER_UNAVAILABLE") from None
    if len(raw) > MAX_RESPONSE_BYTES:
        raise ExecutionJobError("ISSUER_RESPONSE_TOO_LARGE")
    try:
        decoded = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        raise ExecutionJobError("ISSUER_RESPONSE_MALFORMED") from None
    if not isinstance(decoded, dict):
        raise ExecutionJobError("ISSUER_RESPONSE_MALFORMED")
    return decoded


def _fetch(environ: Mapping[str, str], authorization_id: str, role: str) -> dict[str, Any]:
    repository = _required(environ, "GITHUB_REPOSITORY")
    token = request_oidc_token(environ, oidc_audience(repository))
    response = post_execution(
        execution_base_url(environ),
        EXECUTION_FETCH_PATH,
        {
            "schema_version": EXECUTION_REQUEST_SCHEMA_VERSION,
            "authorization_id": authorization_id,
            "role": role,
            "oidc_token": token,
        },
    )
    if response.get("authorization_id") != authorization_id:
        raise ExecutionJobError("ISSUER_RESPONSE_MISBOUND")
    return response


def _git(cwd: Path, *args: str, input_bytes: bytes | None = None) -> bytes:
    env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": str(cwd),
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_TERMINAL_PROMPT": "0",
    }
    completed = subprocess.run(
        ("git", *args), cwd=cwd, env=env, input=input_bytes, capture_output=True, check=False, timeout=300
    )
    if completed.returncode != 0:
        raise ExecutionJobError("WORKSPACE_GIT_FAILED", args[0])
    return completed.stdout


def materialize_base(workspace: Path, *, repository: str, base_sha: str) -> None:
    """A credential-free workspace at exactly the signed base."""
    if _SHA_RE.fullmatch(base_sha) is None or re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository) is None:
        raise ExecutionJobError("HANDOFF_TARGET_INVALID")
    workspace.mkdir(parents=True)
    _git(workspace, "init", "--quiet")
    _git(workspace, "fetch", "--quiet", "--depth=1", "--no-tags", f"https://github.com/{repository}.git", base_sha)
    _git(workspace, "checkout", "--quiet", "--detach", "FETCH_HEAD")
    if _git(workspace, "rev-parse", "HEAD").decode().strip() != base_sha:
        raise ExecutionJobError("WORKSPACE_NOT_AT_BASE")


def model_environment(environ: Mapping[str, str], *, workspace: Path, home: Path) -> dict[str, str]:
    """The model child's whole environment: an allowlist, never the job environment."""
    key_name = _required(environ, MODEL_API_KEY_NAME_ENV)
    if (
        _ENV_NAME_RE.fullmatch(key_name) is None
        or key_name in PUBLICATION_CREDENTIAL_ENV
        or key_name in OIDC_REQUEST_ENV
        or key_name.startswith(("GITHUB_", "ACTIONS_", "RUNNER_", "GIT_"))
    ):
        raise ExecutionJobError("MODEL_KEY_NAME_INVALID", key_name)
    return {
        "PATH": environ.get("PATH", "/usr/bin:/bin"),
        "LANG": "C.UTF-8",
        "HOME": str(home),
        "XDG_CONFIG_HOME": str(home / ".config"),
        "XDG_DATA_HOME": str(home / ".local" / "share"),
        "XDG_CACHE_HOME": str(home / ".cache"),
        "PWD": str(workspace),
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_TERMINAL_PROMPT": "0",
        key_name: _required(environ, MODEL_API_KEY_ENV),
    }


def collect_result(workspace: Path, *, authorization_id: str, branch: str, base_sha: str) -> str:
    """Turn the workspace change into the closed-schema hostile result document."""
    _git(workspace, "add", "--all")
    listing = _git(workspace, "diff", "--cached", "--no-renames", "--name-status", "-z", base_sha).split(b"\0")
    entries = [item for item in listing if item]
    if not entries:
        raise ExecutionJobError("NO_CANDIDATE_CHANGE")
    files: list[dict[str, str]] = []
    for status, raw_path in zip(entries[::2], entries[1::2], strict=True):
        if status not in (b"A", b"M"):
            # The result schema carries file content only; a deletion, type
            # change or anything else is not expressible and fails closed.
            raise ExecutionJobError("UNSUPPORTED_CANDIDATE_CHANGE", status.decode(errors="replace"))
        path = raw_path.decode("utf-8")
        mode = _git(workspace, "ls-files", "--stage", "--", path).decode().split(" ", 1)[0]
        if mode not in ("100644", "100755"):
            raise ExecutionJobError("UNSUPPORTED_CANDIDATE_MODE")
        content = _git(workspace, "cat-file", "blob", f":{path}")
        files.append(
            {
                "path": path,
                "content_b64": base64.b64encode(content).decode("ascii"),
                "sha256": hashlib.sha256(content).hexdigest(),
                "mode": mode,
            }
        )
    return json.dumps(
        {
            "schema_version": RESULT_SCHEMA_VERSION,
            "authorization_id": authorization_id,
            "base_sha": base_sha,
            "branch": branch,
            "files": files,
        },
        sort_keys=True,
        separators=(",", ":"),
    )


def run_execute(authorization_id: str, environ: Mapping[str, str]) -> str:
    leaked = publication_credentials_present(environ)
    if leaked:
        raise ExecutionJobError("EXECUTOR_HAS_PUBLICATION_AUTHORITY", ",".join(leaked))
    executable = _required(environ, OPENCODE_EXECUTABLE_ENV)
    handoff = _fetch(environ, authorization_id, ROLE_EXECUTOR)
    if handoff.get("schema_version") != EXECUTION_HANDOFF_SCHEMA_VERSION:
        raise ExecutionJobError("HANDOFF_SCHEMA_MISMATCH")
    if handoff.get("repository") != environ.get("GITHUB_REPOSITORY"):
        raise ExecutionJobError("HANDOFF_REPOSITORY_MISMATCH")
    prompt = handoff.get("exact_prompt")
    branch, base_sha = handoff.get("branch"), handoff.get("base_sha")
    if not isinstance(prompt, str) or not prompt or not isinstance(branch, str) or not isinstance(base_sha, str):
        raise ExecutionJobError("HANDOFF_INCOMPLETE")
    with tempfile.TemporaryDirectory(prefix="hunter-issue-agent-executor-") as directory:
        root = Path(directory)
        workspace = root / "workspace"
        home = root / "home"
        home.mkdir(mode=0o700)
        materialize_base(workspace, repository=str(handoff["repository"]), base_sha=base_sha)
        argv = [executable, "run"]
        model = environ.get(MODEL_ENV, "").strip()
        if model:
            argv.extend(("--model", model))
        argv.append(prompt)
        try:
            completed = subprocess.run(
                argv,
                cwd=workspace,
                env=model_environment(environ, workspace=workspace, home=home),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
                timeout=MODEL_TIMEOUT_SECONDS,
            )
        except subprocess.TimeoutExpired:
            raise ExecutionJobError("MODEL_TIMEOUT") from None
        if completed.returncode != 0:
            raise ExecutionJobError("MODEL_FAILED", f"exit {completed.returncode}")
        document = collect_result(workspace, authorization_id=authorization_id, branch=branch, base_sha=base_sha)
    repository = _required(environ, "GITHUB_REPOSITORY")
    payload = {
        "schema_version": EXECUTION_REQUEST_SCHEMA_VERSION,
        "authorization_id": authorization_id,
        "role": ROLE_EXECUTOR,
        "oidc_token": request_oidc_token(environ, oidc_audience(repository)),
        "result_document": document,
    }
    if len(json.dumps(payload).encode("utf-8")) > MAX_RESULT_REQUEST_BYTES:
        raise ExecutionJobError("RESULT_TOO_LARGE")
    ack = post_execution(execution_base_url(environ), EXECUTION_RESULT_PATH, payload)
    if ack.get("schema_version") != EXECUTION_RESULT_ACK_SCHEMA_VERSION or ack.get("authorization_id") != (
        authorization_id
    ):
        raise ExecutionJobError("RESULT_NOT_ACCEPTED")
    return str(ack.get("result_sha256"))


def _verified_candidate(
    authorization_id: str, environ: Mapping[str, str], role: str
) -> tuple[ValidatedReplacementResult, Any]:
    candidate = _fetch(environ, authorization_id, role)
    if candidate.get("schema_version") != EXECUTION_CANDIDATE_SCHEMA_VERSION:
        raise ExecutionJobError("CANDIDATE_SCHEMA_MISMATCH")
    if candidate.get("validation_definition") != VALIDATION_DEFINITION:
        raise ExecutionJobError("VALIDATION_DEFINITION_MISMATCH")
    document = candidate.get("result_document")
    if not isinstance(document, str):
        raise ExecutionJobError("CANDIDATE_INCOMPLETE")
    try:
        signed = SignedIssueAgentAuthorization.from_json(str(candidate.get("signed_authorization")))
        IssueAgentAuthorizationVerifier.from_environment(environ=environ).verify(signed)
        if signed.authorization.authorization_id != authorization_id:
            raise ExecutionJobError("CANDIDATE_AUTHORIZATION_MISMATCH")
        receipt = verify_validation_receipt(
            str(candidate.get("validation_receipt")),
            result_document=document,
            signed_authorization=signed,
            expected_validation_definition=VALIDATION_DEFINITION,
        )
        validated = validate_replacement_result(document, signed_authorization=signed, rehearsal=False)
    except ExecutionJobError:
        raise
    except Exception:  # noqa: BLE001 - the reason may quote candidate content
        raise ExecutionJobError("CANDIDATE_VERIFICATION_FAILED") from None
    return validated, receipt


def writer_identity(login: str) -> tuple[str, str]:
    """The canonical Git identity CODE_WRITE_POLICY binds to an authorized signer."""
    policy = json.loads(CODE_WRITE_POLICY.read_text(encoding="utf-8"))
    if login not in policy["ingress_provenance"]["authorized_signers"]:
        raise ExecutionJobError("WRITER_NOT_AUTHORIZED_SIGNER", login)
    for identity in policy["writer_identity_binding"]["identities"]:
        if identity.get("login") == login and identity.get("channel") == "clone-capable":
            return str(identity["canonical_git_name"]), str(identity["canonical_git_email"])
    raise ExecutionJobError("WRITER_NOT_BOUND", login)


def _bind_writer(login: str) -> tuple[str, str]:
    name, email = writer_identity(login)
    for role in ("AUTHOR", "COMMITTER"):
        os.environ[f"GIT_{role}_NAME"] = name
        os.environ[f"GIT_{role}_EMAIL"] = email
    return name, email


def run_validate(authorization_id: str, repo: Path, environ: Mapping[str, str]) -> str:
    leaked = publication_credentials_present(environ)
    if leaked:
        raise ExecutionJobError("VALIDATOR_HAS_PUBLICATION_AUTHORITY", ",".join(leaked))
    if not publisher_environment_is_safe(environ):
        raise ExecutionJobError("VALIDATOR_HAS_MODEL_AUTHORITY")
    _bind_writer(_required(environ, PUBLISHER_WRITER_ENV))
    validated, _receipt = _verified_candidate(authorization_id, environ, ROLE_VALIDATOR)
    try:
        return run_credential_free_candidate_safety(repo, validated=validated)
    except ReplacementExecutorError:
        # The hook output is candidate-derived content and is never printed.
        raise ExecutionJobError("CANDIDATE_SAFETY_FAILED") from None


def missing_publication_credentials(environ: Mapping[str, str]) -> list[str]:
    return [
        name
        for name in (PUBLISHER_SIGNING_KEY_ENV, PUBLISHER_PUSH_TOKEN_ENV, PUBLISHER_WRITER_ENV)
        if not environ.get(name, "").strip()
    ]


def _configure_publication(directory: Path, *, email: str, signing_key: str, push_token: str) -> Path:
    """Process-scoped Git configuration for SSH signing and token push; nothing persisted."""
    key_path = directory / "signing-key"
    key_path.write_text(signing_key.strip() + "\n", encoding="utf-8")
    key_path.chmod(0o600)
    public = subprocess.run(("ssh-keygen", "-y", "-f", str(key_path)), capture_output=True, check=False, timeout=30)
    if public.returncode != 0 or not public.stdout.strip():
        raise ExecutionJobError("SIGNING_KEY_INVALID")
    allowed = directory / "allowed-signers"
    allowed.write_bytes(email.encode("utf-8") + b" " + public.stdout.strip() + b"\n")
    basic = base64.b64encode(f"x-access-token:{push_token}".encode()).decode("ascii")
    settings = (
        ("gpg.format", "ssh"),
        ("user.signingkey", str(key_path)),
        ("gpg.ssh.allowedSignersFile", str(allowed)),
        ("http.https://github.com/.extraheader", f"AUTHORIZATION: basic {basic}"),
    )
    os.environ["GIT_CONFIG_COUNT"] = str(len(settings))
    for index, (key, value) in enumerate(settings):
        os.environ[f"GIT_CONFIG_KEY_{index}"] = key
        os.environ[f"GIT_CONFIG_VALUE_{index}"] = value
    os.environ["GIT_TERMINAL_PROMPT"] = "0"
    return key_path


def run_publish(authorization_id: str, repo: Path, safety_tree: str, environ: Mapping[str, str]) -> tuple[str, str]:
    missing = missing_publication_credentials(environ)
    if missing:
        raise ExecutionJobError("MISSING_PUBLICATION_CREDENTIAL", ",".join(missing))
    if not publisher_environment_is_safe(environ):
        raise ExecutionJobError("PUBLISHER_HAS_MODEL_AUTHORITY")
    if _SHA_RE.fullmatch(safety_tree) is None:
        raise ExecutionJobError("SAFETY_TREE_INVALID")
    _name, email = _bind_writer(environ[PUBLISHER_WRITER_ENV].strip())
    validated, receipt = _verified_candidate(authorization_id, environ, ROLE_PUBLISHER)
    with tempfile.TemporaryDirectory(prefix="hunter-issue-agent-publisher-") as directory:
        key_path = _configure_publication(
            Path(directory),
            email=email,
            signing_key=environ[PUBLISHER_SIGNING_KEY_ENV],
            push_token=environ[PUBLISHER_PUSH_TOKEN_ENV].strip(),
        )
        try:
            publication = publish_create_only(
                repo,
                validated=validated,
                verified_receipt=receipt,
                safety_tree=safety_tree,
                signing_key=str(key_path),
            )
        except ReplacementExecutorError as error:
            code = "PUBLICATION_BRANCH_EXISTS" if "already exists" in str(error) else "PUBLICATION_REFUSED"
            raise ExecutionJobError(code) from None
    return publication.branch, publication.head_sha


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="hunter_issue_agent_github_executor")
    parser.add_argument("role", choices=("execute", "validate", "publish"))
    parser.add_argument("--authorization-id", required=True)
    parser.add_argument("--repository-checkout", type=Path, default=Path.cwd())
    parser.add_argument("--safety-tree", default="")
    parser.add_argument("--output")
    return parser


def main(argv: list[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    try:
        if _AUTHORIZATION_ID_RE.fullmatch(arguments.authorization_id) is None:
            raise ExecutionJobError("AUTHORIZATION_ID_INVALID")
        if arguments.role == "execute":
            digest = run_execute(arguments.authorization_id, os.environ)
            print(f"issue-agent execute: RESULT_ACCEPTED result_sha256={digest}")
        elif arguments.role == "validate":
            tree = run_validate(arguments.authorization_id, arguments.repository_checkout, os.environ)
            if arguments.output:
                with Path(arguments.output).open("a", encoding="utf-8") as handle:
                    handle.write(f"tree={tree}\n")
            print(f"issue-agent validate: CANDIDATE_SAFE tree={tree}")
        else:
            branch, head = run_publish(
                arguments.authorization_id, arguments.repository_checkout, arguments.safety_tree, os.environ
            )
            print(f"issue-agent publish: PUBLISHED branch={branch} head={head}")
        return 0
    except ExecutionJobError as error:
        suffix = f" ({error.detail})" if error.detail else ""
        print(f"issue-agent {arguments.role}: {error.code}{suffix}", file=sys.stderr)
        return 3 if error.code == "MISSING_PUBLICATION_CREDENTIAL" else 2


if __name__ == "__main__":
    raise SystemExit(main())
