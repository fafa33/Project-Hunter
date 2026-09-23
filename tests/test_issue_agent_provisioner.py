"""Issue #497 trusted provisioning edge: boundary, idempotency and composition tests.

The subject is the repository-owned provisioning boundary
(``scripts/hunter_issue_agent_provisioner.py``) that runs on Railway beside the
read-only execution issuer over the same persistent evidence database.  These
tests build the real deployment fixture -- a real Source Handling bootstrap over
an on-disk SQLite database, the real pinned production rule, the real
provenance resolver, and the real signed authorization documents minted by the
same trigger code that runs in GitHub Actions -- and drive the edge through a
real threaded ``HTTPServer`` on an ephemeral port.

The decisive composition test proves the Issue #497 acceptance criterion: a
brand-new authorized Issue auto-provisions through the boundary and is then
accepted by the real issuer edge, with no manual per-Issue provisioning step in
between.  The mismatch test proves the boundary fails closed (422) when the
per-Issue authority head was already planted with other content, so it can
never supersede provisioned state.
"""

from __future__ import annotations

import hashlib
import http.client
import json
import os
import threading
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import bootstrap_source_handling_authority as bootstrap
import hunter_issue_agent_issuer as issuer
import hunter_issue_agent_provisioner as provisioner
import hunter_issue_agent_trigger as trigger
import provision_source_handling_issue_authority as provisioning
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from issue_agent_edge_transport import MAX_REQUEST_BYTES
from issue_agent_wire import EdgeTransportClientMixin

from hunter.automation.agent_fallback_runtime import AgentFallbackRuntimeReceipt
from hunter.automation.issue_agent_execution import (
    EVIDENCE_DATABASE_ENV,
    EXECUTION_BRANCH_ENV,
    ISSUE_AGENT_AUTHORIZATION_LABEL,
    ISSUE_AGENT_PROFILE_REGISTRY,
    ISSUE_AGENT_ROUTE_REGISTRY,
    ISSUE_AGENT_VERIFYING_KEY_ENV,
    OWNER_LOGIN_ENV,
    REPOSITORY_CHECKOUT_ENV,
    REPOSITORY_ENV,
    SOURCE_HANDLING_GENESIS_RULE_SHA256_ENV,
    SOURCE_HANDLING_VERIFICATION_KEY_ENV,
    SOURCE_HANDLING_VERIFICATION_KEY_SHA256_ENV,
    IssueAgentConfigurationError,
    IssueAgentExecutionLedger,
    SignedIssueAgentAuthorization,
    build_production_source_handling_resolver,
    issue_agent_document_id,
)
from hunter.evidence_intelligence import smart_prompt_routing, source_handling_provenance
from hunter.evidence_intelligence.engineering_task_ingress import GovernedEngineeringTaskIngress
from hunter.evidence_intelligence.intake import EvidenceIntelligenceIntakeService
from hunter.evidence_intelligence.repository import EvidenceIntelligenceRepository
from hunter.evidence_intelligence.smart_prompt_routing import SmartPromptMachine
from hunter.evidence_intelligence.source_handling_persistence import (
    IssueSourceTransientIntakeBoundary,
    SourceHandlingBlockedError,
)
from hunter.evidence_intelligence.source_handling_provenance import (
    GENESIS_RULE_SHA256_ENV as PROV_GENESIS_RULE_SHA256_ENV,
)
from hunter.evidence_intelligence.source_handling_provenance import (
    VERIFICATION_KEY_ENV as PROV_VERIFICATION_KEY_ENV,
)
from hunter.evidence_intelligence.source_handling_provenance import (
    VERIFICATION_KEY_SHA256_ENV as PROV_VERIFICATION_KEY_SHA256_ENV,
)
from hunter.evidence_intelligence.source_handling_provenance import (
    production_provenance_resolver,
)

RULE_GOLDEN = bootstrap.PINNED_PRODUCTION_RULE_SHA256
REPOSITORY = "fafa33/Project-Hunter"
OWNER = "fafa33"
BRANCH = "issue-497-harden-issue-agent"
ISSUE_NUMBER = 497
ISSUE_URL = f"https://github.com/{REPOSITORY}/issues/{ISSUE_NUMBER}"
ISSUE_TITLE = "Harden Issue Agent admission: remove cutover loss and per-Issue provisioning"
ISSUE_BODY = "The governed issue-agent path must provision authority automatically before dispatch."
UPDATED_AT = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
AUTOMATION_SIGNING_KEY_HEX = "11" * 32
AUTOMATION_VERIFYING_KEY_HEX = "d04ab232742bb4ab3a1368bd4615e4e6d0224ab71a016baf8520a332c9778737"
ISSUER_SIGNING_KEY = Ed25519PrivateKey.from_private_bytes(bytes.fromhex("33" * 32))
ISSUER_VERIFYING_KEY_HEX = (
    ISSUER_SIGNING_KEY.public_key()
    .public_bytes(encoding=serialization.Encoding.Raw, format=serialization.PublicFormat.Raw)
    .hex()
)
FOREIGN_ISSUER_KEY = Ed25519PrivateKey.from_private_bytes(bytes.fromhex("44" * 32))


def _private_key_bytes() -> bytes:
    return Ed25519PrivateKey.generate().private_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PrivateFormat.Raw,
        encryption_algorithm=serialization.NoEncryption(),
    )


def _public_key_bytes(private_key: bytes) -> bytes:
    return (
        Ed25519PrivateKey.from_private_bytes(private_key)
        .public_key()
        .public_bytes(encoding=serialization.Encoding.Raw, format=serialization.PublicFormat.Raw)
    )


def _operator_environment(
    monkeypatch: pytest.MonkeyPatch, database: Path, private_key: bytes, *, include_signing_key: bool = True
) -> None:
    rule = bootstrap._load_production_rule()
    verification_key_hex, verification_key_sha256, genesis_rule_sha256 = bootstrap._derived_digests(private_key, rule)
    if include_signing_key:
        monkeypatch.setenv(provisioner._SOURCE_HANDLING_SIGNING_KEY_ENV, private_key.hex())
    monkeypatch.setenv(EVIDENCE_DATABASE_ENV, str(database))
    monkeypatch.setenv(PROV_VERIFICATION_KEY_ENV, verification_key_hex)
    monkeypatch.setenv(PROV_VERIFICATION_KEY_SHA256_ENV, verification_key_sha256)
    monkeypatch.setenv(PROV_GENESIS_RULE_SHA256_ENV, genesis_rule_sha256)
    monkeypatch.setenv(REPOSITORY_ENV, REPOSITORY)
    monkeypatch.setenv(OWNER_LOGIN_ENV, OWNER)
    monkeypatch.setenv(ISSUE_AGENT_VERIFYING_KEY_ENV, ISSUER_VERIFYING_KEY_HEX)
    monkeypatch.setenv(SOURCE_HANDLING_VERIFICATION_KEY_ENV, verification_key_hex)
    monkeypatch.setenv(SOURCE_HANDLING_VERIFICATION_KEY_SHA256_ENV, verification_key_sha256)
    monkeypatch.setenv(SOURCE_HANDLING_GENESIS_RULE_SHA256_ENV, genesis_rule_sha256)


def _bootstrap(database: Path, private_key: bytes) -> None:
    saved = os.environ.get(provisioning.SIGNING_KEY_ENV)
    os.environ[provisioning.SIGNING_KEY_ENV] = private_key.hex()
    try:
        bootstrap.main(["--database", str(database), "--json"])
    finally:
        if saved is None:
            os.environ.pop(provisioning.SIGNING_KEY_ENV, None)
        else:
            os.environ[provisioning.SIGNING_KEY_ENV] = saved


# --- Authorization documents ------------------------------------------------


def _event(*, body: str = ISSUE_BODY, title: str = ISSUE_TITLE) -> dict[str, Any]:
    return {
        "action": "labeled",
        "repository": {"full_name": REPOSITORY},
        "sender": {"login": OWNER},
        "label": {"name": ISSUE_AGENT_AUTHORIZATION_LABEL},
        "issue": {
            "number": ISSUE_NUMBER,
            "state": "open",
            "html_url": ISSUE_URL,
            "title": title,
            "body": body,
            "updated_at": UPDATED_AT,
        },
    }


def _authorization_document(
    *,
    signing_key: Any = ISSUER_SIGNING_KEY,
    event: dict[str, Any] | None = None,
    **overrides: Any,
) -> str:
    source = _event(**overrides) if event is None else event
    authorization = trigger.authorize_event(
        source,
        expected_repository=REPOSITORY,
        owner_login=OWNER,
        authorization_label=ISSUE_AGENT_AUTHORIZATION_LABEL,
    )
    return trigger.sign_authorization(authorization, signing_key=signing_key).to_json()


# --- Fallback runtime double (provider + git are genuinely external) ---------


class RecordingFallback:
    def __init__(self) -> None:
        self.documents: list[str | bytes] = []
        self._receipt = AgentFallbackRuntimeReceipt(
            provider="codex",
            head_before="a" * 40,
            head_after="b" * 40,
            attempts=(),
            validation_succeeded=True,
        )

    def dispatch(self, document: str | bytes) -> AgentFallbackRuntimeReceipt:
        self.documents.append(document)
        return self._receipt


# --- Wire harness: real threaded HTTPServer on an ephemeral port -------------


class ProvisioningEdge(EdgeTransportClientMixin):
    def __init__(self, configuration: provisioner.ProvisionerConfiguration, *, max_workers: int = 8) -> None:
        self.server = provisioner.ProvisionerServer(
            "127.0.0.1",
            0,
            configuration,
            max_workers=max_workers,
        )
        self.server.start()
        self.port = self.server._server.server_address[1]

    def post(self, body: bytes | None, *, headers: dict[str, str] | None = None) -> tuple[int, str]:
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=15)
        connection.request("POST", "/issue-agent/provision", body=body, headers=headers or {})
        response = connection.getresponse()
        received = response.read()
        connection.close()
        return response.status, received.decode("utf-8")


class IssuerEdge(EdgeTransportClientMixin):
    def __init__(self, services: issuer.IssuerServices) -> None:
        self.server = issuer.IssuerServer("127.0.0.1", 0, services)
        self.server.start()
        self.port = self.server._server.server_address[1]

    def post(self, body: bytes) -> tuple[int, str]:
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=15)
        connection.request("POST", "/issue-agent/authorize", body=body)
        response = connection.getresponse()
        received = response.read()
        connection.close()
        return response.status, received.decode("utf-8")


@pytest.fixture(autouse=True)
def _automation_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HUNTER_PROMPT_AUTOMATION_SIGNING_KEY", AUTOMATION_SIGNING_KEY_HEX)
    monkeypatch.setenv("HUNTER_PROMPT_AUTOMATION_VERIFYING_KEY", AUTOMATION_VERIFYING_KEY_HEX)
    # ``production_provenance_resolver`` builds its view once from the first
    # caller's operator environment and caches it in a module global.  Every
    # deployment here has its own evidence database, so the cache must be
    # dropped before each test or a later deployment would resolve against an
    # earlier test's database (same pattern as test_source_handling_provisioning).
    monkeypatch.setattr(source_handling_provenance, "_production_view", None)


@pytest.fixture
def edge() -> Any:
    hooks: list[ProvisioningEdge] = []

    def _make(configuration: provisioner.ProvisionerConfiguration, **kwargs: Any) -> ProvisioningEdge:
        hook = ProvisioningEdge(configuration, **kwargs)
        hooks.append(hook)
        return hook

    yield _make
    for hook in hooks:
        hook.close()


@pytest.fixture
def deployment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """A freshly bootstrapped authority database plus provisioner configuration."""
    database = tmp_path / "evidence.sqlite"
    private_key = _private_key_bytes()
    _bootstrap(database, private_key)
    _operator_environment(monkeypatch, database, private_key)
    configuration = provisioner.ProvisionerConfiguration.from_environment()
    return {
        "database": database,
        "private_key": private_key,
        "configuration": configuration,
    }


# --- Transport boundary -----------------------------------------------------


def test_healthz_is_ok(deployment: dict[str, Any], edge: Any) -> None:
    hook = edge(deployment["configuration"])
    status, body = hook.get("/healthz")
    assert status == 200
    assert json.loads(body)["service"] == "hunter-issue-agent-provisioner"


def test_wrong_path_is_not_found(deployment: dict[str, Any], edge: Any) -> None:
    hook = edge(deployment["configuration"])
    status, _ = hook.get("/issue-agent/nope")
    assert status == 404


def test_missing_content_length_is_refused(deployment: dict[str, Any], edge: Any) -> None:
    hook = edge(deployment["configuration"])
    connection = http.client.HTTPConnection("127.0.0.1", hook.port, timeout=15)
    connection.putrequest("POST", "/issue-agent/provision")
    connection.putheader("Content-Type", "application/json")
    connection.endheaders()
    response = connection.getresponse()
    received = response.read()
    connection.close()
    assert response.status == 411
    assert received.decode("utf-8")


def test_invalid_content_length_is_bad_request(deployment: dict[str, Any], edge: Any) -> None:
    hook = edge(deployment["configuration"])
    status, _ = hook.post(b"{}", headers={"Content-Length": "not-a-number"})
    assert status == 400


def test_payload_too_large_is_refused(deployment: dict[str, Any], edge: Any) -> None:
    hook = edge(deployment["configuration"])
    connection = http.client.HTTPConnection("127.0.0.1", hook.port, timeout=15)
    connection.putrequest("POST", "/issue-agent/provision")
    connection.putheader("Content-Type", "application/json")
    connection.putheader("Content-Length", str(MAX_REQUEST_BYTES + 1))
    connection.endheaders()
    response = connection.getresponse()
    received = response.read()
    connection.close()
    assert response.status == 413
    assert received.decode("utf-8")


def test_malformed_document_is_bad_request(deployment: dict[str, Any], edge: Any) -> None:
    hook = edge(deployment["configuration"])
    status, body = hook.post(b"{}")
    assert status == 400
    assert "error" in json.loads(body)


def test_duplicate_key_document_is_refused(deployment: dict[str, Any], edge: Any) -> None:
    hook = edge(deployment["configuration"])
    duplicate = b'{"schema_version":"a","schema_version":"b"}'
    status, body = hook.post(duplicate)
    assert status == 400
    assert "duplicate" in json.loads(body)["error"].lower()


def test_foreign_issuer_signature_is_unauthorized(deployment: dict[str, Any], edge: Any) -> None:
    hook = edge(deployment["configuration"])
    status, body = hook.post(_authorization_document(signing_key=FOREIGN_ISSUER_KEY).encode("utf-8"))
    assert status == 401
    assert "trusted Issue authorization issuer" in json.loads(body)["error"]


def test_wrong_repository_is_forbidden(deployment: dict[str, Any], edge: Any) -> None:
    """The boundary refuses documents naming a repository other than this deployment."""
    configured = replace(deployment["configuration"], repository="other/report")
    hook = edge(configured)
    status, _ = hook.post(_authorization_document().encode("utf-8"))
    assert status == 403


def test_wrong_owner_is_forbidden(deployment: dict[str, Any], edge: Any) -> None:
    """The boundary refuses documents authorized by someone other than this deployment's owner."""
    configured = replace(deployment["configuration"], owner_login="someone-else")
    hook = edge(configured)
    status, _ = hook.post(_authorization_document().encode("utf-8"))
    assert status == 403


# --- Provisioning behaviour -------------------------------------------------


def test_fresh_issue_provisions_and_rerun_is_an_idempotent_noop(deployment: dict[str, Any], edge: Any) -> None:
    hook = edge(deployment["configuration"])
    document = _authorization_document().encode("utf-8")

    status, body = hook.post(document)
    assert status == 200
    payload = json.loads(body)
    assert payload["authorization_id"]
    assert payload["document_id"] == issue_agent_document_id(
        SignedIssueAgentAuthorization.from_json(document).authorization
    )
    assert set(payload["records"]) == {"FACT", "FIELD_CATEGORY_REGISTRY", "POLICY"}
    assert all(entry["status"] == "provisioned" for entry in payload["records"].values())
    assert payload["as_of"]

    status, body = hook.post(document)
    assert status == 200
    payload = json.loads(body)
    assert payload["status"] == "already-provisioned"
    assert all(entry["status"] == "already-provisioned" for entry in payload["records"].values())
    assert payload["document_id"] == issue_agent_document_id(
        SignedIssueAgentAuthorization.from_json(document).authorization
    )


def test_provisioning_binds_only_repository_defaults() -> None:
    """Issue body text can change the document identity, never the classification options."""
    fact_options, policy_options = provisioner._repository_default_options()
    assert fact_options.sensitivity == "PUBLIC"
    assert fact_options.persistence_restriction == "FULL_CONTENT_ALLOWED"
    assert fact_options.operation_restrictions == ()
    assert fact_options.secret_presence == ()
    assert policy_options.processing_decision == "ALLOW"
    assert policy_options.delete_or_expire_disposition == "ALLOW"


def test_mismatched_existing_authority_fails_closed_without_mutation(deployment: dict[str, Any], edge: Any) -> None:
    """A conflicting per-Issue head planted for the same document is never superseded."""
    database = deployment["database"]
    signing_key = deployment["private_key"]
    configuration = deployment["configuration"]
    hook = edge(configuration)

    document = _authorization_document()
    authorization = SignedIssueAgentAuthorization.from_json(document).authorization
    rule = bootstrap._load_production_rule()

    conflicting_fact_options = provisioning._FactOptions(
        sensitivity="INTERNAL",
        operation_restrictions=(),
        persistence_restriction="FULL_CONTENT_ALLOWED",
        secret_presence=(),
    )
    _, policy_options = provisioner._repository_default_options()
    provisioning._run(
        database=str(database),
        signing_key=signing_key,
        rule=rule,
        authorization=authorization,
        fact_options=conflicting_fact_options,
        policy_options=policy_options,
        provenance_authority_identity=provisioning.AUTHORITY_COMPONENT_ID,
        as_of=None,
    )

    status, body = hook.post(document.encode("utf-8"))
    assert status == 422
    assert "refusing to replace" in json.loads(body)["error"]

    rerun = provisioning._run(
        database=str(database),
        signing_key=signing_key,
        rule=rule,
        authorization=authorization,
        fact_options=conflicting_fact_options,
        policy_options=policy_options,
        provenance_authority_identity=provisioning.AUTHORITY_COMPONENT_ID,
        as_of=None,
    )
    assert rerun["status"] == "already-provisioned"
    assert rerun["document_id"] == issue_agent_document_id(authorization)


def test_missing_provisioner_configuration_fails_closed() -> None:
    with pytest.raises(IssueAgentConfigurationError):
        provisioner.ProvisionerConfiguration.from_environment(environ={})


def test_configuration_requires_operator_provenance_variables(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    database = tmp_path / "evidence.sqlite"
    private_key = _private_key_bytes()
    _bootstrap(database, private_key)
    monkeypatch.setenv(provisioner._SOURCE_HANDLING_SIGNING_KEY_ENV, private_key.hex())
    monkeypatch.setenv(EVIDENCE_DATABASE_ENV, str(database))
    monkeypatch.setenv(REPOSITORY_ENV, REPOSITORY)
    monkeypatch.setenv(OWNER_LOGIN_ENV, OWNER)
    monkeypatch.setenv(ISSUE_AGENT_VERIFYING_KEY_ENV, ISSUER_VERIFYING_KEY_HEX)

    with pytest.raises(SourceHandlingBlockedError, match="incomplete"):
        provisioner.ProvisionerConfiguration.from_environment()


def test_configuration_from_environment_binds_deployment(deployment: dict[str, Any]) -> None:
    configuration = deployment["configuration"]
    assert configuration.repository == REPOSITORY
    assert configuration.owner_login == OWNER
    assert len(configuration.signing_key) == 32
    assert configuration.production_rule["authorization_rule_id"]


# --- Composition: the automatic owner-label -> issuer path -------------------


def _issuer_environment(
    tmp_path: Path, database: Path, verification_key: bytes, *, overrides: dict[str, str]
) -> dict[str, str]:
    environment = {
        REPOSITORY_ENV: REPOSITORY,
        OWNER_LOGIN_ENV: OWNER,
        EVIDENCE_DATABASE_ENV: str(database),
        EXECUTION_BRANCH_ENV: BRANCH,
        REPOSITORY_CHECKOUT_ENV: str(tmp_path),
        SOURCE_HANDLING_VERIFICATION_KEY_ENV: verification_key.hex(),
        SOURCE_HANDLING_VERIFICATION_KEY_SHA256_ENV: hashlib.sha256(verification_key).hexdigest(),
        SOURCE_HANDLING_GENESIS_RULE_SHA256_ENV: RULE_GOLDEN,
        smart_prompt_routing._PROMPT_AUTOMATION_VERIFYING_KEY_ENV: AUTOMATION_VERIFYING_KEY_HEX,
        smart_prompt_routing._PROMPT_AUTOMATION_SIGNING_KEY_ENV: AUTOMATION_SIGNING_KEY_HEX,
        ISSUE_AGENT_VERIFYING_KEY_ENV: ISSUER_VERIFYING_KEY_HEX,
    }
    environment.update(overrides)
    return environment


def _issuer_services(
    direction_configuration: issuer.IssuerConfiguration,
    *,
    fallback: RecordingFallback,
) -> issuer.IssuerServices:
    resolver = build_production_source_handling_resolver(
        direction_configuration,
        provenance_resolver=production_provenance_resolver,
    )
    repository = EvidenceIntelligenceRepository(direction_configuration.evidence_database)
    ledger = IssueAgentExecutionLedger(
        direction_configuration.evidence_database,
        instance_id=f"issue-497-test-{threading.get_ident()}",
    )
    boundary = IssueSourceTransientIntakeBoundary(
        intake=EvidenceIntelligenceIntakeService(repository),
        resolver=resolver,
        clock=direction_configuration.clock,
    )
    machine = SmartPromptMachine(
        repository=repository,
        profiles=ISSUE_AGENT_PROFILE_REGISTRY,
        routes=ISSUE_AGENT_ROUTE_REGISTRY,
        source_handling_resolver=resolver,
        clock=direction_configuration.clock,
    )
    ingress = GovernedEngineeringTaskIngress(
        machine=machine,
        routes=ISSUE_AGENT_ROUTE_REGISTRY,
        profiles=ISSUE_AGENT_PROFILE_REGISTRY,
    )
    return issuer.IssuerServices(
        configuration=direction_configuration,
        repository=repository,
        source_handling_resolver=resolver,
        ledger=ledger,
        fallback=fallback,
        ingress=ingress,
        boundary=boundary,
    )


def test_composition_fresh_issue_auto_provisions_then_issuer_accepts(
    tmp_path: Path, deployment: dict[str, Any], edge: Any
) -> None:
    """Brand-new Issue: provisioner 200 then issuer 200, with no manual step."""
    database = deployment["database"]
    private_key = deployment["private_key"]
    configuration = deployment["configuration"]

    hook = edge(configuration)
    document = _authorization_document().encode("utf-8")
    status, body = hook.post(document)
    assert status == 200
    assert json.loads(body)["status"] == "provisioned"

    issuer_environment = _issuer_environment(tmp_path, database, _public_key_bytes(private_key), overrides={})
    issuer_configuration = issuer.IssuerConfiguration.from_environment(
        environ=issuer_environment, provenance_resolver=production_provenance_resolver
    )
    fallback = RecordingFallback()
    services = _issuer_services(issuer_configuration, fallback=fallback)
    webhook = IssuerEdge(services)
    try:
        status, body = webhook.post(document)
    finally:
        webhook.close()

    assert status == 200, body
    assert json.loads(body)["state"] == "DISPATCHED"
    assert fallback.documents


def test_composition_without_provisioning_the_issuer_refuses(tmp_path: Path, deployment: dict[str, Any]) -> None:
    """Hardening: on a fresh bootstrap the issuer alone refuses the brand-new Issue.

    Only the automatic provisioning boundary can unblock it, so skipping
    provisioning (the hidden manual step being eliminated) can never be an
    accepted surrogate for authority the repository never gave.
    """
    database = deployment["database"]
    private_key = deployment["private_key"]

    document = _authorization_document().encode("utf-8")
    issuer_environment = _issuer_environment(tmp_path, database, _public_key_bytes(private_key), overrides={})
    issuer_configuration = issuer.IssuerConfiguration.from_environment(
        environ=issuer_environment, provenance_resolver=production_provenance_resolver
    )
    services = _issuer_services(issuer_configuration, fallback=RecordingFallback())
    webhook = IssuerEdge(services)
    try:
        status, body = webhook.post(document)
    finally:
        webhook.close()

    assert status == 422
    assert "authority" in json.loads(body)["error"].lower()
