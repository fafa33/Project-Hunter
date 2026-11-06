"""ADR 0041 F2/F3/F4: the control domain admits and binds the single fallback from definitive facts only."""

from __future__ import annotations

import dataclasses
from pathlib import Path
from typing import Any

import pytest
import test_issue_agent_fallback_executor as fx
import test_issue_agent_roles as rt
from test_issue_agent_control import CONFIG, FakeGitHub, ledger_state, provisioned, step, write_roots

from hunter.automation import issue_agent_control as control
from hunter.automation import issue_agent_mac_fallback as mac
from hunter.automation import issue_agent_state as state

repos = rt.repos
_prompt_keys = rt._prompt_keys
FALLBACK_CONFIG = dataclasses.replace(CONFIG, fallback_handoff_recipient=fx.FALLBACK_KEY.public_key())
JOBS = f"/repos/{rt.REPOSITORY}/actions/runs/100/jobs"
ARTIFACTS = f"/repos/{rt.REPOSITORY}/actions/runs/100/artifacts"


# --- trust roots -----------------------------------------------------------------------------------------------


def test_the_fallback_recipient_is_optional_and_distinct(tmp_path: Path) -> None:
    assert control.load_configuration(write_roots(tmp_path / "a", provisioned())).fallback_handoff_recipient is None
    key = fx.FALLBACK_KEY.public_key().public_bytes_raw().hex()
    loaded = control.load_configuration(write_roots(tmp_path / "b", provisioned(fallback_handoff_recipient=key)))
    assert loaded.fallback_handoff_recipient is not None
    assert loaded.fallback_handoff_recipient.public_bytes_raw() == fx.FALLBACK_KEY.public_key().public_bytes_raw()


@pytest.mark.parametrize(
    "value",
    [rt.HANDOFF_KEY.public_key().public_bytes_raw().hex(), "zz" * 32, "", 7, None],
)
def test_a_reused_or_malformed_fallback_recipient_is_missing_configuration(tmp_path: Path, value: object) -> None:
    with pytest.raises(control.ControlRefused) as refused:
        control.load_configuration(write_roots(tmp_path, provisioned(fallback_handoff_recipient=value)))
    assert refused.value.code == "MISSING_CONFIGURATION"


# --- bind: admission --------------------------------------------------------------------------------------------


def primary_failed_world() -> FakeGitHub:
    github = FakeGitHub()
    github.routes[JOBS] = {"total_count": 1, "jobs": [github.job("execute", 9, "success")]}
    return github


def test_bind_admits_the_fallback_from_agreeing_signals_and_records_the_bound_offer(repos: dict[str, Any]) -> None:
    fx.offer_and_admit(repos, admit=False)
    decision = step(
        repos,
        primary_failed_world(),
        configuration=FALLBACK_CONFIG,
        executor_advisory_code="PROVIDER_UNAVAILABLE",
        executor_stderr_category="QUOTA_EXCEEDED",
        executor_probe_category="QUOTA_EXCEEDED",
    )
    assert decision.target_state == state.FALLBACK_AUTHORIZED
    view = ledger_state(repos)
    evidence = view.evidence[state.FALLBACK_AUTHORIZED]
    assert view.state == state.FALLBACK_AUTHORIZED
    assert evidence["primary_cause"] == evidence["stderr_category"] == evidence["probe_category"] == "QUOTA_EXCEEDED"
    assert evidence["primary_executor_job_id"] == 9 and evidence["primary_result_artifacts"] == 0
    assert evidence["fallback_execution_id"] == view.evidence[state.AUTHORIZED]["fallback"]["execution_id"]


@pytest.mark.parametrize(
    "stderr,probe",
    [("RATE_LIMIT", ""), ("RATE_LIMIT", "UNCLASSIFIED"), ("", "RATE_LIMIT"), ("RATE_LIMIT", "rate-limit-forged")],
)
def test_bind_never_admits_on_an_untrusted_hint_alone(repos: dict[str, Any], stderr: str, probe: str) -> None:
    fx.offer_and_admit(repos, admit=False)
    decision = step(
        repos,
        primary_failed_world(),
        configuration=FALLBACK_CONFIG,
        executor_stderr_category=stderr or None,
        executor_probe_category=probe or None,
    )
    assert decision.code == "EXECUTION_NOT_COMPLETED"
    assert ledger_state(repos).state == state.FAILED


@pytest.mark.parametrize("role", ["record-validation", "finalize"])
def test_no_other_control_role_writes_the_admission(repos: dict[str, Any], role: str) -> None:
    fx.offer_and_admit(repos, admit=False)
    decision = step(
        repos,
        primary_failed_world(),
        role=role,
        configuration=FALLBACK_CONFIG,
        executor_stderr_category="RATE_LIMIT",
        executor_probe_category="RATE_LIMIT",
    )
    assert decision.action == "noop" and ledger_state(repos).state == state.AUTHORIZED


# --- bind-fallback: the attempt-2 result ------------------------------------------------------------------------


def fallback_result(repos: dict[str, Any], tmp_path: Path, monkeypatch: Any) -> bytes:
    ledger, _primary, second = fx.offer_and_admit(repos)
    port = fx.mac_port(tmp_path, monkeypatch, fx.codex_edits)
    outcome = fx.run(ledger, second, fx.fallback_config(repos), port, mac.WORK_ROOT / fx.WORKROOT_ID)
    assert outcome.sealed_result is not None
    return outcome.sealed_result


def fallback_world(sealed: bytes, name: str | None = None) -> FakeGitHub:
    github = FakeGitHub()
    github.routes[JOBS] = {
        "total_count": 2,
        "jobs": [github.job("execute", 9, "success"), github.job("execute-fallback", 10, "success")],
    }
    github.routes[ARTIFACTS] = [github.artifact(31, name or state.fallback_result_artifact_name(rt.AUTH), sealed)]
    return github


def test_bind_fallback_binds_the_attempt_two_result_with_its_attestation(
    repos: dict[str, Any], tmp_path: Path, monkeypatch: Any
) -> None:
    sealed = fallback_result(repos, tmp_path, monkeypatch)
    decision = step(repos, fallback_world(sealed), configuration=FALLBACK_CONFIG, fallback_attestation_sha256="ab" * 32)
    assert decision.target_state == state.RESULT_BOUND
    view = ledger_state(repos)
    evidence = view.evidence[state.RESULT_BOUND]
    assert evidence["executor_job_id"] == 10 and evidence["result_artifact"]["artifact_id"] == 31
    assert evidence["fallback"] == {"execution_attempt": 2, "executor": "codex", "attestation_sha256": "ab" * 32}
    assert view.execution_id == view.evidence[state.AUTHORIZED]["fallback"]["execution_id"]


@pytest.mark.parametrize("attestation", [None, "", "AB" * 32, "ab" * 31])
def test_a_fallback_result_without_a_well_formed_attestation_never_binds(
    repos: dict[str, Any], tmp_path: Path, monkeypatch: Any, attestation: str | None
) -> None:
    sealed = fallback_result(repos, tmp_path, monkeypatch)
    decision = step(
        repos, fallback_world(sealed), configuration=FALLBACK_CONFIG, fallback_attestation_sha256=attestation
    )
    assert decision.code == "EXECUTOR_RESULT_REJECTED"
    assert ledger_state(repos).evidence[state.FAILED]["failed_from_state"] == state.FALLBACK_AUTHORIZED


def test_a_primary_sealed_result_under_the_fallback_name_is_an_integrity_failure(
    repos: dict[str, Any], tmp_path: Path, monkeypatch: Any
) -> None:
    fx.offer_and_admit(repos)
    # A genuine attempt-1 result (sealed under the primary identity) renamed into the fallback's slot.
    ledger, envelope = rt.authorize({**repos, "tmp": _scratch(repos), "remote": _scratch_remote(repos)})
    primary = rt.execute(repos, ledger, envelope, rt.RecordingIsolation(rt.CANARY))
    assert primary.sealed_result is not None
    decision = step(
        repos,
        fallback_world(primary.sealed_result),
        configuration=FALLBACK_CONFIG,
        fallback_attestation_sha256="ab" * 32,
    )
    assert decision.code == "TRANSPORT_INTEGRITY_FAILED"


def _scratch(repos: dict[str, Any]) -> Path:
    path = repos["tmp"] / "scratch-primary"
    path.mkdir(exist_ok=True)
    return path


def _scratch_remote(repos: dict[str, Any]) -> Path:
    import subprocess

    target = repos["tmp"] / "scratch-primary.git"
    if not target.exists():
        subprocess.run(  # main only: the real remote already carries this Issue's ledger branch
            [
                "git",
                "clone",
                "--quiet",
                "--bare",
                "--single-branch",
                "--branch",
                "main",
                str(repos["remote"]),
                str(target),
            ],
            check=True,
        )
    return target


def test_a_late_primary_artifact_during_the_fallback_is_an_integrity_failure(
    repos: dict[str, Any], tmp_path: Path, monkeypatch: Any
) -> None:
    sealed = fallback_result(repos, tmp_path, monkeypatch)
    github = fallback_world(sealed)
    github.routes[ARTIFACTS].append(github.artifact(32, state.result_artifact_name(rt.AUTH), sealed))
    decision = step(repos, github, configuration=FALLBACK_CONFIG, fallback_attestation_sha256="ab" * 32)
    assert decision.code == "TRANSPORT_INTEGRITY_FAILED"


def test_a_running_fallback_job_is_a_noop(repos: dict[str, Any]) -> None:
    fx.offer_and_admit(repos)
    github = FakeGitHub()
    github.routes[JOBS] = {
        "total_count": 2,
        "jobs": [github.job("execute", 9), github.job("execute-fallback", 10, None, "in_progress")],  # type: ignore[arg-type]
    }
    assert step(repos, github, configuration=FALLBACK_CONFIG).action == "noop"
    assert ledger_state(repos).state == state.FALLBACK_AUTHORIZED
