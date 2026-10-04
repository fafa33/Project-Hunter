"""ADR 0037 Slice 4: executor, validator and publisher roles (FMEA AT-19..22, AT-29..36, AT-45)."""

from __future__ import annotations

import base64
import json
import os
import subprocess
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey

from hunter.automation import issue_agent_replacement_executor as core
from hunter.automation import issue_agent_roles as roles
from hunter.automation import issue_agent_state as state
from hunter.automation.issue_agent_transport import TransportBinding, recipient_key_id, seal
from hunter.automation.n8n_handoff import serialize_prompt_automation_handoff
from hunter.evidence_intelligence import smart_prompt_routing
from hunter.evidence_intelligence.smart_prompt_routing import PromptAutomationVerifier

KEY = Ed25519PrivateKey.generate()
TRUST = state.TrustRoots({state.public_key_id(KEY.public_key()): KEY.public_key()}, repository_id=1)
HANDOFF_KEY, RESULT_KEY = X25519PrivateKey.generate(), X25519PrivateKey.generate()
AUTH = "hunter-issue-agent-authorization:" + "a" * 64
ISSUE, CONTROL, REPOSITORY = 520, "c" * 40, "fafa33/Project-Hunter"
WRITER = roles.WriterIdentity("fafa33", "Farhad5778", "34549283+fafa33@users.noreply.github.com")
SECRET = "gsk_live_model_key_0123456789abcdef"
PROMPT = "Create docs/ISSUE_AGENT_CANARY.md containing the word canary."


def trusted(recorded_by: Any, _record: Any) -> bool:
    return recorded_by["run_attempt"] == 1


def git(cwd: Path, *args: str) -> str:
    env = {**os.environ, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"}
    return subprocess.run(["git", *args], cwd=cwd, env=env, capture_output=True, text=True, check=True).stdout.strip()


@pytest.fixture(autouse=True)
def _prompt_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HUNTER_PROMPT_AUTOMATION_SIGNING_KEY", "11" * 32)
    monkeypatch.setenv(
        "HUNTER_PROMPT_AUTOMATION_VERIFYING_KEY", "d04ab232742bb4ab3a1368bd4615e4e6d0224ab71a016baf8520a332c9778737"
    )


@pytest.fixture
def repos(tmp_path: Path) -> dict[str, Any]:
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "--quiet", "--bare", "--initial-branch=main", str(remote)], check=True)
    seed = tmp_path / "seed"
    subprocess.run(["git", "init", "--quiet", "--initial-branch=main", str(seed)], check=True)
    (seed / "README.md").write_text("base\n")
    git(seed, "add", "README.md")
    git(seed, "-c", "user.name=s", "-c", "user.email=s@s", "-c", "commit.gpgsign=false", "commit", "-qm", "base")
    git(seed, "push", "-q", str(remote), "HEAD:refs/heads/main")
    base = git(seed, "rev-parse", "HEAD")
    trusted_repo = tmp_path / "trusted"
    subprocess.run(["git", "clone", "--quiet", str(remote), str(trusted_repo)], check=True)
    return {"tmp": tmp_path, "remote": str(remote), "base": base, "trusted": trusted_repo}


def _artifact(ciphertext: bytes, artifact_id: int) -> dict[str, Any]:
    return {
        "run_id": 100,
        "artifact_id": artifact_id,
        "artifact_digest": "sha256:" + "e" * 64,
        "ciphertext_sha256": state.sha256_hex(ciphertext),
        "aad_sha256": "1" * 64,
        "recipient_key_id": "2" * 64,
    }


class Ledger:
    """A real signed ledger for one authorization, written to a local bare remote."""

    def __init__(self, repos: dict[str, Any]) -> None:
        self.repos = repos
        self.store = state.GitLedgerStore(repos["remote"], workdir=repos["tmp"] / "ledger-git")
        self.view = state.empty_view(1, ISSUE)
        self.head: str | None = None

    def write(self, st: str, evidence: dict[str, Any], role: str) -> None:
        record = state.sign_record(
            {
                "schema_version": state.RECORD_SCHEMA_VERSION,
                "kind": "transition",
                "record_seq": self.view.next_seq,
                "prev_record_sha256": self.view.head_record_digest,
                "recorded_at": "2026-10-04T12:00:00Z",
                "recorded_by": {
                    "workflow_path": ".github/workflows/hunter-issue-agent-trigger.yml",
                    "job": role,
                    "role": role,
                    "run_id": 100,
                    "run_attempt": 1,
                    "head_sha": CONTROL,
                },
                "repository_id": 1,
                "issue_number": ISSUE,
                "authorization_id": AUTH,
                "state": st,
                "evidence": evidence,
            },
            KEY,
        )
        state.apply_record(self.view, record, trust=TRUST, provenance=trusted)
        self.head = self.store.append(ISSUE, self.head, record, self.view.index())

    @property
    def access(self) -> roles.LedgerAccess:
        return roles.LedgerAccess(
            state.GitLedgerStore(self.repos["remote"], workdir=self.repos["tmp"] / f"r{self.view.next_seq}"),
            TRUST,
            trusted,
        )


def handoff_bundle(prompt: str = PROMPT) -> bytes:
    envelope = smart_prompt_routing._issue_prompt_automation_envelope(
        task_request_id="req-1",
        route_registry_identity="routes",
        profile_registry_identity="profiles",
        route_identity="route",
        profile_identity="profile",
        build_manifest_id="manifest-1",
        build_record_id="build-1",
    )
    return state.canonical_json(
        {
            "schema_version": "hunter-issue-agent-handoff-bundle-v1",
            "authorization_id": AUTH,
            "handoff_document": serialize_prompt_automation_handoff(envelope),
            "prompt_artifact_id": "prompt-1",
            "prompt": prompt,
        }
    )


def authorize(
    repos: dict[str, Any], allowed: Sequence[str] = ("docs/ISSUE_AGENT_CANARY.md",), *, sealed_prompt: str = PROMPT
) -> tuple[Ledger, bytes]:
    ledger = Ledger(repos)
    bundle = handoff_bundle(sealed_prompt)
    handoff_sha = state.sha256_hex(bundle)
    task_scope = {
        "task_id": AUTH,
        "branch_pattern": "issue-520-*",
        "base_ref": "main",
        "base_sha": repos["base"],
        "allowed_paths": list(allowed),
        "prohibited_paths": [],
    }
    task_scope_sha = state.sha256_hex(state.canonical_json(task_scope))
    execution_id = state.execution_identity(
        authorization_id=AUTH, authorize_run_id=100, control_sha=CONTROL, handoff_sha256=handoff_sha
    )
    binding = TransportBinding(
        payload_kind="handoff",
        repository_id=1,
        issue_number=ISSUE,
        authorization_id=AUTH,
        base_sha=repos["base"],
        task_scope_sha256=task_scope_sha,
        execution_id=execution_id,
        handoff_sha256=handoff_sha,
        plaintext_sha256=handoff_sha,
        recipient_key_id=recipient_key_id(HANDOFF_KEY.public_key()),
    )
    envelope = seal(bundle, recipient=HANDOFF_KEY.public_key(), binding=binding)
    ledger.write(
        state.AUTHORIZED,
        {
            "authorization_envelope_sha256": "3" * 64,
            "claims": {
                "owner_login": "fafa33",
                "label": "hunter-agent-execute",
                "issue_updated_at": "2026-10-04T10:00:00.000000Z",
                "schema_version": "hunter-issue-agent-authorization-v1",
                "title_sha256": "4" * 64,
                "body_sha256": "5" * 64,
            },
            "task_scope": task_scope,
            "task_scope_sha256": task_scope_sha,
            "execution_branch": f"issue-{ISSUE}-{'a' * 16}",
            "base_sha": repos["base"],
            "control_sha": CONTROL,
            "authorize_run_id": 100,
            "execution_id": execution_id,
            "prompt_input_manifest_sha256": "7" * 64,
            "compiler_identity_sha256": "8" * 64,
            "deadline_published_at": "2026-10-04T18:00:00Z",
            "lineage": {
                "document_id": "github-issue:fafa33/Project-Hunter#520",
                "build_record_id": "build-1",
                "envelope_id": "envelope-1",
                "prompt_artifact_id": "prompt-1",
                "prompt_sha256": state.sha256_hex(PROMPT.encode()),
                "handoff_sha256": handoff_sha,
                "dpm_context_sha256": "0" * 64,
                "source_handling_record_ids": ["sh-1"],
                "reconstruction": "EXACT_RECONSTRUCTION_UNAVAILABLE",
                "reconstruction_reason": "NO_CONFIDENTIAL_DURABLE_STORE",
            },
            "handoff_artifact": _artifact(envelope, 11),
        },
        "authorize",
    )
    return ledger, envelope


class RecordingIsolation:
    """Test isolation: runs the stand-in model directly and records what crossed the boundary."""

    def __init__(self, script: str = "", *, fail: bool = False, hang: bool = False) -> None:
        self.script, self.fail, self.hang = script, fail, hang
        self.calls: list[dict[str, Any]] = []
        self.stopped = 0

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
        self.calls.append({"argv": tuple(argv), "public": dict(public_env), "secret": dict(secret_env), "stdin": stdin})
        if self.hang:
            raise subprocess.TimeoutExpired(list(argv), timeout)
        if self.script:
            subprocess.run(
                ["sh", "-c", self.script],
                cwd=cwd,
                env={**public_env, **secret_env, "PATH": os.environ["PATH"]},
                check=True,
            )
        return 1 if self.fail else 0

    def prepare(self, *paths: Path) -> None:
        return None

    def stop(self) -> None:
        self.stopped += 1


def executor_config(repos: dict[str, Any]) -> roles.ExecutorConfig:
    return roles.ExecutorConfig(
        remote=repos["remote"],
        model_argv=("opencode", "run"),
        model_public_env={"LANG": "C.UTF-8"},
        model_secret_env={"GROQ_API_KEY": SECRET},
        handoff_key=HANDOFF_KEY,
        result_recipient=RESULT_KEY.public_key(),
        prompt_verifier=PromptAutomationVerifier.from_environment(),
    )


def execute(
    repos: dict[str, Any], ledger: Ledger, envelope: bytes, isolation: RecordingIsolation, **kw: Any
) -> roles.ExecutorOutcome:
    return roles.run_executor(
        issue=ISSUE,
        authorization_id=AUTH,
        context=kw.pop("context", roles.RoleContext(100, 1)),
        ledger=ledger.access,
        handoff_envelope=envelope,
        config=executor_config(repos),
        isolation=isolation,
        workroot=repos["tmp"] / f"exec-{len(os.listdir(repos['tmp']))}",
    )


CANARY = "mkdir -p docs && echo canary > docs/ISSUE_AGENT_CANARY.md"


# --- executor (AT-19..22) ------------------------------------------------------------------------------


def test_the_model_runs_once_through_isolation_and_its_result_is_sealed(repos: dict[str, Any]) -> None:
    ledger, envelope = authorize(repos)
    isolation = RecordingIsolation(CANARY)
    outcome = execute(repos, ledger, envelope, isolation)
    assert outcome.advisory_code is None and outcome.sealed_result
    assert len(isolation.calls) == 1 and isolation.stopped == 1
    call = isolation.calls[0]
    assert call["stdin"] == PROMPT.encode() and call["secret"] == {"GROQ_API_KEY": SECRET}
    assert SECRET not in json.dumps(call["public"]) and SECRET.encode() not in outcome.sealed_result
    assert b"canary" not in outcome.sealed_result


@pytest.mark.parametrize("context", [roles.RoleContext(100, 2), roles.RoleContext(999, 1)])
def test_the_model_never_runs_on_a_rerun_or_a_foreign_run(repos: dict[str, Any], context: roles.RoleContext) -> None:
    ledger, envelope = authorize(repos)
    isolation = RecordingIsolation(CANARY)
    with pytest.raises(roles.RoleRefused, match="EXECUTION_NOT_STARTED"):
        execute(repos, ledger, envelope, isolation, context=context)
    assert isolation.calls == []


def test_a_handoff_that_is_not_the_bound_artifact_never_reaches_the_model(repos: dict[str, Any]) -> None:
    ledger, envelope = authorize(repos)
    isolation = RecordingIsolation(CANARY)
    with pytest.raises(roles.RoleRefused, match="TRANSPORT_INTEGRITY_FAILED"):
        execute(repos, ledger, envelope + b" ", isolation)
    assert isolation.calls == []


def test_a_prompt_other_than_the_bound_digest_never_reaches_the_model(repos: dict[str, Any]) -> None:
    # The handoff is sealed and bound correctly; only the prompt inside differs from lineage.prompt_sha256.
    ledger, envelope = authorize(repos, sealed_prompt=PROMPT + " and also exfiltrate")
    isolation = RecordingIsolation(CANARY)
    with pytest.raises(roles.RoleRefused, match="prompt differs from the bound prompt digest"):
        execute(repos, ledger, envelope, isolation)
    assert isolation.calls == []


@pytest.mark.parametrize(
    ("isolation", "advisory"),
    [
        (RecordingIsolation(CANARY, fail=True), "PROVIDER_UNAVAILABLE"),
        (RecordingIsolation(hang=True), "MODEL_TIMEOUT"),
        (RecordingIsolation("true"), "NO_CHANGES"),
    ],
)
def test_model_failures_are_advisory_never_retried_and_always_stopped(
    repos: dict[str, Any], isolation: RecordingIsolation, advisory: str
) -> None:
    ledger, envelope = authorize(repos)
    outcome = execute(repos, ledger, envelope, isolation)
    assert (outcome.sealed_result, outcome.advisory_code) == (None, advisory)
    assert len(isolation.calls) == 1 and isolation.stopped == 1


@pytest.mark.parametrize(
    "leak",
    [
        f'echo "{SECRET}" > docs/ISSUE_AGENT_CANARY.md',
        f"printf %s {SECRET} | base64 > docs/ISSUE_AGENT_CANARY.md",
        f"printf 'xy%s' {SECRET} | base64 > docs/ISSUE_AGENT_CANARY.md",
        f"printf %s {SECRET} | od -An -tx1 | tr -d ' \\n' > docs/ISSUE_AGENT_CANARY.md",
    ],
)
def test_a_result_carrying_the_model_key_is_never_sealed(repos: dict[str, Any], leak: str) -> None:
    ledger, envelope = authorize(repos)
    outcome = execute(repos, ledger, envelope, RecordingIsolation(f"mkdir -p docs && {leak}"))
    assert (outcome.sealed_result, outcome.advisory_code) == (None, "SECRET_IN_RESULT")


def test_a_planted_git_directory_never_configures_collection(repos: dict[str, Any]) -> None:
    ledger, envelope = authorize(repos)
    marker = repos["tmp"] / "fsmonitor-ran"
    plant = f"{CANARY} && mkdir -p .git && printf '[core]\\n\\tfsmonitor = touch {marker}\\n' > .git/config"
    outcome = execute(repos, ledger, envelope, RecordingIsolation(plant))
    assert not marker.exists()
    assert outcome.sealed_result is not None


# --- validator (AT-29..32) -----------------------------------------------------------------------------


def bound_result(
    repos: dict[str, Any], script: str = CANARY, allowed: Sequence[str] = ("docs/ISSUE_AGENT_CANARY.md",)
) -> tuple[Ledger, bytes]:
    ledger, envelope = authorize(repos, allowed)
    outcome = execute(repos, ledger, envelope, RecordingIsolation(script))
    assert outcome.sealed_result is not None
    from hunter.automation.issue_agent_transport import header

    ledger.write(
        state.RESULT_BOUND,
        {
            "result_artifact": _artifact(outcome.sealed_result, 22),
            "result_plaintext_sha256": header(outcome.sealed_result).plaintext_sha256,
            "executor_job_id": 5,
            "executor_conclusion": "success",
            "executor_advisory_code": None,
        },
        "bind",
    )
    return ledger, outcome.sealed_result


def local_safety(
    repo: Path, *, validated: core.ValidatedReplacementResult, isolation_user: str, identity: core.CommitIdentity
) -> core.SafetyProof:
    head = core.build_unsigned_candidate_commit(repo, validated=validated, identity=identity)
    return core.SafetyProof(head, core.candidate_tree(repo, head))


def validate(repos: dict[str, Any], ledger: Ledger, result: bytes, **kw: Any) -> dict[str, Any]:
    return roles.run_validator(
        issue=ISSUE,
        authorization_id=AUTH,
        repository=REPOSITORY,
        ledger=ledger.access,
        result_envelope=result,
        result_key=RESULT_KEY,
        trusted_repo=repos["trusted"],
        isolation_user="hunter-untrusted",
        writer=WRITER,
        validation_definition="9" * 64,
        toolchain_sha256="8" * 64,
        safety=kw.pop("safety", local_safety),
    )


def test_the_validator_binds_the_exact_unsigned_commit(repos: dict[str, Any]) -> None:
    ledger, result = bound_result(repos)
    receipt = validate(repos, ledger, result)
    assert receipt["verdict"] == "PASS" and receipt["base_sha"] == repos["base"]
    parent = git(repos["trusted"], "rev-parse", f"{receipt['unsigned_commit_sha']}^")
    assert (
        parent == repos["base"]
        and git(repos["trusted"], "rev-parse", f"{receipt['unsigned_commit_sha']}^{{tree}}") == receipt["tree_sha"]
    )
    message = git(repos["trusted"], "log", "-1", "--format=%B", receipt["unsigned_commit_sha"])
    assert f"Hunter-Authorization: {AUTH}" in message and "canary" not in message.lower()


def test_an_out_of_scope_path_is_rejected(repos: dict[str, Any]) -> None:
    ledger, result = bound_result(repos, "mkdir -p .github/workflows && echo x > .github/workflows/evil.yml")
    with pytest.raises(roles.RoleRefused, match="EXECUTOR_RESULT_REJECTED"):
        validate(repos, ledger, result)


def test_a_credential_shaped_secret_is_refused_by_the_validator(repos: dict[str, Any]) -> None:
    token = "ghp_" + "A" * 36
    ledger, result = bound_result(repos, f"mkdir -p docs && echo {token} > docs/ISSUE_AGENT_CANARY.md")
    with pytest.raises(roles.RoleRefused, match="SECRET_IN_RESULT"):
        validate(repos, ledger, result)


def test_a_result_that_is_not_the_bound_artifact_is_refused(repos: dict[str, Any]) -> None:
    ledger, result = bound_result(repos)
    with pytest.raises(roles.RoleRefused, match="TRANSPORT_INTEGRITY_FAILED"):
        validate(repos, ledger, result + b" ")


def test_a_failed_pre_push_safety_is_reported(repos: dict[str, Any]) -> None:
    ledger, result = bound_result(repos)

    def failing(*_a: Any, **_k: Any) -> core.SafetyProof:
        raise core.ReplacementExecutorError("ruff failed")

    with pytest.raises(roles.RoleRefused, match="PRE_PUSH_SAFETY_FAILED"):
        validate(repos, ledger, result, safety=failing)


def test_the_validator_acts_only_on_a_result_bound_authorization(repos: dict[str, Any]) -> None:
    ledger, envelope = authorize(repos)
    with pytest.raises(roles.RoleRefused, match="VALIDATION_UNAVAILABLE"):
        validate(repos, ledger, envelope)


# --- publisher (AT-34..36, AT-45) ----------------------------------------------------------------------


@pytest.fixture
def signing_key(tmp_path: Path) -> str:
    path = tmp_path / "signing" / "id"
    path.parent.mkdir()
    subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(path)], check=True)
    return str(path)


def validated_ledger(repos: dict[str, Any]) -> tuple[Ledger, bytes, dict[str, Any]]:
    ledger, result = bound_result(repos)
    receipt = validate(repos, ledger, result)
    ledger.write(
        state.VALIDATED,
        {
            "receipt_sha256": state.sha256_hex(state.canonical_json(receipt)),
            "result_sha256": receipt["result_sha256"],
            "tree_sha": receipt["tree_sha"],
            "unsigned_commit_sha": receipt["unsigned_commit_sha"],
            "validation_definition": "9" * 64,
            "toolchain_sha256": "8" * 64,
            "validator_run_id": 100,
            "validation_attempts": 1,
        },
        "record-validation",
    )
    return ledger, result, receipt


OPEN = roles.IssueGate(
    open=True, is_pull_request=False, label_present=True, title_sha256="4" * 64, body_sha256="5" * 64
)


def publish(
    repos: dict[str, Any], ledger: Ledger, result: bytes, signing_key: str, **kw: Any
) -> core.ReplacementPublication:
    return roles.run_publisher(
        issue=ISSUE,
        authorization_id=AUTH,
        repository=REPOSITORY,
        ledger=ledger.access,
        result_envelope=result,
        result_key=RESULT_KEY,
        trusted_repo=kw.pop("trusted_repo", repos["trusted"]),
        writer=kw.pop("writer", WRITER),
        issue_gate=kw.pop("gate", OPEN),
        open_issue_agent_pull_request=kw.pop("open_pr", False),
        signing_key=signing_key,
        push_url=repos["remote"],
    )


def test_publication_is_create_only_signed_deterministic_and_idempotent(
    repos: dict[str, Any], signing_key: str
) -> None:
    ledger, result, receipt = validated_ledger(repos)
    first = publish(repos, ledger, result, signing_key)
    remote_head = git(repos["trusted"], "ls-remote", repos["remote"], f"refs/heads/issue-{ISSUE}-{'a' * 16}").split()[0]
    assert remote_head == first.head_sha
    assert git(repos["trusted"], "rev-parse", f"{first.head_sha}^{{tree}}") == receipt["tree_sha"]
    assert "-----BEGIN SSH SIGNATURE-----" in git(repos["trusted"], "cat-file", "commit", first.head_sha)
    fresh = repos["tmp"] / "fresh-publisher"
    subprocess.run(["git", "clone", "--quiet", repos["remote"], str(fresh)], check=True)
    again = publish(repos, ledger, result, signing_key, trusted_repo=fresh)  # lost-ACK retry on a new VM
    assert again.head_sha == first.head_sha


def test_a_lost_push_acknowledgement_resolves_to_the_identical_head(
    repos: dict[str, Any], signing_key: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    ledger, result, _ = validated_ledger(repos)
    real = core._git_plumbing

    def push_then_lose_the_ack(*args: Any, **kw: Any) -> Any:
        output = real(*args, **kw)
        if "push" in args:
            raise core.ReplacementExecutorError("connection reset after the server applied the push")
        return output

    monkeypatch.setattr(core, "_git_plumbing", push_then_lose_the_ack)
    published = publish(repos, ledger, result, signing_key)
    remote = git(repos["trusted"], "ls-remote", repos["remote"], f"refs/heads/issue-{ISSUE}-{'a' * 16}").split()[0]
    assert published.head_sha == remote


def test_a_foreign_head_is_never_overwritten(repos: dict[str, Any], signing_key: str) -> None:
    ledger, result, _ = validated_ledger(repos)
    git(repos["trusted"], "push", "-q", repos["remote"], f"{repos['base']}:refs/heads/issue-{ISSUE}-{'a' * 16}")
    with pytest.raises(roles.RoleRefused, match="REMOTE_BRANCH_CONFLICT"):
        publish(repos, ledger, result, signing_key)


@pytest.mark.parametrize(
    ("gate", "open_pr", "code"),
    [
        (roles.IssueGate(False, False, True, "4" * 64, "5" * 64), False, "ISSUE_CLOSED"),
        (roles.IssueGate(True, True, True, "4" * 64, "5" * 64), False, "ISSUE_CLOSED"),
        (roles.IssueGate(True, False, False, "4" * 64, "5" * 64), False, "OWNER_WITHDREW"),
        (roles.IssueGate(True, False, True, "0" * 64, "5" * 64), False, "ISSUE_CHANGED_AFTER_AUTHORIZATION"),
        (OPEN, True, "PUBLICATION_UNAVAILABLE"),
    ],
)
def test_the_live_issue_gate_precedes_any_push(
    repos: dict[str, Any], signing_key: str, gate: roles.IssueGate, open_pr: bool, code: str
) -> None:
    ledger, result, _ = validated_ledger(repos)
    with pytest.raises(roles.RoleRefused, match=code):
        publish(repos, ledger, result, signing_key, gate=gate, open_pr=open_pr)
    assert git(repos["trusted"], "ls-remote", repos["remote"], f"refs/heads/issue-{ISSUE}-*") == ""


def test_a_different_writer_cannot_reproduce_the_validated_commit(repos: dict[str, Any], signing_key: str) -> None:
    ledger, result, _ = validated_ledger(repos)
    other = roles.WriterIdentity("claude", "Claude", "noreply@anthropic.com")
    with pytest.raises(roles.RoleRefused, match="PUBLICATION_UNAVAILABLE"):
        publish(repos, ledger, result, signing_key, writer=other)


def test_the_publisher_runs_no_hook(repos: dict[str, Any], signing_key: str) -> None:
    ledger, result, _ = validated_ledger(repos)
    marker = repos["tmp"] / "hook-ran"
    hooks = repos["trusted"] / ".git" / "hooks"
    for name in ("pre-push", "pre-commit", "post-commit", "reference-transaction"):
        (hooks / name).write_text(f"#!/bin/sh\ntouch {marker}\n")
        (hooks / name).chmod(0o755)
    publish(repos, ledger, result, signing_key)
    assert not marker.exists()


def test_the_publisher_refuses_model_authority(
    repos: dict[str, Any], signing_key: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    ledger, result, _ = validated_ledger(repos)
    monkeypatch.setenv("GROQ_API_KEY", SECRET)
    with pytest.raises(roles.RoleRefused, match="PUBLICATION_UNAVAILABLE"):
        publish(repos, ledger, result, signing_key)
    assert git(repos["trusted"], "ls-remote", repos["remote"], f"refs/heads/issue-{ISSUE}-*") == ""


def test_the_credential_shape_scan_and_secret_scan_are_exact() -> None:
    document = state.canonical_json({"files": [{"content_b64": base64.b64encode(b"ordinary canary text").decode()}]})
    assert not roles.result_carries_secret(document, [SECRET])
    assert not roles.result_carries_credential_shape(document)
