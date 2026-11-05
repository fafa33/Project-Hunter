"""Issue #682: publication observes the live ruleset contract with a dedicated read-only credential, or refuses.

``GITHUB_TOKEN`` cannot see ``bypass_actors``; the publisher therefore holds ``HUNTER_HGCC_READ_TOKEN``. These tests run
the REAL observer script in the REAL subprocess boundary against a fake ``gh`` executable on ``PATH``.
"""

from __future__ import annotations

import ast
import json
import os
import stat
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import hunter_github_contract as contract
import hunter_issue_agent_lifecycle as lifecycle
import pytest

REPOSITORY = "fafa33/Project-Hunter"
READ_TOKEN = "github_pat_HGCC_READ_SECRET_VALUE"
PUSH_TOKEN = "github_pat_PUBLISHER_PUSH_SECRET"
RUNNER_TOKEN = "ghs_RUNNER_TOKEN_SECRET"
SECRET_VALUES = (READ_TOKEN, PUSH_TOKEN, RUNNER_TOKEN)
ROOT = Path(__file__).resolve().parents[1]

ACTIVE = [(20523851, "active"), (24526712, "active"), (23493180, "active"), (111, "disabled")]
DETAIL = {"rules": [{"type": "deletion"}], "name": "r", "target": "branch", "conditions": {"ref_name": {}}}


def _install_fake_gh(tmp_path: Path, *, details: dict[int, dict[str, Any]], exit_code: int = 0) -> Path:
    """A ``gh`` that serves rulesets from fixtures and records the environment it was given."""
    fixture = tmp_path / "rulesets.json"
    fixture.write_text(json.dumps({str(key): value for key, value in details.items()}))
    seen = tmp_path / "gh-environment.json"
    binary = tmp_path / "bin"
    binary.mkdir()
    script = binary / "gh"
    script.write_text(
        f"#!{sys.executable}\n"
        "import json, os, sys\n"
        f"open({str(seen)!r}, 'w').write(json.dumps({{'GH_TOKEN': os.environ.get('GH_TOKEN'), "
        "'GITHUB_TOKEN': os.environ.get('GITHUB_TOKEN'), 'keys': sorted(os.environ)}))\n"
        f"if {exit_code}:\n    sys.stderr.write('boom ' + os.environ.get('GH_TOKEN', '') + ' /secret/path')\n    sys.exit({exit_code})\n"
        "args = sys.argv[1:]\n"
        "if args[:2] == ['auth', 'token']:\n    print(os.environ.get('GH_TOKEN', ''))\n    sys.exit(0 if os.environ.get('GH_TOKEN') else 1)\n"
        f"details = json.load(open({str(fixture)!r}))\n"
        "path = args[1]\n"
        "if '/rulesets?' in path:\n"
        f"    print(json.dumps([{{'id': i, 'enforcement': e}} for i, e in {ACTIVE!r}]))\n"
        "else:\n    print(json.dumps(details[path.rsplit('/', 1)[1]]))\n"
    )
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    return binary


def _world(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, *, with_array: bool = True, break_id: int | None = None):
    details = {}
    for ruleset_id, _enforcement in ACTIVE:
        detail = dict(DETAIL, id=ruleset_id)
        if with_array and ruleset_id != break_id:
            detail["bypass_actors"] = []
        details[ruleset_id] = detail
    binary = _install_fake_gh(tmp_path, details=details)
    monkeypatch.setenv("PATH", f"{binary}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv(lifecycle.HGCC_TOKEN_ENV, READ_TOKEN)
    monkeypatch.setenv("GITHUB_TOKEN", RUNNER_TOKEN)
    monkeypatch.setenv(lifecycle.PUSH_TOKEN_ENV, PUSH_TOKEN)
    return tmp_path / "gh-environment.json"


def test_all_active_rulesets_with_bypass_arrays_are_observed_with_the_dedicated_credential_only(monkeypatch, tmp_path):
    seen_path = _world(monkeypatch, tmp_path)
    lifecycle._observe_hgcc_contract(REPOSITORY)  # does not raise
    seen = json.loads(seen_path.read_text())
    assert seen["GH_TOKEN"] == READ_TOKEN
    assert seen["GITHUB_TOKEN"] is None  # the runner token is never offered to the observer
    # LC_CTYPE is added by the Python interpreter that runs the fake gh (C-locale coercion), not by the caller.
    allowed = {"PATH", "HOME", "GH_CONFIG_DIR", "GH_TOKEN", "GH_PROMPT_DISABLED", "GH_NO_UPDATE_NOTIFIER", "LC_CTYPE"}
    assert set(seen["keys"]) <= allowed
    assert PUSH_TOKEN not in json.dumps(seen)


def test_the_credential_is_removed_from_this_process_so_later_subprocesses_cannot_inherit_it(monkeypatch, tmp_path):
    _world(monkeypatch, tmp_path)
    lifecycle._observe_hgcc_contract(REPOSITORY)
    assert lifecycle.HGCC_TOKEN_ENV not in os.environ
    assert os.environ["GITHUB_TOKEN"] == RUNNER_TOKEN and os.environ[lifecycle.PUSH_TOKEN_ENV] == PUSH_TOKEN


@pytest.mark.parametrize("blank", [None, "", "   "])
def test_a_missing_credential_refuses_without_any_fallback(monkeypatch, tmp_path, blank):
    seen_path = _world(monkeypatch, tmp_path)
    if blank is None:
        monkeypatch.delenv(lifecycle.HGCC_TOKEN_ENV)
    else:
        monkeypatch.setenv(lifecycle.HGCC_TOKEN_ENV, blank)
    with pytest.raises(lifecycle.LifecycleRefused) as refusal:
        lifecycle._observe_hgcc_contract(REPOSITORY)
    assert refusal.value.code == "PUBLICATION_UNAVAILABLE" and "READ_CREDENTIAL_MISSING" in str(refusal.value)
    assert not seen_path.exists(), "no observer may run, and GITHUB_TOKEN / the push token must not be tried"


def test_a_ruleset_without_a_bypass_actors_array_refuses_naming_the_closed_reason(monkeypatch, tmp_path):
    _world(monkeypatch, tmp_path, break_id=20523851)
    with pytest.raises(lifecycle.PublicationContractRefused) as refusal:
        lifecycle._observe_hgcc_contract(REPOSITORY)
    assert str(refusal.value) == (
        "PUBLICATION_CONTRACT_REFUSED: HGCC-1 GitHub contract unavailable: "
        "Missing or malformed GitHub ruleset bypass actors"
    )


def test_every_active_ruleset_must_carry_the_array_not_just_the_first(monkeypatch, tmp_path):
    for broken in (24526712, 23493180):
        (
            _world(monkeypatch, tmp_path / str(broken), break_id=broken)
            if (tmp_path / str(broken)).mkdir() is None
            else None
        )
        with pytest.raises(lifecycle.LifecycleRefused, match="bypass actors"):
            lifecycle._observe_hgcc_contract(REPOSITORY)


def test_bypass_actors_that_is_not_a_list_refuses(monkeypatch, tmp_path):
    _world(monkeypatch, tmp_path)
    for bad in (None, {}, "none", 0):
        details = {i: dict(DETAIL, id=i, bypass_actors=[] if i != 24526712 else bad) for i, _ in ACTIVE}
        binary = (
            _install_fake_gh(tmp_path / f"bad-{type(bad).__name__}", details=details)
            if (tmp_path / f"bad-{type(bad).__name__}").mkdir() is None
            else None
        )
        monkeypatch.setenv("PATH", f"{binary}{os.pathsep}{os.environ['PATH']}")
        monkeypatch.setenv(lifecycle.HGCC_TOKEN_ENV, READ_TOKEN)
        with pytest.raises(lifecycle.LifecycleRefused, match="bypass actors"):
            lifecycle._observe_hgcc_contract(REPOSITORY)


def test_a_failing_observer_never_leaks_its_output_or_any_credential(monkeypatch, tmp_path, capsys):
    binary = _install_fake_gh(tmp_path, details={}, exit_code=7)
    monkeypatch.setenv("PATH", f"{binary}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv(lifecycle.HGCC_TOKEN_ENV, READ_TOKEN)
    with pytest.raises(lifecycle.LifecycleRefused) as refusal:
        lifecycle._observe_hgcc_contract(REPOSITORY)
    shown = str(refusal.value) + capsys.readouterr().out + capsys.readouterr().err
    reason = str(refusal.value).split("unavailable: ", 1)[1]
    assert reason in (*contract.REFUSAL_REASONS, "UNCLASSIFIED")
    for leaked in (*SECRET_VALUES, "/secret/path", "boom"):
        assert leaked not in shown


def _stub_subprocess(monkeypatch, *, returncode=0, stdout="", stderr="", raises=None):
    calls: list[tuple[tuple, dict]] = []

    def fake(*args, **kwargs):
        calls.append((args, kwargs))
        if raises is not None:
            raise raises
        return SimpleNamespace(returncode=returncode, stdout=stdout, stderr=stderr)

    monkeypatch.setattr(lifecycle.subprocess, "run", fake)
    monkeypatch.setenv(lifecycle.HGCC_TOKEN_ENV, READ_TOKEN)
    return calls


def _contract(**overrides):
    base = {"schema": "hgcc-1/v1", "repository": REPOSITORY, "rulesets": [{"id": 1, "bypass_actors": []}]}
    return json.dumps({**base, **overrides})


@pytest.mark.parametrize(
    "stdout",
    [
        "",
        "not json",
        "[]",
        _contract(schema="other"),
        _contract(repository="evil/other"),
        _contract(rulesets=[]),
        _contract(rulesets="x"),
        _contract(rulesets=[{"id": 1}]),
        _contract(rulesets=[{"id": 1, "bypass_actors": None}]),
        _contract(rulesets=[{"id": 1, "bypass_actors": []}, {"id": 2, "bypass_actors": {}}]),
    ],
)
def test_incomplete_or_foreign_contract_data_refuses_even_when_the_observer_exits_zero(monkeypatch, stdout):
    _stub_subprocess(monkeypatch, stdout=stdout)
    with pytest.raises(lifecycle.LifecycleRefused, match="OBSERVATION_INCOMPLETE"):
        lifecycle._observe_hgcc_contract(REPOSITORY)


def test_a_complete_contract_passes_and_the_credential_is_only_in_the_child_environment(monkeypatch):
    calls = _stub_subprocess(monkeypatch, stdout=_contract())
    monkeypatch.setenv("GITHUB_TOKEN", RUNNER_TOKEN)
    lifecycle._observe_hgcc_contract(REPOSITORY)
    ((args, kwargs),) = calls
    assert READ_TOKEN not in json.dumps(args)
    assert kwargs["env"]["GH_TOKEN"] == READ_TOKEN and "GITHUB_TOKEN" not in kwargs["env"]
    assert args[0][-1] == REPOSITORY and args[0][1].endswith("hunter_github_contract.py")


@pytest.mark.parametrize("raises", [OSError("no gh"), subprocess.TimeoutExpired("gh", 1)])
def test_an_observer_that_cannot_run_or_times_out_refuses(monkeypatch, raises):
    _stub_subprocess(monkeypatch, raises=raises)
    with pytest.raises(lifecycle.LifecycleRefused, match="OBSERVATION_FAILED"):
        lifecycle._observe_hgcc_contract(REPOSITORY)


def test_only_known_reasons_are_reported_anything_else_is_unclassified(monkeypatch):
    known = contract.REFUSAL_REASONS[-1]
    _stub_subprocess(monkeypatch, returncode=2, stderr=f"[HGCC-1] INDETERMINATE: {known}\n")
    with pytest.raises(lifecycle.LifecycleRefused) as refusal:
        lifecycle._observe_hgcc_contract(REPOSITORY)
    assert str(refusal.value).endswith(known)
    _stub_subprocess(monkeypatch, returncode=2, stderr=f"[HGCC-1] INDETERMINATE: leaked {READ_TOKEN} /private\n")
    with pytest.raises(lifecycle.LifecycleRefused) as refusal:
        lifecycle._observe_hgcc_contract(REPOSITORY)
    assert str(refusal.value).endswith("UNCLASSIFIED") and READ_TOKEN not in str(refusal.value)


def test_the_refusal_vocabulary_covers_every_literal_the_observer_can_raise():
    tree = ast.parse((ROOT / "scripts" / "hunter_github_contract.py").read_text())
    literals = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name in {"github_get", "observe_contract"}:
            for inner in ast.walk(node):
                if (
                    isinstance(inner, ast.Raise)
                    and isinstance(inner.exc, ast.Call)
                    and getattr(inner.exc.func, "id", "") == "ValueError"
                    and inner.exc.args
                    and isinstance(inner.exc.args[0], ast.Constant)
                ):
                    literals.add(inner.exc.args[0].value)
    assert literals and literals <= set(contract.REFUSAL_REASONS)


def test_observe_contract_itself_still_requires_the_array_on_every_active_ruleset():
    def get(bad: int | None):
        def fetch(path: str):
            if "/rulesets?" in path:
                return [{"id": i, "enforcement": e} for i, e in ACTIVE]
            ruleset_id = int(path.rsplit("/", 1)[1])
            detail = dict(DETAIL, id=ruleset_id)
            if ruleset_id != bad:
                detail["bypass_actors"] = [{"actor_id": 1, "actor_type": "RepositoryRole", "bypass_mode": "always"}]
            return detail

        return fetch

    observed = contract.observe_contract(REPOSITORY, get=get(None))
    assert [item["id"] for item in observed["rulesets"]] == [
        20523851,
        24526712,
        23493180,
    ]  # disabled one is not observed
    for bad in (20523851, 24526712, 23493180):
        with pytest.raises(ValueError, match="bypass actors"):
            contract.observe_contract(REPOSITORY, get=get(bad))


def test_publication_observes_before_anything_else_and_the_rest_of_the_flow_keeps_the_runner_token(monkeypatch):
    order: list[str] = []

    class Stop(Exception):
        pass

    class Github:
        def get(self, path: str):
            order.append("github:" + path.split("?")[0].rsplit("/", 1)[-1])
            raise Stop

    def observe(repository: str) -> None:
        order.append("observe:" + repository)

    monkeypatch.setattr(lifecycle, "_observe_hgcc_contract", observe)
    monkeypatch.setattr(lifecycle, "_github", lambda configuration, **_: Github())
    configuration = SimpleNamespace(repository=REPOSITORY)
    with pytest.raises(Stop):
        lifecycle.cmd_publish(configuration, SimpleNamespace(workroot="w"))
    assert order == ["observe:" + REPOSITORY, "github:pulls"]
    # A refusal from the observation stops publication before any PR read.
    order.clear()

    def refuse(repository: str) -> None:
        raise lifecycle.LifecycleRefused("PUBLICATION_UNAVAILABLE", "HGCC-1 GitHub contract unavailable: X")

    monkeypatch.setattr(lifecycle, "_observe_hgcc_contract", refuse)
    with pytest.raises(lifecycle.LifecycleRefused):
        lifecycle.cmd_publish(configuration, SimpleNamespace(workroot="w"))
    assert order == []


# --- definitive versus transient publisher refusals (stuck-authorization recovery) -----------------------


def test_observer_reasons_partition_into_definitive_and_undetermined():
    assert set(contract.DEFINITIVE_REASONS) | set(contract.UNDETERMINED_REASONS) == set(contract.REFUSAL_REASONS)
    assert not set(contract.DEFINITIVE_REASONS) & set(contract.UNDETERMINED_REASONS)
    assert "Missing or malformed GitHub ruleset bypass actors" in contract.DEFINITIVE_REASONS
    assert "GitHub contract API unavailable or access denied" in contract.UNDETERMINED_REASONS


def test_an_incomplete_contract_with_a_clean_exit_is_definitive(monkeypatch):
    _stub_subprocess(monkeypatch, stdout=_contract(rulesets=[{"id": 1}]))
    with pytest.raises(lifecycle.PublicationContractRefused):
        lifecycle._observe_hgcc_contract(REPOSITORY)


@pytest.mark.parametrize("reason", contract.UNDETERMINED_REASONS)
def test_an_undetermined_observation_stays_transient(monkeypatch, reason):
    _stub_subprocess(monkeypatch, returncode=1, stderr=f"[HGCC-1] INDETERMINATE: {reason}\n")
    with pytest.raises(lifecycle.LifecycleRefused) as refusal:
        lifecycle._observe_hgcc_contract(REPOSITORY)
    assert not isinstance(refusal.value, lifecycle.PublicationContractRefused)
    assert refusal.value.code == "PUBLICATION_UNAVAILABLE"


@pytest.mark.parametrize("reason", contract.DEFINITIVE_REASONS)
def test_a_definitive_observer_verdict_is_a_contract_refusal(monkeypatch, reason):
    _stub_subprocess(monkeypatch, returncode=1, stderr=f"[HGCC-1] INDETERMINATE: {reason}\n")
    with pytest.raises(lifecycle.PublicationContractRefused):
        lifecycle._observe_hgcc_contract(REPOSITORY)


@pytest.mark.parametrize(
    "outage",
    [
        {"raises": OSError("no gh")},
        {"returncode": 3, "stderr": "boom"},
        {"returncode": 1, "stderr": "[HGCC-1] INDETERMINATE: not a closed reason"},
    ],
)
def test_outages_and_unclassified_failures_stay_transient(monkeypatch, outage):
    _stub_subprocess(monkeypatch, **outage)
    with pytest.raises(lifecycle.LifecycleRefused) as refusal:
        lifecycle._observe_hgcc_contract(REPOSITORY)
    assert not isinstance(refusal.value, lifecycle.PublicationContractRefused)


def test_a_definitive_refusal_uploads_the_refused_outcome_and_publishes_nothing(monkeypatch, tmp_path, capsys):
    def observe(repository: str) -> None:
        raise lifecycle.PublicationContractRefused("HGCC-1 GitHub contract unavailable: X")

    def forbidden(*_a, **_k):
        raise AssertionError("nothing may be read or pushed after a definitive refusal")

    monkeypatch.setattr(lifecycle, "_observe_hgcc_contract", observe)
    monkeypatch.setattr(lifecycle, "_github", forbidden)
    monkeypatch.setattr(lifecycle.roles, "run_publisher", forbidden)
    monkeypatch.delenv("GITHUB_OUTPUT", raising=False)
    out = tmp_path / "outcome.json"
    arguments = SimpleNamespace(out=str(out), authorization_id="hunter-issue-agent-authorization:" + "a" * 64)
    assert lifecycle.cmd_publish(SimpleNamespace(repository=REPOSITORY), arguments) == 0
    assert json.loads(out.read_text()) == {
        "schema_version": lifecycle.control.OUTCOME_SCHEMA_VERSION,
        "authorization_id": arguments.authorization_id,
        "verdict": "REFUSED",
        "code": "PUBLICATION_CONTRACT_REFUSED",
    }
    assert "refused=true" in capsys.readouterr().out


def test_a_transient_refusal_writes_no_outcome_and_stays_within_the_resume_budget(monkeypatch, tmp_path):
    def observe(repository: str) -> None:
        raise lifecycle.LifecycleRefused("PUBLICATION_UNAVAILABLE", "HGCC-1 GitHub contract unavailable: X")

    monkeypatch.setattr(lifecycle, "_observe_hgcc_contract", observe)
    out = tmp_path / "outcome.json"
    with pytest.raises(lifecycle.LifecycleRefused):
        lifecycle.cmd_publish(SimpleNamespace(repository=REPOSITORY), SimpleNamespace(out=str(out)))
    assert not out.exists()
