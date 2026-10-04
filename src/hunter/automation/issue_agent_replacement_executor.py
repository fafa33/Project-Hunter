"""Credential-isolated result contract for the Issue Agent replacement executor."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import pwd
import re
import shutil
import sqlite3
import subprocess
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from hunter.automation.issue_agent_execution import SignedIssueAgentAuthorization, derive_execution_target
from hunter.task_scope import TaskScopeContract, path_matches_scope_entry

RESULT_SCHEMA_VERSION = "hunter-issue-agent-replacement-result-v1"
REHEARSAL_SCHEMA_VERSION = "hunter-issue-agent-replacement-rehearsal-v1"
_SHA = re.compile(r"[0-9a-f]{64}")
_MAX_RESULT_BYTES = 8 * 1024 * 1024
_MAX_FILE_BYTES = 2 * 1024 * 1024
_MAX_FILES = 256


class ReplacementExecutorError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class CandidateFile:
    path: str
    content: bytes
    sha256: str
    mode: str


@dataclass(frozen=True, slots=True)
class ValidatedReplacementResult:
    authorization_id: str
    repository: str
    branch: str
    base_sha: str
    files: tuple[CandidateFile, ...]
    result_sha256: str
    rehearsal: bool


def _canonical_path(value: object) -> str:
    if not isinstance(value, str) or not value or "\\" in value:
        raise ReplacementExecutorError("candidate path must be a non-empty POSIX path")
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts or "." in path.parts or str(path) != value:
        raise ReplacementExecutorError(f"candidate path is not canonical: {value!r}")
    if value == ".git" or value.startswith(".git/"):
        raise ReplacementExecutorError("candidate may not write Git metadata")
    return value


def _path_allowed(path: str, scope: TaskScopeContract) -> bool:
    return any(path_matches_scope_entry(path, x) for x in scope.allowed_paths) and not any(
        path_matches_scope_entry(path, x) for x in scope.prohibited_paths
    )


def _decode_file(entry: object) -> CandidateFile:
    if not isinstance(entry, Mapping) or set(entry) != {"path", "content_b64", "sha256", "mode"}:
        raise ReplacementExecutorError("candidate file schema mismatch")
    path = _canonical_path(entry["path"])
    digest = entry["sha256"]
    encoded = entry["content_b64"]
    mode = entry["mode"]
    if mode not in ("100644", "100755"):
        raise ReplacementExecutorError(f"candidate file {path!r} has invalid mode")
    if not isinstance(digest, str) or _SHA.fullmatch(digest) is None:
        raise ReplacementExecutorError(f"candidate file {path!r} has invalid sha256")
    if not isinstance(encoded, str):
        raise ReplacementExecutorError(f"candidate file {path!r} content must be base64 text")
    try:
        content = base64.b64decode(encoded, validate=True)
    except ValueError:
        raise ReplacementExecutorError(f"candidate file {path!r} content is invalid base64") from None
    if len(content) > _MAX_FILE_BYTES:
        raise ReplacementExecutorError(f"candidate file {path!r} exceeds size limit")
    if hashlib.sha256(content).hexdigest() != digest:
        raise ReplacementExecutorError(f"candidate file {path!r} digest mismatch")
    return CandidateFile(path, content, digest, mode)


@dataclass(frozen=True, slots=True)
class ResultBinding:
    """What a hostile result must bind, as recorded in the signed anchored ledger (ADR 0037 D2)."""

    authorization_id: str
    repository: str
    branch: str
    base_sha: str
    scope: TaskScopeContract


def validate_replacement_result(
    document: str | bytes, *, signed_authorization: SignedIssueAgentAuthorization, rehearsal: bool
) -> ValidatedReplacementResult:
    target = derive_execution_target(signed_authorization)
    return validate_bound_result(
        document,
        binding=ResultBinding(
            target.authorization_id,
            target.repository,
            target.branch,
            target.base_sha,
            signed_authorization.implementation_scope,
        ),
        rehearsal=rehearsal,
    )


def validate_bound_result(
    document: str | bytes, *, binding: ResultBinding, rehearsal: bool
) -> ValidatedReplacementResult:
    """Validate a hostile result against a ledger binding; the single implementation of the result contract."""

    raw = document.encode() if isinstance(document, str) else bytes(document)
    if len(raw) > _MAX_RESULT_BYTES:
        raise ReplacementExecutorError("replacement result exceeds size limit")
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        raise ReplacementExecutorError("replacement result must be UTF-8 JSON") from None
    expected = {"schema_version", "authorization_id", "base_sha", "branch", "files"}
    if not isinstance(payload, Mapping) or set(payload) != expected:
        raise ReplacementExecutorError("replacement result schema mismatch")
    if payload["schema_version"] != (REHEARSAL_SCHEMA_VERSION if rehearsal else RESULT_SCHEMA_VERSION):
        raise ReplacementExecutorError("replacement result has wrong execution class")
    for key, value in (
        ("authorization_id", binding.authorization_id),
        ("base_sha", binding.base_sha),
        ("branch", binding.branch),
    ):
        if payload[key] != value:
            raise ReplacementExecutorError(f"replacement result {key} does not match signed authorization")
    entries = payload["files"]
    if not isinstance(entries, list) or not entries or len(entries) > _MAX_FILES:
        raise ReplacementExecutorError("replacement result must contain a bounded non-empty file list")
    files = tuple(_decode_file(x) for x in entries)
    paths = [x.path for x in files]
    if len(paths) != len(set(paths)):
        raise ReplacementExecutorError("replacement result contains duplicate paths")
    for path in paths:
        if not _path_allowed(path, binding.scope):
            raise ReplacementExecutorError(f"candidate path is outside signed TaskScope: {path}")
    return ValidatedReplacementResult(
        binding.authorization_id,
        binding.repository,
        binding.branch,
        binding.base_sha,
        files,
        hashlib.sha256(raw).hexdigest(),
        rehearsal,
    )


#: Named model/provider authorities, including the GitHub-hosted executor's own
#: model credential (Issue #557).
MODEL_AUTHORITY_ENV: tuple[str, ...] = (
    "HUNTER_AGENT_CODEX_COMMAND",
    "HUNTER_AGENT_CLAUDE_COMMAND",
    "HUNTER_AGENT_FREEBUFF_COMMAND",
    "HUNTER_AGENT_OPENCODE_COMMAND",
    "HUNTER_AGENT_JULES_COMMAND",
    "HUNTER_ISSUE_AGENT_EXECUTOR_MODEL_API_KEY",
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
    "GEMINI_API_KEY",
    "GROQ_API_KEY",
)


def model_authority_present(environ: Mapping[str, str]) -> list[str]:
    """Every configured model/provider authority: the named set plus any ``*_API_KEY``
    or ``HUNTER_AGENT_*_COMMAND``, so a newly added provider credential is caught too."""
    return sorted(
        name
        for name, value in environ.items()
        if value.strip()
        and (
            name in MODEL_AUTHORITY_ENV
            or name.endswith("_API_KEY")
            or (name.startswith("HUNTER_AGENT_") and name.endswith("_COMMAND"))
        )
    )


def publisher_environment_is_safe(environ: Mapping[str, str]) -> bool:
    return not model_authority_present(environ)


#: Every credential that can publish a candidate. None of them may exist in a
#: boundary that runs a model or executes candidate-controlled content.
PUBLICATION_CREDENTIAL_ENV: tuple[str, ...] = (
    "HUNTER_AGENT_GITHUB_PUSH_TOKEN",
    "HUNTER_ISSUE_AGENT_PR_TOKEN",
    "HUNTER_ISSUE_AGENT_PUBLISHER_PUSH_TOKEN",
    "HUNTER_ISSUE_AGENT_PUBLISHER_SIGNING_KEY",
    "GITHUB_TOKEN",
    "GH_TOKEN",
    "SSH_AUTH_SOCK",
)

#: The GitHub Actions OIDC request capability. Candidate-controlled content
#: never receives it, so it cannot mint an execution identity token.
OIDC_REQUEST_ENV: tuple[str, ...] = ("ACTIONS_ID_TOKEN_REQUEST_TOKEN", "ACTIONS_ID_TOKEN_REQUEST_URL")


def publication_credentials_present(environ: Mapping[str, str]) -> list[str]:
    return [x for x in PUBLICATION_CREDENTIAL_ENV if environ.get(x, "").strip()]


def assert_rehearsal_has_no_publication_authority(environ: Mapping[str, str]) -> None:
    leaked = publication_credentials_present(environ)
    if leaked:
        raise ReplacementExecutorError("rehearsal environment contains publication authority: " + ", ".join(leaked))


@dataclass(frozen=True, slots=True)
class ReplacementValidationReceipt:
    authorization_id: str
    base_sha: str
    branch: str
    result_sha256: str
    validation_definition: str
    schema_version: str = "hunter-issue-agent-replacement-validation-receipt-v1"

    def to_json(self) -> str:
        return json.dumps(
            {
                "authorization_id": self.authorization_id,
                "base_sha": self.base_sha,
                "branch": self.branch,
                "result_sha256": self.result_sha256,
                "schema_version": self.schema_version,
                "validation_definition": self.validation_definition,
            },
            sort_keys=True,
            separators=(",", ":"),
        )


def result_sha256(document: str | bytes) -> str:
    raw = document.encode() if isinstance(document, str) else bytes(document)
    return hashlib.sha256(raw).hexdigest()


def validation_receipt(
    document: str | bytes, *, signed_authorization: SignedIssueAgentAuthorization, validation_definition: str
) -> ReplacementValidationReceipt:
    if not validation_definition.strip():
        raise ReplacementExecutorError("validation definition identity is required")
    validated = validate_replacement_result(document, signed_authorization=signed_authorization, rehearsal=False)
    return ReplacementValidationReceipt(
        validated.authorization_id, validated.base_sha, validated.branch, result_sha256(document), validation_definition
    )


def verify_validation_receipt(
    receipt_document: str | bytes,
    *,
    result_document: str | bytes,
    signed_authorization: SignedIssueAgentAuthorization,
    expected_validation_definition: str,
) -> ReplacementValidationReceipt:
    try:
        payload = json.loads(receipt_document)
    except (TypeError, ValueError):
        raise ReplacementExecutorError("validation receipt must be JSON") from None
    expected = {"authorization_id", "base_sha", "branch", "result_sha256", "schema_version", "validation_definition"}
    if not isinstance(payload, Mapping) or set(payload) != expected:
        raise ReplacementExecutorError("validation receipt schema mismatch")
    if payload["schema_version"] != "hunter-issue-agent-replacement-validation-receipt-v1":
        raise ReplacementExecutorError("validation receipt version mismatch")
    validated = validate_replacement_result(result_document, signed_authorization=signed_authorization, rehearsal=False)
    checks = {
        "authorization_id": validated.authorization_id,
        "base_sha": validated.base_sha,
        "branch": validated.branch,
        "result_sha256": result_sha256(result_document),
        "validation_definition": expected_validation_definition,
    }
    for key, value in checks.items():
        if payload[key] != value:
            raise ReplacementExecutorError(f"validation receipt {key} mismatch")
    return ReplacementValidationReceipt(
        payload["authorization_id"],
        payload["base_sha"],
        payload["branch"],
        payload["result_sha256"],
        payload["validation_definition"],
    )


@dataclass(frozen=True, slots=True)
class ReplacementPublication:
    branch: str
    base_sha: str
    head_sha: str


def _git_plumbing(
    repo: Path, *args: str, env: Mapping[str, str] | None = None, input_bytes: bytes | None = None
) -> str:
    completed = subprocess.run(
        ("git", *args),
        cwd=repo,
        env=None if env is None else dict(env),
        input=input_bytes,
        capture_output=True,
        check=False,
        timeout=120,
    )
    if completed.returncode != 0:
        detail = completed.stderr.decode(errors="replace").strip() or "git plumbing failed"
        raise ReplacementExecutorError(detail)
    return completed.stdout.decode().strip()


_CANDIDATE_COMMIT_MESSAGE = "chore: apply governed Issue Agent replacement result"


@dataclass(frozen=True, slots=True)
class CommitIdentity:
    """ADR 0037 D6: every non-signature commit field is fixed, so the commit is deterministic.

    The validator's unsigned commit and the publisher's signed commit share every field except the
    signature header, and a retry reproduces the identical signed head (SSH Ed25519 is deterministic).
    """

    name: str
    email: str
    timestamp: str
    message: str

    def environment(self) -> dict[str, str]:
        return {
            "GIT_AUTHOR_NAME": self.name,
            "GIT_AUTHOR_EMAIL": self.email,
            "GIT_AUTHOR_DATE": self.timestamp,
            "GIT_COMMITTER_NAME": self.name,
            "GIT_COMMITTER_EMAIL": self.email,
            "GIT_COMMITTER_DATE": self.timestamp,
        }


def candidate_commit_message(*, authorization_id: str, execution_id: str, result_sha256: str) -> str:
    """The fixed template: identifiers only, never model prose (ADR 0037 D6)."""

    return (
        f"issue-agent: governed candidate for {authorization_id.rsplit(':', 1)[-1][:16]}\n\n"
        f"Hunter-Authorization: {authorization_id}\n"
        f"Hunter-Execution: {execution_id}\n"
        f"Hunter-Result: {result_sha256}\n"
    )


def _config_environment(entries: Sequence[tuple[str, str]]) -> dict[str, str]:
    """Git configuration passed through the child environment, never through the argument vector."""

    environment = {"GIT_CONFIG_COUNT": str(len(entries))}
    for index, (key, value) in enumerate(entries):
        environment[f"GIT_CONFIG_KEY_{index}"] = key
        environment[f"GIT_CONFIG_VALUE_{index}"] = value
    return environment


def _candidate_commit(
    root: Path,
    *,
    validated: ValidatedReplacementResult,
    sign: bool,
    signing_key: str,
    identity: CommitIdentity | None = None,
) -> str:
    """Construct the candidate commit from hostile file data by Git plumbing only."""
    if not root.is_dir():
        raise ReplacementExecutorError("publisher repository does not exist")
    if _git_plumbing(root, "cat-file", "-t", validated.base_sha) != "commit":
        raise ReplacementExecutorError("signed authorization base is not a local commit")
    with tempfile.TemporaryDirectory(prefix="hunter-replacement-index-") as directory:
        env = dict(os.environ)
        env["GIT_INDEX_FILE"] = str(Path(directory) / "index")
        env["GIT_CONFIG_GLOBAL"] = os.devnull
        env["GIT_CONFIG_NOSYSTEM"] = "1"
        _git_plumbing(root, "read-tree", validated.base_sha, env=env)
        for item in validated.files:
            blob = _git_plumbing(root, "hash-object", "-w", "--stdin", env=env, input_bytes=item.content)
            _git_plumbing(root, "update-index", "--add", "--cacheinfo", f"{item.mode},{blob},{item.path}", env=env)
        tree = _git_plumbing(root, "write-tree", env=env)
        message = _CANDIDATE_COMMIT_MESSAGE if identity is None else identity.message
        args = ["commit-tree", tree, "-p", validated.base_sha, "-m", message]
        if identity is not None:
            env.update(identity.environment())
        if sign:
            args.insert(1, f"-S{signing_key}" if signing_key else "-S")
            if identity is not None:
                env.update(_config_environment([("gpg.format", "ssh")]))
        head = _git_plumbing(root, *args, env=env)
    if re.fullmatch(r"[0-9a-f]{40}", head) is None:
        raise ReplacementExecutorError("publisher produced an invalid commit id")
    return head


def candidate_tree(repo: str | Path, head: str) -> str:
    tree = _git_plumbing(Path(repo).resolve(), "rev-parse", f"{head}^{{tree}}")
    if re.fullmatch(r"[0-9a-f]{40}", tree) is None:
        raise ReplacementExecutorError("candidate tree id is invalid")
    return tree


def build_signed_candidate_commit(
    repo: str | Path,
    *,
    validated: ValidatedReplacementResult,
    signing_key: str = "",
) -> str:
    """Build a signed candidate commit from hostile file data without checkout or hooks."""
    root = Path(repo).resolve()
    head = _candidate_commit(root, validated=validated, sign=True, signing_key=signing_key)
    _git_plumbing(root, "verify-commit", head)
    return head


def build_unsigned_candidate_commit(
    repo: str | Path, *, validated: ValidatedReplacementResult, identity: CommitIdentity
) -> str:
    """The exact unsigned commit the validator proves and the publisher must reproduce."""

    return _candidate_commit(Path(repo).resolve(), validated=validated, sign=False, signing_key="", identity=identity)


@dataclass(frozen=True, slots=True)
class SafetyProof:
    unsigned_commit_sha: str
    tree_sha: str


def credential_free_candidate_safety(
    repo: str | Path,
    *,
    validated: ValidatedReplacementResult,
    isolation_user: str,
    identity: CommitIdentity,
) -> SafetyProof:
    """``run_credential_free_candidate_safety`` that also returns the exact proven unsigned commit."""

    tree = run_credential_free_candidate_safety(
        repo, validated=validated, isolation_user=isolation_user, identity=identity
    )
    head = _candidate_commit(Path(repo).resolve(), validated=validated, sign=False, signing_key="", identity=identity)
    return SafetyProof(head, tree)


def run_credential_free_candidate_safety(
    repo: str | Path,
    *,
    validated: ValidatedReplacementResult,
    isolation_user: str,
    identity: CommitIdentity | None = None,
) -> str:
    """Run the trusted pre-push safety boundary on exact candidate content; return its tree.

    Issue #557. The pre-push hook executes repository scripts from the candidate
    worktree, so it is candidate-controlled code. It therefore runs only here,
    in a boundary that holds no publication credential at all, against an
    unsigned commit carrying the identical tree, and as the dedicated isolation
    user with an explicit allowlist environment. The publisher later binds its
    own signed commit to this exact tree and never runs the hook itself.
    """
    if validated.rehearsal:
        raise ReplacementExecutorError("rehearsal result cannot reach candidate safety validation")
    require_isolation_user(isolation_user)
    leaked = publication_credentials_present(os.environ)
    if leaked:
        raise ReplacementExecutorError("candidate safety boundary contains publication authority: " + ", ".join(leaked))
    if not publisher_environment_is_safe(os.environ):
        raise ReplacementExecutorError("candidate safety boundary contains model authority")
    if re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", validated.repository) is None:
        raise ReplacementExecutorError("signed repository identity is invalid")
    root = Path(repo).resolve()
    head = _candidate_commit(root, validated=validated, sign=False, signing_key="", identity=identity)
    _run_pre_push_safety(
        root,
        validated=validated,
        head=head,
        push_url=f"https://github.com/{validated.repository}.git",
        isolation_user=isolation_user,
    )
    return candidate_tree(root, head)


#: Parent for untrusted-code directories. ``None`` uses the platform temporary
#: directory; each root is created by ``mkdtemp`` (unpredictable name, owner-only)
#: and only then opened to traversal (0711), never listing or writing by others.
ISOLATION_ROOT: Path | None = None

#: The only parent variables candidate-controlled code ever receives. This is an
#: explicit allowlist, never a scrub of known secrets: anything not named here,
#: including issuer, OIDC, model, signing, push and PR credentials, is absent.
UNTRUSTED_ENV_ALLOWLIST: tuple[str, ...] = ("PATH", "LANG", "LC_ALL", "TZ")

#: The whole environment of the ``sudo`` launcher process itself.
SUDO_ENVIRONMENT: Mapping[str, str] = {"PATH": "/usr/sbin:/usr/bin:/sbin:/bin"}

_USER_RE = re.compile(r"[a-z_][a-z0-9_-]{0,31}")
_ENV_KEY_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def require_isolation_user(user: object) -> str:
    """The dedicated unprivileged OS user that untrusted code runs as (Issue #557).

    A same-user child can read every same-user ancestor's ``/proc/<pid>/environ``,
    so hiding variables from the child is not a credential boundary. Untrusted
    model execution and candidate-controlled validation therefore run as a
    different, non-root uid that cannot read the trusted process tree at all.
    """
    if not isinstance(user, str) or _USER_RE.fullmatch(user) is None:
        raise ReplacementExecutorError("untrusted-code isolation user is not a valid user name")
    try:
        entry = pwd.getpwnam(user)
    except KeyError:
        raise ReplacementExecutorError("untrusted-code isolation user does not exist") from None
    if entry.pw_uid in (0, os.getuid()):
        raise ReplacementExecutorError("untrusted-code isolation user must be a different non-root uid")
    return user


def isolated_command(user: str, environment: Mapping[str, str], argv: Sequence[str]) -> tuple[str, ...]:
    """``argv`` as ``user`` with exactly ``environment`` and nothing inherited."""
    for key in environment:
        if _ENV_KEY_RE.fullmatch(key) is None:
            raise ReplacementExecutorError("untrusted environment carries an invalid variable name")
    assignments = tuple(f"{key}={environment[key]}" for key in sorted(environment))
    return ("sudo", "-n", "-u", user, "--", "/usr/bin/env", "-i", *assignments, *argv)


def isolated_secret_launch(
    user: str,
    environment: Mapping[str, str],
    secret_environment: Mapping[str, str],
    argv: Sequence[str],
) -> tuple[tuple[str, ...], dict[str, str]]:
    """``argv`` as ``user`` with secrets handed over by environment, never by argument.

    Returns ``(command, launcher_environment)``. Non-secret settings are passed as
    ``env`` assignments; every secret value travels only in the ``sudo`` launcher's
    own environment and crosses the uid boundary through ``--preserve-env=<names>``,
    so no secret value appears in any process argument vector or the command line
    sudo logs. ``sudo``'s ``env_reset`` drops everything else from the launcher
    environment, so the child receives only sudo's reset variables, the explicit
    assignments, and the named secrets.
    """
    if not secret_environment:
        raise ReplacementExecutorError("a secret launch requires at least one secret")
    for key in (*environment, *secret_environment):
        if _ENV_KEY_RE.fullmatch(key) is None:
            raise ReplacementExecutorError("untrusted environment carries an invalid variable name")
    if set(environment) & set(secret_environment) or "PATH" in secret_environment:
        raise ReplacementExecutorError("a secret may not shadow a public launcher variable")
    secrets = [value for value in secret_environment.values() if value]
    if len(secrets) != len(secret_environment):
        raise ReplacementExecutorError("a secret launch requires non-empty secret values")
    assignments = tuple(f"{key}={environment[key]}" for key in sorted(environment))
    command = (
        "sudo", "-n", f"--preserve-env={','.join(sorted(secret_environment))}", "-u", user, "--",
        "/usr/bin/env", *assignments, *argv,
    )  # fmt: skip
    if any(secret in part for secret in secrets for part in command):
        raise ReplacementExecutorError("a secret value would appear in the launcher argument vector")
    return command, {**SUDO_ENVIRONMENT, **secret_environment}


def run_privileged(*args: str, allowed_returncodes: tuple[int, ...] = (0,)) -> None:
    """One non-interactive ``sudo`` operation with a secret-free launcher environment."""
    completed = subprocess.run(
        ("sudo", "-n", *args), env=dict(SUDO_ENVIRONMENT), capture_output=True, check=False, timeout=300
    )
    if completed.returncode not in allowed_returncodes:
        raise ReplacementExecutorError(f"isolation operation failed: {args[0]}")


def stop_isolated_processes(user: str) -> None:
    """Kill everything the untrusted user left running (``pkill`` exits 1 when none)."""
    run_privileged("pkill", "-KILL", "-u", user, allowed_returncodes=(0, 1))


def new_isolation_root(prefix: str) -> Path:
    """A private-but-traversable directory under which untrusted directories live."""
    root = Path(tempfile.mkdtemp(prefix=prefix, dir=ISOLATION_ROOT))
    root.chmod(0o711)
    return root


def remove_isolation_root(root: Path, user: str) -> None:
    try:
        stop_isolated_processes(user)
    finally:
        run_privileged("rm", "-rf", "--", str(root), allowed_returncodes=(0, 1))
        shutil.rmtree(root, ignore_errors=True)


def untrusted_environment(environ: Mapping[str, str], *, home: Path, pythonpath: Path) -> dict[str, str]:
    """The explicit minimum environment for candidate-controlled validation."""
    environment = {key: environ[key] for key in UNTRUSTED_ENV_ALLOWLIST if environ.get(key, "").strip()}
    environment.update(
        HOME=str(home),
        PYTHONPATH=str(pythonpath),
        GIT_CONFIG_GLOBAL=os.devnull,
        GIT_CONFIG_NOSYSTEM="1",
        GIT_TERMINAL_PROMPT="0",
    )
    return environment


ISSUE_AGENT_BASE_BRANCH = "main"


def _trusted_base_ref(repo: Path) -> str:
    """The trusted repository's ref for the governed base branch, remote-tracking first."""
    for candidate in (f"refs/remotes/origin/{ISSUE_AGENT_BASE_BRANCH}", f"refs/heads/{ISSUE_AGENT_BASE_BRANCH}"):
        try:
            _git_plumbing(repo, "rev-parse", "--verify", "--quiet", candidate)
        except ReplacementExecutorError:
            continue
        return candidate
    raise ReplacementExecutorError("trusted checkout has no governed base branch")


def _run_pre_push_safety(
    repo: Path, *, validated: ValidatedReplacementResult, head: str, push_url: str, isolation_user: str
) -> None:
    """Run the trusted pre-push hook on exact candidate content as the untrusted user.

    The hook executes candidate-controlled repository scripts. It runs in a
    standalone clone owned by the isolation user, with an explicit allowlist
    environment, so no credential of the validating job -- in its own
    environment or readable from any trusted process -- is available to it.
    """
    user = require_isolation_user(isolation_user)
    hook = _git_plumbing(repo, "show", f"{validated.base_sha}:.githooks/pre-push").encode()
    ref = f"refs/hunter/candidate-safety/{head}"
    root = new_isolation_root("hunter-candidate-safety-")
    candidate, home, hook_path = root / "candidate", root / "home", root / "pre-push"
    try:
        _git_plumbing(repo, "update-ref", ref, head)
        _git_plumbing(
            repo, "-c", "core.hooksPath=/dev/null", "clone", "--quiet", "--no-hardlinks", "--no-checkout",
            str(repo), str(candidate),
        )  # fmt: skip
        _git_plumbing(candidate, "-c", "core.hooksPath=/dev/null", "fetch", "--quiet", "origin", ref)
        # The trusted checkout is pinned to an exact SHA (detached), so the clone
        # gets the governed base branch from its fetched remote-tracking ref.
        _git_plumbing(
            candidate, "-c", "core.hooksPath=/dev/null", "fetch", "--quiet", "origin",
            f"+{_trusted_base_ref(repo)}:refs/remotes/origin/{ISSUE_AGENT_BASE_BRANCH}",
        )  # fmt: skip
        _git_plumbing(candidate, "-c", "core.hooksPath=/dev/null", "checkout", "--quiet", "--detach", head)
        home.mkdir(mode=0o700)
        hook_path.write_bytes(hook)
        hook_path.chmod(0o755)
        run_privileged("chown", "-R", user, str(candidate), str(home))
        line = f"refs/heads/{validated.branch} {head} refs/heads/{validated.branch} {'0' * 40}\n"
        completed = subprocess.run(
            isolated_command(
                user,
                untrusted_environment(os.environ, home=home, pythonpath=candidate / "src"),
                (str(hook_path), "origin", push_url),
            ),
            cwd=candidate,
            env=dict(SUDO_ENVIRONMENT),
            input=line.encode(),
            capture_output=True,
            check=False,
            timeout=900,
        )
        if completed.returncode != 0:
            detail = (
                completed.stderr.decode(errors="replace").strip() or completed.stdout.decode(errors="replace").strip()
            )
            raise ReplacementExecutorError("candidate pre-push safety failed: " + (detail or "unknown failure"))
    finally:
        try:
            remove_isolation_root(root, user)
        finally:
            _git_plumbing(repo, "update-ref", "-d", ref)


def publish_create_only(
    repo: str | Path,
    *,
    validated: ValidatedReplacementResult,
    verified_receipt: ReplacementValidationReceipt,
    safety_tree: str,
    signing_key: str = "",
) -> ReplacementPublication:
    """Publish validated data under a create-only lease; never execute candidate content.

    ``safety_tree`` is the tree that ``run_credential_free_candidate_safety``
    proved in its credential-free boundary. The publisher never runs the
    candidate's pre-push scripts itself: it only refuses a signed commit whose
    tree is not exactly that proven tree.
    """
    if re.fullmatch(r"[0-9a-f]{40}", safety_tree or "") is None:
        raise ReplacementExecutorError("publication requires the exact credential-free safety tree")
    if validated.rehearsal:
        raise ReplacementExecutorError("rehearsal result cannot be published")
    if not publisher_environment_is_safe(os.environ):
        raise ReplacementExecutorError("publisher environment contains model authority")
    if (
        verified_receipt.authorization_id != validated.authorization_id
        or verified_receipt.base_sha != validated.base_sha
        or verified_receipt.result_sha256 != validated.result_sha256
    ):
        raise ReplacementExecutorError("publication receipt does not bind the validated result")
    if verified_receipt.branch != validated.branch:
        raise ReplacementExecutorError("publication receipt branch mismatch")
    root = Path(repo).resolve()
    if re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", validated.repository) is None:
        raise ReplacementExecutorError("signed repository identity is invalid")
    push_url = f"https://github.com/{validated.repository}.git"
    remote = _git_plumbing(root, "ls-remote", push_url, f"refs/heads/{validated.branch}")
    if remote:
        raise ReplacementExecutorError("authorization branch already exists; create-only publication refused")
    head = build_signed_candidate_commit(root, validated=validated, signing_key=signing_key)
    if candidate_tree(root, head) != safety_tree:
        raise ReplacementExecutorError("signed candidate tree differs from the credential-free safety tree")
    _git_plumbing(
        root,
        "push",
        "--no-verify",
        f"--force-with-lease=refs/heads/{validated.branch}:",
        push_url,
        f"{head}:refs/heads/{validated.branch}",
    )
    return ReplacementPublication(validated.branch, validated.base_sha, head)


class PublicationConflictError(ReplacementExecutorError):
    """The authorization branch exists at a foreign head; nothing is overwritten."""

    failure_code = "REMOTE_BRANCH_CONFLICT"


class PublicationRejectedError(ReplacementExecutorError):
    """The platform refused the create-only push (for example a workflow file without that scope)."""

    failure_code = "PUBLICATION_REJECTED_BY_PLATFORM"


def _remote_head(root: Path, push_url: str, ref: str, environment: Mapping[str, str]) -> str | None:
    output = _git_plumbing(root, "ls-remote", push_url, ref, env=environment)
    return output.split()[0] if output else None


def publish_bound_create_only(
    repo: str | Path,
    *,
    validated: ValidatedReplacementResult,
    identity: CommitIdentity,
    expected_unsigned_commit_sha: str,
    expected_tree_sha: str,
    signing_key: str,
    push_url: str,
    push_config: Sequence[tuple[str, str]] = (),
) -> ReplacementPublication:
    """ADR 0037 T4: create-only publication of exactly the validated, deterministic commit.

    The publisher never runs a hook or candidate content (DFF-028). It reproduces the validator's
    unsigned commit byte for byte, signs the identical fields, and pushes with an empty lease. A branch
    already at the identical signed head is a lost-acknowledgement success. Any other head is
    ``REMOTE_BRANCH_CONFLICT`` and is never overwritten.
    """

    if validated.rehearsal:
        raise ReplacementExecutorError("rehearsal result cannot be published")
    if not publisher_environment_is_safe(os.environ):
        raise ReplacementExecutorError("publisher environment contains model authority")
    root = Path(repo).resolve()
    unsigned = build_unsigned_candidate_commit(root, validated=validated, identity=identity)
    if unsigned != expected_unsigned_commit_sha:
        raise ReplacementExecutorError("publisher did not reproduce the validated unsigned commit")
    head = _candidate_commit(root, validated=validated, sign=True, signing_key=signing_key, identity=identity)
    if candidate_tree(root, head) != expected_tree_sha:
        raise ReplacementExecutorError("signed candidate tree differs from the validated tree")
    ref = f"refs/heads/{validated.branch}"
    environment = {
        **os.environ,
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_TERMINAL_PROMPT": "0",
        **_config_environment([("core.hooksPath", os.devnull), *push_config]),
    }
    # The empty lease is the create-only guard (S0 A-1); the read-back resolves lost acknowledgements.
    try:
        _git_plumbing(root, "push", f"--force-with-lease={ref}:", push_url, f"{head}:{ref}", env=environment)
    except ReplacementExecutorError as error:
        observed = _remote_head(root, push_url, ref, environment)
        if observed == head:
            return ReplacementPublication(validated.branch, validated.base_sha, head)  # lost acknowledgement
        if observed is not None:
            raise PublicationConflictError("authorization branch exists at a foreign head") from None
        if "refusing to allow" in str(error) or "workflow" in str(error):
            raise PublicationRejectedError("the platform refused the create-only push") from None
        raise
    return ReplacementPublication(validated.branch, validated.base_sha, head)


class ReplacementResultLedger:
    """Replay-safe terminal binding between one authorization and one exact result."""

    def __init__(self, database: str | Path) -> None:
        self._database = Path(database)
        with sqlite3.connect(self._database) as db:
            db.execute("""CREATE TABLE IF NOT EXISTS issue_agent_replacement_results (
                authorization_id TEXT PRIMARY KEY, result_sha256 TEXT NOT NULL,
                validation_definition TEXT NOT NULL, published_head TEXT)""")

    def record_validated(self, receipt: ReplacementValidationReceipt) -> None:
        with sqlite3.connect(self._database) as db:
            row = db.execute(
                "SELECT result_sha256, validation_definition FROM issue_agent_replacement_results WHERE authorization_id=?",
                (receipt.authorization_id,),
            ).fetchone()
            identity = (receipt.result_sha256, receipt.validation_definition)
            if row is not None and tuple(row) != identity:
                raise ReplacementExecutorError("authorization already bound to a different replacement result")
            db.execute(
                "INSERT OR IGNORE INTO issue_agent_replacement_results "
                "(authorization_id,result_sha256,validation_definition,published_head) VALUES (?,?,?,NULL)",
                (receipt.authorization_id, *identity),
            )

    def record_published(self, receipt: ReplacementValidationReceipt, head: str) -> None:
        if re.fullmatch(r"[0-9a-f]{40}", head) is None:
            raise ReplacementExecutorError("published head must be an exact commit SHA")
        with sqlite3.connect(self._database) as db:
            row = db.execute(
                "SELECT result_sha256, validation_definition, published_head "
                "FROM issue_agent_replacement_results WHERE authorization_id=?",
                (receipt.authorization_id,),
            ).fetchone()
            if row is None or tuple(row[:2]) != (receipt.result_sha256, receipt.validation_definition):
                raise ReplacementExecutorError("replacement result was not validated for this authorization")
            if row[2] not in (None, head):
                raise ReplacementExecutorError("authorization already published a different head")
            db.execute(
                "UPDATE issue_agent_replacement_results SET published_head=? WHERE authorization_id=?",
                (head, receipt.authorization_id),
            )
