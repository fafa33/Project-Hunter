"""ADR 0041: the executor role across the primary and the single admitted fallback (adversarial and regression)."""

from __future__ import annotations

import base64
import hashlib
import json
import subprocess
from pathlib import Path
from typing import Any

import pytest
import test_issue_agent_roles as base
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
from test_issue_agent_mac_fallback import installed, provisioned_checkout
from test_issue_agent_roles import (
    AUTH,
    CANARY,
    HANDOFF_KEY,
    ISSUE,
    RESULT_KEY,
    RecordingIsolation,
    authorize,
    executor_config,
    handoff_bundle,
)

from hunter.automation import issue_agent_mac_fallback as mac
from hunter.automation import issue_agent_roles as roles
from hunter.automation import issue_agent_state as state
from hunter.automation.issue_agent_transport import TransportBinding, open_sealed, recipient_key_id, seal

repos = base.repos  # the real local remote and signed-ledger fixtures of the executor role tests
_prompt_keys = base._prompt_keys
FALLBACK_KEY = X25519PrivateKey.generate()
APPROVED_RUN = 200  # the owner-approved resume run that claims the fallback nonce (ADR 0041 F1)
NONCE = "ab" * 32


def write_record(ledger: Any, kind: str, evidence: dict[str, Any], role: str, run_id: int, job: str) -> None:
    """Append a non-transition record (resume protocol) to the roles-test signed ledger."""

    record = state.sign_record(
        {
            "schema_version": state.RECORD_SCHEMA_VERSION,
            "kind": kind,
            "record_seq": ledger.view.next_seq,
            "prev_record_sha256": ledger.view.head_record_digest,
            "recorded_at": "2026-10-04T12:00:00Z",
            "recorded_by": {
                "workflow_path": ".github/workflows/hunter-issue-agent-trigger.yml",
                "job": job,
                "role": role,
                "run_id": run_id,
                "run_attempt": 1,
                "head_sha": base.CONTROL,
            },
            "repository_id": 1,
            "issue_number": ISSUE,
            "authorization_id": AUTH,
            "state": state.FALLBACK_AUTHORIZED,
            "evidence": evidence,
        },
        base.KEY,
    )
    state.apply_record(ledger.view, record, trust=base.TRUST, provenance=base.trusted)
    ledger.head = ledger.store.append(ISSUE, ledger.head, record, ledger.view.index())


def approve(ledger: Any, run_id: int = APPROVED_RUN) -> None:
    request = {"stage": "fallback", "nonce": NONCE, "attempt": 1, "dispatched_at": "2026-10-04T12:00:00Z"}
    write_record(ledger, "resume_requested", request, "owner-decision", 100, "fallback-decision")
    write_record(ledger, "resume_bound", {"nonce": NONCE, "run_id": run_id}, "bind", run_id, "resume-bind")


WORKROOT_ID = "f" * 32


def offer_and_admit(repos: dict[str, Any], *, admit: bool = True, approved: bool = True) -> tuple[Any, bytes, bytes]:
    """A real signed ledger: AUTHORIZED with a fallback offer (second seal to X_FB), then FALLBACK_AUTHORIZED."""

    # The reference AUTHORIZED evidence and primary seal come from the canonical helper, run against a scratch clone
    # so the real ledger below carries exactly one AUTHORIZED record (the CAS would refuse a second one).
    scratch = {**repos, "tmp": repos["tmp"] / "scratch", "remote": repos["tmp"] / "scratch.git"}
    scratch["tmp"].mkdir(exist_ok=True)
    subprocess.run(["git", "clone", "--quiet", "--bare", str(repos["remote"]), str(scratch["remote"])], check=True)
    reference, primary = authorize(scratch)
    record = reference.view.authorizations[AUTH].evidence[state.AUTHORIZED]
    fallback_id = state.execution_identity(
        authorization_id=AUTH,
        authorize_run_id=100,
        control_sha=record["control_sha"],
        handoff_sha256=record["lineage"]["handoff_sha256"],
        execution_attempt=2,
    )
    binding = TransportBinding(
        payload_kind="handoff",
        repository_id=1,
        issue_number=ISSUE,
        authorization_id=AUTH,
        base_sha=record["base_sha"],
        task_scope_sha256=record["task_scope_sha256"],
        execution_id=fallback_id,
        handoff_sha256=record["lineage"]["handoff_sha256"],
        plaintext_sha256=record["lineage"]["handoff_sha256"],
        recipient_key_id=recipient_key_id(FALLBACK_KEY.public_key()),
    )
    second = seal(handoff_bundle(), recipient=FALLBACK_KEY.public_key(), binding=binding)
    evidence = dict(record)
    evidence["fallback"] = {
        "execution_id": fallback_id,
        "handoff_artifact": {
            "run_id": 100,
            "artifact_id": 12,
            "artifact_digest": "sha256:" + "e" * 64,
            "ciphertext_sha256": state.sha256_hex(second),
            "aad_sha256": binding.digest(),
            "recipient_key_id": recipient_key_id(FALLBACK_KEY.public_key()),
        },
    }
    fresh = base.Ledger(repos)
    fresh.write(state.AUTHORIZED, evidence, "authorize")
    if admit:
        fresh.write(
            state.FALLBACK_AUTHORIZED,
            {
                "primary_executor_job_id": 5,
                "primary_executor_conclusion": "success",
                "primary_cause": "RATE_LIMIT",
                "stderr_category": "RATE_LIMIT",
                "probe_category": "RATE_LIMIT",
                "primary_result_artifacts": 0,
                "fallback_executor": "codex",
                "fallback_execution_id": fallback_id,
            },
            "bind",
        )
        if approved:
            approve(fresh)
    return fresh, primary, second


def mac_port(tmp_path: Path, monkeypatch: Any, run: Any) -> mac.MacCodexIsolation:
    work = (tmp_path / "mac-work").resolve()
    monkeypatch.setattr(mac, "WORK_ROOT", work)
    monkeypatch.setattr(mac, "grant_agent_access", lambda path, cfg: None)
    monkeypatch.setattr(mac, "_agent_processes", lambda user: False)
    monkeypatch.setattr(mac, "_chmod_acl", lambda entry, path: None)
    checkout = provisioned_checkout(tmp_path / "pin", codex_sha256=hashlib.sha256(b"codex").hexdigest())
    table = installed(checkout, mac.load_host_config(checkout))

    def launcher(header: Any, prompt: bytes, timeout: float) -> dict[str, Any]:
        if header["kind"] == "attest":
            return {"status": "ATTESTED", "cases": {"identity": "PASS", "direct-egress": "PASS"}}
        return run(work / header["workroot_id"] / "workspace", prompt)

    port = mac.MacCodexIsolation(
        checkout, launcher=launcher, read=lambda p: table[p], principals=lambda c: {"ok": True}, gateway=lambda c: None
    )
    (work / WORKROOT_ID).mkdir(parents=True)
    port.attest(work / WORKROOT_ID)
    return port


def codex_edits(workspace: Path, prompt: bytes, *, extra: str = "") -> dict[str, Any]:
    assert b"canary" in prompt  # the sealed task reached Codex, and only through the launcher
    (workspace / "docs").mkdir(exist_ok=True)
    (workspace / "docs" / "ISSUE_AGENT_CANARY.md").write_text("canary\n" + extra)
    return {"status": "COMPLETED", "exit": "ZERO", "stderr_category": "UNCLASSIFIED", "session_secrets": ["s" * 40]}


def fallback_config(repos: dict[str, Any], key: X25519PrivateKey = FALLBACK_KEY, argv: tuple[str, ...] = ()) -> Any:
    config = executor_config(repos)
    return roles.ExecutorConfig(
        remote=config.remote,
        model_argv=argv or ("codex", "exec", "--model", "gpt-5.1-codex"),
        model_public_env=config.model_public_env,
        model_secret_env={},
        handoff_key=key,
        result_recipient=config.result_recipient,
        prompt_verifier=config.prompt_verifier,
    )


def run(
    ledger: Any, envelope: bytes, config: Any, isolation: Any, workroot: Path, run_id: int | None = None
) -> roles.ExecutorOutcome:
    fallback = ledger.view.authorizations[AUTH].state == state.FALLBACK_AUTHORIZED
    return roles.run_executor(
        issue=ISSUE,
        authorization_id=AUTH,
        context=roles.RoleContext(run_id or (APPROVED_RUN if fallback else 100), 1),
        ledger=ledger.access,
        handoff_envelope=envelope,
        config=config,
        isolation=isolation,
        workroot=workroot,
    )


def test_the_admitted_fallback_runs_codex_once_and_seals_under_the_attempt_two_identity(
    repos: dict[str, Any], tmp_path: Path, monkeypatch: Any
) -> None:
    ledger, _primary, second = offer_and_admit(repos)
    port = mac_port(tmp_path, monkeypatch, codex_edits)
    outcome = run(ledger, second, fallback_config(repos), port, mac.WORK_ROOT / WORKROOT_ID)
    assert outcome.advisory_code is None and outcome.sealed_result
    view = ledger.view.authorizations[AUTH]
    bound = view.evidence[state.AUTHORIZED]
    document = json.loads(
        open_sealed(
            outcome.sealed_result,
            recipient=RESULT_KEY,
            expected=TransportBinding(
                payload_kind="result",
                repository_id=1,
                issue_number=ISSUE,
                authorization_id=AUTH,
                base_sha=bound["base_sha"],
                task_scope_sha256=bound["task_scope_sha256"],
                execution_id=bound["fallback"]["execution_id"],
                handoff_sha256=bound["lineage"]["handoff_sha256"],
                plaintext_sha256=_plaintext(outcome.sealed_result),
                recipient_key_id=recipient_key_id(RESULT_KEY.public_key()),
            ),
        )
    )
    files = {item["path"]: base64.b64decode(item["content_b64"]) for item in document["files"]}
    assert files == {"docs/ISSUE_AGENT_CANARY.md": b"canary\n"}
    assert view.execution_id == bound["fallback"]["execution_id"] != bound["execution_id"]


def _plaintext(sealed: bytes) -> str:
    from hunter.automation.issue_agent_transport import header

    return header(sealed).plaintext_sha256


def test_the_primary_handoff_can_never_drive_the_fallback(
    repos: dict[str, Any], tmp_path: Path, monkeypatch: Any
) -> None:
    ledger, primary, _second = offer_and_admit(repos)
    port = mac_port(tmp_path, monkeypatch, codex_edits)
    for envelope, key in ((primary, FALLBACK_KEY), (primary, HANDOFF_KEY)):
        with pytest.raises(roles.RoleRefused) as refused:
            run(ledger, envelope, fallback_config(repos, key), port, mac.WORK_ROOT / WORKROOT_ID)
        assert refused.value.code == "TRANSPORT_INTEGRITY_FAILED"


def test_the_fallback_seal_never_drives_the_primary(repos: dict[str, Any]) -> None:
    ledger, _primary, second = offer_and_admit(repos, admit=False)
    with pytest.raises(roles.RoleRefused) as refused:
        run(ledger, second, executor_config(repos), RecordingIsolation(CANARY), repos["tmp"] / "exec-fb-primary")
    assert refused.value.code == "TRANSPORT_INTEGRITY_FAILED"


@pytest.mark.parametrize("run_id", [100, 201])
def test_the_fallback_runs_only_in_the_owner_approved_run_that_claimed_it(
    repos: dict[str, Any], tmp_path: Path, monkeypatch: Any, run_id: int
) -> None:
    ledger, _primary, second = offer_and_admit(repos)
    port = mac_port(tmp_path, monkeypatch, codex_edits)
    with pytest.raises(roles.RoleRefused) as refused:
        run(ledger, second, fallback_config(repos), port, mac.WORK_ROOT / WORKROOT_ID, run_id=run_id)
    assert refused.value.code == "EXECUTION_NOT_STARTED"


def test_an_admitted_but_unapproved_fallback_never_runs(
    repos: dict[str, Any], tmp_path: Path, monkeypatch: Any
) -> None:
    ledger, _primary, second = offer_and_admit(repos, approved=False)
    port = mac_port(tmp_path, monkeypatch, codex_edits)
    for run_id in (100, APPROVED_RUN):
        with pytest.raises(roles.RoleRefused, match="owner-approved"):
            run(ledger, second, fallback_config(repos), port, mac.WORK_ROOT / WORKROOT_ID, run_id=run_id)
    assert not (mac.WORK_ROOT / WORKROOT_ID / "workspace").exists()


def test_a_fallback_key_cannot_open_the_offer_without_the_admission(repos: dict[str, Any]) -> None:
    ledger, _primary, second = offer_and_admit(repos, admit=False)
    with pytest.raises(roles.RoleRefused):
        run(ledger, second, fallback_config(repos), RecordingIsolation(CANARY), repos["tmp"] / "exec-unadmitted")


@pytest.mark.parametrize(
    "argv", [("kilo", "run", "--model", "kilo/x"), ("opencode", "run"), ("/usr/bin/env", "codex", "exec")]
)
def test_the_admitted_attempt_runs_only_codex(repos: dict[str, Any], argv: tuple[str, ...]) -> None:
    ledger, _primary, second = offer_and_admit(repos)
    isolation = RecordingIsolation(CANARY)
    with pytest.raises(roles.RoleRefused) as refused:
        run(ledger, second, fallback_config(repos, argv=argv), isolation, repos["tmp"] / "exec-fb-argv")
    assert refused.value.code == "EXECUTION_NOT_STARTED" and isolation.calls == []


def test_codex_never_runs_through_any_port_but_the_attested_mac_port(repos: dict[str, Any]) -> None:
    ledger, _primary, second = offer_and_admit(repos)
    isolation = RecordingIsolation(CANARY)
    workroot = repos["tmp"] / "exec-fb-recording"
    with pytest.raises(roles.RoleRefused, match="attested"):
        run(ledger, second, fallback_config(repos), isolation, workroot)
    assert isolation.calls == [] and not workroot.exists()


def test_an_unattested_mac_port_is_refused_before_the_workspace(
    repos: dict[str, Any], tmp_path: Path, monkeypatch: Any
) -> None:
    ledger, _primary, second = offer_and_admit(repos)
    port = mac_port(tmp_path, monkeypatch, codex_edits)
    port.attested = False
    with pytest.raises(roles.RoleRefused, match="attested"):
        run(ledger, second, fallback_config(repos), port, mac.WORK_ROOT / WORKROOT_ID)
    assert not (mac.WORK_ROOT / WORKROOT_ID / "workspace").exists()


def test_a_result_carrying_the_session_token_is_never_sealed(
    repos: dict[str, Any], tmp_path: Path, monkeypatch: Any
) -> None:
    ledger, _primary, second = offer_and_admit(repos)
    port = mac_port(tmp_path, monkeypatch, lambda ws, prompt: codex_edits(ws, prompt, extra="s" * 40))
    outcome = run(ledger, second, fallback_config(repos), port, mac.WORK_ROOT / WORKROOT_ID)
    assert outcome == roles.ExecutorOutcome(None, "SECRET_IN_RESULT")


def test_a_failed_fallback_never_probes_or_signals_another_fallback(
    repos: dict[str, Any], tmp_path: Path, monkeypatch: Any
) -> None:
    ledger, _primary, second = offer_and_admit(repos)
    port = mac_port(
        tmp_path,
        monkeypatch,
        lambda ws, prompt: {"status": "COMPLETED", "exit": "NONZERO", "stderr_category": "RATE_LIMIT"},
    )
    probes: list[int] = []
    config = fallback_config(repos)
    config = roles.ExecutorConfig(**{**_fields(config), "provider_probe": lambda: probes.append(1) or "RATE_LIMIT"})
    outcome = run(ledger, second, config, port, mac.WORK_ROOT / WORKROOT_ID)
    assert outcome == roles.ExecutorOutcome(None, "PROVIDER_UNAVAILABLE") and probes == []


def _fields(config: roles.ExecutorConfig) -> dict[str, Any]:
    return {name: getattr(config, name) for name in roles.ExecutorConfig.__dataclass_fields__}


# --- the primary's fallback signals -------------------------------------------------------------------------


def primary_with_probe(repos: dict[str, Any], probe: Any) -> roles.ExecutorConfig:
    return roles.ExecutorConfig(**{**_fields(executor_config(repos)), "provider_probe": probe})


def test_a_failed_primary_reports_its_hint_and_the_trusted_probe(repos: dict[str, Any]) -> None:
    ledger, envelope = authorize(repos)
    isolation = RecordingIsolation(CANARY, fail=True)
    isolation.last_stderr_category = "RATE_LIMIT"  # type: ignore[attr-defined]
    order: list[str] = []
    original_stop = isolation.stop

    def stop() -> None:
        order.append("stop")
        original_stop()

    isolation.stop = stop  # type: ignore[method-assign]
    outcome = run(
        ledger,
        envelope,
        primary_with_probe(repos, lambda: order.append("probe") or "RATE_LIMIT"),
        isolation,
        repos["tmp"] / "exec-primary-probe",
    )
    assert outcome == roles.ExecutorOutcome(None, "PROVIDER_UNAVAILABLE", "RATE_LIMIT", "RATE_LIMIT")
    assert order == ["stop", "probe"]  # the probe runs only after every isolated process has stopped


@pytest.mark.parametrize("kind", ["success", "timeout", "no-probe"])
def test_the_probe_runs_only_for_a_launched_primary_that_exited_non_zero(repos: dict[str, Any], kind: str) -> None:
    ledger, envelope = authorize(repos)
    isolation = RecordingIsolation(CANARY, hang=kind == "timeout")
    probes: list[int] = []
    probe = None if kind == "no-probe" else (lambda: probes.append(1) or "RATE_LIMIT")
    if kind == "no-probe":
        isolation = RecordingIsolation(CANARY, fail=True)
    outcome = run(ledger, envelope, primary_with_probe(repos, probe), isolation, repos["tmp"] / f"exec-{kind}")
    assert probes == [] and outcome.stderr_category is None and outcome.probe_category is None
