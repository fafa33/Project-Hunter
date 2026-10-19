"""Executor, validator and publisher roles of the GitHub-native Issue Agent lifecycle (ADR 0037 D1, D3, D6).

Each role runs on its own fresh GitHub-hosted VM and trusts only:

- the signed, anchored ledger (re-verified here);
- sealed transport whose binding is derived from that ledger;
- the code at the bound ``control_sha``.

Hard boundaries:

* **Executor**: holds the model key and the handoff decryption key only. It launches the model only
  through an ``Isolation`` (production: a distinct non-root uid via ``sudo -n -u … env -i``). It kills
  every isolated process before collecting, collects through a trusted git directory, refuses a result
  carrying the model key, and seals the result. It never writes the ledger.
* **Validator**: credential-free toward candidate code. It decrypts and validates the result against
  the ledger binding in the trusted step, then proves the trusted pre-push safety over the exact
  unsigned commit as the isolation uid. It emits a digest-only receipt and never writes the ledger.
* **Publisher**: holds publication credentials only and runs no candidate content (DFF-028). It
  reproduces the validated unsigned commit byte for byte, signs the identical fields and pushes
  create-only.

No role reruns the model, resolves an indefinite observation as negative, or trusts another role's word
in place of a ledger or GitHub fact.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import subprocess
import urllib.parse
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final, Protocol

from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey

from hunter.automation import issue_agent_replacement_executor as core
from hunter.automation import issue_agent_state as state
from hunter.automation.issue_agent_transport import (
    TransportBinding,
    TransportIntegrityError,
    open_sealed,
    recipient_key_id,
    seal,
)
from hunter.automation.n8n_handoff import PromptAutomationEnvelopeHandoff
from hunter.evidence_intelligence.smart_prompt_routing import PromptAutomationVerifier
from hunter.task_scope import TaskScopeContract

RECEIPT_SCHEMA_VERSION: Final = "hunter-issue-agent-validation-receipt-v2"
MODEL_TIMEOUT_SECONDS: Final = 50 * 60
_SHA40 = re.compile(r"[0-9a-f]{40}")


class RoleRefused(RuntimeError):
    """A role refused to act. ``code`` is the closed failure or advisory code it reports."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code


@dataclass(frozen=True, slots=True)
class RoleContext:
    """The trusted run the role executes in (bound to the ledger's provenance)."""

    run_id: int
    run_attempt: int


@dataclass(frozen=True, slots=True)
class LedgerAccess:
    store: state.GitLedgerStore
    trust: state.TrustRoots
    provenance: state.ProvenanceCheck


def _authorization(access: LedgerAccess, issue: int, authorization_id: str) -> state.AuthorizationView:
    _, entries = access.store.read(issue)
    view = state.verify_chain(
        [entry.record for entry in entries],
        repository_id=access.trust.repository_id,
        issue_number=issue,
        trust=access.trust,
        provenance=access.provenance,
        indexes=[entry.index for entry in entries],
    )
    if authorization_id not in view.authorizations or view.active != authorization_id:
        raise RoleRefused("STATE_CORRUPT", "the authorization is not the Issue's active authorization")
    return view.authorizations[authorization_id]


def _scope(task_scope: Mapping[str, Any]) -> TaskScopeContract:
    return TaskScopeContract.from_dict(dict(task_scope))


def result_binding(view: state.AuthorizationView, recipient: X25519PrivateKey) -> TransportBinding:
    """The result transport binding, derived only from the signed ledger."""

    bound = view.evidence[state.AUTHORIZED]
    result = view.evidence[state.RESULT_BOUND]
    return TransportBinding(
        payload_kind="result",
        repository_id=view.records[0]["repository_id"],
        issue_number=view.records[0]["issue_number"],
        authorization_id=view.authorization_id,
        base_sha=bound["base_sha"],
        task_scope_sha256=bound["task_scope_sha256"],
        execution_id=bound["execution_id"],
        handoff_sha256=bound["lineage"]["handoff_sha256"],
        plaintext_sha256=result["result_plaintext_sha256"],
        recipient_key_id=recipient_key_id(recipient.public_key()),
    )


# --- isolation port ------------------------------------------------------------------------------------


class Isolation(Protocol):
    """Runs untrusted code as a separate principal that cannot read any trusted process or credential."""

    def run(
        self,
        argv: Sequence[str],
        *,
        cwd: Path,
        public_env: Mapping[str, str],
        secret_env: Mapping[str, str],
        stdin: bytes,
        timeout: float,
    ) -> int: ...

    def prepare(self, *paths: Path) -> None: ...

    def stop(self) -> None: ...


@dataclass(frozen=True, slots=True)
class SudoIsolation:
    """Production isolation: a distinct, non-root uid via ``sudo -n -u <uid> -- /usr/bin/env -i`` (S0 A-6)."""

    user: str

    def __post_init__(self) -> None:
        core.require_isolation_user(self.user)

    def run(
        self,
        argv: Sequence[str],
        *,
        cwd: Path,
        public_env: Mapping[str, str],
        secret_env: Mapping[str, str],
        stdin: bytes,
        timeout: float,
    ) -> int:
        if secret_env:
            command, launcher = core.isolated_secret_launch(self.user, public_env, secret_env, argv)
        else:
            command, launcher = core.isolated_command(self.user, public_env, argv), dict(core.SUDO_ENVIRONMENT)
        completed = subprocess.run(  # noqa: S603 - fixed launcher, closed argv
            command,
            cwd=cwd,
            env=launcher,
            input=stdin,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
            timeout=timeout,
        )
        return completed.returncode

    def prepare(self, *paths: Path) -> None:
        core.run_privileged("chown", "-R", self.user, *(str(path) for path in paths))

    def stop(self) -> None:
        core.stop_isolated_processes(self.user)


# --- trusted git over a workspace the model controls -----------------------------------------------------


def _git(git_dir: Path, work_tree: Path, *args: str) -> bytes:
    """Git against the trusted metadata directory only; nothing in the work tree configures it."""

    environment = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": str(git_dir),
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_TERMINAL_PROMPT": "0",
    }
    command = (
        "git", f"--git-dir={git_dir}", f"--work-tree={work_tree}",
        "-c", "core.hooksPath=/dev/null", "-c", "core.fsmonitor=false", *args,
    )  # fmt: skip
    completed = subprocess.run(command, cwd=work_tree, env=environment, capture_output=True, check=False, timeout=300)
    if completed.returncode != 0:
        raise RoleRefused("EXECUTION_NOT_COMPLETED", f"workspace git {args[0]} failed")
    return completed.stdout


def materialize_base(
    workspace: Path, git_dir: Path, *, remote: str, base_sha: str, remediation_branch: str | None = None
) -> None:
    """A credential-free workspace at exactly the signed base.

    The Issue path proves its base is reachable from remote ``main``. A remediation (ADR 0039 L4) instead runs
    on its parent PR branch: the base must be *exactly* the branch's current head, which is the value the
    control job bound into the signed authorization. A moved branch is ``PR_HEAD_MOVED`` and no model runs.
    """

    if _SHA40.fullmatch(base_sha) is None:
        raise RoleRefused("BASE_NOT_ON_MAIN", "base is not an exact commit")
    workspace.mkdir(parents=True)
    _git(git_dir, workspace, "init", "--quiet")
    git_dir.chmod(0o700)
    _git(git_dir, workspace, "fetch", "--quiet", "--no-tags", remote, "+refs/heads/main:refs/remotes/origin/main")
    if remediation_branch is None:
        environment = {
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_CONFIG_NOSYSTEM": "1",
            "PATH": os.environ.get("PATH", ""),
        }
        ancestry = subprocess.run(
            ("git", f"--git-dir={git_dir}", "merge-base", "--is-ancestor", base_sha, "refs/remotes/origin/main"),
            env=environment,
            capture_output=True,
            check=False,
        ).returncode
        if ancestry != 0:
            raise RoleRefused("BASE_NOT_ON_MAIN", "the signed base is not reachable from main")
    else:
        _git(
            git_dir, workspace, "fetch", "--quiet", "--no-tags", remote,
            f"+refs/heads/{remediation_branch}:refs/remotes/origin/{remediation_branch}",
        )  # fmt: skip
        head = _git(git_dir, workspace, "rev-parse", f"refs/remotes/origin/{remediation_branch}").decode().strip()
        if head != base_sha:
            raise RoleRefused("PR_HEAD_MOVED", "the remediated pull request head is no longer the bound head")
    _git(git_dir, workspace, "checkout", "--quiet", "--detach", base_sha)


def collect_result(workspace: Path, git_dir: Path, *, authorization_id: str, branch: str, base_sha: str) -> bytes:
    """The closed-schema hostile result: added or modified regular files only (salvaged from #558)."""

    _git(git_dir, workspace, "add", "--all")
    listing = _git(git_dir, workspace, "diff", "--cached", "--no-renames", "--name-status", "-z", base_sha).split(b"\0")
    entries = [item for item in listing if item]
    if not entries:
        raise RoleRefused("NO_CHANGES", "the model produced no candidate change")
    files: list[dict[str, str]] = []
    for status, raw_path in zip(entries[::2], entries[1::2], strict=True):
        if status not in (b"A", b"M"):
            raise RoleRefused("EXECUTOR_RESULT_REJECTED", "unsupported change kind")
        try:
            path = raw_path.decode("utf-8")
        except UnicodeDecodeError:
            raise RoleRefused("EXECUTOR_RESULT_REJECTED", "non-UTF-8 path") from None
        mode = _git(git_dir, workspace, "ls-files", "--stage", "--", path).decode().split(" ", 1)[0]
        if mode not in ("100644", "100755"):
            raise RoleRefused("EXECUTOR_RESULT_REJECTED", "unsupported file mode")
        content = _git(git_dir, workspace, "cat-file", "blob", f":{path}")
        files.append(
            {
                "path": path,
                "content_b64": base64.b64encode(content).decode("ascii"),
                "sha256": hashlib.sha256(content).hexdigest(),
                "mode": mode,
            }
        )
    return state.canonical_json(
        {
            "schema_version": core.RESULT_SCHEMA_VERSION,
            "authorization_id": authorization_id,
            "base_sha": base_sha,
            "branch": branch,
            "files": files,
        }
    )


#: First base64 character that depends only on the secret, for each byte alignment inside a stream.
_BASE64_SECRET_ONLY_START: Final = (0, 2, 3)


def _encodings(secret: str) -> set[bytes]:
    """A secret's raw, hex, URL-encoded and every-alignment base64 (standard and URL-safe) forms."""

    raw = secret.encode("utf-8")
    variants = {raw, raw.hex().encode(), raw.hex().upper().encode(), urllib.parse.quote(secret, safe="").encode()}
    for encode in (base64.b64encode, base64.urlsafe_b64encode):
        for offset, start in enumerate(_BASE64_SECRET_ONLY_START):
            encoded = encode(b"\0" * offset + raw).rstrip(b"=")
            variants.add(encoded[start:-2])  # the tail may mix with whatever byte follows the secret
    return {variant for variant in variants if len(variant) >= 8}


#: Credential shapes refused without knowing any secret value (defence in depth beside push protection).
_CREDENTIAL_SHAPE = re.compile(
    rb"(?:gh[pousr]_[A-Za-z0-9]{36,}|github_pat_[A-Za-z0-9_]{60,}|-----BEGIN [A-Z ]*PRIVATE KEY-----|AKIA[0-9A-Z]{16})"
)


def result_carries_credential_shape(document: bytes) -> bool:
    payload = json.loads(document)
    return any(_CREDENTIAL_SHAPE.search(base64.b64decode(item["content_b64"])) for item in payload["files"])


def result_carries_secret(document: bytes, secrets: Sequence[str]) -> bool:
    """True if any file in the result contains a secret raw, hex, URL-encoded or base64-encoded."""

    payload = json.loads(document)
    contents = [base64.b64decode(item["content_b64"]) for item in payload["files"]]
    needles = {variant for secret in secrets if secret for variant in _encodings(secret)}
    return any(needle in content for content in contents for needle in needles)


# --- executor ------------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ExecutorConfig:
    remote: str
    model_argv: tuple[str, ...]
    model_public_env: Mapping[str, str]
    model_secret_env: Mapping[str, str]
    handoff_key: X25519PrivateKey
    result_recipient: Any  # X25519PublicKey
    prompt_verifier: PromptAutomationVerifier
    model_timeout_seconds: float = MODEL_TIMEOUT_SECONDS


@dataclass(frozen=True, slots=True)
class ExecutorOutcome:
    sealed_result: bytes | None
    advisory_code: str | None


def run_executor(
    *,
    issue: int,
    authorization_id: str,
    context: RoleContext,
    ledger: LedgerAccess,
    handoff_envelope: bytes,
    config: ExecutorConfig,
    isolation: Isolation,
    workroot: Path,
) -> ExecutorOutcome:
    """Run the model exactly once for an ``AUTHORIZED`` authorization of this run. Never writes the ledger."""

    view = _authorization(ledger, issue, authorization_id)
    bound = view.evidence.get(state.AUTHORIZED)
    if view.state != state.AUTHORIZED or bound is None:
        raise RoleRefused("EXECUTION_NOT_STARTED", "the authorization is not awaiting execution")
    if context.run_attempt != 1 or bound["authorize_run_id"] != context.run_id:
        raise RoleRefused("EXECUTION_NOT_STARTED", "the model runs only in attempt 1 of its own authorize run")
    expected = TransportBinding(
        payload_kind="handoff",
        repository_id=view.records[0]["repository_id"],
        issue_number=issue,
        authorization_id=authorization_id,
        base_sha=bound["base_sha"],
        task_scope_sha256=bound["task_scope_sha256"],
        execution_id=bound["execution_id"],
        handoff_sha256=bound["lineage"]["handoff_sha256"],
        plaintext_sha256=bound["lineage"]["handoff_sha256"],
        recipient_key_id=recipient_key_id(config.handoff_key.public_key()),
    )
    if state.sha256_hex(handoff_envelope) != bound["handoff_artifact"]["ciphertext_sha256"]:
        raise RoleRefused("TRANSPORT_INTEGRITY_FAILED", "handoff ciphertext is not the bound artifact")
    try:
        bundle = json.loads(open_sealed(handoff_envelope, recipient=config.handoff_key, expected=expected))
    except TransportIntegrityError:
        raise RoleRefused("TRANSPORT_INTEGRITY_FAILED", "handoff envelope does not open under its binding") from None
    handoff = PromptAutomationEnvelopeHandoff.from_json(bundle["handoff_document"])
    handoff.to_envelope().verify_issuer_signature(config.prompt_verifier)
    if handoff.build_record_id != bound["lineage"]["build_record_id"]:
        raise RoleRefused("TRANSPORT_INTEGRITY_FAILED", "handoff lineage differs from the ledger")
    prompt = str(bundle["prompt"]).encode("utf-8")
    if state.sha256_hex(prompt) != bound["lineage"]["prompt_sha256"]:
        raise RoleRefused("TRANSPORT_INTEGRITY_FAILED", "prompt differs from the bound prompt digest")

    workspace, git_dir, home = workroot / "workspace", workroot / "trusted.git", workroot / "home"
    # ADR 0039 L4: a remediation executes on its parent PR branch at the exact bound head, not on main.
    materialize_base(
        workspace,
        git_dir,
        remote=config.remote,
        base_sha=bound["base_sha"],
        remediation_branch=bound["execution_branch"] if bound.get("remediation") is not None else None,
    )
    home.mkdir(mode=0o700)
    isolation.prepare(workspace, home)
    advisory: str | None = None
    try:
        status = isolation.run(
            config.model_argv,
            cwd=workspace,
            public_env={**config.model_public_env, "HOME": str(home), "PWD": str(workspace)},
            secret_env=config.model_secret_env,
            stdin=prompt,
            timeout=config.model_timeout_seconds,
        )
        if status != 0:
            advisory = "PROVIDER_UNAVAILABLE"
    except subprocess.TimeoutExpired:
        advisory = "MODEL_TIMEOUT"
    finally:
        isolation.stop()  # nothing the model started survives into collection
    if advisory is not None:
        return ExecutorOutcome(None, advisory)
    try:
        document = collect_result(
            workspace,
            git_dir,
            authorization_id=authorization_id,
            branch=bound["execution_branch"],
            base_sha=bound["base_sha"],
        )
    except RoleRefused as refusal:
        return ExecutorOutcome(None, refusal.code if refusal.code in state.ADVISORY_CODES else "NO_CHANGES")
    if result_carries_secret(document, list(config.model_secret_env.values())):
        return ExecutorOutcome(None, "SECRET_IN_RESULT")  # never sealed, never uploaded
    binding = TransportBinding(
        payload_kind="result",
        repository_id=view.records[0]["repository_id"],
        issue_number=issue,
        authorization_id=authorization_id,
        base_sha=bound["base_sha"],
        task_scope_sha256=bound["task_scope_sha256"],
        execution_id=bound["execution_id"],
        handoff_sha256=bound["lineage"]["handoff_sha256"],
        plaintext_sha256=state.sha256_hex(document),
        recipient_key_id=recipient_key_id(config.result_recipient),
    )
    return ExecutorOutcome(seal(document, recipient=config.result_recipient, binding=binding), None)


# --- validator -----------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class WriterIdentity:
    login: str
    name: str
    email: str


@dataclass(frozen=True, slots=True)
class RemediationPort:
    """The two trusted services a remediation needs (ADR 0039 L3.2/L5/L6).

    * ``red_green`` proves a finding: every named test fails on the reviewed head plus the result's test files
      only, and passes on the full result. It runs candidate test code as the isolation uid.
    * ``promote`` derives the deterministic registry / finding-disposition delta from the trusted inputs: the
      validated result's proposal, the anchored knowledge ledger's finding provenance and the canonical
      registry at the reviewed head. Both the validator and the publisher call the *same* function and compare
      the resulting digest, so neither can promote anything the other would not, and neither reads a model
      byte beyond the already-validated disposition's two shapes.
    """

    red_green: Callable[..., Mapping[str, Any]] = core.red_green_regression_proof
    promote: Callable[..., Mapping[str, bytes]] | None = None


def _remediation_group(view: state.AuthorizationView) -> dict[str, Any] | None:
    """The AUTHORIZED remediation group of this authorization, or ``None`` for the Issue path."""

    group = view.evidence[state.AUTHORIZED].get("remediation")
    return None if group is None else dict(group)


def commit_identity(view: state.AuthorizationView, writer: WriterIdentity, result_sha256: str) -> core.CommitIdentity:
    """The deterministic commit identity both the validator and the publisher derive from the ledger."""

    bound = view.evidence[state.AUTHORIZED]
    return core.CommitIdentity(
        name=writer.name,
        email=writer.email,
        timestamp=view.records[0]["recorded_at"],
        message=core.candidate_commit_message(
            authorization_id=view.authorization_id, execution_id=bound["execution_id"], result_sha256=result_sha256
        ),
    )


def _open_validated(
    view: state.AuthorizationView, envelope: bytes, recipient: X25519PrivateKey, repository: str
) -> tuple[bytes, core.ValidatedReplacementResult]:
    result = view.evidence[state.RESULT_BOUND]
    if state.sha256_hex(envelope) != result["result_artifact"]["ciphertext_sha256"]:
        raise RoleRefused("TRANSPORT_INTEGRITY_FAILED", "result ciphertext is not the bound artifact")
    try:
        document = open_sealed(envelope, recipient=recipient, expected=result_binding(view, recipient))
    except TransportIntegrityError:
        raise RoleRefused("TRANSPORT_INTEGRITY_FAILED", "result envelope does not open under its binding") from None
    bound = view.evidence[state.AUTHORIZED]
    group = _remediation_group(view)
    try:
        validated = core.validate_bound_result(
            document,
            binding=core.ResultBinding(
                view.authorization_id,
                repository,
                bound["execution_branch"],
                bound["base_sha"],
                _scope(bound["task_scope"]),
                tuple(group["finding_ids"]) if group is not None else (),
            ),
            rehearsal=False,
        )
    except core.ReplacementExecutorError:
        raise RoleRefused("EXECUTOR_RESULT_REJECTED", "the result violates the closed contract or TaskScope") from None
    return document, validated


def proven_finding_evidence(
    view: state.AuthorizationView, validated: core.ValidatedReplacementResult, proof: Mapping[str, Any] | None
) -> dict[str, Any]:
    """ADR 0039 L5/L6: the receipt's proof group, bound to the exact unsigned commit.

    Only public reviewer-thread provenance, the two bounded disposition shapes, the test ids and the delta's
    digest are recorded: no prompt byte, no source byte and no evidence byte reaches the anchored ledger.
    """

    group = _remediation_group(view)
    assert group is not None
    empty = {
        "bound_head_sha": group["bound_head_sha"],
        "finding_ids": list(group["finding_ids"]),
        "proven_finding_ids": [],
        "regression_tests": [],
        "disposition": None,
        "promotion_sha256": core.promotion_digest({}),
    }
    if validated.remediation is None or proof is None:
        return empty  # the fix still publishes; without a proven mapping it earns no classification
    return {
        **empty,
        "proven_finding_ids": [validated.remediation["finding_id"]],
        "regression_tests": sorted(proof["regression_tests"]),
        "disposition": validated.remediation["disposition"],
        "promotion_sha256": proof["promotion_sha256"],
    }


def run_validator(
    *,
    issue: int,
    authorization_id: str,
    repository: str,
    ledger: LedgerAccess,
    result_envelope: bytes,
    result_key: X25519PrivateKey,
    trusted_repo: Path,
    isolation_user: str,
    writer: WriterIdentity,
    validation_definition: str,
    toolchain_sha256: str,
    safety: Callable[..., core.SafetyProof] = core.credential_free_candidate_safety,
    remediation: RemediationPort | None = None,
) -> dict[str, Any]:
    """Prove the credential-free pre-push safety over the exact unsigned commit; return the receipt."""

    view = _authorization(ledger, issue, authorization_id)
    if view.state != state.RESULT_BOUND:
        raise RoleRefused("VALIDATION_UNAVAILABLE", "the authorization is not awaiting validation")
    document, validated = _open_validated(view, result_envelope, result_key, repository)
    if result_carries_credential_shape(document):
        raise RoleRefused("SECRET_IN_RESULT", "the result carries a credential-shaped secret")
    group = _remediation_group(view)
    proof: dict[str, Any] | None = None
    if group is not None:
        if remediation is None:
            raise RoleRefused("VALIDATION_UNAVAILABLE", "a remediation validation requires its trusted ports")
        proof = _prove_remediation(
            validated,
            trusted_repo=trusted_repo,
            isolation_user=isolation_user,
            group=group,
            port=remediation,
        )
        validated = core.with_promotion(validated, proof["delta"])
    identity = commit_identity(view, writer, validated.result_sha256)
    try:
        proof_safety = safety(trusted_repo, validated=validated, isolation_user=isolation_user, identity=identity)
    except core.ReplacementExecutorError:
        raise RoleRefused("PRE_PUSH_SAFETY_FAILED", "the trusted pre-push safety refused the candidate") from None
    bound = view.evidence[state.AUTHORIZED]
    receipt: dict[str, Any] = {
        "schema_version": RECEIPT_SCHEMA_VERSION,
        "authorization_id": authorization_id,
        "execution_id": bound["execution_id"],
        "ciphertext_sha256": view.evidence[state.RESULT_BOUND]["result_artifact"]["ciphertext_sha256"],
        "result_sha256": validated.result_sha256,
        "tree_sha": proof_safety.tree_sha,
        "unsigned_commit_sha": proof_safety.unsigned_commit_sha,
        "base_sha": bound["base_sha"],
        "task_scope_sha256": bound["task_scope_sha256"],
        "validation_definition": validation_definition,
        "toolchain_sha256": toolchain_sha256,
        "verdict": "PASS",
    }
    if group is not None:
        receipt["remediation"] = proven_finding_evidence(view, validated, proof)
    return receipt


def _prove_remediation(
    validated: core.ValidatedReplacementResult,
    *,
    trusted_repo: Path,
    isolation_user: str,
    group: Mapping[str, Any],
    port: RemediationPort,
) -> dict[str, Any]:
    """RED->GREEN, then the deterministic promotion delta. Either a proven mapping or no mapping at all."""

    if validated.remediation is None:
        return {"regression_tests": [], "promotion": None, "promotion_sha256": core.promotion_digest({}), "delta": {}}
    proposal = validated.remediation
    try:
        observed = port.red_green(
            trusted_repo,
            validated=validated,
            tests=proposal["regression_tests"],
            isolation_user=isolation_user,
        )
    except core.ReplacementExecutorError:
        # Without the RED->GREEN pair there is no classification, so the fix still publishes and the finding
        # stays known and unresolved (ADR 0039 L3.2).
        raise RoleRefused("PRE_PUSH_SAFETY_FAILED", "the finding is not proven by a RED->GREEN regression") from None
    if sorted(observed["regression_tests"]) != sorted(proposal["regression_tests"]):
        raise RoleRefused("PRE_PUSH_SAFETY_FAILED", "the regression proof does not cover every named test")
    promote = port.promote
    if promote is None:
        return {
            "regression_tests": list(proposal["regression_tests"]),
            "promotion_sha256": core.promotion_digest({}),
            "delta": {},
        }
    delta = dict(
        _promotion_delta(
            promote,
            repo=trusted_repo,
            validated=validated,
            group=group,
            proposal=proposal,
            stage="the trusted pre-push safety refused the proven promotion",
        )
    )
    return {
        "regression_tests": list(proposal["regression_tests"]),
        "promotion_sha256": core.promotion_digest(delta),
        "delta": delta,
    }


def _promotion_delta(
    promote: Callable[..., Mapping[str, bytes]],
    *,
    repo: Path,
    validated: core.ValidatedReplacementResult,
    group: Mapping[str, Any],
    proposal: Mapping[str, Any],
    stage: str,
) -> Mapping[str, bytes]:
    """One call site for the trusted promotion, used identically by the validator and the publisher."""

    try:
        return promote(
            repo=repo,
            validated=validated,
            group=group,
            proposal=proposal,
            finding_id=proposal["finding_id"],
        )
    except Exception:  # a promotion this repository cannot derive is not a publishable candidate
        raise RoleRefused("PRE_PUSH_SAFETY_FAILED", stage) from None


# --- publisher -----------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class IssueGate:
    """Live Issue facts at publication time (definitive observations only)."""

    open: bool
    is_pull_request: bool
    label_present: bool
    title_sha256: str
    body_sha256: str


def run_publisher(
    *,
    issue: int,
    authorization_id: str,
    repository: str,
    ledger: LedgerAccess,
    result_envelope: bytes,
    result_key: X25519PrivateKey,
    trusted_repo: Path,
    writer: WriterIdentity,
    issue_gate: IssueGate,
    open_issue_agent_pull_request: bool,
    signing_key: str,
    push_url: str,
    push_config: Sequence[tuple[str, str]] = (),
    derive_promotion: Callable[..., Mapping[str, bytes]] | None = None,
) -> core.ReplacementPublication:
    """Publish exactly the validated commit (T4): create-only for an Issue, an exact-lease fast-forward for a
    remediation (ADR 0039 L7, RD-5). Runs no candidate content and pushes no other ref."""

    view = _authorization(ledger, issue, authorization_id)
    if view.state != state.VALIDATED:
        raise RoleRefused("PUBLICATION_UNAVAILABLE", "the authorization is not awaiting publication")
    claims = view.evidence[state.AUTHORIZED]["claims"]
    if not issue_gate.open or issue_gate.is_pull_request:
        raise RoleRefused("ISSUE_CLOSED", "the Issue is no longer an open Issue")
    if not issue_gate.label_present:
        raise RoleRefused("OWNER_WITHDREW", "the owner removed the execution label")
    if (issue_gate.title_sha256, issue_gate.body_sha256) != (claims["title_sha256"], claims["body_sha256"]):
        raise RoleRefused("ISSUE_CHANGED_AFTER_AUTHORIZATION", "the Issue changed after authorization")
    group = _remediation_group(view)
    if group is None and open_issue_agent_pull_request:
        raise RoleRefused("PUBLICATION_UNAVAILABLE", "another Issue-Agent pull request is open for this Issue")
    _, validated = _open_validated(view, result_envelope, result_key, repository)
    validation = view.evidence[state.VALIDATED]
    if validated.result_sha256 != validation["result_sha256"]:
        raise RoleRefused("TRANSPORT_INTEGRITY_FAILED", "the result is not the validated result")
    if group is not None:
        validated = _rederive_promotion(
            validated,
            trusted_repo=trusted_repo,
            group=group,
            evidence=validation.get("remediation"),
            derive=derive_promotion,
        )
    try:
        if group is None:
            return core.publish_bound_create_only(
                trusted_repo,
                validated=validated,
                identity=commit_identity(view, writer, validated.result_sha256),
                expected_unsigned_commit_sha=validation["unsigned_commit_sha"],
                expected_tree_sha=validation["tree_sha"],
                signing_key=signing_key,
                push_url=push_url,
                push_config=push_config,
            )
        if validation.get("remediation") is None:
            raise RoleRefused("PUBLICATION_UNAVAILABLE", "the validated remediation evidence is missing")
        return core.publish_bound_fast_forward(
            trusted_repo,
            validated=validated,
            identity=commit_identity(view, writer, validated.result_sha256),
            expected_unsigned_commit_sha=validation["unsigned_commit_sha"],
            expected_tree_sha=validation["tree_sha"],
            lease_sha=group["bound_head_sha"],
            signing_key=signing_key,
            push_url=push_url,
            push_config=push_config,
        )
    except (core.PublicationConflictError, core.PublicationRejectedError) as error:
        raise RoleRefused(error.failure_code, str(error)) from None
    except core.ReplacementExecutorError:
        raise RoleRefused("PUBLICATION_UNAVAILABLE", "publication failed before any write") from None


def _rederive_promotion(
    validated: core.ValidatedReplacementResult,
    *,
    trusted_repo: Path,
    group: Mapping[str, Any],
    evidence: Mapping[str, Any] | None,
    derive: Callable[..., Mapping[str, bytes]] | None,
) -> core.ValidatedReplacementResult:
    """ADR 0039 L6 (RD-3): the publisher re-derives the identical promotion delta, or nothing is pushed.

    The delta is a pure function of the validated result's proposal, the anchored finding provenance and the
    canonical registry at the reviewed head, so the publisher needs no model output and no extra authority: it
    re-derives the bytes and compares the digest against the one the credential-free validator proved.
    """

    expected = None if evidence is None else str(evidence["promotion_sha256"])
    if validated.remediation is None:
        if expected not in (None, core.promotion_digest({})):
            raise RoleRefused("PUBLICATION_UNAVAILABLE", "the recorded promotion does not match the result")
        return validated
    if derive is None:
        raise RoleRefused("PUBLICATION_UNAVAILABLE", "a promoted remediation needs its trusted deriver")
    delta = dict(
        _promotion_delta(
            derive,
            repo=trusted_repo,
            validated=validated,
            group=group,
            proposal=validated.remediation,
            stage="the proven finding cannot be promoted deterministically",
        )
    )
    if core.promotion_digest(delta) != expected:
        raise RoleRefused("PUBLICATION_UNAVAILABLE", "publisher did not reproduce the validated promotion delta")
    return core.with_promotion(validated, delta)


__all__ = [
    "ExecutorConfig",
    "ExecutorOutcome",
    "IssueGate",
    "LedgerAccess",
    "RemediationPort",
    "RoleContext",
    "RoleRefused",
    "SudoIsolation",
    "WriterIdentity",
    "materialize_base",
    "result_carries_secret",
    "run_executor",
    "run_publisher",
    "run_validator",
]
