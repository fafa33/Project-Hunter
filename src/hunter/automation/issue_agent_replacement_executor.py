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
from typing import Any, Final

from hunter.automation.issue_agent_execution import (
    PROMOTION_PATHS,
    SignedIssueAgentAuthorization,
    derive_execution_target,
)
from hunter.task_scope import TaskScopeContract, path_matches_scope_entry

RESULT_SCHEMA_VERSION = "hunter-issue-agent-replacement-result-v1"
REHEARSAL_SCHEMA_VERSION = "hunter-issue-agent-replacement-rehearsal-v1"
_SHA = re.compile(r"[0-9a-f]{64}")
_TEST_ID = re.compile(r"tests/[A-Za-z0-9_./-]{1,200}\.py::[A-Za-z0-9_\[\]-]{1,200}")
_FAMILY = re.compile(r"DFF-[0-9]{3}")
_TITLE = re.compile(r"[a-z0-9][a-z0-9-]{2,99}")
_TEXT = re.compile(r"[\x20-\x7e]{12,1000}")
_MAX_RESULT_BYTES = 8 * 1024 * 1024
_MAX_FILE_BYTES = 2 * 1024 * 1024
_MAX_FILES = 256
MAX_REGRESSION_TESTS = 32
#: The interpreter that runs candidate regression tests in the isolation boundary. Pinned here so the proof
#: names exactly one toolchain, and overridable only so a test can execute the same code path.
REGRESSION_PYTHON: Final = "python3"


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
    #: ADR 0039 L3.2: the result's optional, hostile defect-family proposal. Proven, never trusted.
    remediation: dict[str, Any] | None = None


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
    #: ADR 0039 L3.2/L6: the sorted finding ids this authorization remediates. Empty (the default) means the
    #: authorization is not a remediation and the result must carry no ``remediation`` proposal at all.
    remediation_finding_ids: tuple[str, ...] = ()


def _decode_remediation(entry: object, paths: Sequence[str]) -> dict[str, Any]:
    """The closed, bounded, printable-ASCII defect-family proposal of ADR 0039 L3.2.

    Hostile data: the shape is bounded here, and nothing it says is believed until the RED->GREEN proof and the
    trusted promotion derivation (L6) have both succeeded. A new-family invariant is prose, never a byte of the
    confidential prompt, the source corpus or the evidence database.
    """

    if not isinstance(entry, Mapping) or set(entry) != {"finding_id", "disposition", "regression_tests"}:
        raise ReplacementExecutorError("remediation proposal schema mismatch")
    finding = entry["finding_id"]
    if not isinstance(finding, str) or _SHA.fullmatch(finding) is None:
        raise ReplacementExecutorError("remediation proposal names no finding")
    disposition = entry["disposition"]
    if not isinstance(disposition, Mapping):
        raise ReplacementExecutorError("remediation disposition must be an object")
    if set(disposition) == {"family_id"}:
        family = disposition["family_id"]
        if not isinstance(family, str) or _FAMILY.fullmatch(family) is None:
            raise ReplacementExecutorError("remediation disposition names no canonical family")
    elif set(disposition) == {"new_family"}:
        proposal = disposition["new_family"]
        if (
            not isinstance(proposal, Mapping)
            or set(proposal) != {"title", "invariant"}
            or not isinstance(proposal["title"], str)
            or _TITLE.fullmatch(proposal["title"]) is None
            or not isinstance(proposal["invariant"], str)
            or _TEXT.fullmatch(proposal["invariant"]) is None
        ):
            raise ReplacementExecutorError("a new-family proposal must carry a bounded title and invariant")
    else:
        raise ReplacementExecutorError("remediation disposition must be exactly one family_id or new_family")
    tests = entry["regression_tests"]
    if not isinstance(tests, list) or not 1 <= len(tests) <= MAX_REGRESSION_TESTS:
        raise ReplacementExecutorError("a remediation proposal names one to thirty-two regression tests")
    if any(not isinstance(item, str) or _TEST_ID.fullmatch(item) is None for item in tests):
        raise ReplacementExecutorError("a named regression test is not a repository test id")
    # RED runs the reviewed head plus the result's *test files only*, so every named test must ship in the result.
    missing = sorted({item.split("::", 1)[0] for item in tests} - set(paths))
    if missing:
        raise ReplacementExecutorError(f"a named regression test is not in the result: {missing[0]}")
    if tests != sorted(set(tests)):
        raise ReplacementExecutorError("regression tests must be unique and sorted")
    return {"finding_id": finding, "disposition": dict(disposition), "regression_tests": list(tests)}


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
    remediation = bool(binding.remediation_finding_ids)
    # ADR 0039 L3.2: the defect-family proposal is *optional* and exists only on a remediation. Anything else
    # in the document -- present or absent -- is refused, so a non-remediation can never smuggle one in.
    required = {"schema_version", "authorization_id", "base_sha", "branch", "files"}
    permitted = required | ({"remediation"} if remediation else set())
    if not isinstance(payload, Mapping) or not required <= set(payload) <= permitted:
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
    proposal: dict[str, Any] | None = None
    if remediation:
        # ADR 0039 L6 (RD-3): the model can never write the canonical registry or finding-disposition files;
        # only trusted plumbing adds them to the validated tree. Refuse before any safety boundary runs.
        written = sorted(set(paths) & set(PROMOTION_PATHS))
        if written:
            raise ReplacementExecutorError(f"the model may not write the canonical promotion file {written[0]}")
        if "remediation" in payload:
            proposal = _decode_remediation(payload["remediation"], paths)
            if proposal["finding_id"] not in binding.remediation_finding_ids:
                raise ReplacementExecutorError("the remediation proposal names a finding outside this authorization")
    return ValidatedReplacementResult(
        binding.authorization_id,
        binding.repository,
        binding.branch,
        binding.base_sha,
        files,
        hashlib.sha256(raw).hexdigest(),
        rehearsal,
        proposal,
    )


def with_promotion(validated: ValidatedReplacementResult, delta: Mapping[str, bytes]) -> ValidatedReplacementResult:
    """The L6 validated tree: the model result plus the deterministic promotion delta, by trusted plumbing.

    Pure and total: the validator derives the delta and the publisher re-derives the identical bytes, so both
    build byte-identical commits from the same result and the same promotion input. The delta may only name
    ``PROMOTION_PATHS``, and it replaces (never merges with) any result file, which the contract already
    refuses for a remediation.
    """

    if validated.rehearsal:
        raise ReplacementExecutorError("rehearsal result cannot carry a promotion delta")
    unauthorized = sorted(set(delta) - set(PROMOTION_PATHS))
    if unauthorized:
        raise ReplacementExecutorError(f"a promotion delta may only name {PROMOTION_PATHS}: {unauthorized[0]}")
    if not delta:
        return validated
    added = tuple(
        CandidateFile(
            path,
            content,
            hashlib.sha256(content).hexdigest(),
            _existing_mode(validated, path),
        )
        for path, content in sorted(delta.items())
    )
    kept = tuple(item for item in validated.files if item.path not in delta)
    return ValidatedReplacementResult(
        validated.authorization_id,
        validated.repository,
        validated.branch,
        validated.base_sha,
        kept + added,
        validated.result_sha256,
        validated.rehearsal,
        validated.remediation,
    )


def _existing_mode(validated: ValidatedReplacementResult, path: str) -> str:
    return next((item.mode for item in validated.files if item.path == path), "100644")


def promotion_digest(delta: Mapping[str, bytes]) -> str:
    """The digest the ledger records so the publisher can prove it re-derived the identical delta."""

    return hashlib.sha256(
        canonical_bytes({path: hashlib.sha256(content).hexdigest() for path, content in sorted(delta.items())})
    ).hexdigest()


def canonical_bytes(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


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


def git_blob(repo: str | Path, revision_path: str) -> bytes:
    """The raw bytes of ``<commit>:<path>`` by trusted Git plumbing; no hooks, no work tree, no stripping."""

    completed = subprocess.run(
        ("git", "show", revision_path),
        cwd=Path(repo).resolve(),
        env={**os.environ, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"},
        capture_output=True,
        check=False,
        timeout=120,
    )
    if completed.returncode != 0:
        raise ReplacementExecutorError(f"trusted plumbing could not read {revision_path}")
    return completed.stdout


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


def _bind_publication(
    root: Path,
    *,
    validated: ValidatedReplacementResult,
    identity: CommitIdentity,
    expected_unsigned_commit_sha: str,
    expected_tree_sha: str,
    signing_key: str,
) -> str:
    """Reproduce the validator's exact unsigned commit, then sign the identical tree. Runs no candidate content."""

    unsigned = build_unsigned_candidate_commit(root, validated=validated, identity=identity)
    if unsigned != expected_unsigned_commit_sha:
        raise ReplacementExecutorError("publisher did not reproduce the validated unsigned commit")
    head = _candidate_commit(root, validated=validated, sign=True, signing_key=signing_key, identity=identity)
    if candidate_tree(root, head) != expected_tree_sha:
        raise ReplacementExecutorError("signed candidate tree differs from the validated tree")
    return head


def _push_environment(push_config: Sequence[tuple[str, str]]) -> dict[str, Any]:
    return {
        **os.environ,
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_TERMINAL_PROMPT": "0",
        **_config_environment([("core.hooksPath", os.devnull), *push_config]),
    }


def publish_bound_fast_forward(
    repo: str | Path,
    *,
    validated: ValidatedReplacementResult,
    identity: CommitIdentity,
    expected_unsigned_commit_sha: str,
    expected_tree_sha: str,
    lease_sha: str,
    signing_key: str,
    push_url: str,
    push_config: Sequence[tuple[str, str]] = (),
) -> ReplacementPublication:
    """ADR 0039 L7 (RD-5): fast-forward the branch from the exact bound head, under that head's exact lease.

    The new commit's single parent is ``validated.base_sha`` (the remediated PR head, which the control job
    observed and bound), so the remote fast-forwards and history is never rewritten.
    ``--force-with-lease=<ref>:<sha>`` makes the update conditional on the branch still being at exactly
    ``lease_sha``: a head that moved in the meantime is ``REMOTE_BRANCH_CONFLICT`` and nothing is overwritten.
    A branch already at the identical signed head is a lost-acknowledgement success, resolved by read-back and
    never by a silent re-attempt.
    """

    if validated.rehearsal:
        raise ReplacementExecutorError("rehearsal result cannot be published")
    if not publisher_environment_is_safe(os.environ):
        raise ReplacementExecutorError("publisher environment contains model authority")
    if re.fullmatch(r"[0-9a-f]{40}", lease_sha or "") is None:
        raise ReplacementExecutorError("a fast-forward publication requires an exact lease commit")
    if validated.base_sha != lease_sha:
        raise ReplacementExecutorError("the lease must be the exact base the remediation commits onto")
    root = Path(repo).resolve()
    head = _bind_publication(
        root,
        validated=validated,
        identity=identity,
        expected_unsigned_commit_sha=expected_unsigned_commit_sha,
        expected_tree_sha=expected_tree_sha,
        signing_key=signing_key,
    )
    ref = f"refs/heads/{validated.branch}"
    environment = _push_environment(push_config)
    try:
        _git_plumbing(root, "push", f"--force-with-lease={ref}:{lease_sha}", push_url, f"{head}:{ref}", env=environment)
    except ReplacementExecutorError:
        if _remote_head(root, push_url, ref, environment) == head:
            return ReplacementPublication(validated.branch, validated.base_sha, head)  # lost acknowledgement
        raise PublicationConflictError(
            "the branch is no longer at the bound PR head; nothing was overwritten"
        ) from None
    return ReplacementPublication(validated.branch, validated.base_sha, head)


# --- the ADR 0039 L3.2 RED->GREEN proof ------------------------------------------------------------------


def _overlay_tree(repo: Path, base_sha: str, files: Sequence[CandidateFile]) -> str:
    """The tree of ``base_sha`` with the given files applied, by trusted plumbing only (no checkout, no hooks)."""

    with tempfile.TemporaryDirectory(prefix="hunter-red-green-index-") as directory:
        env = {**os.environ, "GIT_INDEX_FILE": str(Path(directory) / "index")}
        _git_plumbing(repo, "read-tree", base_sha, env=env)
        for item in files:
            blob = _git_plumbing(repo, "hash-object", "-w", "--stdin", env=env, input_bytes=item.content)
            _git_plumbing(repo, "update-index", "--add", "--cacheinfo", f"{item.mode},{blob},{item.path}", env=env)
        return _git_plumbing(repo, "write-tree", env=env)


def _materialize_tree(repo: Path, tree: str, target: Path) -> None:
    with tempfile.TemporaryDirectory(prefix="hunter-red-green-checkout-") as directory:
        env = {**os.environ, "GIT_INDEX_FILE": str(Path(directory) / "index")}
        _git_plumbing(repo, "read-tree", tree, env=env)
        target.mkdir(parents=True)
        _git_plumbing(repo, "checkout-index", "--all", "--force", f"--prefix={target}/", env=env)


def red_green_regression_proof(
    repo: str | Path,
    *,
    validated: ValidatedReplacementResult,
    tests: Sequence[str],
    isolation_user: str,
    timeout: float = 900.0,
) -> dict[str, Any]:
    """ADR 0039 L3.2 (RD-1): the named tests **fail** on the reviewed head plus the result's test files only,
    and **pass** on the full result.

    Both runs execute candidate-controlled test code, so both run as the isolation uid in a standalone
    directory owned by it, with the explicit allowlist environment and the validating job's network denial.
    Anything other than RED-then-GREEN raises: without that pair of observations there is no classification.
    """

    user = require_isolation_user(isolation_user)
    leaked = publication_credentials_present(os.environ)
    if leaked:
        raise ReplacementExecutorError("regression proof boundary contains publication authority: " + ", ".join(leaked))
    if not publisher_environment_is_safe(os.environ):
        raise ReplacementExecutorError("regression proof boundary contains model authority")
    test_paths = {item.split("::", 1)[0] for item in tests}
    red_files = tuple(item for item in validated.files if item.path in test_paths)
    if {item.path for item in red_files} != test_paths:
        raise ReplacementExecutorError("a named regression test file is not in the result")
    root = Path(repo).resolve()
    red_tree = _overlay_tree(root, validated.base_sha, red_files)
    green_tree = _overlay_tree(root, validated.base_sha, validated.files)
    if green_tree == red_tree:
        raise ReplacementExecutorError("the regression proof is vacuous: the result adds no non-test change")
    argv = (REGRESSION_PYTHON, "-m", "pytest", "-p", "no:cacheprovider", "--no-header", "-q", *tests)
    sandbox_root = new_isolation_root("hunter-red-green-")
    home = sandbox_root / "home"
    outcomes: dict[str, int] = {}
    try:
        home.mkdir(mode=0o700)
        for label, tree in (("red", red_tree), ("green", green_tree)):
            sandbox = sandbox_root / label
            _materialize_tree(root, tree, sandbox)
            run_privileged("chown", "-R", user, str(sandbox), str(home))
            completed = subprocess.run(
                isolated_command(user, untrusted_environment(os.environ, home=home, pythonpath=sandbox / "src"), argv),
                cwd=sandbox,
                env=dict(SUDO_ENVIRONMENT),
                capture_output=True,
                check=False,
                timeout=timeout,
            )
            outcomes[label] = completed.returncode
            if label == "red" and completed.returncode == 0:
                raise ReplacementExecutorError("the named regression test already passed on the reviewed head")
            if label == "green" and completed.returncode != 0:
                raise ReplacementExecutorError("the named regression test does not pass on the full result")
    except subprocess.TimeoutExpired:
        raise ReplacementExecutorError("the regression proof timed out; no classification") from None
    finally:
        remove_isolation_root(sandbox_root, user)
    return {"regression_tests": list(tests), "red_exit": outcomes["red"], "green_exit": outcomes["green"]}


class ReplacementResultLedger:
    """Replay-safe terminal binding between one authorization and one exact result."""

    def __init__(self, database: str | Path) -> None:
        self._database = Path(database)
        with sqlite3.connect(self._database) as db:
            db.execute(
                """CREATE TABLE IF NOT EXISTS issue_agent_replacement_results (
                authorization_id TEXT PRIMARY KEY, result_sha256 TEXT NOT NULL,
                validation_definition TEXT NOT NULL, published_head TEXT)"""
            )

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
