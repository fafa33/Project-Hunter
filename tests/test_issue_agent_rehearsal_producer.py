"""ADR 0037 S6 / Issue #560: the canonical rehearsal producer and its adversarial boundary."""

from __future__ import annotations

import base64
import json
import subprocess
from pathlib import Path

import hunter_issue_agent_rehearsal_producer as producer
import hunter_issue_agent_replacement_rehearsal as rehearsal
import hunter_issue_agent_trigger as trigger
import pytest
import yaml
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from hunter.automation.issue_agent_control import ControlRefused
from hunter.automation.issue_agent_execution import (
    IssueAgentAuthorizationError,
    IssueAgentAuthorizationVerifier,
    IssueAgentIssuerError,
    SignedIssueAgentAuthorization,
    verify_signed_authorization,
)
from hunter.automation.issue_agent_replacement_executor import (
    ReplacementExecutorError,
    validate_replacement_result,
)

ROOT = Path(__file__).resolve().parents[1]
KEY = Ed25519PrivateKey.from_private_bytes(bytes.fromhex("22" * 32))
KEY_HEX = "22" * 32
PUBLIC = KEY.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw).hex()


def _git(path: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(path), *args], capture_output=True, text=True, check=True).stdout.strip()


@pytest.fixture()
def checkout(tmp_path: Path) -> tuple[Path, str]:
    document = json.loads((ROOT / "config" / "issue_agent_trust_roots.json").read_text(encoding="utf-8"))
    document["authorization_verifying_key"] = PUBLIC
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "issue_agent_trust_roots.json").write_text(json.dumps(document), encoding="utf-8")
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "-c", "user.name=t", "-c", "user.email=t@t", "add", "-A")
    _git(tmp_path, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "base")
    return tmp_path, _git(tmp_path, "rev-parse", "HEAD")


def _env(head: str, **override: str) -> dict[str, str]:
    base = {
        "GITHUB_REPOSITORY": "fafa33/Project-Hunter",
        "GITHUB_REF": "refs/heads/main",
        "GITHUB_ACTOR": "fafa33",
        "GITHUB_RUN_ATTEMPT": "1",
        "GITHUB_SHA": head,
        trigger.SIGNING_KEY_ENV: KEY_HEX,
    }
    base.update(override)
    return {k: v for k, v in base.items() if v is not None}


def test_pair_is_canonical_signed_bound_and_validates_as_rehearsal(checkout) -> None:
    path, head = checkout
    document, result = producer.produce(_env(head), checkout=path)
    signed = SignedIssueAgentAuthorization.from_json(document)
    IssueAgentAuthorizationVerifier.from_environment(
        environ={"HUNTER_ISSUE_AGENT_AUTHORIZATION_VERIFYING_KEY": PUBLIC}
    ).verify(signed)
    assert signed.implementation_scope.base_sha == head
    assert signed.implementation_scope.allowed_paths == (producer.REHEARSAL_RESULT_PATH,)
    validated = validate_replacement_result(result, signed_authorization=signed, rehearsal=True)
    assert validated.rehearsal is True and validated.branch.startswith("issue-560-")
    assert [f.path for f in validated.files] == [producer.REHEARSAL_RESULT_PATH]
    assert KEY_HEX.encode() not in document + result


def test_repeated_dispatch_is_byte_identical(checkout) -> None:
    path, head = checkout
    assert producer.produce(_env(head), checkout=path) == producer.produce(_env(head), checkout=path)


def test_cli_writes_pair_and_never_prints_key(checkout, tmp_path, monkeypatch, capsys) -> None:
    path, head = checkout
    for name in ("GITHUB_TOKEN", "GH_TOKEN"):
        monkeypatch.delenv(name, raising=False)
    for name, value in _env(head).items():
        monkeypatch.setenv(name, value)
    output = tmp_path / "gh_output"
    monkeypatch.setenv("GITHUB_OUTPUT", str(output))
    assert producer.main(["--checkout", str(path), "--out-dir", str(tmp_path / "out")]) == 0
    captured = capsys.readouterr()
    assert KEY_HEX not in captured.out + captured.err + output.read_text(encoding="utf-8")
    lines = dict(line.split("=", 1) for line in output.read_text(encoding="utf-8").splitlines())
    assert base64.b64decode(lines["authorization_b64"]) == (tmp_path / "out" / "authorization.json").read_bytes()
    assert base64.b64decode(lines["result_b64"]) == (tmp_path / "out" / "result.json").read_bytes()


@pytest.mark.parametrize(
    "override",
    [
        {"GITHUB_REF": "refs/heads/feature"},
        {"GITHUB_ACTOR": "mallory"},
        {"GITHUB_RUN_ATTEMPT": "2"},
        {"GITHUB_REPOSITORY": "other/repo"},
        {"GITHUB_SHA": "f" * 40},  # not the checked-out head: base mismatch
        {"GITHUB_SHA": "main"},
        {"GITHUB_TOKEN": "t"},  # publication authority
        {"GH_TOKEN": "t"},
        {"ANTHROPIC_API_KEY": "k"},  # model authority
        {trigger.SIGNING_KEY_ENV: None},  # missing key
        {trigger.SIGNING_KEY_ENV: "zz"},
        {trigger.SIGNING_KEY_ENV: "33" * 32},  # key does not match the pinned root
    ],
)
def test_producer_fails_closed(checkout, override) -> None:
    path, head = checkout
    with pytest.raises(Exception) as caught:
        producer.produce(_env(head, **override), checkout=path)
    assert KEY_HEX not in str(caught.value)


def test_unprovisioned_trust_roots_fail_closed(tmp_path) -> None:
    with pytest.raises(ControlRefused):
        producer.produce(_env("a" * 40), checkout=tmp_path)


def _pair(checkout):
    path, head = checkout
    document, result = producer.produce(_env(head), checkout=path)
    return path, SignedIssueAgentAuthorization.from_json(document), document, result


def test_tampered_signature_scope_or_result_is_refused(checkout) -> None:
    path, signed, document, result = _pair(checkout)
    verifier = rehearsal.pinned_verifier(path, {})
    verifier.verify(signed)
    forged = json.loads(document)
    forged["implementation_scope"]["allowed_paths"] = ["src/"]
    with pytest.raises(IssueAgentIssuerError):
        verifier.verify(SignedIssueAgentAuthorization.from_json(json.dumps(forged)))
    payload = json.loads(result)
    payload["base_sha"] = "e" * 40
    with pytest.raises(ReplacementExecutorError, match="base_sha"):
        validate_replacement_result(json.dumps(payload), signed_authorization=signed, rehearsal=True)
    payload = json.loads(result)
    payload["files"][0]["path"] = "src/hunter/evil.py"
    with pytest.raises(ReplacementExecutorError, match="outside signed TaskScope"):
        validate_replacement_result(json.dumps(payload), signed_authorization=signed, rehearsal=True)
    payload = json.loads(result)
    payload["schema_version"] = "hunter-issue-agent-replacement-result-v1"
    with pytest.raises(ReplacementExecutorError, match="execution class"):
        validate_replacement_result(json.dumps(payload), signed_authorization=signed, rehearsal=True)


def test_rehearsal_authorization_is_never_executable(checkout) -> None:
    path, signed, _document, _result = _pair(checkout)
    verifier = rehearsal.pinned_verifier(path, {})
    with pytest.raises(IssueAgentAuthorizationError, match="never executable"):
        verify_signed_authorization(
            signed, issuer_verifier=verifier, repository="fafa33/Project-Hunter", owner_login="fafa33"
        )


def test_rehearsal_refuses_a_non_rehearsal_identity_and_a_divergent_verifying_key(checkout) -> None:
    path, signed, _document, _result = _pair(checkout)
    with pytest.raises(IssueAgentAuthorizationError, match="does not match the repository-pinned root"):
        rehearsal.pinned_verifier(path, {"HUNTER_ISSUE_AGENT_AUTHORIZATION_VERIFYING_KEY": "00" * 32})
    assert rehearsal.pinned_verifier(path, {"HUNTER_ISSUE_AGENT_AUTHORIZATION_VERIFYING_KEY": PUBLIC})


def test_rehearsal_cli_end_to_end_and_rejects_replay_into_other_base(checkout, tmp_path, monkeypatch, capsys) -> None:
    path, signed, document, result = _pair(checkout)
    (tmp_path / "a.json").write_bytes(document)
    (tmp_path / "r.json").write_bytes(result)
    monkeypatch.setattr(
        "sys.argv",
        [
            "x",
            "--authorization",
            str(tmp_path / "a.json"),
            "--result",
            str(tmp_path / "r.json"),
            "--trust-roots-checkout",
            str(path),
        ],
    )
    for name in ("GITHUB_TOKEN", "GH_TOKEN", "HUNTER_ISSUE_AGENT_AUTHORIZATION_VERIFYING_KEY"):
        monkeypatch.delenv(name, raising=False)
    assert rehearsal.main() == 0
    assert "publication authority absent" in capsys.readouterr().out
    monkeypatch.setenv("GITHUB_TOKEN", "x")
    with pytest.raises(ReplacementExecutorError, match="publication authority"):
        rehearsal.main()


def _workflow(name: str) -> dict:
    return yaml.safe_load((ROOT / ".github" / "workflows" / name).read_text(encoding="utf-8"))


def test_producer_workflow_isolates_k_auth_and_has_no_publication_or_model_authority() -> None:
    workflow = _workflow("hunter-issue-agent-rehearsal-producer.yml")
    text = (ROOT / ".github" / "workflows" / "hunter-issue-agent-rehearsal-producer.yml").read_text(encoding="utf-8")
    mint, rehearse = workflow["jobs"]["mint"], workflow["jobs"]["rehearse"]
    assert mint["environment"] == "hunter-issue-agent-control" and "environment" not in rehearse
    assert mint["permissions"] == {"contents": "read"} and rehearse["permissions"] == {"contents": "read"}
    assert text.count("secrets.") == 1 and "HUNTER_ISSUE_AGENT_AUTHORIZATION_SIGNING_KEY" in text
    assert "secrets" not in rehearse and "secrets: inherit" not in text
    structure = json.dumps(workflow)  # comments are prose, not behaviour
    for forbidden in ("PUSH_TOKEN", "PR_TOKEN", "MODEL_API_KEY", "publish", "opencode", "create-pull"):
        assert forbidden not in structure
    assert "github.ref == 'refs/heads/main'" in mint["if"] and "github.repository_owner" in mint["if"]
    assert rehearse["uses"] == "./.github/workflows/hunter-issue-agent-replacement-rehearsal.yml"
