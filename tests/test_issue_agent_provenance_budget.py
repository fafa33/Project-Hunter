"""Issue #577: the validator's run-provenance reads must not exhaust GitHub's REST budget on a long ledger.

Hosted run 42407602300 (and its validation resume 42408388056) failed ``validate`` with an anonymous HTTP 403
``ratelimit-limit=60 remaining=0`` while re-checking provenance (AT-44) of historical ledger records: the #577
ledger holds 54 records written by 26 distinct runs, and the validate job verified the chain three times with
three independent memos (``bound`` step, ``_view`` and ``run_validator``), i.e. 78 anonymous reads against a
60/hour per-runner-IP budget. These tests pin the correction without weakening any provenance check.
"""

from __future__ import annotations

import json
import urllib.error
from collections.abc import Callable
from pathlib import Path
from typing import Any

import hunter_issue_agent_lifecycle as lifecycle
import pytest
import test_issue_agent_control as ct
import test_issue_agent_roles as rt

from hunter.automation import issue_agent_control as control
from hunter.automation import issue_agent_replacement_executor as core
from hunter.automation import issue_agent_roles as roles
from hunter.automation import issue_agent_state as state

API = f"https://api.github.com/repos/{rt.REPOSITORY}/actions/runs"
#: The shape of the hosted #577 ledger: 26 distinct writer runs.
RUN_IDS = tuple(range(40_000, 40_026))
TOKEN = "ghs_job_scoped_read_token_value"


def recorded(run_id: int, **overrides: Any) -> dict[str, Any]:
    value = {**ct.RECORDED, "run_id": run_id}
    value.update(overrides)
    return value


class Api:
    """A GitHub REST endpoint behind the real ``GitHubRest`` client, counting every request it serves."""

    def __init__(self, respond: Callable[[int], Any] | None = None) -> None:
        self.requests: list[Any] = []
        self._respond = respond or (lambda run_id: ct.Response(200, json.dumps(ct.run(run_id)).encode()))

    def open(self, request: Any, _timeout: float) -> Any:
        self.requests.append(request)
        run_id = int(request.full_url.removeprefix(API + "/").split("/")[0])
        return self._respond(run_id)

    def client(self, token: str | None = None) -> control.GitHubRest:
        return control.GitHubRest(rt.REPOSITORY, token, opener=self.open, sleep=lambda _s: None)


def verify_ledger_like(check: state.ProvenanceCheck, run_ids: tuple[int, ...]) -> None:
    """Exactly what ``verify_chain`` asks of provenance: one check per record (two records per run here)."""

    for run_id in run_ids * 2:
        assert check(recorded(run_id), {}) is True


def rate_limited(request: Any) -> Any:
    headers = {"x-ratelimit-limit": "60", "x-ratelimit-remaining": "0", "x-ratelimit-reset": "9999999999"}
    raise urllib.error.HTTPError(request.full_url, 403, "rate limited", headers, None)  # type: ignore[arg-type]


# --- TEST A / B: each distinct run is observed once per step, at its exact attempt -------------------------


def test_a_shared_ledger_access_observes_each_writer_run_once_however_often_the_chain_is_verified() -> None:
    api = Api()
    access = roles.LedgerAccess(object(), rt.TRUST, control.run_provenance(api.client(TOKEN), ct.CONFIG))  # type: ignore[arg-type]
    for _verification in range(3):  # bound, _view and run_validator before the fix
        verify_ledger_like(access.provenance, RUN_IDS)
    assert len(api.requests) == len(RUN_IDS) == 26
    paths = sorted(request.full_url for request in api.requests)
    assert paths == sorted(f"{API}/{run_id}/attempts/1" for run_id in RUN_IDS)  # exact run, exact attempt 1
    assert {request.get_header("Authorization") for request in api.requests} == {f"Bearer {TOKEN}"}


def test_the_validate_job_now_fits_the_budget_that_the_hosted_ledger_exhausted() -> None:
    before, after = Api(), Api()
    for _verification in range(3):  # three independent memos: 3 x 26 = 78 > 60
        verify_ledger_like(control.run_provenance(before.client(), ct.CONFIG), RUN_IDS)
    for _step in ("bound", "validate"):  # one memo per step
        verify_ledger_like(control.run_provenance(after.client(TOKEN), ct.CONFIG), RUN_IDS)
    assert len(before.requests) == 78 > 60
    assert len(after.requests) == 52


# --- TEST C: a memoized run never vouches for a record that claims a different identity --------------------


@pytest.mark.parametrize(
    "claim",
    [
        {"head_sha": "d" * 40},
        {"run_attempt": 2},
        {"job": "publish"},
        {"workflow_path": ".github/workflows/ci.yml"},
    ],
)
def test_a_memoized_run_still_rejects_a_record_with_a_different_head_attempt_or_writer(claim: dict[str, Any]) -> None:
    api = Api()
    check = control.run_provenance(api.client(TOKEN), ct.CONFIG)
    assert check(recorded(RUN_IDS[0]), {}) is True
    assert check(recorded(RUN_IDS[0], **claim), {}) is False


@pytest.mark.parametrize(
    "overrides",
    [{"id": 1}, {"run_attempt": 2}, {"head_sha": "d" * 40}, {"head_branch": "attack"}, {"repository": {"id": 2}}],
)
def test_an_authenticated_read_of_the_wrong_run_is_still_rejected(overrides: dict[str, Any]) -> None:
    api = Api(lambda run_id: ct.Response(200, json.dumps(ct.run(run_id, **overrides)).encode()))
    assert control.run_provenance(api.client(TOKEN), ct.CONFIG)(recorded(RUN_IDS[0]), {}) is False


# --- TEST D / E: a 403 rate limit is indefinite, never a verdict and never cached ---------------------------


def test_a_rate_limit_is_never_a_successful_or_cached_provenance_verdict() -> None:
    calls: list[Any] = []

    def open_(request: Any, _timeout: float) -> Any:
        calls.append(request)
        return rate_limited(request)

    client = control.GitHubRest(rt.REPOSITORY, TOKEN, opener=open_, sleep=lambda _s: None)
    check = control.run_provenance(client, ct.CONFIG)
    for _ in range(2):
        with pytest.raises(control.FactsUnavailable, match="category=rate_limited http=403"):
            check(recorded(RUN_IDS[0]), {})
    assert len(calls) == 2  # not memoized: the next read asks again rather than reusing an indefinite answer


def test_a_rate_limited_validation_writes_no_receipt_and_exits_non_zero(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The existing bounded contract: no receipt artifact, so record-validation spends its one resume, then FAILED."""

    def unavailable(*_args: Any, **_kwargs: Any) -> Any:
        raise control.FactsUnavailable("an observation was indefinite [category=rate_limited http=403]")

    monkeypatch.setattr(lifecycle.control, "load_configuration", lambda _path: ct.CONFIG)
    monkeypatch.setattr(lifecycle, "_view", unavailable)
    out = tmp_path / "receipt.json"
    argv = ["validate", "--issue", "577", "--authorization-id", rt.AUTH, "--result", str(tmp_path / "r")]
    code = lifecycle.main([*argv, "--trusted-repo", str(tmp_path), "--out", str(out)])
    assert code == lifecycle.EXIT_REFUSED != 0
    assert not out.exists()


# --- TEST F / G / H: the token stays in the trusted intake; validation wiring is single and idempotent -------


class Recorder:
    def __init__(self) -> None:
        self.clients: list[tuple[str, str | None]] = []
        self.views: list[Any] = []
        self.validators: list[dict[str, Any]] = []


@pytest.fixture
def wired(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> tuple[Recorder, Any]:
    seen = Recorder()

    class Client:
        def __init__(self, repository: str, token: str | None) -> None:
            seen.clients.append((repository, token))

    def view(_configuration: Any, _issue: int, _authorization_id: str, access: Any = None) -> Any:
        seen.views.append(access)
        return object()

    def validator(**kwargs: Any) -> dict[str, Any]:
        assert lifecycle.PROVENANCE_TOKEN_ENV not in lifecycle.os.environ  # gone before any subprocess could start
        seen.validators.append(kwargs)
        return {"verdict": "PASSED", "authorization_id": kwargs["authorization_id"]}

    monkeypatch.setattr(lifecycle.control, "GitHubRest", Client)
    monkeypatch.setattr(lifecycle, "_store", lambda _c, authenticated: object())
    monkeypatch.setattr(lifecycle, "_view", view)
    monkeypatch.setattr(lifecycle.roles, "run_validator", validator)
    monkeypatch.setattr(lifecycle, "_x25519", lambda _name: object())
    monkeypatch.setattr(lifecycle, "_digest_files", lambda *_a, **_k: "0" * 64)
    (tmp_path / "result.sealed").write_bytes(b"sealed")
    arguments = lifecycle.parse_arguments(
        [
            "validate",
            "--issue",
            "577",
            "--authorization-id",
            rt.AUTH,
            "--result",
            str(tmp_path / "result.sealed"),
            "--trusted-repo",
            str(tmp_path),
            "--out",
            str(tmp_path / "receipt.json"),
        ]
    )
    return seen, arguments


def test_validate_verifies_through_one_ledger_access_and_one_read_client(
    wired: tuple[Recorder, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    seen, arguments = wired
    monkeypatch.setenv(lifecycle.PROVENANCE_TOKEN_ENV, TOKEN)
    assert lifecycle.cmd_validate(ct.CONFIG, arguments) == 0
    assert seen.clients == [(rt.REPOSITORY, TOKEN)]
    (validator,) = seen.validators
    assert seen.views == [validator["ledger"]]  # the same memo serves both verifications
    assert lifecycle.PROVENANCE_TOKEN_ENV not in lifecycle.os.environ


def test_without_the_token_the_reads_stay_anonymous_exactly_as_before(
    wired: tuple[Recorder, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    seen, arguments = wired
    monkeypatch.delenv(lifecycle.PROVENANCE_TOKEN_ENV, raising=False)
    assert lifecycle.cmd_validate(ct.CONFIG, arguments) == 0
    assert seen.clients == [(rt.REPOSITORY, None)]


def test_repeated_validation_is_byte_identical_and_never_publishes(
    wired: tuple[Recorder, Any], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    seen, arguments = wired
    monkeypatch.setattr(lifecycle.roles, "run_publisher", lambda **_k: pytest.fail("validation never publishes"))
    receipts = []
    for _ in range(2):
        monkeypatch.setenv(lifecycle.PROVENANCE_TOKEN_ENV, TOKEN)
        assert lifecycle.cmd_validate(ct.CONFIG, arguments) == 0
        receipts.append((tmp_path / "receipt.json").read_bytes())
    assert receipts[0] == receipts[1]
    assert len(seen.clients) == 2  # one client per step invocation, never shared across steps


def test_the_token_never_reaches_the_isolation_uid(monkeypatch: pytest.MonkeyPatch) -> None:
    """Candidate-controlled validation runs as another uid under ``env -i``: nothing the intake held is inherited."""

    monkeypatch.setenv(lifecycle.PROVENANCE_TOKEN_ENV, TOKEN)
    command = core.isolated_command(lifecycle.ISOLATION_USER, {"PATH": "/usr/bin"}, ("python", "-m", "pytest"))
    assert command[:7] == ("sudo", "-n", "-u", lifecycle.ISOLATION_USER, "--", "/usr/bin/env", "-i")
    assert dict(core.SUDO_ENVIRONMENT) == {"PATH": "/usr/sbin:/usr/bin:/sbin:/bin"}
    assert not any(TOKEN in part or lifecycle.PROVENANCE_TOKEN_ENV in part for part in command)


def test_only_the_trusted_intake_steps_receive_the_job_scoped_read_token() -> None:
    import yaml

    workflow = Path(lifecycle.__file__).parents[1] / ".github/workflows/hunter-issue-agent-trigger.yml"
    jobs = yaml.safe_load(workflow.read_text())["jobs"]
    holders = {
        (job_id, step.get("name"))
        for job_id, job in jobs.items()
        for step in job.get("steps", [])
        if lifecycle.PROVENANCE_TOKEN_ENV in (step.get("env") or {})
    }
    assert holders == {
        ("validate", "Read the bound result binding from the ledger"),
        ("validate", "Validate the exact unsigned commit (credential-free)"),
        ("publish", "Read the bound result binding from the ledger"),
    }
    for job_id, _name in holders:
        assert "write" not in set(jobs[job_id]["permissions"].values())  # read-only job tokens, unchanged
    for job_id, name in holders:
        (step,) = (s for s in jobs[job_id]["steps"] if s.get("name") == name)
        assert (
            step["env"][lifecycle.PROVENANCE_TOKEN_ENV] == "${{ github.token }}"
        )  # the job's own token, no new secret
    assert jobs["execute"]["permissions"] == {}
