"""ADR 0041 F2/F3/F4: the control domain admits and binds the single fallback from definitive facts only."""

from __future__ import annotations

import dataclasses
from pathlib import Path
from typing import Any

import pytest
import test_issue_agent_fallback_executor as fx
import test_issue_agent_roles as rt
from test_issue_agent_control import CONFIG, NOW, FakeGitHub, ledger_state, provisioned, step, write_roots
from test_issue_agent_control import run as control_run

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
    ids=["primary-recipient-reused", "nonhex", "empty", "wrong-type-integer", "null"],
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


@pytest.mark.parametrize("probe", [None, "UNCLASSIFIED"])
def test_issue_577_an_unclassified_primary_exit_is_terminal_never_a_fallback(
    repos: dict[str, Any], probe: str | None
) -> None:
    # Run 42387251897: a keyless free-model primary exited non-zero with an UNCLASSIFIED hint and no trusted probe
    # (F2 probes only with the model key). The generic advisory never earns the provisioned fallback.
    fx.offer_and_admit(repos, admit=False)
    decision = step(
        repos,
        primary_failed_world(),
        configuration=FALLBACK_CONFIG,
        executor_advisory_code="PROVIDER_UNAVAILABLE",
        executor_stderr_category="UNCLASSIFIED",
        executor_probe_category=probe,
    )
    assert decision.action == "fail" and decision.code == "EXECUTION_NOT_COMPLETED"
    view = ledger_state(repos)
    assert view.state == state.FAILED and state.FALLBACK_AUTHORIZED not in view.evidence


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


RESUME = f"/repos/{rt.REPOSITORY}/actions/runs/{fx.APPROVED_RUN}"


def fallback_world(sealed: bytes | None, name: str | None = None, conclusion: str = "success") -> FakeGitHub:
    """The owner-approved resume run (not the authorize run) owns the fallback job and its result."""

    github = FakeGitHub()
    github.routes[f"{RESUME}/attempts/1"] = control_run(fx.APPROVED_RUN, event="workflow_dispatch")
    github.routes[RESUME] = control_run(fx.APPROVED_RUN, status="completed")
    github.routes[f"{RESUME}/jobs"] = {"total_count": 1, "jobs": [github.job("execute-fallback", 10, conclusion)]}
    items = (
        [] if sealed is None else [github.artifact(31, name or state.fallback_result_artifact_name(rt.AUTH), sealed)]
    )
    github.routes[f"{RESUME}/artifacts"] = items
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
    assert evidence["fallback"] == {
        "execution_attempt": 2,
        "executor": "codex",
        "attestation_sha256": "ab" * 32,
        "deadline_published_at": "2026-10-04T19:00:00Z",  # restarted at this bind, not at authorization
    }
    assert evidence["result_artifact"]["run_id"] == fx.APPROVED_RUN  # the validator downloads from the approved run
    # RESULT_BOUND consumes pending_resume, but validation and publication must
    # still use the signed fallback dispatch run, never the primary run.
    assert view.pending_resume is None
    assert control._owning_run(view) == fx.APPROVED_RUN
    assert view.publish_deadline == "2026-10-04T19:00:00Z"
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
    github.routes[f"{RESUME}/artifacts"].append(github.artifact(32, state.result_artifact_name(rt.AUTH), sealed))
    decision = step(repos, github, configuration=FALLBACK_CONFIG, fallback_attestation_sha256="ab" * 32)
    assert decision.code == "TRANSPORT_INTEGRITY_FAILED"


def test_a_running_fallback_job_is_a_noop(repos: dict[str, Any]) -> None:
    fx.offer_and_admit(repos)
    github = fallback_world(None)
    github.routes[RESUME] = control_run(fx.APPROVED_RUN, status="in_progress")
    github.routes[f"{RESUME}/jobs"] = {
        "total_count": 1,
        "jobs": [github.job("execute-fallback", 10, None, "in_progress")],  # type: ignore[arg-type]
    }
    assert step(repos, github, configuration=FALLBACK_CONFIG).action == "noop"
    assert ledger_state(repos).state == state.FALLBACK_AUTHORIZED


def test_a_session_that_never_started_returns_the_request_to_pending(repos: dict[str, Any]) -> None:
    fx.offer_and_admit(repos)
    decision = step(
        repos,
        fallback_world(None),
        configuration=FALLBACK_CONFIG,
        executor_advisory_code="FALLBACK_SESSION_NOT_STARTED",
    )
    assert decision.action in ("abandon_resume", "noop")
    view = ledger_state(repos)
    assert view.state == state.FALLBACK_AUTHORIZED and view.pending_resume is None
    assert view.resume_attempts == {"fallback": 1}


def test_a_launched_fallback_that_failed_is_final(repos: dict[str, Any]) -> None:
    fx.offer_and_admit(repos)
    decision = step(repos, fallback_world(None, conclusion="failure"), configuration=FALLBACK_CONFIG)
    assert decision.code == "EXECUTION_NOT_COMPLETED"
    assert ledger_state(repos).evidence[state.FAILED]["failed_from_state"] == state.FALLBACK_AUTHORIZED


def test_reconcile_never_expires_a_fallback_awaiting_the_owner(repos: dict[str, Any]) -> None:
    fx.offer_and_admit(repos, approved=False)
    for observer in ("reconcile", "bind"):
        assert step(repos, FakeGitHub(), role=observer, configuration=FALLBACK_CONFIG).action == "noop"
    assert ledger_state(repos).state == state.FALLBACK_AUTHORIZED


# --- the owner's explicit decision ------------------------------------------------------------------------------


def decide(repos: dict[str, Any], github: FakeGitHub, decision: str) -> state.Decision:
    return control.owner_decision(
        github=github,
        configuration=FALLBACK_CONFIG,
        store=state.GitLedgerStore(repos["remote"], workdir=repos["tmp"] / f"owner-{len(github.reads)}"),
        signing_key=rt.KEY,
        writer=control.Writer(control.LIFECYCLE_WORKFLOW, "fallback-decision", "owner-decision", 100, 1, rt.CONTROL),
        issue=rt.ISSUE,
        decision=decision,
        clock=lambda: NOW,
        new_nonce=lambda: "cd" * 32,
    )


def test_owner_approval_records_a_nonce_bound_request_before_dispatching_it(repos: dict[str, Any]) -> None:
    fx.offer_and_admit(repos, approved=False)
    github = FakeGitHub()
    assert decide(repos, github, "approve") == state.Decision("resume", stage="fallback")
    view = ledger_state(repos)
    assert view.pending_resume is not None and view.pending_resume["nonce"] == "cd" * 32
    assert view.pending_resume["stage"] == "fallback" and view.pending_resume["bound_run_id"] is None
    assert [inputs for _, inputs in github.dispatched] == [
        {"issue": str(rt.ISSUE), "authorization_id": rt.AUTH, "stage": "fallback", "nonce": "cd" * 32}
    ]


def test_a_second_approval_while_one_is_pending_does_nothing(repos: dict[str, Any]) -> None:
    fx.offer_and_admit(repos)  # already approved and claimed by run 200
    github = fallback_world(None)
    assert decide(repos, github, "approve").action == "noop"
    assert decide(repos, github, "reject").action == "noop"  # Codex may be running in the claimed run
    assert github.dispatched == [] and ledger_state(repos).state == state.FALLBACK_AUTHORIZED


def test_owner_rejection_withdraws_a_pending_fallback(repos: dict[str, Any]) -> None:
    fx.offer_and_admit(repos, approved=False)
    github = FakeGitHub()
    assert decide(repos, github, "reject").code == "OWNER_WITHDREW"
    view = ledger_state(repos)
    assert view.state == state.FAILED and view.evidence[state.FAILED]["code"] == "OWNER_WITHDREW"
    assert github.dispatched == []


def test_a_decision_without_an_admitted_fallback_does_nothing(repos: dict[str, Any]) -> None:
    fx.offer_and_admit(repos, admit=False)
    github = FakeGitHub()
    assert decide(repos, github, "approve").action == "noop"
    assert decide(repos, github, "reject").action == "noop"
    assert ledger_state(repos).state == state.AUTHORIZED and github.dispatched == []


@pytest.mark.parametrize("decision,role", [("delete", "owner-decision"), ("approve", "bind")])
def test_an_unknown_decision_or_writer_is_refused(repos: dict[str, Any], decision: str, role: str) -> None:
    fx.offer_and_admit(repos, approved=False)
    with pytest.raises(control.ControlRefused):
        control.owner_decision(
            github=FakeGitHub(),
            configuration=FALLBACK_CONFIG,
            store=state.GitLedgerStore(repos["remote"], workdir=repos["tmp"] / "owner-bad"),
            signing_key=rt.KEY,
            writer=control.Writer(control.LIFECYCLE_WORKFLOW, "fallback-decision", role, 100, 1, rt.CONTROL),
            issue=rt.ISSUE,
            decision=decision,
            clock=lambda: NOW,
        )


# --- ADR 0041 F8: the armed canary ------------------------------------------------------------------------------

CANARY_REFUSAL = {
    "provider_host": state.CANARY_PROVIDER_HOST,
    "primary_refusals": 3,
    "probe_refusals": 1,
    "refusal_digest": "d" * 64,
}


def test_bind_records_the_canary_as_canary_network_with_its_refusal_evidence(repos: dict[str, Any]) -> None:
    fx.offer_and_admit(repos, admit=False, canary=True)
    decision = step(
        repos,
        primary_failed_world(),
        configuration=FALLBACK_CONFIG,
        executor_advisory_code="PROVIDER_UNAVAILABLE",
        executor_stderr_category="NETWORK",
        executor_probe_category="NETWORK",
        executor_canary_refusal=dict(CANARY_REFUSAL),
    )
    assert decision.target_state == state.FALLBACK_AUTHORIZED
    evidence = ledger_state(repos).evidence[state.FALLBACK_AUTHORIZED]
    assert evidence["primary_cause"] == "CANARY_NETWORK"
    assert evidence["stderr_category"] == evidence["probe_category"] == "NETWORK"
    assert evidence["canary_refusal"] == CANARY_REFUSAL


@pytest.mark.parametrize("refusal", [None, {**CANARY_REFUSAL, "probe_refusals": 0}, {"forged": True}])
def test_bind_never_admits_an_armed_canary_without_conforming_refusal_evidence(
    repos: dict[str, Any], refusal: Any
) -> None:
    fx.offer_and_admit(repos, admit=False, canary=True)
    decision = step(
        repos,
        primary_failed_world(),
        configuration=FALLBACK_CONFIG,
        executor_stderr_category="NETWORK",
        executor_probe_category="NETWORK",
        executor_canary_refusal=refusal,
    )
    assert decision.code == "EXECUTION_NOT_COMPLETED" and ledger_state(repos).state == state.FAILED
