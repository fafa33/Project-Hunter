"""Issue #557 production cutover regressions.

The production ``issues:labeled`` trigger used to dispatch only to the retired
Railway execution edge, which answers 503 since PR #522, so an accepted owner
label never reached the PR #524 replacement executor and no Draft PR could
exist. These tests pin the cutover: Railway authority -> SPM/DPM signed handoff
-> GitHub-OIDC-bound executor -> hostile closed-schema result -> credential-free
validation -> credential-isolated create-only publication -> the existing
Draft-PR workflow.

The issuer path runs on the real deployment fixture (real Source Handling
authority, Evidence repository, Smart Prompt Machine and envelope signer); only
GitHub's OIDC key, the model and the git remote are stand-ins.
"""

from __future__ import annotations

import base64
import hashlib
import http.client
import json
import re
import subprocess
import threading
import urllib.error
from datetime import timedelta
from pathlib import Path
from typing import Any

import hunter_issue_agent_candidate_pr as candidate_pr
import hunter_issue_agent_github_executor as driver
import hunter_issue_agent_ingress as ingress
import hunter_issue_agent_issuer as issuer
import hunter_issue_agent_trigger as trigger
import pytest
import yaml
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from test_issue_agent_issuer import (
    AUTOMATION_SIGNING_KEY_HEX,
    AUTOMATION_VERIFYING_KEY_HEX,
    OWNER,
    REPOSITORY,
    START,
    Deployment,
    _authorization_document,
    _event,
    _inner,
    _provenance,
    _wait_for_ledger_state,
)

from hunter.automation.issue_agent_execution import (
    ISSUE_AGENT_FAILURE_CODES,
    IssueAgentRemoteExecutionError,
    SignedIssueAgentAuthorization,
    derive_execution_target,
)
from hunter.automation.issue_agent_github_execution import (
    EXECUTION_FETCH_PATH,
    EXECUTION_REQUEST_SCHEMA_VERSION,
    EXECUTION_RESULT_PATH,
    EXECUTOR_PROVIDER,
    GITHUB_OIDC_ISSUER,
    TRIGGER_WORKFLOW_PATH,
    VALIDATION_DEFINITION,
    ExecutionBindingError,
    ExecutionIdentityError,
    GitHubActionsOidcVerifier,
    GitHubHostedExecutionRuntime,
    evidence_prompt_resolver,
    oidc_audience,
)
from hunter.automation.issue_agent_replacement_executor import (
    RESULT_SCHEMA_VERSION,
    validate_replacement_result,
    verify_validation_receipt,
)
from hunter.evidence_intelligence.engineering_context_authority import ENGINEERING_CONTEXT_SCHEMA_VERSION

WORKFLOW = Path(".github/workflows/hunter-issue-agent-trigger.yml")
WORKFLOW_REF = f"{REPOSITORY}/{TRIGGER_WORKFLOW_PATH}@refs/heads/main"
OIDC_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
FOREIGN_OIDC_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _jwks() -> dict[str, Any]:
    numbers = OIDC_KEY.public_key().public_numbers()
    return {
        "keys": [
            {
                "kty": "RSA",
                "alg": "RS256",
                "use": "sig",
                "kid": "github-test",
                "n": _b64url(numbers.n.to_bytes((numbers.n.bit_length() + 7) // 8, "big")),
                "e": _b64url(numbers.e.to_bytes(3, "big")),
            }
        ]
    }


def _claims(**overrides: Any) -> dict[str, Any]:
    now = int(START.timestamp())
    claims: dict[str, Any] = {
        "iss": GITHUB_OIDC_ISSUER,
        "aud": oidc_audience(REPOSITORY),
        "iat": now,
        "nbf": now,
        "exp": now + 300,
        "repository": REPOSITORY,
        "repository_owner": OWNER,
        "actor": OWNER,
        "ref": "refs/heads/main",
        "ref_type": "branch",
        "event_name": "issues",
        "workflow_ref": WORKFLOW_REF,
        "job_workflow_ref": WORKFLOW_REF,
        "runner_environment": "github-hosted",
        "run_id": "1001",
        "run_attempt": "1",
    }
    claims.update(overrides)
    return {k: v for k, v in claims.items() if v is not None}


def _token(*, key: Any = OIDC_KEY, kid: str = "github-test", **overrides: Any) -> str:
    header = _b64url(json.dumps({"alg": "RS256", "kid": kid, "typ": "JWT"}).encode())
    payload = _b64url(json.dumps(_claims(**overrides)).encode())
    signature = key.sign(f"{header}.{payload}".encode(), padding.PKCS1v15(), hashes.SHA256())
    return f"{header}.{payload}.{_b64url(signature)}"


def _verifier() -> GitHubActionsOidcVerifier:
    return GitHubActionsOidcVerifier(repository=REPOSITORY, owner_login=OWNER, jwks_fetcher=_jwks)


@pytest.fixture(autouse=True)
def _automation_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HUNTER_PROMPT_AUTOMATION_SIGNING_KEY", AUTOMATION_SIGNING_KEY_HEX)
    monkeypatch.setenv("HUNTER_PROMPT_AUTOMATION_VERIFYING_KEY", AUTOMATION_VERIFYING_KEY_HEX)


def _runtime(deployment: Deployment, **kwargs: Any) -> GitHubHostedExecutionRuntime:
    # Short result window: an accepted execution holds one of the issuer's
    # bounded execution slots until it gets a result or times out.
    kwargs.setdefault("result_timeout_seconds", 3.0)
    configuration = deployment.configuration
    return GitHubHostedExecutionRuntime(
        repository=configuration.repository,
        owner_login=configuration.owner_login,
        evidence_database=configuration.evidence_database,
        oidc_verifier=_verifier(),
        prompt_resolver=evidence_prompt_resolver(
            configuration.evidence_database, prompt_verifier=configuration.prompt_verifier, clock=deployment.clock.now
        ),
        clock=deployment.clock.now,
        **kwargs,
    )


class Edge:
    """The issuer on a real ephemeral port, admission as production composes it."""

    def __init__(self, services: issuer.IssuerServices, *, admission: bool = True) -> None:
        self.server = issuer.IssuerServer("127.0.0.1", 0, services, execution_admission_enabled=admission)
        self.server.start()
        self.port = self.server._server.server_address[1]

    def post(self, path: str, body: bytes) -> tuple[int, dict[str, Any]]:
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=15)
        connection.request("POST", path, body=body, headers={"Content-Length": str(len(body))})
        response = connection.getresponse()
        raw = response.read()
        connection.close()
        return response.status, json.loads(raw.decode("utf-8"))

    def fetch(self, authorization_id: str, role: str, **token: Any) -> tuple[int, dict[str, Any]]:
        return self.post(
            EXECUTION_FETCH_PATH,
            json.dumps(
                {
                    "schema_version": EXECUTION_REQUEST_SCHEMA_VERSION,
                    "authorization_id": authorization_id,
                    "role": role,
                    "oidc_token": _token(**token),
                }
            ).encode(),
        )

    def result(self, authorization_id: str, document: str, **token: Any) -> tuple[int, dict[str, Any]]:
        return self.post(
            EXECUTION_RESULT_PATH,
            json.dumps(
                {
                    "schema_version": EXECUTION_REQUEST_SCHEMA_VERSION,
                    "authorization_id": authorization_id,
                    "role": "executor",
                    "oidc_token": _token(**token),
                    "result_document": document,
                }
            ).encode(),
        )


@pytest.fixture
def edge() -> Any:
    edges: list[Edge] = []

    def _make(services: issuer.IssuerServices, **kwargs: Any) -> Edge:
        made = Edge(services, **kwargs)
        edges.append(made)
        return made

    yield _make
    for made in edges:
        # Drain accepted workers so their execution slots are released.
        made.server.shutdown(timeout=10)


def _result(
    signed: SignedIssueAgentAuthorization, *, path: str = "docs/ISSUE_AGENT_CANARY.md", **overrides: Any
) -> str:
    target = derive_execution_target(signed)
    content = b"# Issue Agent canary\n"
    document = {
        "schema_version": RESULT_SCHEMA_VERSION,
        "authorization_id": target.authorization_id,
        "base_sha": target.base_sha,
        "branch": target.branch,
        "files": [
            {
                "path": path,
                "content_b64": base64.b64encode(content).decode(),
                "sha256": hashlib.sha256(content).hexdigest(),
                "mode": "100644",
            }
        ],
    }
    document.update(overrides)
    return json.dumps(document)


def _accepted(edge_: Edge, document: str) -> dict[str, Any]:
    status, accepted = edge_.post("/issue-agent/authorize", document.encode())
    assert status == 200, accepted
    assert accepted["state"] == "DISPATCHED"
    return accepted


# --- the observed failure, and the cutover end to end -------------------------


def test_label_acceptance_reaches_the_replacement_executor_through_spm_dpm(tmp_path: Path, edge: Any) -> None:
    """Regression for the observed #520 failure: acceptance now reaches execution."""
    deployment = Deployment(tmp_path)
    runtime = _runtime(deployment)
    services = deployment.services(fallback=runtime)
    hook = edge(services)
    document = _authorization_document()
    signed = SignedIssueAgentAuthorization.from_json(document)
    accepted = _accepted(hook, document)
    authorization_id = accepted["authorization_id"]
    assert trigger.accepted_execution_identity(json.dumps(accepted).encode(), authorization_id=authorization_id)

    status, handoff = hook.fetch(authorization_id, "executor")
    assert status == 200
    # The handoff is the one SPM signed and the ledger recorded before the ACK,
    # and the prompt carries the EngineeringContextAuthority/DPM context.
    entry = services.ledger.entry(authorization_id)
    assert entry is not None and entry.state == "DISPATCHED"
    assert handoff["handoff_document"] == entry.handoff_document == accepted["handoff_document"]
    assert ENGINEERING_CONTEXT_SCHEMA_VERSION in handoff["exact_prompt"]
    target = derive_execution_target(signed)
    assert (handoff["branch"], handoff["base_sha"]) == (target.branch, target.base_sha)

    status, ack = hook.result(authorization_id, _result(signed))
    assert status == 200 and ack["authorization_id"] == authorization_id
    completed = _wait_for_ledger_state(services.ledger, authorization_id, "COMPLETED")
    assert completed.provider == EXECUTOR_PROVIDER
    assert completed.head_after is None

    status, candidate = hook.fetch(authorization_id, "validator")
    assert status == 200
    receipt = verify_validation_receipt(
        candidate["validation_receipt"],
        result_document=candidate["result_document"],
        signed_authorization=SignedIssueAgentAuthorization.from_json(candidate["signed_authorization"]),
        expected_validation_definition=VALIDATION_DEFINITION,
    )
    assert (receipt.authorization_id, receipt.base_sha, receipt.branch) == (
        authorization_id,
        target.base_sha,
        target.branch,
    )
    assert receipt.result_sha256 == ack["result_sha256"]
    assert hook.fetch(authorization_id, "publisher")[0] == 200
    # Every role operation is single-use: no second publication can be fetched.
    assert hook.fetch(authorization_id, "publisher")[0] == 409


def test_an_invalid_spm_handoff_releases_no_prompt(tmp_path: Path, edge: Any) -> None:
    deployment = Deployment(tmp_path)
    runtime = _runtime(deployment)
    services = deployment.services(fallback=runtime)
    hook = edge(services)
    accepted = _accepted(hook, _authorization_document())
    handoff = json.loads(accepted["handoff_document"])
    handoff["issuer_signature"] = ("0" if handoff["issuer_signature"][0] != "0" else "1") + handoff["issuer_signature"][
        1:
    ]
    resolver = evidence_prompt_resolver(
        deployment.configuration.evidence_database,
        prompt_verifier=deployment.configuration.prompt_verifier,
        clock=deployment.clock.now,
    )
    with pytest.raises(Exception):  # noqa: B017 - any refusal; never a prompt
        resolver(json.dumps(handoff))
    with pytest.raises(Exception):  # noqa: B017
        runtime.admit(SignedIssueAgentAuthorization.from_json(_authorization_document()), '{"not":"a handoff"}')


def test_no_implementation_without_the_admitted_signed_handoff(tmp_path: Path, edge: Any) -> None:
    """Provider-direct execution: no admitted handoff, no prompt, no result."""
    deployment = Deployment(tmp_path)
    services = deployment.services(fallback=_runtime(deployment))
    hook = edge(services)
    signed = SignedIssueAgentAuthorization.from_json(_authorization_document())
    unknown = signed.authorization.authorization_id
    assert hook.fetch(unknown, "executor")[0] == 404
    assert hook.result(unknown, _result(signed))[0] == 404

    accepted = _accepted(hook, _authorization_document())
    # A result before the executor fetched the exact handoff is refused.
    assert hook.result(accepted["authorization_id"], _result(signed))[0] == 403


def test_resolver_failure_is_terminal_and_never_executes(tmp_path: Path, edge: Any) -> None:
    deployment = Deployment(tmp_path)

    def no_prompt(_handoff: str) -> str:
        raise RuntimeError("reconstruction unavailable")

    configuration = deployment.configuration
    runtime = GitHubHostedExecutionRuntime(
        repository=configuration.repository,
        owner_login=configuration.owner_login,
        evidence_database=configuration.evidence_database,
        oidc_verifier=_verifier(),
        prompt_resolver=no_prompt,
        clock=deployment.clock.now,
        result_timeout_seconds=3.0,
    )
    services = deployment.services(fallback=runtime)
    hook = edge(services)
    accepted = _accepted(hook, _authorization_document())
    status, body = hook.fetch(accepted["authorization_id"], "executor")
    assert status == 422 and "exact_prompt" not in body
    failed = _wait_for_ledger_state(services.ledger, accepted["authorization_id"], "FAILED")
    assert failed.failure_code == "EXECUTION_ERROR"


# --- OIDC identity binding -----------------------------------------------------


@pytest.mark.parametrize(
    "override",
    [
        {"repository": "attacker/Project-Hunter"},
        {"repository_owner": "attacker"},
        {"actor": "someone-else"},
        {"ref": "refs/heads/issue-557-production-cutover"},
        {"event_name": "workflow_dispatch"},
        {"workflow_ref": f"{REPOSITORY}/.github/workflows/other.yml@refs/heads/main"},
        {"job_workflow_ref": "attacker/x/.github/workflows/reusable.yml@refs/heads/main"},
        {"runner_environment": "self-hosted"},
        {"run_id": None},
        {"run_attempt": "x"},
    ],
)
def test_oidc_identity_must_be_the_trusted_trigger_run(override: dict[str, Any]) -> None:
    with pytest.raises((ExecutionBindingError, ExecutionIdentityError)):
        _verifier().verify(_token(**override), now=START)


@pytest.mark.parametrize(
    "token",
    [
        lambda: _token(key=FOREIGN_OIDC_KEY),
        lambda: _token(kid="unknown"),
        lambda: _token(iss="https://evil.example"),
        lambda: _token(aud="hunter-issue-agent-execution:other/repo"),
        lambda: _token(exp=int(START.timestamp()) - 3600),
        lambda: _token(iat=int((START + timedelta(hours=1)).timestamp())),
        lambda: "not.a.jwt",
        lambda: "",
    ],
)
def test_oidc_token_authenticity_and_time_fail_closed(token: Any) -> None:
    with pytest.raises(ExecutionIdentityError):
        _verifier().verify(token(), now=START)


def test_oidc_claims_resolve_the_exact_run() -> None:
    claims = _verifier().verify(_token(run_id="77", run_attempt="2"), now=START)
    assert claims.run == ("77", "2")


def test_execution_is_bound_to_one_run_and_every_role_is_single_use(tmp_path: Path, edge: Any) -> None:
    deployment = Deployment(tmp_path)
    services = deployment.services(fallback=_runtime(deployment))
    hook = edge(services)
    authorization_id = _accepted(hook, _authorization_document())["authorization_id"]
    signed = SignedIssueAgentAuthorization.from_json(_authorization_document())

    # A validator/publisher can never claim the execution first.
    assert hook.fetch(authorization_id, "publisher")[0] == 403
    assert hook.fetch(authorization_id, "executor")[0] == 200
    assert hook.fetch(authorization_id, "executor")[0] == 409
    assert hook.fetch(authorization_id, "executor", run_id="2002")[0] == 403
    # A re-run of the same workflow is a different attempt and is refused.
    assert hook.result(authorization_id, _result(signed), run_attempt="2")[0] == 403
    assert hook.fetch(authorization_id, "validator")[0] == 404
    assert hook.result(authorization_id, _result(signed))[0] == 200
    assert hook.result(authorization_id, _result(signed))[0] == 409
    assert hook.fetch(authorization_id, "validator", run_id="2002")[0] == 403


# --- result binding ------------------------------------------------------------


@pytest.mark.parametrize(
    "overrides",
    [
        {"base_sha": "b" * 40},
        {"branch": "issue-423-0000000000000000"},
        {"authorization_id": "hunter-issue-agent-authorization:" + "0" * 64},
        {"exact_prompt": "leak"},
    ],
)
def test_a_misbound_result_is_rejected_and_terminal(tmp_path: Path, edge: Any, overrides: dict[str, Any]) -> None:
    deployment = Deployment(tmp_path)
    services = deployment.services(fallback=_runtime(deployment))
    hook = edge(services)
    authorization_id = _accepted(hook, _authorization_document())["authorization_id"]
    signed = SignedIssueAgentAuthorization.from_json(_authorization_document())
    assert hook.fetch(authorization_id, "executor")[0] == 200
    assert hook.result(authorization_id, _result(signed, **overrides))[0] == 422
    failed = _wait_for_ledger_state(services.ledger, authorization_id, "FAILED")
    assert failed.failure_code == "EXECUTOR_RESULT_REJECTED"
    assert hook.result(authorization_id, _result(signed))[0] == 409


def test_a_result_outside_the_signed_task_scope_is_rejected(tmp_path: Path, edge: Any) -> None:
    deployment = Deployment(tmp_path)
    services = deployment.services(fallback=_runtime(deployment))
    hook = edge(services)
    authorization_id = _accepted(hook, _authorization_document())["authorization_id"]
    signed = SignedIssueAgentAuthorization.from_json(_authorization_document())
    assert hook.fetch(authorization_id, "executor")[0] == 200
    assert hook.result(authorization_id, _result(signed, path=".github/workflows/x.yml"))[0] == 422


def test_no_result_in_time_fails_closed_and_a_late_result_is_refused(tmp_path: Path, edge: Any) -> None:
    deployment = Deployment(tmp_path)
    services = deployment.services(fallback=_runtime(deployment, result_timeout_seconds=0.2))
    hook = edge(services)
    authorization_id = _accepted(hook, _authorization_document())["authorization_id"]
    failed = _wait_for_ledger_state(services.ledger, authorization_id, "FAILED")
    assert failed.failure_code == "EXECUTOR_RESULT_TIMEOUT"
    assert hook.fetch(authorization_id, "executor")[0] == 409
    assert {"EXECUTOR_RESULT_TIMEOUT", "EXECUTOR_RESULT_REJECTED"} <= ISSUE_AGENT_FAILURE_CODES
    with pytest.raises(ValueError):
        IssueAgentRemoteExecutionError("PROVIDER_SAID_SO", "free text")


def test_a_replayed_authorization_is_never_admitted_twice(tmp_path: Path, edge: Any) -> None:
    deployment = Deployment(tmp_path)
    services = deployment.services(fallback=_runtime(deployment))
    hook = edge(services)
    document = _authorization_document()
    _accepted(hook, document)
    status, _body = hook.post("/issue-agent/authorize", document.encode())
    assert status == 409


# --- legacy Railway execution stays retired ------------------------------------


def test_production_composition_never_builds_the_legacy_railway_runtime(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import hunter.automation.issue_agent_workspace as workspace

    def retired(*_a: Any, **_k: Any) -> None:
        raise AssertionError("the retired Railway workspace runtime must not be composed")

    monkeypatch.setattr(workspace.IssueAgentWorkspaceRuntime, "__init__", retired)
    deployment = Deployment(tmp_path)
    services = issuer.compose_services(deployment.configuration)
    assert type(services.fallback) is GitHubHostedExecutionRuntime
    assert "IssueAgentWorkspaceRuntime" not in Path("scripts/hunter_issue_agent_issuer.py").read_text()


def test_production_main_enables_admission_only_for_the_github_runtime(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    deployment = Deployment(tmp_path)
    seen: dict[str, Any] = {}

    class FakeServer:
        def __init__(self, host: str, port: int, services: Any, **kwargs: Any) -> None:
            seen.update(kwargs, services=services)
            self._shutdown_event = threading.Event()
            self._shutdown_event.set()

        def start(self) -> None:
            return None

        def shutdown(self, timeout: float = 30.0) -> None:
            return None

    monkeypatch.setattr(issuer, "_import_provenance_resolver", lambda _path: _provenance)
    monkeypatch.setattr(issuer.IssuerConfiguration, "from_environment", lambda **_k: deployment.configuration)
    monkeypatch.setattr(issuer, "IssuerServer", FakeServer)
    monkeypatch.setattr(issuer.signal, "signal", lambda *_a: None)
    assert issuer.main(["--provenance-resolver", "x.y"]) == 0
    assert seen["execution_admission_enabled"] is True
    assert type(seen["services"].fallback) is GitHubHostedExecutionRuntime


def test_execution_routes_fail_closed_when_admission_is_retired(tmp_path: Path, edge: Any) -> None:
    deployment = Deployment(tmp_path)
    services = deployment.services(fallback=_runtime(deployment))
    hook = edge(services, admission=False)
    signed = SignedIssueAgentAuthorization.from_json(_authorization_document())
    assert hook.post("/issue-agent/authorize", _authorization_document().encode())[0] == 503
    assert hook.fetch(signed.authorization.authorization_id, "executor")[0] == 503


def test_execution_routes_refuse_a_legacy_runtime(tmp_path: Path, edge: Any) -> None:
    deployment = Deployment(tmp_path)
    hook = edge(deployment.services())
    signed = SignedIssueAgentAuthorization.from_json(_authorization_document())
    assert hook.fetch(signed.authorization.authorization_id, "executor")[0] == 503


def test_ingress_routes_the_execution_operations_to_the_issuer_only() -> None:
    routes = ingress.IngressRoutes.for_ports(provisioner_port=8081, issuer_port=8082)
    for path in (EXECUTION_FETCH_PATH, EXECUTION_RESULT_PATH):
        upstream = routes.post_route(path)
        assert upstream is not None and upstream.port == 8082 and upstream.path == path
    assert ingress.EXECUTION_FETCH_PATH == EXECUTION_FETCH_PATH
    assert ingress.EXECUTION_RESULT_PATH == EXECUTION_RESULT_PATH
    assert routes.post_route("/issue-agent/execution/other") is None


# --- trigger: opaque identity only, no Issue content in public logs -----------


def test_trigger_dispatches_only_the_opaque_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: Any
) -> None:
    import test_issue_agent_trigger as trigger_tests

    event_path = tmp_path / "event.json"
    event = _event(body="SECRET-ISSUE-CONTENT must not reach public logs")
    event_path.write_text(json.dumps(event), encoding="utf-8")
    monkeypatch.setenv(trigger.SIGNING_KEY_ENV, trigger_tests.ISSUER_KEY_HEX)
    monkeypatch.setenv("HUNTER_ISSUE_AGENT_WEBHOOK_URL", "https://hook.example/issue-agent/authorize")
    monkeypatch.setenv(trigger.PROVISIONING_URL_ENV, "https://hook.example/issue-agent/provision")
    posted: list[str] = []

    def accept(_provision: str, _webhook: str, document: str, **_k: Any) -> bytes:
        posted.append(document)
        authorization_id = json.loads(document)["authorization"]["authorization_id"]
        return json.dumps(
            {
                "authorization_id": authorization_id,
                "state": "DISPATCHED",
                "schema_version": trigger.ACCEPTED_SCHEMA_VERSION,
            }
        ).encode()

    monkeypatch.setattr(trigger, "_provision_and_dispatch", accept)
    argv = ["--event", str(event_path), "--repository", REPOSITORY, "--owner-login", OWNER]
    assert trigger.main(argv) == 0
    authorization_id = json.loads(posted[0])["authorization"]["authorization_id"]
    out = capsys.readouterr().out
    # stdout is exactly the identity the workflow binds; no path argument exists.
    assert out == f"{authorization_id}\n"
    assert "SECRET-ISSUE-CONTENT" not in out
    with pytest.raises(SystemExit):
        trigger.main([*argv, "--execution-identity-out", str(tmp_path / "x")])

    monkeypatch.setattr(trigger, "_provision_and_dispatch", lambda *_a, **_k: b'{"state":"FAILED"}')
    assert trigger.main(argv) == 2


# --- GitHub-hosted jobs: credential separation and silence --------------------


def _jobs() -> dict[str, Any]:
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))["jobs"]


def _secrets(job: Any) -> set[str]:
    return set(re.findall(r"secrets\.([A-Z0-9_]+)", json.dumps(job)))


def test_workflow_runs_each_trust_domain_in_its_own_job() -> None:
    jobs = _jobs()
    assert list(jobs) == ["authorize-and-dispatch", "execute", "validate", "publish"]
    assert jobs["execute"]["needs"] == "authorize-and-dispatch"
    assert jobs["validate"]["needs"] == ["authorize-and-dispatch", "execute"]
    assert jobs["publish"]["needs"] == ["authorize-and-dispatch", "validate"]
    for name in ("execute", "validate", "publish"):
        assert jobs[name]["runs-on"] == "ubuntu-latest"
        assert jobs[name]["permissions"] == {"contents": "read", "id-token": "write"}
    assert _secrets(jobs["execute"]) == {"HUNTER_ISSUE_AGENT_WEBHOOK_URL", "HUNTER_ISSUE_AGENT_EXECUTOR_MODEL_API_KEY"}
    assert _secrets(jobs["validate"]) == {
        "HUNTER_ISSUE_AGENT_WEBHOOK_URL",
        "HUNTER_ISSUE_AGENT_AUTHORIZATION_VERIFYING_KEY",
    }
    assert _secrets(jobs["publish"]) == {
        "HUNTER_ISSUE_AGENT_WEBHOOK_URL",
        "HUNTER_ISSUE_AGENT_AUTHORIZATION_VERIFYING_KEY",
        "HUNTER_ISSUE_AGENT_PUBLISHER_SIGNING_KEY",
        "HUNTER_ISSUE_AGENT_PUBLISHER_PUSH_TOKEN",
    }
    text = WORKFLOW.read_text(encoding="utf-8")
    workflow = yaml.safe_load(text)
    # Sonar githubactions:S8264 -- no workflow-level grant; each job declares its own.
    assert workflow["permissions"] == {}
    assert jobs["authorize-and-dispatch"]["permissions"] == {"contents": "read", "issues": "read"}
    for name in ("execute", "validate", "publish"):
        installs = [step["run"] for step in jobs[name]["steps"] if "pip install" in str(step.get("run", ""))]
        assert installs, name
        for script in installs:
            # Sonar githubactions:S8541/S8544 -- exact pins, wheels only, no local build.
            assert "--only-binary=:all:" in script
            command = re.search(r"pip install(?:[^\n]*\\\n)*[^\n]*", script)
            assert command is not None
            for requirement in re.findall(r'"([^"]+)"', command.group(0)):
                assert re.fullmatch(r"[A-Za-z0-9_.-]+==[0-9][A-Za-z0-9_.]*", requirement), requirement
            assert " ./engine" not in script and " -e " not in script and not script.rstrip().endswith(" .")
    for name in ("execute", "validate"):
        steps = json.dumps(jobs[name]["steps"])
        assert "useradd --create-home --shell /usr/sbin/nologin hunter-untrusted" in steps
        assert "HUNTER_ISSUE_AGENT_UNTRUSTED_USER" in steps
    assert "upload-artifact" not in text and "set -x" not in text and "github.token" not in text
    assert "GITHUB_TOKEN" not in text and "HUNTER_ISSUE_AGENT_PR_TOKEN" not in text
    for job in jobs.values():
        for step in job["steps"]:
            if str(step.get("uses", "")).startswith("actions/checkout"):
                assert step["with"]["persist-credentials"] is False
                assert step["with"]["ref"] == "${{ github.event.repository.default_branch }}"
    # The publisher installs and runs nothing from the candidate.
    assert "pip install --disable-pip-version-check -e" not in json.dumps(jobs["publish"])


def test_executor_refuses_publication_authority(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("GITHUB_TOKEN", "HUNTER_ISSUE_AGENT_PUBLISHER_PUSH_TOKEN", "HUNTER_ISSUE_AGENT_PUBLISHER_SIGNING_KEY"):
        with pytest.raises(driver.ExecutionJobError) as caught:
            driver.run_execute("hunter-issue-agent-authorization:" + "a" * 64, {name: "credential"})
        assert caught.value.code == "EXECUTOR_HAS_PUBLICATION_AUTHORITY"


def test_model_child_environment_is_an_allowlist(tmp_path: Path) -> None:
    environ = {
        "PATH": "/usr/bin",
        "ACTIONS_ID_TOKEN_REQUEST_TOKEN": "oidc",
        "ACTIONS_ID_TOKEN_REQUEST_URL": "https://oidc",
        "GITHUB_OUTPUT": "/tmp/out",
        "HUNTER_ISSUE_AGENT_WEBHOOK_URL": "https://issuer/issue-agent/authorize",
        driver.MODEL_API_KEY_ENV: "model-key",
        driver.MODEL_API_KEY_NAME_ENV: "GROQ_API_KEY",
    }
    child = driver.model_environment(environ, workspace=tmp_path, home=tmp_path)
    assert child["GROQ_API_KEY"] == "model-key"
    assert not any(name.startswith(("ACTIONS_", "GITHUB_", "HUNTER_")) for name in child)
    for bad in ("GITHUB_TOKEN", "ACTIONS_ID_TOKEN_REQUEST_TOKEN", "HUNTER_ISSUE_AGENT_PUBLISHER_PUSH_TOKEN", "x"):
        with pytest.raises(driver.ExecutionJobError):
            driver.model_environment({**environ, driver.MODEL_API_KEY_NAME_ENV: bad}, workspace=tmp_path, home=tmp_path)


def test_publisher_fails_closed_on_missing_credentials_without_printing_values(
    monkeypatch: pytest.MonkeyPatch, capsys: Any
) -> None:
    for name in (driver.PUBLISHER_SIGNING_KEY_ENV, driver.PUBLISHER_PUSH_TOKEN_ENV, driver.PUBLISHER_WRITER_ENV):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv(driver.PUBLISHER_PUSH_TOKEN_ENV, "TOKEN-VALUE")
    rc = driver.main(["publish", "--authorization-id", "hunter-issue-agent-authorization:" + "a" * 64])
    err = capsys.readouterr().err
    assert rc == 3
    assert "MISSING_PUBLICATION_CREDENTIAL" in err
    assert driver.PUBLISHER_SIGNING_KEY_ENV in err and driver.PUBLISHER_WRITER_ENV in err
    assert "TOKEN-VALUE" not in err


def test_publisher_refuses_model_authority() -> None:
    environ = {
        driver.PUBLISHER_SIGNING_KEY_ENV: "key",
        driver.PUBLISHER_PUSH_TOKEN_ENV: "token",
        driver.PUBLISHER_WRITER_ENV: "fafa33",
        "GROQ_API_KEY": "model",
    }
    with pytest.raises(driver.ExecutionJobError) as caught:
        driver.run_publish("hunter-issue-agent-authorization:" + "a" * 64, Path("."), "c" * 40, environ)
    assert caught.value.code == "PUBLISHER_HAS_MODEL_AUTHORITY"


def test_validator_refuses_any_credential() -> None:
    with pytest.raises(driver.ExecutionJobError) as caught:
        driver.run_validate("hunter-issue-agent-authorization:" + "a" * 64, Path("."), {"GITHUB_TOKEN": "t"})
    assert caught.value.code == "VALIDATOR_HAS_PUBLICATION_AUTHORITY"
    with pytest.raises(driver.ExecutionJobError) as caught:
        driver.run_validate("hunter-issue-agent-authorization:" + "a" * 64, Path("."), {"OPENAI_API_KEY": "m"})
    assert caught.value.code == "VALIDATOR_HAS_MODEL_AUTHORITY"


def test_issuer_refusal_text_never_reaches_job_logs(monkeypatch: pytest.MonkeyPatch, capsys: Any) -> None:
    class Opener:
        def open(self, request: Any, timeout: float) -> Any:
            if "audience=" in getattr(request, "full_url", ""):
                raise AssertionError("unexpected")
            raise urllib.error.HTTPError(request.full_url, 422, "x", {}, None)  # type: ignore[arg-type]

    monkeypatch.setattr(driver, "_OPENER", Opener())
    with pytest.raises(driver.ExecutionJobError) as caught:
        driver.post_execution("https://issuer.example", EXECUTION_FETCH_PATH, {"exact_prompt": "SECRET"})
    assert caught.value.code == "ISSUER_REFUSED" and caught.value.detail == "HTTP 422"


def test_issuer_url_is_derived_from_the_existing_webhook_secret() -> None:
    assert driver.execution_base_url({driver.WEBHOOK_URL_ENV: "https://hunter.example/issue-agent/authorize"}) == (
        "https://hunter.example"
    )
    for bad in ("http://hunter.example/issue-agent/authorize", "https://u:p@h/issue-agent/authorize", "https://h/x"):
        with pytest.raises(driver.ExecutionJobError):
            driver.execution_base_url({driver.WEBHOOK_URL_ENV: bad})


def _workspace(tmp_path: Path) -> tuple[Path, Path, str]:
    """A workspace whose trusted Git metadata lives outside it, as the executor builds it."""
    workspace, git_dir = tmp_path / "ws", tmp_path / "base.git"
    workspace.mkdir(parents=True)
    env = {"GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_NOSYSTEM": "1", "PATH": "/usr/bin:/bin:/usr/local/bin"}
    env.update(GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@e", GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@e")

    def git(*args: str) -> str:
        return subprocess.run(
            ("git", f"--git-dir={git_dir}", f"--work-tree={workspace}", *args),
            cwd=workspace, env=env, check=True, capture_output=True, text=True,
        ).stdout.strip()  # fmt: skip

    git("init", "-q")
    (workspace / "docs").mkdir()
    (workspace / "docs" / "old.md").write_text("old\n")
    git("add", ".")
    git("commit", "-q", "-m", "seed")
    return workspace, git_dir, git("rev-parse", "HEAD")


def test_collected_result_is_the_closed_schema_document(tmp_path: Path) -> None:
    workspace, git_dir, base = _workspace(tmp_path)
    (workspace / "docs" / "ISSUE_AGENT_CANARY.md").write_text("# canary\n")
    (workspace / "docs" / "old.md").write_text("new\n")
    signed = SignedIssueAgentAuthorization.from_json(_authorization_document())
    target = derive_execution_target(signed)
    document = driver.collect_result(
        workspace, git_dir, authorization_id=target.authorization_id, branch=target.branch, base_sha=base
    )
    payload = json.loads(document)
    assert set(payload) == {"schema_version", "authorization_id", "base_sha", "branch", "files"}
    assert sorted(f["path"] for f in payload["files"]) == ["docs/ISSUE_AGENT_CANARY.md", "docs/old.md"]
    rebased = json.loads(document)
    rebased["base_sha"] = target.base_sha
    validated = validate_replacement_result(json.dumps(rebased), signed_authorization=signed, rehearsal=False)
    assert {f.content for f in validated.files} == {b"# canary\n", b"new\n"}


def test_an_inexpressible_candidate_change_fails_closed(tmp_path: Path) -> None:
    workspace, git_dir, base = _workspace(tmp_path)
    (workspace / "docs" / "old.md").unlink()
    with pytest.raises(driver.ExecutionJobError) as caught:
        driver.collect_result(workspace, git_dir, authorization_id="a", branch="b", base_sha=base)
    assert caught.value.code == "UNSUPPORTED_CANDIDATE_CHANGE"
    workspace2, git_dir2, base2 = _workspace(tmp_path / "second")
    with pytest.raises(driver.ExecutionJobError) as caught:
        driver.collect_result(workspace2, git_dir2, authorization_id="a", branch="b", base_sha=base2)
    assert caught.value.code == "NO_CANDIDATE_CHANGE"


def test_a_model_planted_git_directory_never_configures_or_runs_collection(tmp_path: Path) -> None:
    """The trusted process (which holds the OIDC capability) runs no model-written Git config."""
    workspace, git_dir, base = _workspace(tmp_path)
    pwned = tmp_path / "PWNED"
    planted = workspace / ".git"
    (planted / "hooks").mkdir(parents=True)
    (planted / "config").write_text(f"[core]\n\tfsmonitor = touch {pwned}\n\thooksPath = hooks\n")
    hook = planted / "hooks" / "pre-commit"
    hook.write_text(f"#!/bin/sh\ntouch {pwned}\n")
    hook.chmod(0o755)
    (workspace / "docs" / "ISSUE_AGENT_CANARY.md").write_text("# canary\n")
    document = driver.collect_result(workspace, git_dir, authorization_id="a", branch="b", base_sha=base)
    assert not pwned.exists()
    assert [f["path"] for f in json.loads(document)["files"]] == ["docs/ISSUE_AGENT_CANARY.md"]


def test_publisher_identity_is_bound_by_code_write_policy() -> None:
    assert driver.writer_identity("fafa33") == ("Farhad5778", "34549283+fafa33@users.noreply.github.com")
    with pytest.raises(driver.ExecutionJobError):
        driver.writer_identity("attacker")


# --- one Issue -> at most one active Draft PR -----------------------------------


def test_a_second_authorization_branch_cannot_open_a_second_pr_for_the_issue() -> None:
    head = "c" * 40
    branch = "issue-423-" + "1" * 16

    def request_json(_repo: str, _token: str, method: str, path: str, _payload: Any) -> Any:
        if path.startswith("git/ref/heads/"):
            return {"object": {"sha": head}}
        if path.startswith("issues/"):
            return {"number": 423, "state": "open", "title": "t"}
        if path.startswith("pulls?"):
            return [
                {"number": 9, "head": {"ref": "issue-423-" + "2" * 16, "repo": {"full_name": REPOSITORY}}},
                {"number": 10, "head": {"ref": "issue-999-" + "2" * 16, "repo": {"full_name": REPOSITORY}}},
            ]
        if path.startswith("compare/"):
            commit = {
                "sha": head,
                "parents": [{"sha": "b" * 40}],
                "committer": {"login": "fafa33"},
                "commit": {"verification": {"verified": True, "reason": "valid"}},
            }
            return {"commits": [commit], "total_commits": 1}
        raise AssertionError(path)

    evidence = candidate_pr.gather_evidence(
        repository=REPOSITORY,
        branch=branch,
        head_sha=head,
        token="t",
        request_json=request_json,
        authorized_signers=frozenset({"fafa33"}),
    )
    assert [p["number"] for p in evidence.open_pull_requests] == [9]
    decision = candidate_pr.decide_candidate_pr(evidence)
    assert decision.open is False and "already open for this Issue" in decision.reason


def test_dispatch_refuses_an_unadmitted_handoff_instead_of_executing(tmp_path: Path) -> None:
    deployment = Deployment(tmp_path)
    runtime = _runtime(deployment, result_timeout_seconds=5)
    signed = SignedIssueAgentAuthorization.from_json(_authorization_document())
    assert _inner(_authorization_document()).authorization_id == signed.authorization.authorization_id
    with pytest.raises(Exception, match="not the admitted execution"):
        runtime.dispatch("{}", derive_execution_target(signed))


# --- PR #558 review: OIDC audience/nbf, handoff state machine, model isolation ---


@pytest.mark.parametrize(
    "aud",
    [
        ["hunter-issue-agent-execution:fafa33/Project-Hunter", 7],
        ["hunter-issue-agent-execution:fafa33/Project-Hunter", {"x": 1}],
        ["hunter-issue-agent-execution:fafa33/Project-Hunter", None],
        ["hunter-issue-agent-execution:fafa33/Project-Hunter", ""],
        [],
        {"aud": "hunter-issue-agent-execution:fafa33/Project-Hunter"},
        12,
        "",
        True,
    ],
)
def test_malformed_audience_claims_fail_closed(aud: Any) -> None:
    with pytest.raises(ExecutionIdentityError, match="audience"):
        _verifier().verify(_token(aud=aud), now=START)


def test_audience_claim_absent_or_null_fails_closed() -> None:
    claims = _claims()
    claims.pop("aud")
    header = _b64url(json.dumps({"alg": "RS256", "kid": "github-test"}).encode())
    for body in (claims, {**claims, "aud": None}):
        payload = _b64url(json.dumps(body).encode())
        signature = OIDC_KEY.sign(f"{header}.{payload}".encode(), padding.PKCS1v15(), hashes.SHA256())
        with pytest.raises(ExecutionIdentityError, match="audience"):
            _verifier().verify(f"{header}.{payload}.{_b64url(signature)}", now=START)


@pytest.mark.parametrize("nbf", ["0", 1.5, None, True, [1], {"t": 1}])
def test_malformed_nbf_is_never_skipped(nbf: Any) -> None:
    claims = _claims()
    claims["nbf"] = nbf
    header = _b64url(json.dumps({"alg": "RS256", "kid": "github-test"}).encode())
    payload = _b64url(json.dumps(claims).encode())
    signature = OIDC_KEY.sign(f"{header}.{payload}".encode(), padding.PKCS1v15(), hashes.SHA256())
    with pytest.raises(ExecutionIdentityError, match="nbf"):
        _verifier().verify(f"{header}.{payload}.{_b64url(signature)}", now=START)


def test_well_formed_audiences_are_accepted() -> None:
    expected = oidc_audience(REPOSITORY)
    for aud in (expected, [expected], ["another-audience", expected]):
        assert _verifier().verify(_token(aud=aud), now=START).run == ("1001", "1")


def _blocking_runtime(deployment: Deployment, gate: threading.Event, entered: threading.Event, *, fail: bool):
    configuration = deployment.configuration

    def resolve(_handoff: str) -> str:
        entered.set()
        assert gate.wait(10)
        if fail:
            raise RuntimeError("prompt reconstruction failed")
        return "exact prompt"

    return GitHubHostedExecutionRuntime(
        repository=configuration.repository,
        owner_login=configuration.owner_login,
        evidence_database=configuration.evidence_database,
        oidc_verifier=_verifier(),
        prompt_resolver=resolve,
        clock=deployment.clock.now,
        result_timeout_seconds=5.0,
    )


@pytest.mark.parametrize("fail", [True, False])
def test_no_result_is_accepted_while_the_handoff_is_in_flight(tmp_path: Path, edge: Any, fail: bool) -> None:
    """Exact race from the review: a result racing an unresolved prompt is refused."""
    deployment = Deployment(tmp_path)
    gate, entered = threading.Event(), threading.Event()
    services = deployment.services(fallback=_blocking_runtime(deployment, gate, entered, fail=fail))
    hook = edge(services)
    authorization_id = _accepted(hook, _authorization_document())["authorization_id"]
    signed = SignedIssueAgentAuthorization.from_json(_authorization_document())
    fetched: dict[str, Any] = {}
    fetcher = threading.Thread(target=lambda: fetched.update(r=hook.fetch(authorization_id, "executor")))
    fetcher.start()
    assert entered.wait(5)
    # HANDOFF_IN_FLIGHT: the same OIDC-bound run races a result in.
    assert hook.result(authorization_id, _result(signed))[0] == 403
    assert services.ledger.entry(authorization_id).state == "DISPATCHED"
    gate.set()
    fetcher.join(10)
    if fail:
        assert fetched["r"][0] == 422
        assert _wait_for_ledger_state(services.ledger, authorization_id, "FAILED").failure_code == "EXECUTION_ERROR"
        # A failed resolution never becomes result-eligible.
        assert hook.result(authorization_id, _result(signed))[0] == 409
    else:
        assert fetched["r"][0] == 200
        assert hook.result(authorization_id, _result(signed))[0] == 200
        assert _wait_for_ledger_state(services.ledger, authorization_id, "COMPLETED").provider == EXECUTOR_PROVIDER


def test_model_runs_as_the_isolation_user_without_any_oidc_or_issuer_capability(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Review finding: the model must not be able to observe or recover OIDC minting capability."""
    import hunter.automation.issue_agent_replacement_executor as core

    secrets_ = {
        "ACTIONS_ID_TOKEN_REQUEST_TOKEN": "oidc-request-secret",
        "ACTIONS_ID_TOKEN_REQUEST_URL": "https://oidc-request-secret.example/token",
        "HUNTER_ISSUE_AGENT_WEBHOOK_URL": "https://issuer-secret.example/issue-agent/authorize",
    }
    environ = {
        **secrets_,
        "PATH": "/usr/bin",
        "GITHUB_REPOSITORY": REPOSITORY,
        driver.OPENCODE_EXECUTABLE_ENV: "/usr/local/bin/hunter-opencode",
        driver.UNTRUSTED_USER_ENV: "hunter-untrusted",
        driver.MODEL_API_KEY_ENV: "model-key",
        driver.MODEL_API_KEY_NAME_ENV: "GROQ_API_KEY",
    }
    signed = SignedIssueAgentAuthorization.from_json(_authorization_document())
    target = derive_execution_target(signed)
    launched: list[tuple[tuple[str, ...], dict[str, str]]] = []
    events: list[str] = []

    class Done:
        returncode = 0

    def fake_run(argv: Any, **kwargs: Any) -> Any:
        launched.append((tuple(argv), dict(kwargs.get("env") or {})))
        return Done()

    monkeypatch.setattr(driver, "require_isolation_user", lambda user: user)
    monkeypatch.setattr(core, "ISOLATION_ROOT", tmp_path)
    monkeypatch.setattr(driver.subprocess, "run", fake_run)
    monkeypatch.setattr(driver, "run_privileged", lambda *args, **_k: events.append(" ".join(args)))
    monkeypatch.setattr(core, "run_privileged", lambda *args, **_k: events.append(" ".join(args)))
    monkeypatch.setattr(
        driver,
        "_fetch",
        lambda *_a: {
            "schema_version": "hunter-issue-agent-execution-handoff-v1",
            "repository": REPOSITORY,
            "branch": target.branch,
            "base_sha": target.base_sha,
            "exact_prompt": "PROMPT",
            "authorization_id": target.authorization_id,
        },
    )
    monkeypatch.setattr(driver, "materialize_base", lambda workspace, git_dir, **_k: workspace.mkdir())
    monkeypatch.setattr(driver, "collect_result", lambda *_a, **_k: "{}")

    def mint(*_a: Any) -> str:
        events.append("oidc")
        return "token"

    monkeypatch.setattr(driver, "request_oidc_token", mint)
    monkeypatch.setattr(
        driver,
        "post_execution",
        lambda *_a: {"schema_version": "hunter-issue-agent-execution-result-accepted-v1",
                     "authorization_id": target.authorization_id, "result_sha256": "d"},
    )  # fmt: skip
    assert driver.run_execute(target.authorization_id, environ) == "d"
    model = [argv for argv, _ in launched if "/usr/local/bin/hunter-opencode" in argv]
    assert len(model) == 1
    argv = model[0]
    # A different uid, a cleared environment, and only the model allowlist.
    assert argv[:7] == ("sudo", "-n", "-u", "hunter-untrusted", "--", "/usr/bin/env", "-i")
    for every_argv, every_env in launched:
        flattened = " ".join(every_argv) + " " + " ".join(f"{k}={v}" for k, v in every_env.items())
        for value in secrets_.values():
            assert value not in flattened
        assert set(every_env) <= {"PATH"}
    # The workspace is handed to the isolation user before, and the user's
    # processes are killed before the trusted process mints its result token.
    assert any(event.startswith("chown -R hunter-untrusted") for event in events)
    assert events.index("pkill -KILL -u hunter-untrusted") < events.index("oidc")


def test_executor_refuses_to_run_a_model_without_uid_isolation(monkeypatch: pytest.MonkeyPatch) -> None:
    environ = {driver.OPENCODE_EXECUTABLE_ENV: "/usr/local/bin/hunter-opencode", driver.UNTRUSTED_USER_ENV: "root"}
    monkeypatch.setattr(driver, "_fetch", lambda *_a: pytest.fail("no handoff may be fetched without isolation"))
    for env in (environ, {**environ, driver.UNTRUSTED_USER_ENV: "no-such-hunter-user-xyz"}):
        with pytest.raises(driver.ExecutionJobError) as caught:
            driver.run_execute("hunter-issue-agent-authorization:" + "a" * 64, env)
        assert caught.value.code == "UNTRUSTED_ISOLATION_UNAVAILABLE"


def test_job_scripts_take_no_file_path_from_arguments(capsys: Any) -> None:
    """Sonar pythonsecurity:S8707 -- no CLI argument selects a path that is written or read."""
    for flag in ("--output", "--repository-checkout"):
        with pytest.raises(SystemExit):
            driver.main(["validate", "--authorization-id", "x", flag, "/tmp/x"])
    assert driver.main(["validate", "--authorization-id", "not-an-identity"]) == 2
    assert capsys.readouterr().out == ""
