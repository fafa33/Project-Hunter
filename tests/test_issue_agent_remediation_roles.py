"""ADR 0039 S5b-3: the remediation executor base, the RED->GREEN proof, the L6 promotion and the
exact-lease fast-forward publication.

Real stack throughout: a real bare remote, a real signed ledger, the real result contract, the real
credential-free validator and the real publisher. Only the two privileged ports (the isolation uid and its
sudo calls) are substituted, so every tree, digest, exit code and push is observed rather than asserted.
"""

from __future__ import annotations

import base64
import json
import os
import shutil
import subprocess
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey

from hunter.automation import issue_agent_remediation as remediation
from hunter.automation import issue_agent_replacement_executor as core
from hunter.automation import issue_agent_roles as roles
from hunter.automation import issue_agent_state as state
from hunter.automation.issue_agent_transport import TransportBinding, header, recipient_key_id, seal
from hunter.automation.n8n_handoff import serialize_prompt_automation_handoff
from hunter.evidence_intelligence import smart_prompt_routing
from hunter.evidence_intelligence.smart_prompt_routing import PromptAutomationVerifier
from hunter.task_scope import TaskScopeContract

KEY = Ed25519PrivateKey.generate()
TRUST = state.TrustRoots({state.public_key_id(KEY.public_key()): KEY.public_key()}, repository_id=1)
HANDOFF_KEY, RESULT_KEY = X25519PrivateKey.generate(), X25519PrivateKey.generate()
ISSUE, CONTROL, REPOSITORY = 520, "c" * 40, "fafa33/Project-Hunter"
PARENT = "hunter-issue-agent-authorization:" + "a" * 64
REMEDIATION = "hunter-issue-agent-authorization:" + "b" * 64
BRANCH = f"issue-{ISSUE}-{'a' * 16}"
PULL_REQUEST = 600
FINDING = "1" * 64
WRITER = roles.WriterIdentity("fafa33", "Farhad5778", "34549283+fafa33@users.noreply.github.com")
SECRET = "gsk_live_model_key_0123456789abcdef"
PROMPT = "Remediate the reviewed finding."

GUARD = "src/hunter/guard.py"
TEST = "tests/test_guard.py"
TEST_ID = f"{TEST}::test_guard_rejects_the_second_call"

SOURCE_AT_BASE = "def guard(seen, value):\n    return value in seen\n"
SOURCE_FIXED = (
    "def guard(seen, value):\n    if value in seen:\n        raise ValueError('duplicate')\n    return True\n"
)
TEST_SOURCE = (
    "from src.hunter import guard\n"
    "\n"
    "\n"
    "def test_guard_rejects_the_second_call():\n"
    "    assert guard.guard(set(), 'a') is True\n"
    "    try:\n"
    "        guard.guard({'a'}, 'a')\n"
    "    except ValueError:\n"
    "        return\n"
    "    raise AssertionError('the duplicate was not rejected')\n"
)
KNOWN_FAMILY = {"family_id": "DFF-001"}
NEW_FAMILY = {
    "new_family": {"title": "duplicate-guard-accepts-a-repeated-value", "invariant": "a guard rejects a repeated value"}
}
PROPOSAL_KEY = "remediation"
ALLOWED = ["src/hunter/", "tests/"]


#: The canonical registry and dispositions as they stand on ``main``, so every PR head already carries them.
SEEDED_REGISTRY = {
    "version": 1,
    "purpose": "canonical registry",
    "defects": [],
    "families": [
        {
            "id": "DFF-001",
            "title": "an-already-proven-family",
            "invariant": "an already proven invariant that holds on every guard surface",
            "applicability": {"changed_paths": ["src/hunter/"], "rationale": "the guard surface"},
            "prevention": {"mechanism": "ADR 0039 L3.2", "boundary": "review"},
            "regression_evidence": ["tests/test_other.py::test_the_previous_proof"],
            "lifecycle": "regression-tested",
            "sources": ["PR #590"],
        }
    ],
}
SEEDED_DISPOSITIONS = {"version": 1, "purpose": "canonical dispositions", "findings": []}


def trusted(recorded_by: Any, _record: Any) -> bool:
    return recorded_by["run_attempt"] == 1


def git(cwd: Path, *args: str) -> str:
    env = {**os.environ, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"}
    return subprocess.run(["git", *args], cwd=cwd, env=env, capture_output=True, text=True, check=True).stdout.strip()


@pytest.fixture(autouse=True)
def _prompt_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HUNTER_PROMPT_AUTOMATION_SIGNING_KEY", "11" * 32)
    monkeypatch.setenv(
        "HUNTER_PROMPT_AUTOMATION_VERIFYING_KEY",
        "d04ab232742bb4ab3a1368bd4615e4e6d0224ab71a016baf8520a332c9778737",
    )


@pytest.fixture
def repos(tmp_path: Path) -> dict[str, Any]:
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "--quiet", "--bare", "--initial-branch=main", str(remote)], check=True)
    seed = tmp_path / "seed"
    subprocess.run(["git", "init", "--quiet", "--initial-branch=main", str(seed)], check=True)
    (seed / "src" / "hunter").mkdir(parents=True)
    (seed / "tests").mkdir()
    (seed / "README.md").write_text("base\n")
    (seed / GUARD).write_text(SOURCE_AT_BASE)
    (seed / TEST).write_text(TEST_SOURCE)
    (seed / "pyproject.toml").write_text('[tool.pytest.ini_options]\ntestpaths = ["tests"]\n')
    (seed / "docs").mkdir()
    (seed / "docs" / "DEFECT_REGISTRY.json").write_text(_canonical(SEEDED_REGISTRY))
    (seed / "docs" / "REVIEWER_FINDING_DISPOSITIONS.json").write_text(_canonical(SEEDED_DISPOSITIONS))
    git(seed, "add", "-A")
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
    """A real signed ledger for one Issue, written to a local bare remote."""

    def __init__(self, repos: dict[str, Any]) -> None:
        self.repos = repos
        self.store = state.GitLedgerStore(repos["remote"], workdir=repos["tmp"] / "ledger-git")
        self.view = state.empty_view(1, ISSUE)
        self.head: str | None = None
        self.sequence = 0

    def write(self, authorization_id: str, st: str, evidence: dict[str, Any], role: str) -> None:
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
                "authorization_id": authorization_id,
                "state": st,
                "evidence": evidence,
            },
            KEY,
        )
        state.apply_record(self.view, record, trust=TRUST, provenance=trusted)
        self.head = self.store.append(ISSUE, self.head, record, self.view.index())
        self.sequence += 1

    @property
    def access(self) -> roles.LedgerAccess:
        return roles.LedgerAccess(
            state.GitLedgerStore(self.repos["remote"], workdir=self.repos["tmp"] / f"r{self.sequence}"),
            TRUST,
            trusted,
        )


def _handoff(authorization_id: str, task_scope: Mapping[str, Any], base_sha: str) -> tuple[bytes, str]:
    envelope = smart_prompt_routing._issue_prompt_automation_envelope(
        task_request_id="req-1",
        route_registry_identity="routes",
        profile_registry_identity="profiles",
        route_identity="route",
        profile_identity="profile",
        build_manifest_id="manifest-1",
        build_record_id="build-1",
    )
    bundle = state.canonical_json(
        {
            "schema_version": "hunter-issue-agent-handoff-bundle-v1",
            "authorization_id": authorization_id,
            "handoff_document": serialize_prompt_automation_handoff(envelope),
            "prompt_artifact_id": "prompt-1",
            "prompt": PROMPT,
        }
    )
    handoff_sha = state.sha256_hex(bundle)
    binding = TransportBinding(
        payload_kind="handoff",
        repository_id=1,
        issue_number=ISSUE,
        authorization_id=authorization_id,
        base_sha=base_sha,
        task_scope_sha256=state.sha256_hex(state.canonical_json(dict(task_scope))),
        execution_id=state.execution_identity(
            authorization_id=authorization_id, authorize_run_id=100, control_sha=CONTROL, handoff_sha256=handoff_sha
        ),
        handoff_sha256=handoff_sha,
        plaintext_sha256=handoff_sha,
        recipient_key_id=recipient_key_id(HANDOFF_KEY.public_key()),
    )
    return seal(bundle, recipient=HANDOFF_KEY.public_key(), binding=binding), handoff_sha


def _evidence(
    authorization_id: str,
    task_scope: Mapping[str, Any],
    base_sha: str,
    handoff: bytes,
    handoff_sha: str,
    remediation_group: dict[str, Any] | None,
) -> dict[str, Any]:
    evidence = {
        "authorization_envelope_sha256": "3" * 64,
        "claims": {
            "owner_login": "fafa33",
            "label": "hunter-agent-execute",
            "issue_updated_at": "2026-10-04T10:00:00.000000Z",
            "schema_version": "hunter-issue-agent-remediation-authorization-v1",
            "title_sha256": "4" * 64,
            "body_sha256": "5" * 64,
        },
        "task_scope": dict(task_scope),
        "task_scope_sha256": state.sha256_hex(state.canonical_json(dict(task_scope))),
        "execution_branch": BRANCH,
        "base_sha": base_sha,
        "control_sha": CONTROL,
        "authorize_run_id": 100,
        "execution_id": state.execution_identity(
            authorization_id=authorization_id, authorize_run_id=100, control_sha=CONTROL, handoff_sha256=handoff_sha
        ),
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
        "handoff_artifact": _artifact(handoff, 11),
    }
    if remediation_group is not None:
        evidence["remediation"] = remediation_group
    return evidence


def _candidate(path: str, content: bytes) -> dict[str, Any]:
    import hashlib

    return {
        "path": path,
        "content_b64": base64.b64encode(content).decode("ascii"),
        "sha256": hashlib.sha256(content).hexdigest(),
        "mode": "100644",
    }


def _result_document(
    base_sha: str,
    files: Sequence[Mapping[str, Any]],
    proposal: Mapping[str, Any] | None = None,
    *,
    authorization_id: str = REMEDIATION,
) -> bytes:
    document: dict[str, Any] = {
        "schema_version": core.RESULT_SCHEMA_VERSION,
        "authorization_id": authorization_id,
        "base_sha": base_sha,
        "branch": BRANCH,
        "files": list(files),
    }
    if proposal is not None:
        document[PROPOSAL_KEY] = dict(proposal)
    return bytes(state.canonical_json(document))


def _seal_result(document: bytes, base_sha: str, task_scope: Mapping[str, Any], handoff_sha: str) -> bytes:
    return seal(
        document,
        recipient=RESULT_KEY.public_key(),
        binding=TransportBinding(
            payload_kind="result",
            repository_id=1,
            issue_number=ISSUE,
            authorization_id=REMEDIATION,
            base_sha=base_sha,
            task_scope_sha256=state.sha256_hex(state.canonical_json(dict(task_scope))),
            execution_id=state.execution_identity(
                authorization_id=REMEDIATION, authorize_run_id=100, control_sha=CONTROL, handoff_sha256=handoff_sha
            ),
            handoff_sha256=handoff_sha,
            plaintext_sha256=state.sha256_hex(document),
            recipient_key_id=recipient_key_id(RESULT_KEY.public_key()),
        ),
    )


def remediating(
    repos: dict[str, Any], *, ledger: Ledger | None = None, bound_head: str | None = None, attempt: int = 1
) -> tuple[Ledger, bytes, str, dict[str, Any]]:
    """A real COMPLETED parent whose branch is really published, then the bound remediation authorization."""

    parent_scope = {
        "task_id": PARENT,
        "branch_pattern": f"issue-{ISSUE}-*",
        "base_ref": "main",
        "base_sha": repos["base"],
        "allowed_paths": ["README.md"],
        "prohibited_paths": [],
    }
    ledger = Ledger(repos) if ledger is None else ledger
    parent_handoff, parent_handoff_sha = _handoff(PARENT, parent_scope, repos["base"])
    ledger.write(
        PARENT,
        state.AUTHORIZED,
        _evidence(PARENT, parent_scope, repos["base"], parent_handoff, parent_handoff_sha, None),
        "authorize",
    )
    parent_document = _result_document(repos["base"], [_candidate("README.md", b"base\n")], authorization_id=PARENT)
    parent_result = seal(
        parent_document,
        recipient=RESULT_KEY.public_key(),
        binding=TransportBinding(
            payload_kind="result",
            repository_id=1,
            issue_number=ISSUE,
            authorization_id=PARENT,
            base_sha=repos["base"],
            task_scope_sha256=state.sha256_hex(state.canonical_json(parent_scope)),
            execution_id=state.execution_identity(
                authorization_id=PARENT, authorize_run_id=100, control_sha=CONTROL, handoff_sha256=parent_handoff_sha
            ),
            handoff_sha256=parent_handoff_sha,
            plaintext_sha256=state.sha256_hex(parent_document),
            recipient_key_id=recipient_key_id(RESULT_KEY.public_key()),
        ),
    )
    ledger.write(
        PARENT,
        state.RESULT_BOUND,
        {
            "result_artifact": _artifact(parent_result, 22),
            "result_plaintext_sha256": state.sha256_hex(parent_document),
            "executor_job_id": 5,
            "executor_conclusion": "success",
            "executor_advisory_code": None,
        },
        "bind",
    )
    parent = core.validate_bound_result(
        parent_document,
        binding=core.ResultBinding(PARENT, REPOSITORY, BRANCH, repos["base"], TaskScopeContract(**parent_scope)),
        rehearsal=False,
    )
    parent_identity = core.CommitIdentity(WRITER.name, WRITER.email, "2026-10-04T12:00:00Z", "parent candidate")
    parent_unsigned = core.build_unsigned_candidate_commit(repos["trusted"], validated=parent, identity=parent_identity)
    parent_tree = core.candidate_tree(repos["trusted"], parent_unsigned)
    ledger.write(
        PARENT,
        state.VALIDATED,
        {
            "receipt_sha256": "6" * 64,
            "result_sha256": parent.result_sha256,
            "tree_sha": parent_tree,
            "unsigned_commit_sha": parent_unsigned,
            "validation_definition": "9" * 64,
            "toolchain_sha256": "8" * 64,
            "validator_run_id": 100,
            "validation_attempts": 1,
        },
        "record-validation",
    )
    git(repos["trusted"], "push", "-q", repos["remote"], f"{parent_unsigned}:refs/heads/{BRANCH}")
    execution_id = state.execution_identity(
        authorization_id=PARENT, authorize_run_id=100, control_sha=CONTROL, handoff_sha256=parent_handoff_sha
    )
    ledger.write(
        PARENT,
        state.PUBLISHED,
        {
            "writer_login": WRITER.login,
            "publication_identity": state.publication_identity(
                repository_id=1,
                issue_number=ISSUE,
                authorization_id=PARENT,
                base_sha=repos["base"],
                task_scope_sha256=state.sha256_hex(state.canonical_json(parent_scope)),
                execution_id=execution_id,
                result_sha256=parent.result_sha256,
                tree_sha=parent_tree,
                unsigned_commit_sha=parent_unsigned,
                control_sha=CONTROL,
                writer_login=WRITER.login,
            ),
            "head_sha": parent_unsigned,
            "commit_verified": True,
            "publish_attempts": 1,
            "deadline_completed_at": "2026-10-04T18:00:00Z",
        },
        "finalize",
    )
    ledger.write(
        PARENT,
        state.COMPLETED,
        {
            "pull_request_number": PULL_REQUEST,
            "pull_request_node_id": "PR_kwNode",
            "pull_request_head_sha": parent_unsigned,
            "draft": True,
            "preflight_run_id": 7,
            "preflight_conclusion": "success",
        },
        "candidate-pr-record",
    )

    head = parent_unsigned if bound_head is None else bound_head
    scope = {
        "task_id": REMEDIATION,
        "branch_pattern": f"issue-{ISSUE}-*",
        "base_ref": "main",
        "base_sha": head,
        "allowed_paths": list(ALLOWED) + ["docs/"],
        "prohibited_paths": list(remediation.PROMOTION_PATHS),
    }
    handoff, handoff_sha = _handoff(REMEDIATION, scope, head)
    ledger.write(
        REMEDIATION,
        state.AUTHORIZED,
        _evidence(
            REMEDIATION,
            scope,
            head,
            handoff,
            handoff_sha,
            {
                "parent_authorization_id": PARENT,
                "pull_request_number": PULL_REQUEST,
                "bound_head_sha": head,
                "finding_ids": [FINDING],
                "attempt": attempt,
            },
        ),
        "authorize",
    )
    return ledger, handoff, handoff_sha, scope


class RecordingIsolation:
    """Runs the stand-in agent directly and records what crossed the boundary."""

    def __init__(self, script: str = "") -> None:
        self.script = script
        self.calls: list[dict[str, Any]] = []

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
        self.calls.append({"argv": tuple(argv), "stdin": stdin})
        if self.script:
            subprocess.run(
                ["sh", "-c", self.script],
                cwd=cwd,
                env={**public_env, **secret_env, "PATH": os.environ["PATH"]},
                check=True,
            )
        return 0

    def prepare(self, *paths: Path) -> None:
        return None

    def stop(self) -> None:
        return None


def agent_script(*, with_test: bool = True, source: str = SOURCE_FIXED, extra: str = "") -> str:
    parts = ["mkdir -p src/hunter tests", f"cat > {GUARD} <<'HUNTER_EOF'\n{source}\nHUNTER_EOF"]
    if with_test:
        parts.append(f"cat > {TEST} <<'HUNTER_EOF'\n{TEST_SOURCE}\nHUNTER_EOF")
    if extra:
        parts.append(extra)
    return " && ".join(parts)


def execute(repos: dict[str, Any], ledger: Ledger, handoff: bytes, script: str) -> roles.ExecutorOutcome:
    return roles.run_executor(
        issue=ISSUE,
        authorization_id=REMEDIATION,
        context=roles.RoleContext(100, 1),
        ledger=ledger.access,
        handoff_envelope=handoff,
        config=roles.ExecutorConfig(
            remote=repos["remote"],
            model_argv=("opencode", "run"),
            model_public_env={"LANG": "C.UTF-8"},
            model_secret_env={"GROQ_API_KEY": SECRET},
            handoff_key=HANDOFF_KEY,
            result_recipient=RESULT_KEY.public_key(),
            prompt_verifier=PromptAutomationVerifier.from_environment(),
        ),
        isolation=RecordingIsolation(script),
        workroot=repos["tmp"] / f"exec-{len(list(repos['tmp'].iterdir()))}",
    )


def bind_result(ledger: Ledger, sealed: bytes) -> None:
    ledger.write(
        REMEDIATION,
        state.RESULT_BOUND,
        {
            "result_artifact": _artifact(sealed, 22),
            "result_plaintext_sha256": header(sealed).plaintext_sha256,
            "executor_job_id": 5,
            "executor_conclusion": "success",
            "executor_advisory_code": None,
        },
        "bind",
    )


def local_safety(
    repo: Path, *, validated: core.ValidatedReplacementResult, isolation_user: str, identity: core.CommitIdentity
) -> core.SafetyProof:
    head = core.build_unsigned_candidate_commit(repo, validated=validated, identity=identity)
    return core.SafetyProof(head, core.candidate_tree(repo, head))


def local_promotion(
    *, repo: Path, group: Mapping[str, Any], proposal: Mapping[str, Any], finding_id: str = FINDING, **_rest: Any
) -> Mapping[str, bytes]:
    """The one trusted promotion service: verified ledger provenance plus the reviewed-head registry."""

    return remediation.promote(repo=repo, group=group, proposal=proposal, finding=_finding(finding_id))


@pytest.fixture
def unprivileged(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """Run the privileged RED->GREEN boundary as the current user: only the uid boundary is substituted."""

    def root(prefix: str) -> Path:
        path = tmp_path / prefix
        path.mkdir(parents=True, exist_ok=True)
        return path

    monkeypatch.setattr(core, "require_isolation_user", lambda user: user)
    monkeypatch.setattr(core, "run_privileged", lambda *args, **kw: None)
    monkeypatch.setattr(core, "new_isolation_root", root)
    monkeypatch.setattr(core, "remove_isolation_root", lambda path, _user: shutil.rmtree(path, ignore_errors=True))
    monkeypatch.setattr(core, "isolated_command", lambda _user, _environment, argv: tuple(argv))
    monkeypatch.setattr(core, "REGRESSION_PYTHON", sys.executable)
    return tmp_path


def _finding(finding_id: str, *, reviewer: str = "codex[bot]") -> dict[str, Any]:
    """One ingested finding exactly as the anchored knowledge ledger projects it for promotion."""

    return {
        "finding_id": finding_id,
        "path": GUARD,
        "pull_request_number": PULL_REQUEST,
        "reviewed_head_sha": "d" * 40,
        "reviewer": reviewer,
        "comment_id": 4242,
    }


def _canonical(document: object) -> str:
    """The canonical serialization of both governed promotion files (pinned by ADR 0039 L6)."""

    return json.dumps(document, indent=2, ensure_ascii=False) + "\n"


def validated_state(ledger: Ledger, receipt: Mapping[str, Any]) -> None:
    ledger.write(
        REMEDIATION,
        state.VALIDATED,
        {
            "receipt_sha256": state.sha256_hex(state.canonical_json(dict(receipt))),
            "result_sha256": receipt["result_sha256"],
            "tree_sha": receipt["tree_sha"],
            "unsigned_commit_sha": receipt["unsigned_commit_sha"],
            "validation_definition": "9" * 64,
            "toolchain_sha256": "8" * 64,
            "validator_run_id": 100,
            "validation_attempts": 1,
            **({"remediation": dict(receipt["remediation"])} if "remediation" in receipt else {}),
        },
        "record-validation",
    )


def signing_key(tmp_path: Path) -> str:
    path = tmp_path / "signing" / "id"
    path.parent.mkdir()
    subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(path)], check=True)
    return str(path)


OPEN = roles.IssueGate(
    open=True, is_pull_request=False, label_present=True, title_sha256="4" * 64, body_sha256="5" * 64
)


def validate(
    repos: dict[str, Any],
    ledger: Ledger,
    sealed: bytes,
    *,
    port: roles.RemediationPort | None,
    safety: Any = local_safety,
) -> dict[str, Any]:
    return roles.run_validator(
        issue=ISSUE,
        authorization_id=REMEDIATION,
        repository=REPOSITORY,
        ledger=ledger.access,
        result_envelope=sealed,
        result_key=RESULT_KEY,
        trusted_repo=repos["trusted"],
        isolation_user="hunter-untrusted",
        writer=WRITER,
        validation_definition="9" * 64,
        toolchain_sha256="8" * 64,
        safety=safety,
        remediation=port,
    )


def publish(
    repos: dict[str, Any],
    ledger: Ledger,
    sealed: bytes,
    key: str,
    *,
    trusted_repo: Path | None = None,
    derive: Any = local_promotion,
    gate: roles.IssueGate = OPEN,
    open_pr: bool = True,
) -> core.ReplacementPublication:
    return roles.run_publisher(
        issue=ISSUE,
        authorization_id=REMEDIATION,
        repository=REPOSITORY,
        ledger=ledger.access,
        result_envelope=sealed,
        result_key=RESULT_KEY,
        trusted_repo=trusted_repo or repos["trusted"],
        writer=WRITER,
        issue_gate=gate,
        open_issue_agent_pull_request=open_pr,
        signing_key=key,
        push_url=repos["remote"],
        derive_promotion=derive,
    )


# --- the executor base is the bound PR head, not main (ADR 0039 L4) ----------------------------------------


def test_the_executor_runs_on_the_parent_branch_at_the_exact_bound_head(
    repos: dict[str, Any], unprivileged: Path
) -> None:
    ledger, handoff, _, _ = remediating(repos)
    bound = _remote_branch(repos)
    outcome = execute(repos, ledger, handoff, agent_script())
    assert outcome.advisory_code is None and outcome.sealed_result is not None
    # The reviewed head is the parent's published commit: reachable from the PR branch and not from main.
    assert bound != repos["base"]
    assert _is_ancestor(repos, repos["base"], bound)
    assert not _is_ancestor(repos, bound, repos["base"])


def test_a_moved_pr_head_never_reaches_the_model(repos: dict[str, Any], unprivileged: Path) -> None:
    ledger, handoff, _, _ = remediating(repos)
    calls: list[Mapping[str, Any]] = []
    isolation = _Recording(calls, agent_script())
    git(repos["trusted"], "push", "-q", "-f", repos["remote"], f"{repos['base']}:refs/heads/{BRANCH}")
    with pytest.raises(roles.RoleRefused, match="PR_HEAD_MOVED"):
        roles.run_executor(
            issue=ISSUE,
            authorization_id=REMEDIATION,
            context=roles.RoleContext(100, 1),
            ledger=ledger.access,
            handoff_envelope=handoff,
            config=_executor_config(repos),
            isolation=isolation,
            workroot=repos["tmp"] / "moved",
        )
    assert calls == []


def _executor_config(repos: dict[str, Any]) -> roles.ExecutorConfig:
    return roles.ExecutorConfig(
        remote=repos["remote"],
        model_argv=("opencode", "run"),
        model_public_env={"LANG": "C.UTF-8"},
        model_secret_env={"GROQ_API_KEY": SECRET},
        handoff_key=HANDOFF_KEY,
        result_recipient=RESULT_KEY.public_key(),
        prompt_verifier=PromptAutomationVerifier.from_environment(),
    )


class _Recording(RecordingIsolation):
    """A stand-in agent whose calls are recorded so a test can prove the model never ran."""

    def __init__(self, calls: list[Mapping[str, Any]], script: str) -> None:
        super().__init__(script)
        self._calls = calls

    def run(self, argv: Sequence[str], **kw: Any) -> int:
        self._calls.append({"argv": tuple(argv)})
        return super().run(argv, **kw)


# --- the RED -> GREEN proof (ADR 0039 L3.2) ---------------------------------------------------------------


FIX = agent_script()
PROPOSAL = {"finding_id": FINDING, "disposition": KNOWN_FAMILY, "regression_tests": [TEST_ID]}


def bound_result(
    ledger: Ledger,
    scope: Mapping[str, Any],
    *,
    files: Sequence[Mapping[str, Any]],
    proposal: Mapping[str, Any] | None = None,
    base_sha: str | None = None,
) -> bytes:
    """The sealed result exactly as the executor would produce it, with the optional model proposal."""

    base = base_sha if base_sha is not None else str(scope["base_sha"])
    document = _result_document(base, files, proposal)
    handoff_sha = _handoff_sha(ledger, REMEDIATION)
    sealed = _seal_result(document, base, scope, handoff_sha)
    bind_result(ledger, sealed)
    return sealed


def _handoff_sha(ledger: Ledger, authorization_id: str) -> str:
    _commit, entries = ledger.access.store.read(ISSUE)
    records = state.verify_chain(
        [entry.record for entry in entries],
        repository_id=1,
        issue_number=ISSUE,
        trust=TRUST,
        provenance=trusted,
        indexes=[entry.index for entry in entries],
    )
    return str(records.authorizations[authorization_id].evidence[state.AUTHORIZED]["lineage"]["handoff_sha256"])


def candidate_files(
    *, source: str = SOURCE_FIXED, tests: str | None = TEST_SOURCE, extra: Sequence[str] = ()
) -> list[Any]:
    files: list[Any] = [_candidate(GUARD, source.encode())]
    if tests is not None:
        files.append(_candidate(TEST, tests.encode()))
    files.extend(_candidate(path, b"{}\n") for path in extra)
    return files


def proven_ledger(repos: dict[str, Any], **changes: Any) -> tuple[Ledger, dict[str, Any], bytes]:
    ledger, handoff, handoff_sha, scope = remediating(repos)
    sealed = bound_result(ledger, scope, **changes)
    return ledger, scope, sealed


def test_a_proven_finding_carries_its_proof_into_the_receipt(repos: dict[str, Any], unprivileged: Path) -> None:
    ledger, scope, sealed = proven_ledger(repos, files=candidate_files(), proposal=PROPOSAL)
    receipt = validate(
        repos,
        ledger,
        sealed,
        port=roles.RemediationPort(red_green=core.red_green_regression_proof, promote=local_promotion),
    )
    proof = receipt["remediation"]
    assert proof["proven_finding_ids"] == [FINDING]
    assert proof["regression_tests"] == [TEST_ID]
    assert proof["disposition"] == KNOWN_FAMILY
    assert proof["bound_head_sha"] == scope["base_sha"]
    assert receipt["base_sha"] == scope["base_sha"]
    assert proof["promotion_sha256"] == core.promotion_digest(
        local_promotion(
            repo=repos["trusted"],
            group={"pull_request_number": PULL_REQUEST, "bound_head_sha": scope["base_sha"]},
            proposal={"finding_id": FINDING, "disposition": KNOWN_FAMILY, "regression_tests": [TEST_ID]},
        )
    )


def test_the_red_run_sees_only_the_test_files_over_the_reviewed_head(repos: dict[str, Any], unprivileged: Path) -> None:
    """RED proves the *test* fails before the fix, not that the whole tree fails to import."""

    ledger, scope, sealed = proven_ledger(repos, files=candidate_files(), proposal=PROPOSAL)
    validated = _validated(ledger, sealed, scope)
    red = core._overlay_tree(
        repos["trusted"], str(scope["base_sha"]), tuple(item for item in validated.files if item.path == TEST)
    )
    green = core._overlay_tree(repos["trusted"], str(scope["base_sha"]), validated.files)
    assert red != green
    _pytest = (sys.executable, "-m", "pytest", "-p", "no:cacheprovider", "--no-header", "-q", TEST_ID)
    for tree, expected in ((red, 1), (green, 0)):
        sandbox = unprivileged / f"tree-{expected}"
        core._materialize_tree(repos["trusted"], tree, sandbox)
        completed = subprocess.run(_pytest, cwd=sandbox, capture_output=True, check=False, timeout=600)
        assert completed.returncode == expected, completed.stdout.decode()


def _view(ledger: Ledger) -> state.AuthorizationView:
    _commit, entries = ledger.access.store.read(ISSUE)
    view = state.verify_chain(
        [entry.record for entry in entries],
        repository_id=1,
        issue_number=ISSUE,
        trust=TRUST,
        provenance=trusted,
        indexes=[entry.index for entry in entries],
    )
    assert view.active == REMEDIATION
    return view.authorizations[REMEDIATION]


def _validated(ledger: Ledger, sealed: bytes, scope: Mapping[str, Any]) -> core.ValidatedReplacementResult:
    from hunter.automation.issue_agent_transport import open_sealed

    document = open_sealed(
        sealed,
        recipient=RESULT_KEY,
        expected=roles.result_binding(_view(ledger), RESULT_KEY),
    )
    return core.validate_bound_result(
        document,
        binding=core.ResultBinding(
            REMEDIATION,
            REPOSITORY,
            BRANCH,
            str(scope["base_sha"]),
            TaskScopeContract.from_dict(dict(scope)),
            (FINDING,),
        ),
        rehearsal=False,
    )


def test_a_test_that_already_passes_is_never_a_proof(repos: dict[str, Any], unprivileged: Path) -> None:
    ledger, scope, sealed = proven_ledger(
        repos, files=candidate_files(source=SOURCE_FIXED, tests=PASSING_TEST), proposal=PROPOSAL
    )
    with pytest.raises(roles.RoleRefused, match="RED->GREEN"):
        validate(repos, ledger, sealed, port=roles.RemediationPort(promote=local_promotion))


def test_a_result_that_does_not_fix_the_failure_is_never_a_proof(repos: dict[str, Any], unprivileged: Path) -> None:
    ledger, scope, sealed = proven_ledger(repos, files=candidate_files(source=SOURCE_AT_BASE), proposal=PROPOSAL)
    with pytest.raises(roles.RoleRefused, match="RED->GREEN"):
        validate(repos, ledger, sealed, port=roles.RemediationPort(promote=local_promotion))


def test_a_remediation_result_may_not_write_the_canonical_promotion_files(
    repos: dict[str, Any], unprivileged: Path
) -> None:
    ledger, scope, sealed = proven_ledger(
        repos,
        files=candidate_files(extra=remediation.PROMOTION_PATHS),
        proposal=PROPOSAL,
    )
    with pytest.raises(roles.RoleRefused, match="EXECUTOR_RESULT_REJECTED"):
        validate(repos, ledger, sealed, port=roles.RemediationPort(promote=local_promotion))


def test_a_result_without_a_proposal_publishes_the_fix_but_earns_no_classification(
    repos: dict[str, Any], unprivileged: Path
) -> None:
    ledger, scope, sealed = proven_ledger(repos, files=candidate_files())
    receipt = validate(repos, ledger, sealed, port=roles.RemediationPort(promote=local_promotion))
    assert receipt["remediation"]["proven_finding_ids"] == []
    assert receipt["remediation"]["disposition"] is None
    assert receipt["remediation"]["promotion_sha256"] == core.promotion_digest({})


def test_a_remediation_validation_without_its_trusted_ports_fails_closed(
    repos: dict[str, Any], unprivileged: Path
) -> None:
    ledger, scope, sealed = proven_ledger(repos, files=candidate_files(), proposal=PROPOSAL)
    with pytest.raises(roles.RoleRefused, match="VALIDATION_UNAVAILABLE"):
        validate(repos, ledger, sealed, port=None)


# --- the L6 promotion and the exact-lease fast-forward (ADR 0039 L6/L7) ------------------------------------


PASSING_TEST = (
    "from src.hunter import guard\n"
    "\n"
    "\n"
    "def test_guard_rejects_the_second_call():\n"
    "    assert guard.guard({'a'}, 'a') is False\n"
)


@pytest.fixture
def key(tmp_path: Path) -> str:
    return signing_key(tmp_path)


def ready(repos: dict[str, Any], **changes: Any) -> tuple[Ledger, bytes, dict[str, Any]]:
    """A validated remediation, ready for the exact-lease fast-forward publication."""

    ledger, scope, sealed = proven_ledger(repos, **changes)
    receipt = validate(
        repos,
        ledger,
        sealed,
        port=roles.RemediationPort(red_green=core.red_green_regression_proof, promote=local_promotion),
    )
    validated_state(ledger, receipt)
    return ledger, sealed, receipt


def test_a_known_family_finding_promotes_into_the_same_commit(
    repos: dict[str, Any], unprivileged: Path, key: str
) -> None:
    ledger, sealed, receipt = ready(repos, files=candidate_files(), proposal=PROPOSAL)
    publication = publish(repos, ledger, sealed, key)
    assert git(repos["trusted"], "rev-parse", f"{publication.head_sha}^") == receipt["remediation"]["bound_head_sha"]
    assert (
        "docs/DEFECT_REGISTRY.json"
        in git(repos["trusted"], "ls-tree", "-r", "--name-only", publication.head_sha).split()
    )
    registry = json.loads(git(repos["trusted"], "show", f"{publication.head_sha}:docs/DEFECT_REGISTRY.json"))
    family = next(item for item in registry["families"] if item["id"] == "DFF-001")
    assert family["regression_evidence"] == ["tests/test_other.py::test_the_previous_proof", TEST_ID]
    dispositions = json.loads(
        git(repos["trusted"], "show", f"{publication.head_sha}:docs/REVIEWER_FINDING_DISPOSITIONS.json")
    )
    record = dispositions["findings"][-1]
    assert record["mapped_defect_id"] == "DFF-001"
    assert record["classification"] == "recurrence"
    assert record["test_reference"] == TEST_ID
    assert record["source_provenance"]["pr_number"] == PULL_REQUEST


def test_a_new_family_finding_creates_the_smallest_truthful_family(
    repos: dict[str, Any], unprivileged: Path, key: str
) -> None:
    proposal = {**PROPOSAL, "disposition": NEW_FAMILY}
    ledger, sealed, _ = ready(repos, files=candidate_files(), proposal=proposal)
    publication = publish(repos, ledger, sealed, key)
    registry = json.loads(git(repos["trusted"], "show", f"{publication.head_sha}:docs/DEFECT_REGISTRY.json"))
    created = next(item for item in registry["families"] if item["id"] != "DFF-001")
    assert created["lifecycle"] == "regression-tested"
    assert created["regression_evidence"] == [TEST_ID]
    assert created["title"] == NEW_FAMILY["new_family"]["title"]
    dispositions = json.loads(
        git(repos["trusted"], "show", f"{publication.head_sha}:docs/REVIEWER_FINDING_DISPOSITIONS.json")
    )
    record = dispositions["findings"][-1]
    assert record["classification"] == "new_systemic_defect"
    assert record["mapped_defect_id"] == created["id"]


def test_the_fast_forward_never_overwrites_a_moved_head(repos: dict[str, Any], unprivileged: Path, key: str) -> None:
    ledger, sealed, _ = ready(repos, files=candidate_files(), proposal=PROPOSAL)
    other = repos["base"]
    git(repos["trusted"], "push", "-q", "-f", repos["remote"], f"{other}:refs/heads/{BRANCH}")
    with pytest.raises(roles.RoleRefused, match="REMOTE_BRANCH_CONFLICT"):
        publish(repos, ledger, sealed, key, trusted_repo=_fresh_clone(repos))
    assert _remote_branch(repos) == other


def test_the_publisher_refuses_a_promotion_it_cannot_reproduce(
    repos: dict[str, Any], unprivileged: Path, key: str
) -> None:
    ledger, sealed, _ = ready(repos, files=candidate_files(), proposal=PROPOSAL)

    def wrong(**_rest: Any) -> Mapping[str, bytes]:
        return {"docs/DEFECT_REGISTRY.json": b'{"version": 1}\n'}

    with pytest.raises(roles.RoleRefused, match="did not reproduce the validated promotion delta"):
        publish(repos, ledger, sealed, key, derive=wrong)


def test_a_promoted_remediation_still_needs_the_live_issue_gate(
    repos: dict[str, Any], unprivileged: Path, key: str
) -> None:
    ledger, sealed, _ = ready(repos, files=candidate_files(), proposal=PROPOSAL)
    before = _remote_branch(repos)
    with pytest.raises(roles.RoleRefused, match="OWNER_WITHDREW"):
        publish(repos, ledger, sealed, key, gate=roles.IssueGate(True, False, False, "4" * 64, "5" * 64))
    assert _remote_branch(repos) == before


def test_a_lost_publication_acknowledgement_resolves_to_the_identical_head(
    repos: dict[str, Any], unprivileged: Path, key: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    ledger, sealed, _ = ready(repos, files=candidate_files(), proposal=PROPOSAL)
    real = core._git_plumbing

    def push_then_lose(*args: Any, **kw: Any) -> Any:
        output = real(*args, **kw)
        if "push" in args:
            raise core.ReplacementExecutorError("connection reset after the server applied the push")
        return output

    monkeypatch.setattr(core, "_git_plumbing", push_then_lose)
    publication = publish(repos, ledger, sealed, key)
    assert publication.head_sha == _remote_branch(repos)


def test_the_remediated_commit_is_signed_with_a_single_parent(
    repos: dict[str, Any], unprivileged: Path, key: str
) -> None:
    ledger, sealed, _ = ready(repos, files=candidate_files(), proposal=PROPOSAL)
    publication = publish(repos, ledger, sealed, key)
    assert len(git(repos["trusted"], "rev-list", "--parents", "-n", "1", publication.head_sha).split()) == 2
    assert "BEGIN SSH SIGNATURE" in git(repos["trusted"], "cat-file", "commit", publication.head_sha)


def test_the_publisher_never_runs_the_candidate_hook(repos: dict[str, Any], unprivileged: Path, key: str) -> None:
    ledger, sealed, _ = ready(repos, files=candidate_files(), proposal=PROPOSAL)
    marker = repos["tmp"] / "hook-ran"
    hooks = repos["trusted"] / ".git" / "hooks"
    hooks.mkdir(exist_ok=True)
    for name in ("pre-push", "pre-commit", "post-commit", "reference-transaction"):
        (hooks / name).write_text(f"#!/bin/sh\ntouch {marker}\n")
        (hooks / name).chmod(0o755)
    publish(repos, ledger, sealed, key)
    assert not marker.exists()


def _fresh_clone(repos: dict[str, Any]) -> Path:
    target = repos["tmp"] / "fresh-publisher"
    subprocess.run(["git", "clone", "--quiet", repos["remote"], str(target)], check=True)
    return target


def _remote_branch(repos: dict[str, Any]) -> str:
    return git(repos["trusted"], "ls-remote", repos["remote"], f"refs/heads/{BRANCH}").split()[0]


def _is_ancestor(repos: dict[str, Any], ancestor: str, descendant: str) -> bool:
    return (
        subprocess.run(
            ["git", "merge-base", "--is-ancestor", ancestor, descendant],
            cwd=repos["trusted"],
            capture_output=True,
            check=False,
        ).returncode
        == 0
    )
