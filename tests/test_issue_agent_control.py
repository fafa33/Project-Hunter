"""Control-domain recorder (S5): provenance (AT-44), anchor, facts, decisions, records -- on the real stack."""

from __future__ import annotations

import dataclasses
import hashlib
import json
import subprocess
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

import pytest
import test_issue_agent_roles as rt

from hunter.automation import issue_agent_control as control
from hunter.automation import issue_agent_state as state

LIFECYCLE, RECONCILE = control.LIFECYCLE_WORKFLOW, control.RECONCILE_WORKFLOW
BRANCH = f"issue-{rt.ISSUE}-{'a' * 16}"
PIN = state.AnchorPin(ruleset_id=7, updated_at="2026-10-04T00:00:00Z")
NOW = datetime(2026, 10, 4, 13, 0, tzinfo=UTC)
CONFIG = control.Configuration(
    repository=rt.REPOSITORY,
    repository_id=1,
    owner_login="fafa33",
    state_keys=rt.TRUST.state_keys,
    anchor=PIN,
    handoff_recipient=rt.HANDOFF_KEY.public_key(),
    result_recipient=rt.RESULT_KEY.public_key(),
    writer=rt.WRITER,
    authorization_verifying_key="ab" * 32,
    prompt_verifying_key="cd" * 32,
    source_handling=control.SourceHandlingRoot(
        "ef" * 32, hashlib.sha256(bytes.fromhex("ef" * 32)).hexdigest(), "01" * 32
    ),
)


def run(run_id: int, path: str = LIFECYCLE, event: str = "issues", **overrides: Any) -> dict[str, Any]:
    value = {
        "id": run_id,
        "run_attempt": 1,
        "path": path,
        "event": event,
        "head_branch": "main",
        "head_sha": rt.CONTROL,
        "repository": {"id": 1},
        "head_repository": {"id": 1},
        "status": "completed",
    }
    value.update(overrides)
    return value


class FakeGitHub:
    """Definitive facts by path. Unlisted paths are indefinite, so a forgotten fact can never pass silently."""

    def __init__(self) -> None:
        R = rt.REPOSITORY
        self.routes: dict[str, Any] = {
            f"/repos/{R}/rulesets/7": {
                "id": 7,
                "enforcement": "active",
                "updated_at": PIN.updated_at,
                "rules": [{"type": "deletion"}, {"type": "non_fast_forward"}],
            },
            f"/repos/{R}/rules/branches/hunter-state/v1/issue-{rt.ISSUE}": [
                {"type": "deletion", "ruleset_id": 7},
                {"type": "non_fast_forward", "ruleset_id": 7},
            ],
            f"/repos/{R}/actions/runs/100/attempts/1": run(100),
            f"/repos/{R}/actions/runs/300/attempts/1": run(300, RECONCILE, "schedule"),
            f"/repos/{R}/actions/runs/100": run(100, status="in_progress"),
            f"/repos/{R}/actions/runs/100/jobs": {"total_count": 1, "jobs": [self.job("execute", 9)]},
            f"/repos/{R}/git/ref/heads/{BRANCH}": None,
            f"/repos/{R}/pulls": [],
            f"/repos/{R}/actions/runs/100/artifacts": [],
        }
        self.blobs: dict[int, bytes | None] = {}
        self.dispatched: list[tuple[str, dict[str, str]]] = []
        self.reads: list[str] = []

    @staticmethod
    def job(name: str, job_id: int, conclusion: str = "success", status: str = "completed") -> dict[str, Any]:
        return {"id": job_id, "name": name, "status": status, "conclusion": conclusion}

    def artifact(self, artifact_id: int, name: str, content: bytes | None, **extra: Any) -> dict[str, Any]:
        self.blobs[artifact_id] = content
        item = {"id": artifact_id, "name": name, "digest": "sha256:" + "e" * 64, "size_in_bytes": 10, "expired": False}
        item.update(extra)
        self.routes[f"/repos/{rt.REPOSITORY}/actions/artifacts/{artifact_id}"] = item
        return item

    def get(self, path: str) -> control.Read:
        self.reads.append(path)
        parts = urlsplit(path)
        if parts.path not in self.routes:
            return control.Read("unknown")
        value = self.routes[parts.path]
        if isinstance(value, control.Read):
            return value
        if value is None:
            return control.Read("absent")
        if parts.path.endswith("/artifacts") and isinstance(value, list):
            name = parse_qs(parts.query).get("name", [""])[0]
            items = [item for item in value if item["name"] == name]
            return control.Read("ok", {"total_count": len(items), "artifacts": items})
        return control.Read("ok", value)

    def download_artifact(self, artifact_id: int) -> control.Read:
        if artifact_id not in self.blobs:
            return control.Read("unknown")
        return control.Read("ok", self.blobs[artifact_id])

    def dispatch(self, workflow_file: str, inputs: Mapping[str, str]) -> bool:
        self.dispatched.append((workflow_file, dict(inputs)))
        return True


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
    rt.git(seed, "add", "README.md")
    rt.git(seed, "-c", "user.name=s", "-c", "user.email=s@s", "-c", "commit.gpgsign=false", "commit", "-qm", "base")
    rt.git(seed, "push", "-q", str(remote), "HEAD:refs/heads/main")
    trusted_repo = tmp_path / "trusted"
    subprocess.run(["git", "clone", "--quiet", str(remote), str(trusted_repo)], check=True)
    return {"tmp": tmp_path, "remote": str(remote), "base": rt.git(seed, "rev-parse", "HEAD"), "trusted": trusted_repo}


@pytest.fixture
def signing_key(tmp_path: Path) -> str:
    path = tmp_path / "signing" / "id"
    path.parent.mkdir()
    subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(path)], check=True)
    return str(path)


def writer(role: str = "bind", job: str | None = None, workflow: str = LIFECYCLE, run_id: int = 100) -> control.Writer:
    return control.Writer(workflow, job or role, role, run_id, 1, rt.CONTROL)


def step(
    repos: dict[str, Any],
    github: FakeGitHub,
    role: str = "bind",
    configuration: control.Configuration = CONFIG,
    **kw: Any,
) -> state.Decision:
    who = writer("reconcile", workflow=RECONCILE, run_id=300) if role == "reconcile" else writer(role)
    return control.step(
        github=github,
        configuration=configuration,
        store=state.GitLedgerStore(repos["remote"], workdir=repos["tmp"] / f"ctl-{len(github.reads)}-{role}"),
        signing_key=rt.KEY,
        writer=who,
        issue=rt.ISSUE,
        clock=lambda: NOW,
        new_nonce=lambda: "f" * 64,
        **kw,
    )


def ledger_state(repos: dict[str, Any]) -> state.AuthorizationView:
    _, entries = state.GitLedgerStore(repos["remote"], workdir=repos["tmp"] / "check").read(rt.ISSUE)
    view = state.verify_chain(
        [e.record for e in entries], repository_id=1, issue_number=rt.ISSUE, trust=rt.TRUST, provenance=rt.trusted
    )
    return view.authorizations[rt.AUTH]


def executed(repos: dict[str, Any]) -> tuple[rt.Ledger, bytes]:
    ledger, envelope = rt.authorize(repos)
    outcome = rt.execute(repos, ledger, envelope, rt.RecordingIsolation(rt.CANARY))
    assert outcome.sealed_result is not None
    return ledger, outcome.sealed_result


# --- configuration (MISSING_CONFIGURATION is the inert S5 state) --------------------------------------


def test_the_committed_s6_trust_roots_are_provisioned_and_loadable() -> None:
    configuration = control.load_configuration(Path(__file__).resolve().parents[1])
    assert configuration.repository == "fafa33/Project-Hunter"
    assert configuration.repository_id == 1292945327
    assert configuration.owner_login == "fafa33"
    assert configuration.anchor.ruleset_id == 24526712


def provisioned(**overrides: Any) -> dict[str, Any]:
    document = {
        "schema_version": control.TRUST_ROOTS_SCHEMA_VERSION,
        "provisioned": True,
        "repository": rt.REPOSITORY,
        "repository_id": 1,
        "owner_login": "fafa33",
        "state_keys": [rt.KEY.public_key().public_bytes_raw().hex()],
        "anchor": {"ruleset_id": 7, "updated_at": PIN.updated_at},
        "handoff_recipient": rt.HANDOFF_KEY.public_key().public_bytes_raw().hex(),
        "result_recipient": rt.RESULT_KEY.public_key().public_bytes_raw().hex(),
        "writer": {"login": "fafa33", "name": "Farhad5778", "email": "34549283+fafa33@users.noreply.github.com"},
        "authorization_verifying_key": rt.KEY.public_key().public_bytes_raw().hex(),
        "prompt_verifying_key": rt.KEY.public_key().public_bytes_raw().hex(),
        "source_handling": {
            "verification_key": rt.KEY.public_key().public_bytes_raw().hex(),
            "verification_key_sha256": hashlib.sha256(rt.KEY.public_key().public_bytes_raw()).hexdigest(),
            "genesis_rule_sha256": "01" * 32,
        },
    }
    document.update(overrides)
    return document


def write_roots(tmp_path: Path, document: object) -> Path:
    path = tmp_path / control.TRUST_ROOTS_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(document))
    return tmp_path


def test_provisioned_trust_roots_load_exactly(tmp_path: Path) -> None:
    configuration = control.load_configuration(write_roots(tmp_path, provisioned()))
    assert configuration.trust.state_keys.keys() == rt.TRUST.state_keys.keys()
    assert configuration.anchor == PIN and configuration.writer == rt.WRITER


@pytest.mark.parametrize(
    "document",
    [
        provisioned(provisioned=False),
        provisioned(provisioned="true"),
        provisioned(schema_version="v0"),
        provisioned(extra=1),
        provisioned(state_keys=[]),
        provisioned(state_keys=["00" * 31]),
        provisioned(repository_id=0),
        provisioned(repository="not a repository"),
        provisioned(anchor={"ruleset_id": 7}),
        provisioned(writer={"login": "fafa33", "name": "x"}),
        provisioned(prompt_verifying_key="zz"),
        provisioned(source_handling={"verification_key": "ab" * 32}),
        provisioned(
            source_handling={
                "verification_key": "ab" * 32,
                "verification_key_sha256": "00" * 32,
                "genesis_rule_sha256": "01" * 32,
            }
        ),
        [],
    ],
)
def test_any_incomplete_trust_root_is_missing_configuration(tmp_path: Path, document: object) -> None:
    with pytest.raises(control.ControlRefused, match="MISSING_CONFIGURATION"):
        control.load_configuration(write_roots(tmp_path, document))


# --- provenance (AT-44) and anchor ---------------------------------------------------------------------


RECORDED = {
    "workflow_path": LIFECYCLE,
    "job": "bind",
    "role": "bind",
    "run_id": 100,
    "run_attempt": 1,
    "head_sha": rt.CONTROL,
}


def test_a_record_from_a_trusted_lifecycle_run_is_valid() -> None:
    assert control.run_provenance(FakeGitHub(), CONFIG)(RECORDED, {}) is True


@pytest.mark.parametrize(
    ("recorded", "run_overrides"),
    [
        ({"workflow_path": ".github/workflows/ci.yml"}, {}),
        ({"job": "publish"}, {}),
        ({"role": "finalize"}, {}),
        ({"run_attempt": 2}, {}),
        ({}, {"head_branch": "issue-1-attack"}),
        ({}, {"head_sha": "d" * 40}),
        ({}, {"path": ".github/workflows/ci.yml"}),
        ({}, {"event": "pull_request_target"}),
        ({}, {"run_attempt": 2}),
        ({}, {"repository": {"id": 2}}),
        ({}, {"head_repository": {"id": 2}}),
        ({}, {"id": 101}),
    ],
)
def test_a_record_from_any_other_run_is_invalid(recorded: dict[str, Any], run_overrides: dict[str, Any]) -> None:
    github = FakeGitHub()
    github.routes[f"/repos/{rt.REPOSITORY}/actions/runs/100/attempts/1"] = run(100, **run_overrides)
    assert control.run_provenance(github, CONFIG)({**RECORDED, **recorded}, {}) is False


def test_a_deleted_run_is_invalid_but_an_outage_is_never_a_verdict() -> None:
    github = FakeGitHub()
    github.routes[f"/repos/{rt.REPOSITORY}/actions/runs/100/attempts/1"] = None
    assert control.run_provenance(github, CONFIG)(RECORDED, {}) is False
    github.routes[f"/repos/{rt.REPOSITORY}/actions/runs/100/attempts/1"] = control.Read("unknown")
    with pytest.raises(control.FactsUnavailable):
        control.run_provenance(github, CONFIG)(RECORDED, {})


def test_an_anchor_mismatch_is_the_global_freeze_and_an_outage_is_a_noop() -> None:
    github = FakeGitHub()
    control.require_anchor(github, CONFIG, state.ledger_ref(rt.ISSUE))
    github.routes[f"/repos/{rt.REPOSITORY}/rulesets/7"]["updated_at"] = "2026-10-05T00:00:00Z"
    with pytest.raises(control.Frozen, match="ANCHOR_INTEGRITY_FAILED"):
        control.require_anchor(github, CONFIG, state.ledger_ref(rt.ISSUE))
    github.routes[f"/repos/{rt.REPOSITORY}/rulesets/7"] = control.Read("unknown")
    with pytest.raises(control.FactsUnavailable):
        control.require_anchor(github, CONFIG, state.ledger_ref(rt.ISSUE))


# --- T2 bind ----------------------------------------------------------------------------------------


def test_bind_records_the_single_result_artifact_from_its_sealed_header(repos: dict[str, Any]) -> None:
    _, sealed = executed(repos)
    github = FakeGitHub()
    github.routes[f"/repos/{rt.REPOSITORY}/actions/runs/100/artifacts"] = [
        github.artifact(22, state.result_artifact_name(rt.AUTH), sealed)
    ]
    assert step(repos, github).target_state == state.RESULT_BOUND
    view = ledger_state(repos)
    evidence = view.evidence[state.RESULT_BOUND]
    assert view.state == state.RESULT_BOUND
    assert evidence["result_artifact"]["artifact_id"] == 22 and evidence["executor_job_id"] == 9
    assert evidence["result_artifact"]["ciphertext_sha256"] == state.sha256_hex(sealed)
    assert evidence["result_artifact"]["recipient_key_id"] == rt.recipient_key_id(rt.RESULT_KEY.public_key())


@pytest.mark.parametrize("tamper", ["foreign recipient", "not an envelope", "duplicate"])
def test_bind_refuses_a_result_that_is_not_the_expected_artifact(repos: dict[str, Any], tamper: str) -> None:
    _, sealed = executed(repos)
    github = FakeGitHub()
    name = state.result_artifact_name(rt.AUTH)
    items = [github.artifact(22, name, b"{}" if tamper == "not an envelope" else sealed)]
    if tamper == "duplicate":
        items.append(github.artifact(23, name, sealed))
    github.routes[f"/repos/{rt.REPOSITORY}/actions/runs/100/artifacts"] = items
    configuration = (
        dataclasses.replace(CONFIG, result_recipient=rt.HANDOFF_KEY.public_key())
        if tamper == "foreign recipient"
        else CONFIG
    )
    assert step(repos, github, configuration=configuration).code == "TRANSPORT_INTEGRITY_FAILED"
    view = ledger_state(repos)
    assert view.state == state.FAILED and view.evidence[state.FAILED]["code"] == "TRANSPORT_INTEGRITY_FAILED"


@pytest.mark.parametrize(
    ("conclusion", "advisory", "code"),
    [
        ("skipped", None, "EXECUTION_NOT_STARTED"),
        ("failure", None, "EXECUTION_NOT_COMPLETED"),
        ("success", "SECRET_IN_RESULT", "SECRET_IN_RESULT"),
        ("failure", "MODEL_TIMEOUT", "EXECUTOR_RESULT_TIMEOUT"),
        ("failure", "NOT_A_CODE", "EXECUTION_NOT_COMPLETED"),
    ],
)
def test_no_result_fails_closed_and_never_redispatches_the_model(
    repos: dict[str, Any], conclusion: str, advisory: str | None, code: str
) -> None:
    rt.authorize(repos)
    github = FakeGitHub()
    github.routes[f"/repos/{rt.REPOSITORY}/actions/runs/100/jobs"] = {
        "total_count": 1,
        "jobs": [github.job("execute", 9, conclusion)],
    }
    assert step(repos, github, executor_advisory_code=advisory).code == code
    assert ledger_state(repos).evidence[state.FAILED]["code"] == code
    assert github.dispatched == []


def test_an_indefinite_fact_writes_nothing(repos: dict[str, Any]) -> None:
    rt.authorize(repos)
    github = FakeGitHub()
    github.routes[f"/repos/{rt.REPOSITORY}/actions/runs/100/artifacts"] = control.Read("unknown")
    with pytest.raises(control.FactsUnavailable):
        step(repos, github)
    assert ledger_state(repos).state == state.AUTHORIZED


def test_a_still_running_executor_is_a_noop(repos: dict[str, Any]) -> None:
    rt.authorize(repos)
    github = FakeGitHub()
    github.routes[f"/repos/{rt.REPOSITORY}/actions/runs/100/jobs"] = {
        "total_count": 1,
        "jobs": [github.job("execute", 9, None, "in_progress")],  # type: ignore[arg-type]
    }
    assert step(repos, github).action == "noop"
    assert ledger_state(repos).state == state.AUTHORIZED


def test_a_branch_before_validation_freezes_without_any_write(repos: dict[str, Any]) -> None:
    rt.authorize(repos)
    github = FakeGitHub()
    github.routes[f"/repos/{rt.REPOSITORY}/git/ref/heads/{BRANCH}"] = {"object": {"sha": "d" * 40}}
    with pytest.raises(control.Frozen, match="STATE_ROLLBACK_SUSPECTED"):
        step(repos, github)
    assert ledger_state(repos).state == state.AUTHORIZED


def test_a_forged_ledger_record_freezes(repos: dict[str, Any]) -> None:
    rt.authorize(repos)
    github = FakeGitHub()
    github.routes[f"/repos/{rt.REPOSITORY}/actions/runs/100/attempts/1"] = run(100, head_branch="attacker")
    with pytest.raises(control.Frozen, match="STATE_CORRUPT"):
        step(repos, github)


# --- T3 record-validation -----------------------------------------------------------------------------


def validation_world(repos: dict[str, Any], receipt: dict[str, Any] | None) -> FakeGitHub:
    github = FakeGitHub()
    github.routes[f"/repos/{rt.REPOSITORY}/actions/runs/100/jobs"] = {
        "total_count": 2,
        "jobs": [github.job("execute", 9), github.job("validate", 10)],
    }
    github.artifact(22, state.result_artifact_name(rt.AUTH), b"sealed")
    name = control.outcome_artifact_name(rt.AUTH, "validation", 1)
    items = [] if receipt is None else [github.artifact(31, name, json.dumps(receipt).encode())]
    github.routes[f"/repos/{rt.REPOSITORY}/actions/runs/100/artifacts"] = items
    return github


def test_a_matching_pass_receipt_records_validated(repos: dict[str, Any]) -> None:
    ledger, result = rt.bound_result(repos)
    receipt = rt.validate(repos, ledger, result)
    assert step(repos, validation_world(repos, receipt), role="record-validation").target_state == state.VALIDATED
    evidence = ledger_state(repos).evidence[state.VALIDATED]
    assert evidence["unsigned_commit_sha"] == receipt["unsigned_commit_sha"]
    assert evidence["validator_run_id"] == 100 and evidence["validation_attempts"] == 1


@pytest.mark.parametrize("code", sorted(state.VALIDATION_REFUSAL_CODES))
def test_a_trusted_validator_refusal_is_terminal(repos: dict[str, Any], code: str) -> None:
    ledger, result = rt.bound_result(repos)
    bound = ledger.view.authorizations[rt.AUTH].evidence
    refusal = {
        "schema_version": "hunter-issue-agent-validation-receipt-v2",
        "authorization_id": rt.AUTH,
        "execution_id": bound[state.AUTHORIZED]["execution_id"],
        "ciphertext_sha256": bound[state.RESULT_BOUND]["result_artifact"]["ciphertext_sha256"],
        "verdict": "REFUSED",
        "code": code,
    }
    assert step(repos, validation_world(repos, refusal), role="record-validation").code == code


@pytest.mark.parametrize(
    "mutation",
    [
        {"result_sha256": "0" * 64},
        {"execution_id": "0" * 64},
        {"ciphertext_sha256": "0" * 64},
        {"base_sha": "0" * 40},
        {"verdict": "REFUSED", "code": "VALIDATION_UNAVAILABLE"},
        {"smuggled": "field"},
    ],
)
def test_a_receipt_not_bound_to_the_ledger_is_ignored_and_validation_resumes(
    repos: dict[str, Any], mutation: dict[str, Any]
) -> None:
    ledger, result = rt.bound_result(repos)
    receipt = {**rt.validate(repos, ledger, result), **mutation}
    decision = step(repos, validation_world(repos, receipt), role="record-validation")
    assert decision.action == "resume"
    view = ledger_state(repos)
    assert view.state == state.RESULT_BOUND and view.pending_resume is not None


def test_a_resume_is_recorded_before_its_nonce_is_dispatched(repos: dict[str, Any]) -> None:
    rt.bound_result(repos)
    github = validation_world(repos, None)
    assert step(repos, github, role="record-validation").action == "resume"
    pending = ledger_state(repos).pending_resume
    assert pending == {
        "stage": "validation",
        "nonce": "f" * 64,
        "attempt": 1,
        "dispatched_at": "2026-10-04T13:00:00Z",
        "bound_run_id": None,
    }
    assert github.dispatched == [
        (
            "hunter-issue-agent-trigger.yml",
            {"issue": str(rt.ISSUE), "authorization_id": rt.AUTH, "stage": "validation", "nonce": "f" * 64},
        )
    ]


def test_bind_role_cannot_write_validation(repos: dict[str, Any]) -> None:
    ledger, result = rt.bound_result(repos)
    receipt = rt.validate(repos, ledger, result)
    assert step(repos, validation_world(repos, receipt), role="bind").action == "noop"
    assert ledger_state(repos).state == state.RESULT_BOUND


# --- resume binding ---------------------------------------------------------------------------------


def resume_world(repos: dict[str, Any]) -> FakeGitHub:
    rt.bound_result(repos)
    github = validation_world(repos, None)
    step(repos, github, role="record-validation")
    github.routes[f"/repos/{rt.REPOSITORY}/actions/runs/400/attempts/1"] = run(400, event="workflow_dispatch")
    github.routes[f"/repos/{rt.REPOSITORY}/compare/{rt.CONTROL}...main"] = {"status": "ahead"}
    return github


def bind_resume(repos: dict[str, Any], github: FakeGitHub, nonce: str = "f" * 64, stage: str = "validation") -> str:
    return control.bind_resume(
        github=github,
        configuration=CONFIG,
        store=state.GitLedgerStore(repos["remote"], workdir=repos["tmp"] / f"bind-{len(github.reads)}"),
        signing_key=rt.KEY,
        writer=writer("bind", job="resume-bind", run_id=400),
        issue=rt.ISSUE,
        authorization_id=rt.AUTH,
        stage=stage,
        nonce=nonce,
        clock=lambda: NOW,
    )


def test_a_resume_run_binds_its_nonce_once(repos: dict[str, Any]) -> None:
    github = resume_world(repos)
    assert bind_resume(repos, github) == rt.CONTROL
    assert ledger_state(repos).pending_resume["bound_run_id"] == 400  # type: ignore[index]
    with pytest.raises(control.ControlRefused, match="RERUN_REFUSED"):
        bind_resume(repos, github)


@pytest.mark.parametrize(("nonce", "stage"), [("e" * 64, "validation"), ("f" * 64, "publication")])
def test_a_stale_or_foreign_resume_is_refused_without_writing(repos: dict[str, Any], nonce: str, stage: str) -> None:
    github = resume_world(repos)
    with pytest.raises(control.ControlRefused, match="RERUN_REFUSED"):
        bind_resume(repos, github, nonce, stage)
    assert ledger_state(repos).pending_resume["bound_run_id"] is None  # type: ignore[index]


def test_a_control_commit_no_longer_on_main_fails_the_authorization(repos: dict[str, Any]) -> None:
    github = resume_world(repos)
    github.routes[f"/repos/{rt.REPOSITORY}/compare/{rt.CONTROL}...main"] = {"status": "diverged"}
    with pytest.raises(control.ControlRefused, match="CONTROL_SHA_NOT_ON_MAIN"):
        bind_resume(repos, github)
    assert ledger_state(repos).evidence[state.FAILED]["code"] == "CONTROL_SHA_NOT_ON_MAIN"


# --- T4 finalize ---------------------------------------------------------------------------------------


def commit_json(repos: dict[str, Any], sha: str, *, verified: bool = True, login: str = "fafa33") -> dict[str, Any]:
    raw = rt.git(repos["trusted"], "cat-file", "commit", sha) + "\n"
    lines, payload, skipping = raw.split("\n"), [], False
    for line in lines:
        if line.startswith("gpgsig "):
            skipping = True
            continue
        if skipping and line.startswith(" "):
            continue
        skipping = False
        payload.append(line)
    return {
        "sha": sha,
        "parents": [{"sha": p} for p in rt.git(repos["trusted"], "rev-list", "--parents", "-n", "1", sha).split()[1:]],
        "author": {"login": login},
        "committer": {"login": login},
        "commit": {
            "tree": {"sha": rt.git(repos["trusted"], "rev-parse", f"{sha}^{{tree}}")},
            "verification": {
                "verified": verified,
                "reason": "valid" if verified else "unsigned",
                "payload": "\n".join(payload),
            },
        },
    }


def published_world(repos: dict[str, Any], signing_key: str) -> tuple[FakeGitHub, str]:
    ledger, result, _ = rt.validated_ledger(repos)
    head = rt.publish(repos, ledger, result, signing_key).head_sha
    rt.git(repos["trusted"], "fetch", "-q", repos["remote"], f"refs/heads/{BRANCH}")
    github = FakeGitHub()
    github.routes[f"/repos/{rt.REPOSITORY}/actions/runs/100/jobs"] = {
        "total_count": 3,
        "jobs": [github.job("execute", 9), github.job("validate", 10), github.job("publish", 11)],
    }
    github.artifact(22, state.result_artifact_name(rt.AUTH), b"sealed")
    github.routes[f"/repos/{rt.REPOSITORY}/git/ref/heads/{BRANCH}"] = {"object": {"sha": head}}
    github.routes[f"/repos/{rt.REPOSITORY}/commits/{head}"] = commit_json(repos, head)
    return github, head


def test_a_conforming_published_head_records_published(repos: dict[str, Any], signing_key: str) -> None:
    github, head = published_world(repos, signing_key)
    assert step(repos, github, role="finalize").target_state == state.PUBLISHED
    evidence = ledger_state(repos).evidence[state.PUBLISHED]
    assert evidence["head_sha"] == head and evidence["commit_verified"] is True
    assert evidence["deadline_completed_at"] == "2026-10-05T13:00:00Z"


@pytest.mark.parametrize(
    "tamper",
    [
        {"verified": False},
        {"login": "someone-else"},
        "payload",
        "tree",
        "parent",
    ],
)
def test_a_non_conforming_head_is_a_conflict_never_published(
    repos: dict[str, Any], signing_key: str, tamper: Any
) -> None:
    github, head = published_world(repos, signing_key)
    path = f"/repos/{rt.REPOSITORY}/commits/{head}"
    if isinstance(tamper, dict):
        github.routes[path] = commit_json(repos, head, **tamper)
    elif tamper == "payload":
        github.routes[path]["commit"]["verification"]["payload"] += "x"
    elif tamper == "tree":
        github.routes[path]["commit"]["tree"]["sha"] = "0" * 40
    else:
        github.routes[path]["parents"] = [{"sha": "0" * 40}]
    assert step(repos, github, role="finalize").code == "REMOTE_BRANCH_CONFLICT"


@pytest.mark.parametrize("code", sorted(state.PUBLICATION_REFUSAL_CODES))
def test_a_trusted_publisher_refusal_is_terminal(repos: dict[str, Any], code: str) -> None:
    rt.validated_ledger(repos)
    github = FakeGitHub()
    github.routes[f"/repos/{rt.REPOSITORY}/actions/runs/100/jobs"] = {
        "total_count": 1,
        "jobs": [github.job("publish", 11)],
    }
    github.artifact(22, state.result_artifact_name(rt.AUTH), b"sealed")
    outcome = {
        "schema_version": control.OUTCOME_SCHEMA_VERSION,
        "authorization_id": rt.AUTH,
        "verdict": "REFUSED",
        "code": code,
    }
    name = control.outcome_artifact_name(rt.AUTH, "publication", 1)
    github.routes[f"/repos/{rt.REPOSITORY}/actions/runs/100/artifacts"] = [
        github.artifact(41, name, json.dumps(outcome).encode())
    ]
    assert step(repos, github, role="finalize").code == code


# --- T5 completion -------------------------------------------------------------------------------------


def completion_world(repos: dict[str, Any], signing_key: str, conclusion: str | None) -> tuple[FakeGitHub, str]:
    github, head = published_world(repos, signing_key)
    step(repos, github, role="finalize")
    github.routes[f"/repos/{rt.REPOSITORY}/pulls"] = [
        {"number": 600, "node_id": "PR_x", "draft": True, "base": {"ref": "main"}, "head": {"ref": BRANCH, "sha": head}}
    ]
    runs = (
        []
        if conclusion is None
        else [
            {
                "id": 77,
                "run_number": 5,
                "head_sha": head,
                "head_branch": BRANCH,
                "status": "completed",
                "conclusion": conclusion,
            }
        ]
    )
    github.routes[f"/repos/{rt.REPOSITORY}/actions/workflows/{control.PREFLIGHT_WORKFLOW_FILE}/runs"] = {
        "workflow_runs": runs
    }
    return github, head


def test_completed_requires_the_draft_pr_and_a_successful_exact_head_preflight(
    repos: dict[str, Any], signing_key: str
) -> None:
    github, head = completion_world(repos, signing_key, "success")
    assert step(repos, github, role="reconcile").target_state == state.COMPLETED
    evidence = ledger_state(repos).evidence[state.COMPLETED]
    assert evidence["pull_request_head_sha"] == head and evidence["preflight_run_id"] == 77


def test_a_failed_preflight_fails_even_with_a_pr(repos: dict[str, Any], signing_key: str) -> None:
    github, _ = completion_world(repos, signing_key, "failure")
    assert step(repos, github, role="reconcile").code == "CANDIDATE_PREFLIGHT_FAILED"


def test_no_preflight_yet_is_a_noop(repos: dict[str, Any], signing_key: str) -> None:
    github, _ = completion_world(repos, signing_key, None)
    assert step(repos, github, role="reconcile").action == "noop"
    assert ledger_state(repos).state == state.PUBLISHED


def test_a_non_draft_pr_never_completes(repos: dict[str, Any], signing_key: str) -> None:
    github, _ = completion_world(repos, signing_key, "success")
    github.routes[f"/repos/{rt.REPOSITORY}/pulls"][0]["draft"] = False
    assert step(repos, github, role="reconcile").action == "noop"


# --- REST client --------------------------------------------------------------------------------------


class Response:
    def __init__(self, status: int, body: bytes) -> None:
        self.status, self._body = status, body

    def read(self, _limit: int) -> bytes:
        return self._body

    def __enter__(self) -> Response:
        return self

    def __exit__(self, *_: object) -> None:
        return None


def opener(responses: dict[str, Callable[[Any], Any]], seen: list[Any]) -> Callable[[Any, float], Any]:
    def open_(request: Any, _timeout: float) -> Any:
        seen.append(request)
        return responses[request.full_url](request)

    return open_


def test_the_rest_client_maps_only_200_and_404_to_definitive_facts() -> None:
    import urllib.error

    def fail(code: int) -> Callable[[Any], Any]:
        def raise_(request: Any) -> Any:
            raise urllib.error.HTTPError(request.full_url, code, "x", {}, None)  # type: ignore[arg-type]

        return raise_

    base = "https://api.github.com/repos/fafa33/Project-Hunter"
    seen: list[Any] = []
    client = control.GitHubRest(
        rt.REPOSITORY,
        "tok",
        opener=opener(
            {
                f"{base}/a": lambda _r: Response(200, b'{"ok": 1}'),
                f"{base}/b": fail(404),
                f"{base}/c": fail(502),
                f"{base}/d": fail(429),
                f"{base}/e": lambda _r: Response(200, b"not json"),
                f"{base}/f": lambda _r: Response(202, b'{"computing": true}'),
                f"{base}/g": lambda _r: Response(200, b"[" + b"0," * control.MAX_ARTIFACT_BYTES + b"0]"),
            },
            seen,
        ),
    )
    assert client.get("/repos/fafa33/Project-Hunter/a") == control.Read("ok", {"ok": 1})
    assert client.get("/repos/fafa33/Project-Hunter/b").status == "absent"
    assert {client.get(f"/repos/fafa33/Project-Hunter/{p}").status for p in "cdefg"} == {"unknown"}
    assert all(r.get_header("Authorization") == "Bearer tok" for r in seen)
    with pytest.raises(ValueError):
        client.get("/user")


def test_an_anonymous_client_sends_no_authorization_header() -> None:
    seen: list[Any] = []
    base = "https://api.github.com/repos/fafa33/Project-Hunter"
    client = control.GitHubRest(
        rt.REPOSITORY, None, opener=opener({f"{base}/a": lambda _r: Response(200, b"{}")}, seen)
    )
    assert client.get("/repos/fafa33/Project-Hunter/a").status == "ok"
    assert seen[0].get_header("Authorization") is None
    with pytest.raises(control.ControlRefused, match="MISSING_CONFIGURATION"):
        control.GitHubRest(rt.REPOSITORY, "")


def test_the_artifact_blob_redirect_never_receives_the_token() -> None:
    import io
    import urllib.error
    import zipfile

    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w") as z:
        z.writestr("result", b"sealed")
    blob = "https://blob.example/signed?sig=x"

    def redirect(request: Any) -> Any:
        raise urllib.error.HTTPError(request.full_url, 302, "Found", {"Location": blob}, None)  # type: ignore[arg-type]

    seen: list[Any] = []
    client = control.GitHubRest(
        rt.REPOSITORY,
        "tok",
        opener=opener(
            {
                "https://api.github.com/repos/fafa33/Project-Hunter/actions/artifacts/5/zip": redirect,
                blob: lambda _r: Response(200, archive.getvalue()),
            },
            seen,
        ),
    )
    assert client.download_artifact(5) == control.Read("ok", b"sealed")
    assert seen[0].get_header("Authorization") == "Bearer tok"
    assert seen[1].get_header("Authorization") is None
