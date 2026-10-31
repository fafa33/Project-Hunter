"""ADR 0037 Slice 4: executor, validator and publisher roles (FMEA AT-19..22, AT-29..36, AT-45)."""

from __future__ import annotations

import base64
import json
import os
import re
import subprocess
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import hunter_opencode_diagnostic_probe as diagnostic_probe
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey

from hunter.automation import issue_agent_replacement_executor as core
from hunter.automation import issue_agent_roles as roles
from hunter.automation import issue_agent_state as state
from hunter.automation.issue_agent_transport import TransportBinding, recipient_key_id, seal
from hunter.automation.n8n_handoff import serialize_prompt_automation_handoff
from hunter.evidence_intelligence import smart_prompt_routing
from hunter.evidence_intelligence.smart_prompt_routing import (
    ENGINEERING_IMPLEMENT_PROFILE,
    ENGINEERING_REVIEW_FIX_PROFILE,
    WORKSPACE_EDIT_OUTPUT_CONTRACT,
    WORKSPACE_EDIT_OUTPUT_CONTRACT_VERSION,
    PromptAutomationVerifier,
)

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
        self.calls.append(
            {
                "argv": tuple(argv),
                "cwd": cwd,
                "public": dict(public_env),
                "secret": dict(secret_env),
                "stdin": stdin,
            }
        )
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


def opencode_config(repos: dict[str, Any]) -> roles.ExecutorConfig:
    """The production OpenCode command exactly as the execute job passes it after ``--model-argv``."""

    config = executor_config(repos)
    return roles.ExecutorConfig(
        remote=config.remote,
        model_argv=("/opt/hunter/bin/opencode", "run", "--model", "opencode/big-pickle", "--agent", "build"),
        model_public_env=config.model_public_env,
        model_secret_env=config.model_secret_env,
        handoff_key=config.handoff_key,
        result_recipient=config.result_recipient,
        prompt_verifier=config.prompt_verifier,
    )


def run_opencode(
    repos: dict[str, Any], ledger: Ledger, envelope: bytes, isolation: RecordingIsolation
) -> tuple[roles.ExecutorOutcome, dict[str, Any]]:
    outcome = roles.run_executor(
        issue=ISSUE,
        authorization_id=AUTH,
        context=roles.RoleContext(100, 1),
        ledger=ledger.access,
        handoff_envelope=envelope,
        config=opencode_config(repos),
        isolation=isolation,
        workroot=repos["tmp"] / "run-opencode",
    )
    assert len(isolation.calls) == 1
    return outcome, isolation.calls[0]


def opencode_inline_config(call: Mapping[str, Any]) -> dict[str, Any]:
    return json.loads(call["public"]["OPENCODE_CONFIG_CONTENT"])


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
    assert call["stdin"] == b"" and call["secret"] == {"GROQ_API_KEY": SECRET}
    assert Path(call["argv"][-1]).read_bytes() == PROMPT.encode()
    assert call["public"]["XDG_CONFIG_HOME"] == str(Path(call["public"]["HOME"]) / ".config")
    assert call["public"]["XDG_CACHE_HOME"] == str(Path(call["public"]["HOME"]) / ".cache")
    assert call["public"]["XDG_DATA_HOME"] == str(Path(call["public"]["HOME"]) / ".local" / "share")
    assert call["public"]["XDG_STATE_HOME"] == str(Path(call["public"]["HOME"]) / ".local" / "state")
    assert call["public"]["HOME"] != str(Path.home())
    assert SECRET not in json.dumps(call["public"]) and SECRET.encode() not in outcome.sealed_result
    assert b"canary" not in outcome.sealed_result


def test_real_subprocess_receives_prompt_and_writes_candidate_file(repos: dict[str, Any]) -> None:
    """Exercise real subprocess stdin, workspace write, and sealed result collection."""
    ledger, envelope = authorize(repos)

    class RealSubprocessIsolation(RecordingIsolation):
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
            self.calls.append(
                {"argv": tuple(argv), "public": dict(public_env), "secret": dict(secret_env), "stdin": stdin}
            )
            program = (
                "import pathlib,sys; p=pathlib.Path(sys.argv[1]).read_bytes(); "
                "assert b'ISSUE_AGENT_CANARY.md' in p; "
                "d=pathlib.Path('docs'); d.mkdir(exist_ok=True); "
                "(d/'ISSUE_AGENT_CANARY.md').write_text('canary\\n')"
            )
            result = subprocess.run(
                ["python3", "-c", program, argv[-1]],
                cwd=cwd,
                input=stdin,
                capture_output=True,
                timeout=timeout,
                check=False,
            )
            assert result.returncode == 0, result.stderr
            return result.returncode

    isolation = RealSubprocessIsolation()
    outcome = execute(repos, ledger, envelope, isolation)
    assert outcome.advisory_code is None and outcome.sealed_result is not None
    assert isolation.calls[0]["stdin"] == b""
    assert Path(isolation.calls[0]["argv"][-1]).read_bytes() == PROMPT.encode()
    assert isolation.stopped == 1


def test_opencode_receives_signed_prompt_via_private_attachment_not_argv(repos: dict[str, Any]) -> None:
    ledger, envelope = authorize(repos)
    isolation = RecordingIsolation(CANARY)
    outcome, call = run_opencode(repos, ledger, envelope, isolation)
    assert outcome.advisory_code is None and outcome.sealed_result is not None
    assert call["stdin"] == b""
    assert PROMPT not in " ".join(call["argv"])
    assert call["argv"][-2] == "--file"
    path = Path(call["argv"][-1])
    assert path.read_bytes() == PROMPT.encode()
    assert path.stat().st_mode & 0o777 == 0o600
    assert "Implement the signed task" in call["argv"][-3]
    assert "Do not merely explain a plan" in call["argv"][-3]
    assert "do not fabricate edits" in call["argv"][-3]


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


def test_collector_rejection_is_not_misreported_as_no_changes(
    repos: dict[str, Any], monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    ledger, envelope = authorize(repos)

    def reject(*args: Any, **kwargs: Any) -> bytes:
        raise roles.RoleRefused("UNRECOGNIZED_COLLECTOR_FAILURE", "sensitive untrusted detail")

    monkeypatch.setattr(roles, "collect_result", reject)
    isolation = RecordingIsolation("true")
    outcome = execute(repos, ledger, envelope, isolation)
    assert outcome.sealed_result is None
    assert outcome.advisory_code == "EXECUTOR_RESULT_REJECTED"
    assert "sensitive untrusted detail" not in capsys.readouterr().out
    assert isolation.stopped == 1


DIAGNOSTIC_LINE = re.compile(
    r"^executor workspace diagnostic: ignored_files=-?\d+ unreadable_paths=\d+ home_files=-?\d+ truncated=[01]$"
)


def _bare_workspace(tmp_path: Path) -> tuple[Path, Path, Path]:
    workspace, git_dir, home = tmp_path / "workspace", tmp_path / "trusted.git", tmp_path / "home"
    workspace.mkdir()
    home.mkdir()
    roles._git(git_dir, workspace, "init", "--quiet")
    return workspace, git_dir, home


def test_a_zero_change_run_reports_counts_only_and_stays_a_terminal_no_changes(
    repos: dict[str, Any], capsys: pytest.CaptureFixture[str]
) -> None:
    ledger, envelope = authorize(repos)
    # The stand-in model writes outside the workspace, under a name that must never be echoed.
    isolation = RecordingIsolation('echo x > "$HOME/leaked-name-1f3a"')
    outcome = execute(repos, ledger, envelope, isolation)
    assert (outcome.sealed_result, outcome.advisory_code) == (None, "NO_CHANGES")
    out = capsys.readouterr().out
    lines = [line for line in out.splitlines() if line.startswith("executor workspace diagnostic")]
    assert len(lines) == 1 and DIAGNOSTIC_LINE.fullmatch(lines[0])
    assert "home_files=1" in lines[0] and "ignored_files=0" in lines[0] and "unreadable_paths=0" in lines[0]
    assert "leaked-name-1f3a" not in out and SECRET not in out


def test_a_collected_change_emits_no_workspace_diagnostic(
    repos: dict[str, Any], capsys: pytest.CaptureFixture[str]
) -> None:
    ledger, envelope = authorize(repos)
    outcome = execute(repos, ledger, envelope, RecordingIsolation(CANARY))
    assert outcome.advisory_code is None and outcome.sealed_result
    assert "executor workspace diagnostic" not in capsys.readouterr().out


def test_an_ignored_edit_is_distinguished_from_an_edit_never_made(tmp_path: Path) -> None:
    workspace, git_dir, home = _bare_workspace(tmp_path)
    assert roles.workspace_diagnostics(workspace, git_dir, home) == {
        "ignored_files": 0,
        "unreadable_paths": 0,
        "home_files": 0,
        "truncated": 0,
    }
    (workspace / ".gitignore").write_text("*.log\n")
    (workspace / "edited.log").write_text("model output\n")
    assert roles.workspace_diagnostics(workspace, git_dir, home)["ignored_files"] == 1


def test_isolated_home_state_is_not_counted_as_a_stray_write(tmp_path: Path) -> None:
    workspace, git_dir, home = _bare_workspace(tmp_path)
    for state_dir in (".config", ".cache", ".local/share"):
        (home / state_dir).mkdir(parents=True)
        (home / state_dir / "state.json").write_text("{}")
    (home / "hunter-task.txt").write_text("signed prompt")
    assert roles.workspace_diagnostics(workspace, git_dir, home)["home_files"] == 0
    (home / "stray.txt").write_text("x")
    assert roles.workspace_diagnostics(workspace, git_dir, home)["home_files"] == 1


@pytest.mark.skipif(os.geteuid() == 0, reason="root can read everything")
def test_files_the_collector_cannot_read_are_counted(tmp_path: Path) -> None:
    workspace, git_dir, home = _bare_workspace(tmp_path)
    (workspace / "private.txt").write_text("x")
    (workspace / "private.txt").chmod(0)
    (workspace / "closed").mkdir()
    (workspace / "closed" / "inner.txt").write_text("x")
    (workspace / "closed").chmod(0)
    try:
        assert roles.workspace_diagnostics(workspace, git_dir, home)["unreadable_paths"] >= 2
    finally:
        (workspace / "closed").chmod(0o700)


def test_an_unavailable_git_view_is_reported_as_unknown_never_as_zero(tmp_path: Path) -> None:
    workspace, _, home = tmp_path / "workspace", tmp_path / "x", tmp_path / "home"
    workspace.mkdir()
    home.mkdir()
    assert roles.workspace_diagnostics(workspace, tmp_path / "not-a-git-dir", home)["ignored_files"] == -1


# --- the delivered task contract and the model's write attempts (Issue #577, run 40413003807) -------------------


def _real_contract_prompt(issue_text: str = "mission text that must never be logged") -> bytes:
    """The prompt as the Smart Prompt Machine renders it: the real specification and the real OBJECTIVE nesting.

    ``OBJECTIVE`` is ``{"untrusted_user_task": <JSON string>}`` whose inner document carries the DPM context and the
    Issue text under separate keys (``issue_agent_task_text`` + ``smart_prompt_machine.compile``).
    """

    from hunter.evidence_intelligence.pre_model import EvidenceExtractionIntent, _render_prompt
    from hunter.evidence_intelligence.smart_prompt_routing import ENGINEERING_IMPLEMENT_PROFILE as profile

    inner = json.dumps(
        {
            "governed_prevention_context": {
                "scope_allowed_paths": ["src/a.py", "tests/test_a.py", "tests/test_b.py"],
                "scope_prohibited_paths": [".github/", "config/"],
            },
            "untrusted_task": issue_text,
        }
    )
    intent = EvidenceExtractionIntent(
        task_type=profile.task_type,
        objective=json.dumps({"untrusted_user_task": inner}),
        workflow_stage=profile.workflow_stage,
        target_id="github-issue:o/r#1",
        output_contract_id=profile.output_contract_id,
        output_contract_version=profile.output_contract_version,
        context_policy_id=profile.context_policy_id,
        replay_mode="CURRENT",
        historical_cutoff=None,
    )
    rendered = _render_prompt(intent=intent, specification=profile.specification, spans=(), missingness_reason_codes=())
    return rendered.encode()


def test_the_delivered_contract_is_described_by_structure_never_by_content() -> None:
    summary = roles.prompt_contract_summary(_real_contract_prompt())
    assert summary["sections"] == 7  # SYSTEM, TASK, OBJECTIVE, AUTHORITY, OUTPUT_CONTRACT, CONTEXT, MISSINGNESS
    assert summary["output_contract"] == "workspace_edits"
    assert summary["extraction_proposal_authority"] == 1
    assert (summary["scope_allowed_paths"], summary["scope_prohibited_paths"]) == (3, 2)
    line = " ".join(f"{name}={value}" for name, value in summary.items())
    for forbidden in ("mission text", "src/a.py", ".github", "Execute exactly"):
        assert forbidden not in line


def test_scope_counts_come_from_the_dpm_context_and_cannot_be_spoofed_by_issue_text() -> None:
    spoof = '{"scope_allowed_paths": ["/", "/etc"], "scope_prohibited_paths": []}'
    summary = roles.prompt_contract_summary(_real_contract_prompt(issue_text=spoof))
    assert (summary["scope_allowed_paths"], summary["scope_prohibited_paths"]) == (3, 2)


@pytest.mark.parametrize(
    ("prompt", "expected"),
    [
        (b"", {"sections": 0, "output_contract": "absent", "scope_allowed_paths": -1, "scope_prohibited_paths": -1}),
        (b"OUTPUT_CONTRACT\nfree text\n", {"output_contract": "other", "extraction_proposal_authority": 0}),
        (b'"scope_allowed_paths": [1, 2]', {"scope_allowed_paths": -1}),
        (b'"scope_allowed_paths": [broken', {"scope_allowed_paths": -1}),
    ],
)
def test_an_unrecognised_contract_is_unknown_never_assumed(prompt: bytes, expected: dict[str, Any]) -> None:
    summary = roles.prompt_contract_summary(prompt)
    assert {key: summary[key] for key in expected} == expected


# --- the output-contract / editing-directive boundary (Issue #577) ---------------------------------------------
#
# The Smart Prompt Machine's engineering-implement prompt is a JSON-object proposal contract with
# "ExtractionProposal" authority, while the executor's only addition asks for repository edits. Collection reads
# the workspace diff and ignores everything the model prints, so the diff is the sole deliverable. These tests pin
# that boundary deterministically: reconciling the two sides must be a deliberate change, not drift.


def test_the_trusted_contract_and_the_editing_directive_agree() -> None:
    summary = roles.prompt_contract_summary(_real_contract_prompt())
    assert summary["output_contract"] == "workspace_edits"
    assert summary["extraction_proposal_authority"] == 1  # the ceiling stays: edits promote nothing canonical
    assert "editing actual repository files" in roles.EDIT_DIRECTIVE
    assert "do not fabricate edits" in roles.EDIT_DIRECTIVE
    assert "edit the repository files" in WORKSPACE_EDIT_OUTPUT_CONTRACT


def test_every_profile_the_executor_serves_names_edits_as_its_deliverable() -> None:
    from hunter.automation.issue_agent_execution import ISSUE_AGENT_PROFILE_REGISTRY

    for declared in (ENGINEERING_IMPLEMENT_PROFILE, ENGINEERING_REVIEW_FIX_PROFILE):
        served = ISSUE_AGENT_PROFILE_REGISTRY.resolve(declared.profile_id, declared.version)
        assert served.specification.output_contract == WORKSPACE_EDIT_OUTPUT_CONTRACT
        assert served.output_contract_version == WORKSPACE_EDIT_OUTPUT_CONTRACT_VERSION


def test_a_printed_json_object_contract_is_detected_as_contradicting_the_editing_directive() -> None:
    legacy = _real_contract_prompt().replace(WORKSPACE_EDIT_OUTPUT_CONTRACT.encode(), b'{"type":"object"}')
    assert roles.prompt_contract_summary(legacy)["output_contract"] == "json_object"
    assert roles.prompt_contract_summary(b"OUTPUT_CONTRACT\nsomething else\n")["output_contract"] == "other"


def test_the_directive_is_the_only_instruction_added_to_the_signed_prompt(repos: dict[str, Any]) -> None:
    ledger, envelope = authorize(repos)
    isolation = RecordingIsolation(CANARY)
    execute(repos, ledger, envelope, isolation)
    argv = isolation.calls[0]["argv"]
    assert argv[:3] == ("opencode", "--pure", "run")
    assert argv[3] == "--dir" and argv[4] == isolation.calls[0]["public"]["PWD"]
    assert argv[5] == roles.EDIT_DIRECTIVE and argv[6] == "--file"
    assert len(argv) == 8 and Path(argv[7]).read_bytes() == PROMPT.encode()


# --- the OpenCode workspace and permission contract (Issue #574) ------------------------------------------------
#
# OpenCode must execute inside the isolated workspace it is handed, and the trusted permission contract must be the
# one OpenCode resolves. Repository files are untrusted candidate content, so they must not be able to widen that
# contract. These tests pin the argv/env contract deterministically: changing it has to be a deliberate change.


def test_opencode_runs_in_the_isolated_workspace_it_is_given(repos: dict[str, Any]) -> None:
    ledger, envelope = authorize(repos)
    outcome, call = run_opencode(repos, ledger, envelope, RecordingIsolation(CANARY))
    assert outcome.sealed_result is not None
    workspace = Path(call["cwd"])
    assert call["argv"][:3] == ("/opt/hunter/bin/opencode", "--pure", "run")
    assert call["argv"][3:5] == ("--dir", str(workspace))
    assert call["public"]["PWD"] == str(workspace)
    assert workspace.name == "workspace" and workspace.is_dir()
    # The workspace and the credential home stay distinct: a model edit can never reach the home state.
    assert Path(call["public"]["HOME"]) != workspace
    assert "OLDPWD" not in call["public"]


def test_the_opencode_permission_contract_allows_only_workspace_operations(repos: dict[str, Any]) -> None:
    ledger, envelope = authorize(repos)
    _, call = run_opencode(repos, ledger, envelope, RecordingIsolation(CANARY))
    permission = opencode_inline_config(call)["permission"]
    assert permission["*"] == "deny"
    assert permission["external_directory"] == "deny"
    for allowed in ("read", "edit", "glob", "grep", "lsp"):
        assert permission[allowed] == "allow"
    for denied in ("webfetch", "websearch", "task", "skill", "question"):
        assert permission.get(denied, "deny") == "deny"
    # The shipped `build` agent's rules take precedence over global ones, so the same contract is repeated there.
    assert opencode_inline_config(call)["agent"]["build"]["permission"] == permission


def test_repository_files_cannot_override_the_trusted_permission_contract(repos: dict[str, Any]) -> None:
    ledger, envelope = authorize(repos)
    # A workspace config that would re-enable everything if it were read.
    plant = 'printf %s \'{"permission":{"*":"allow","external_directory":"allow"}}\' > opencode.json'
    _, call = run_opencode(repos, ledger, envelope, RecordingIsolation(plant))
    assert call["public"]["OPENCODE_DISABLE_PROJECT_CONFIG"] == "1"
    assert "--pure" in call["argv"]
    permission = opencode_inline_config(call)["permission"]
    assert permission["*"] == "deny" and permission["external_directory"] == "deny"


def test_the_model_shell_is_a_trusted_deny_all_executable_outside_the_workspace(repos: dict[str, Any]) -> None:
    ledger, envelope = authorize(repos)
    _, call = run_opencode(repos, ledger, envelope, RecordingIsolation(CANARY))
    config = opencode_inline_config(call)
    shell = Path(config["shell"])
    workspace = Path(call["cwd"])
    assert call["public"]["SHELL"] == str(shell)
    assert shell.is_file() and os.access(shell, os.X_OK)
    assert workspace not in shell.parents and shell.stat().st_mode & 0o222 == 0
    # Invoked exactly as OpenCode invokes a non-bash/zsh shell: ``<shell> -c <command>``.
    completed = subprocess.run(
        [str(shell), "-c", "echo should-not-run"], cwd=workspace, capture_output=True, text=True, check=False
    )
    assert completed.returncode == 126
    assert "should-not-run" not in completed.stdout
    assert "forbids shell execution" in completed.stderr


def test_an_opencode_run_that_exits_zero_without_authorized_edits_is_not_success(repos: dict[str, Any]) -> None:
    ledger, envelope = authorize(repos)
    outcome, call = run_opencode(repos, ledger, envelope, RecordingIsolation("true"))
    assert (outcome.sealed_result, outcome.advisory_code) == (None, "NO_CHANGES")
    assert call["stdin"] == b""


def test_the_opencode_contract_keeps_the_model_key_out_of_the_public_environment_and_result(
    repos: dict[str, Any],
) -> None:
    ledger, envelope = authorize(repos)
    outcome, call = run_opencode(repos, ledger, envelope, RecordingIsolation(CANARY))
    assert call["secret"] == {"GROQ_API_KEY": SECRET}
    assert SECRET not in json.dumps(call["public"])
    assert SECRET not in json.dumps(opencode_inline_config(call))
    assert outcome.sealed_result is not None and SECRET.encode() not in outcome.sealed_result


def test_the_opencode_contract_does_not_bypass_task_scope_or_result_binding(repos: dict[str, Any]) -> None:
    from hunter.automation.issue_agent_transport import header

    ledger, envelope = authorize(repos)
    out_of_scope = "mkdir -p .github/workflows && echo x > .github/workflows/evil.yml"
    outcome, _ = run_opencode(repos, ledger, envelope, RecordingIsolation(out_of_scope))
    # The executor collects the diff; the trusted validator still owns TaskScope enforcement.
    assert outcome.sealed_result is not None
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
    with pytest.raises(roles.RoleRefused, match="EXECUTOR_RESULT_REJECTED"):
        validate(repos, ledger, outcome.sealed_result)


def test_an_executable_task_is_collected_when_the_model_edits_a_tracked_file(repos: dict[str, Any]) -> None:
    ledger, envelope = authorize(repos)
    outcome = execute(repos, ledger, envelope, RecordingIsolation('echo "- documented" >> README.md'))
    assert outcome.advisory_code is None and outcome.sealed_result


def test_a_non_actionable_task_answered_as_a_json_proposal_is_never_collected(
    repos: dict[str, Any], capsys: pytest.CaptureFixture[str]
) -> None:
    ledger, envelope = authorize(repos)
    # A model that obeys the trusted OUTPUT_CONTRACT prints a JSON object and edits nothing.
    isolation = RecordingIsolation('echo \'{"proposal": "no change is required", "files": []}\'')
    outcome = execute(repos, ledger, envelope, isolation)
    assert (outcome.sealed_result, outcome.advisory_code) == (None, "NO_CHANGES")
    assert len(isolation.calls) == 1 and isolation.stopped == 1


def test_printed_output_alone_never_turns_a_zero_change_run_into_success(repos: dict[str, Any]) -> None:
    ledger, envelope = authorize(repos)
    noisy = RecordingIsolation("i=0; while [ $i -lt 50 ]; do echo '{\"edited\": true}'; i=$((i+1)); done")
    outcome = execute(repos, ledger, envelope, noisy)
    assert (outcome.sealed_result, outcome.advisory_code) == (None, "NO_CHANGES")


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


# --- production isolation port (S0 A-6) --------------------------------------------------------------------


class _Launch:
    def __init__(self) -> None:
        self.calls: list[tuple[tuple[str, ...], dict[str, Any]]] = []
        self.returncode = 7

    def __call__(self, argv: Sequence[str], **kwargs: Any) -> Any:
        self.calls.append((tuple(argv), kwargs))
        return subprocess.CompletedProcess(argv, self.returncode, b"", b"")


@pytest.fixture
def sudo(monkeypatch: pytest.MonkeyPatch) -> tuple[roles.SudoIsolation, _Launch]:
    monkeypatch.setattr(core, "require_isolation_user", lambda user: user)
    launch = _Launch()
    monkeypatch.setattr(roles.subprocess, "run", launch)
    monkeypatch.setattr(core.subprocess, "run", launch)
    return roles.SudoIsolation("hunter-untrusted"), launch


def test_the_model_secret_crosses_the_uid_boundary_only_through_the_launcher_environment(
    sudo: tuple[roles.SudoIsolation, _Launch], tmp_path: Path
) -> None:
    isolation, launch = sudo
    code = isolation.run(
        ("opencode", "run", "--model", "m"),
        cwd=tmp_path,
        public_env={"LANG": "C.UTF-8"},
        secret_env={"GROQ_API_KEY": SECRET},
        stdin=b"prompt",
        timeout=12.0,
    )
    assert code == 7
    ((argv, kwargs),) = launch.calls
    assert argv == (
        "sudo", "-n", "--preserve-env=GROQ_API_KEY", "-u", "hunter-untrusted", "--", "/usr/bin/env",
        "LANG=C.UTF-8", "opencode", "run", "--model", "m",
    )  # fmt: skip
    assert SECRET not in " ".join(argv)
    assert kwargs["env"] == {**core.SUDO_ENVIRONMENT, "GROQ_API_KEY": SECRET}
    assert kwargs["input"] == b"prompt" and kwargs["timeout"] == 12.0 and kwargs["cwd"] == tmp_path
    assert kwargs["stdout"] is not subprocess.DEVNULL and kwargs["stderr"] is not subprocess.DEVNULL


def test_model_budget_is_bounded_below_hosted_job_timeout() -> None:
    assert roles.MODEL_TIMEOUT_SECONDS == 12 * 60
    assert roles.MODEL_TIMEOUT_SECONDS < 20 * 60


def test_a_secret_free_launch_inherits_nothing(sudo: tuple[roles.SudoIsolation, _Launch], tmp_path: Path) -> None:
    isolation, launch = sudo
    isolation.run(("true",), cwd=tmp_path, public_env={"LANG": "C"}, secret_env={}, stdin=b"", timeout=1.0)
    ((argv, kwargs),) = launch.calls
    assert argv == ("sudo", "-n", "-u", "hunter-untrusted", "--", "/usr/bin/env", "-i", "LANG=C", "true")
    assert kwargs["env"] == dict(core.SUDO_ENVIRONMENT)


def test_isolation_hands_over_the_workspace_and_kills_every_leftover(
    sudo: tuple[roles.SudoIsolation, _Launch], tmp_path: Path
) -> None:
    isolation, launch = sudo
    launch.returncode = 0
    isolation.prepare(tmp_path / "workspace")
    isolation.stop()
    assert [argv for argv, _ in launch.calls] == [
        ("sudo", "-n", "chown", "-R", "hunter-untrusted", str(tmp_path / "workspace")),
        ("sudo", "-n", "pkill", "-KILL", "-u", "hunter-untrusted"),
    ]


@pytest.mark.parametrize("user", ["root", "Hunter Untrusted", "", "../x"])
def test_isolation_refuses_root_or_an_invalid_user(user: str) -> None:
    with pytest.raises(core.ReplacementExecutorError):
        roles.SudoIsolation(user)


def test_isolation_diagnostic_classifies_without_leaking_untrusted_output(
    sudo: tuple[roles.SudoIsolation, _Launch], tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    isolation, launch = sudo
    private_text = "SENSITIVE_PROMPT_AND_KEY"

    def run_with_private_output(argv: Sequence[str], **kwargs: Any) -> Any:
        kwargs["stderr"].write(f"permission denied {private_text}".encode())
        kwargs["stdout"].write(private_text.encode())
        return subprocess.CompletedProcess(argv, 0)

    launch.calls.clear()
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(roles.subprocess, "run", run_with_private_output)
        assert isolation.run(("opencode", "run"), cwd=tmp_path, public_env={}, secret_env={}, stdin=b"", timeout=1) == 0
    diagnostic = capsys.readouterr().out
    assert "stderr_category=PERMISSION" in diagnostic
    assert "exit=ZERO" in diagnostic
    assert "stdout_present=True" in diagnostic
    assert private_text not in diagnostic


def test_isolation_diagnostic_detects_opencode_provider_error_without_leaking(
    sudo: tuple[roles.SudoIsolation, _Launch], tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    isolation, _ = sudo
    private_text = "PRIVATE_PROVIDER_DETAIL_NEVER_LOG"

    def fake_run(argv: Sequence[str], **kwargs: Any) -> Any:
        kwargs["stderr"].write(
            (
                '{"type":"error","error":{"name":"ProviderModelNotFoundError",' '"message":"' + private_text + '"}}\n'
            ).encode()
        )
        return subprocess.CompletedProcess(argv, 0)

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(roles.subprocess, "run", fake_run)
        isolation.run(("opencode", "run"), cwd=tmp_path, public_env={}, secret_env={}, stdin=b"", timeout=1)
    diagnostic = capsys.readouterr().out
    assert "stderr_category=MODEL_NOT_FOUND" in diagnostic
    assert private_text not in diagnostic


def test_isolation_observes_private_output_growth_without_disclosing_content(
    sudo: tuple[roles.SudoIsolation, _Launch], tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    import time

    isolation, _ = sudo
    private = b"SECRET_MODEL_STREAM_NOT_FOR_LOGS"

    def fake_run(argv: Sequence[str], **kwargs: Any) -> Any:
        kwargs["stdout"].write(private)
        kwargs["stdout"].flush()
        time.sleep(5.2)
        return subprocess.CompletedProcess(argv, 0)

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(roles.subprocess, "run", fake_run)
        assert (
            isolation.run(("opencode", "run"), cwd=tmp_path, public_env={}, secret_env={}, stdin=b"", timeout=10) == 0
        )
    output = capsys.readouterr().out
    assert "first_output_observed=True" in output
    assert "output_advanced=True" in output
    assert private.decode() not in output


def test_isolation_timeout_reports_safe_output_presence_without_leaking(
    sudo: tuple[roles.SudoIsolation, _Launch], tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    isolation, _ = sudo
    private = "PRIVATE_PROMPT_TOKEN_NOT_FOR_LOGS"

    def timed_out(argv: Sequence[str], **kwargs: Any) -> Any:
        kwargs["stdout"].write(private.encode())
        kwargs["stderr"].write(private.encode())
        raise subprocess.TimeoutExpired(argv, kwargs["timeout"])

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(roles.subprocess, "run", timed_out)
        with pytest.raises(subprocess.TimeoutExpired):
            isolation.run(("opencode", "run"), cwd=tmp_path, public_env={}, secret_env={}, stdin=b"", timeout=1)
    diagnostic = capsys.readouterr().out
    assert "isolated model launch: started" in diagnostic
    assert "exit=TIMEOUT stdout_present=True stderr_present=True" in diagnostic
    assert private not in diagnostic


# --- hosted diagnostic parity and closed-vocabulary failure classification (Issue #574) -------------------------
#
# A hosted diagnostic exit is only evidence about production if it launches the production contract. These tests pin
# both halves: the diagnostic's launch is the executor's launch, and an unexplained provider failure is classified from
# a closed vocabulary instead of being reported as UNCLASSIFIED while arbitrary stderr stays private.


def test_the_hosted_diagnostic_probe_launches_the_executor_contract(repos: dict[str, Any]) -> None:
    ledger, envelope = authorize(repos)
    _, call = run_opencode(repos, ledger, envelope, RecordingIsolation(CANARY))
    home, workspace = Path(call["public"]["HOME"]), Path(call["cwd"])
    probe = diagnostic_probe.probe_invocation(
        opencode=Path(call["argv"][0]), model="opencode/big-pickle", home=home, workspace=workspace
    )
    # Same argv, same environment: the hosted diagnostic cannot describe a different runtime than production.
    assert probe.argv == call["argv"]
    assert probe.public_env == call["public"]
    assert probe.stdin == call["stdin"] == b""
    assert diagnostic_probe.contract_telemetry(probe, workspace) == {
        "pure": True,
        "workspace_bound": True,
        "workspace_only_tools": True,
        "shell_denied": True,
        "project_config_disabled": True,
    }


@pytest.mark.parametrize(
    ("stderr", "expected"),
    [
        ("ProviderModelNotFoundError: model not available", "MODEL_NOT_FOUND"),
        ('{"name":"AI_APICallError","message":"upstream failed"}', "PROVIDER_CALL_FAILED"),
        ("429 Too Many Requests", "RATE_LIMIT"),
        ("insufficient_quota: check your plan", "QUOTA_EXCEEDED"),
        ("401 Unauthorized: invalid api key", "AUTHENTICATION"),
        ("connect ECONNREFUSED 127.0.0.1:443", "NETWORK"),
        ("503 Service Unavailable", "PROVIDER_UNAVAILABLE"),
        ("provider is overloaded, retry later", "PROVIDER_OVERLOADED"),
        ("permission denied for /tmp/x", "PERMISSION"),
        ("something nobody has seen before", "UNCLASSIFIED"),
    ],
)
def test_the_stderr_vocabulary_classifies_known_provider_failures(stderr: str, expected: str) -> None:
    assert roles.stderr_category(stderr.encode()) == expected


def test_the_stderr_vocabulary_is_closed_and_never_echoes_the_sample() -> None:
    vocabulary = {code for _, code in roles._STDERR_CATEGORIES} | {"UNCLASSIFIED"}
    sample = f"AI_APICallError {SECRET} {PROMPT}".encode()
    category = roles.stderr_category(sample)
    assert category in vocabulary
    assert SECRET not in category and PROMPT not in category


def test_a_classified_provider_failure_reaches_the_hosted_diagnostic_line(
    sudo: tuple[roles.SudoIsolation, _Launch], tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    isolation, launch = sudo
    private = "PRIVATE_CREDENTIAL_SHAPED_TEXT"

    def failing(argv: Sequence[str], **kwargs: Any) -> Any:
        kwargs["stderr"].write(f"AI_APICallError: {private}".encode())
        return subprocess.CompletedProcess(argv, 1)

    launch.calls.clear()
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(roles.subprocess, "run", failing)
        assert isolation.run(("opencode", "run"), cwd=tmp_path, public_env={}, secret_env={}, stdin=b"", timeout=1) == 1
    diagnostic = capsys.readouterr().out
    assert "exit=NONZERO stderr_category=PROVIDER_CALL_FAILED" in diagnostic
    assert private not in diagnostic


def test_roles_git_rejects_command_argument_injection(tmp_path: Path) -> None:
    git_dir = tmp_path / "trusted.git"
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    for dangerous in ("--upload-pack=/bin/sh", "--receive-pack=/bin/sh", "--exec-path=/tmp"):
        with pytest.raises(roles.RoleRefused) as exc_info:
            roles._git(git_dir, workspace, dangerous)
        assert exc_info.value.code == "EXECUTION_NOT_COMPLETED"

    with pytest.raises(roles.RoleRefused) as exc_info:
        roles._git(git_dir, workspace, "status\0evil")
    assert exc_info.value.code == "EXECUTION_NOT_COMPLETED"

    with pytest.raises(roles.RoleRefused) as exc_info:
        roles._git(tmp_path / "-git", workspace, "status")
    assert exc_info.value.code == "EXECUTION_NOT_COMPLETED"

    with pytest.raises(roles.RoleRefused) as exc_info:
        roles._git(git_dir, tmp_path / "-ws", "status")
    assert exc_info.value.code == "EXECUTION_NOT_COMPLETED"


def test_roles_collect_result_rejects_invalid_base_sha(tmp_path: Path) -> None:
    workspace, git_dir = tmp_path / "workspace", tmp_path / "git"
    workspace.mkdir()
    git_dir.mkdir()

    for bad_sha in ("not-a-sha", "--upload-pack", "0" * 39, "0" * 41, "-c"):
        with pytest.raises(roles.RoleRefused) as exc_info:
            roles.collect_result(workspace, git_dir, authorization_id="auth", branch="b", base_sha=bad_sha)
        assert exc_info.value.code == "BASE_NOT_ON_MAIN"


def test_roles_materialize_base_rejects_unsafe_remote_and_branch(tmp_path: Path) -> None:
    workspace, git_dir = tmp_path / "workspace", tmp_path / "git"

    with pytest.raises(roles.RoleRefused) as exc_info:
        roles.materialize_base(workspace, git_dir, remote="--upload-pack=evil", base_sha="0" * 40)
    assert exc_info.value.code == "CONFIGURATION_INVALID"

    with pytest.raises(roles.RoleRefused) as exc_info:
        roles.materialize_base(
            workspace, git_dir, remote="https://github.com/o/r.git", base_sha="0" * 40, remediation_branch="-b"
        )
    assert exc_info.value.code == "CONFIGURATION_INVALID"

    with pytest.raises(roles.RoleRefused) as exc_info:
        roles.materialize_base(
            workspace, git_dir, remote="https://github.com/o/r.git", base_sha="0" * 40, remediation_branch="../main"
        )
    assert exc_info.value.code == "CONFIGURATION_INVALID"
