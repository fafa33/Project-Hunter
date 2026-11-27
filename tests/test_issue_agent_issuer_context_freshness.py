"""Issue #575 finding 3: a long-running issuer must resolve knowledge per authorization.

The service composes once at process startup, but authorizations are minted
against the currently verified DPM/SPM knowledge.  This suite holds one issuer
object alive while knowledge changes, then proves that a later signed
authorization compiles and verifies against its own matching context.  A stale,
missing or mismatched context is refused; the signed lineage checks remain the
only authority.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import hunter_issue_agent_issuer as issuer
import pytest
import test_issue_agent_issuer as tii

from hunter.automation import issue_agent_state as state
from hunter.automation.issue_agent_execution import (
    IssueAgentExecutionError,
    SignedIssueAgentAuthorization,
)
from hunter.evidence_intelligence.engineering_context_authority import EngineeringContextAuthority


class FakeContext:
    """A minimal ECA stand-in whose canonical bytes identify one knowledge state."""

    def __init__(self, marker: str) -> None:
        self.marker = marker

    def canonical_json(self, key: str, *, scope: Any) -> str:
        return json.dumps({"key": key, "marker": self.marker}, sort_keys=True)


class FakeMachine:
    def __init__(self, *, engineering_context_authority: FakeContext, **_kwargs: Any) -> None:
        self.context = engineering_context_authority


class FakeIngress:
    def __init__(self, *, machine: FakeMachine, routes: Any, profiles: Any) -> None:
        self.machine = machine

    def compile(self, _request: Any, *, implementation_scope: Any) -> Any:
        marker = self.machine.context.marker
        content = f"prompt-{marker}"
        build_id = f"build-{marker}"
        return SimpleNamespace(
            envelope=SimpleNamespace(
                verify_issuer_signature=lambda _verifier: None,
                build_record_id=build_id,
                envelope_id=f"envelope-{marker}",
            ),
            compilation=SimpleNamespace(
                manifest=SimpleNamespace(build_record_id=build_id),
                orchestration=SimpleNamespace(
                    build_result=SimpleNamespace(prompt_artifact=SimpleNamespace(content=content))
                ),
            ),
        )


class NoOpBoundary:
    def preflight(self, *_args: Any, **_kwargs: Any) -> None:
        return None

    def ingest(self, *_args: Any, **_kwargs: Any) -> None:
        return None


class NoOpLedger:
    def claim(self, *_args: Any, **_kwargs: Any) -> None:
        return None

    def record_dispatch(self, *_args: Any, **_kwargs: Any) -> None:
        return None

    def fail(self, *_args: Any, **_kwargs: Any) -> None:
        return None


def _scope_digest(signed: SignedIssueAgentAuthorization) -> str:
    scope = signed.implementation_scope
    return hashlib.sha256(
        state.canonical_json(
            {
                "task_id": scope.task_id,
                "branch_pattern": scope.branch_pattern,
                "base_ref": scope.base_ref,
                "base_sha": scope.base_sha,
                "allowed_paths": list(scope.allowed_paths),
                "prohibited_paths": list(scope.prohibited_paths),
            }
        )
    ).hexdigest()


def _evidence(document: str, signed: SignedIssueAgentAuthorization, marker: str) -> dict[str, Any]:
    context = FakeContext(marker)
    prompt = hashlib.sha256(f"prompt-{marker}".encode()).hexdigest()
    dpm = hashlib.sha256(
        context.canonical_json("engineering.implement", scope=signed.implementation_scope).encode()
    ).hexdigest()
    return {
        "authorization_envelope_sha256": hashlib.sha256(document.encode("utf-8")).hexdigest(),
        "task_scope_sha256": _scope_digest(signed),
        "lineage": {"prompt_sha256": prompt, "dpm_context_sha256": dpm},
    }


def _services(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    marker_by_authorization: dict[str, str | None],
    documents: dict[str, str],
    *,
    initial_ingress_marker: str = "old",
    signed_marker_by_authorization: dict[str, str] | None = None,
) -> Any:
    monkeypatch.setattr(issuer, "SmartPromptMachine", FakeMachine)
    monkeypatch.setattr(issuer, "GovernedEngineeringTaskIngress", FakeIngress)
    monkeypatch.setattr(issuer, "serialize_prompt_automation_handoff", lambda _envelope: "handoff")
    for name in (
        "HUNTER_WORKSPACE_API_URL",
        "HUNTER_WORKSPACE_REGISTRY_PATH",
        "HUNTER_WORKSPACE_AUTHORITY_KEY_HEX",
        "HUNTER_WORKSPACE_CHECKPOINT",
        "HUNTER_WORKSPACE_LEASE_TOKEN",
    ):
        monkeypatch.delenv(name, raising=False)
    deployment = tii.Deployment(tmp_path)
    signed_markers = signed_marker_by_authorization or marker_by_authorization
    evidence = {
        identity: _evidence(document, SignedIssueAgentAuthorization.from_json(document), signed_markers[identity])
        for identity, (document, marker) in {
            identity: (documents[identity], marker)
            for identity, marker in marker_by_authorization.items()
            if marker is not None
        }.items()
    }
    binding = issuer.LineageBinding(
        knowledge_overlay=(),
        engineering_context_authority=FakeContext(initial_ingress_marker),
        verify_dispatch=issuer.bind_dispatch_to_lineage(lambda authorization: evidence[authorization.authorization_id]),
        resolve_context=lambda authorization: (
            cast(EngineeringContextAuthority, FakeContext(marker))
            if (marker := marker_by_authorization[authorization.authorization_id])
            else None
        ),
    )
    frozen_ingress = FakeIngress(
        machine=FakeMachine(engineering_context_authority=FakeContext(initial_ingress_marker)),
        routes=(),
        profiles=(),
    )
    services = deployment.services()
    return dataclasses.replace(
        services,
        ingress=frozen_ingress,
        boundary=NoOpBoundary(),
        ledger=NoOpLedger(),
        lineage_binding=binding,
    )


def test_a_live_issuer_accepts_authorization_minted_after_knowledge_changes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    first_document = tii._authorization_document()
    second_document = tii._authorization_document(title="A later authorization after knowledge changed")
    first = SignedIssueAgentAuthorization.from_json(first_document)
    second = SignedIssueAgentAuthorization.from_json(second_document)
    services = _services(
        tmp_path,
        monkeypatch,
        {
            first.authorization.authorization_id: "old",
            second.authorization.authorization_id: "new",
        },
        {
            first.authorization.authorization_id: first_document,
            second.authorization.authorization_id: second_document,
        },
    )

    assert (
        issuer.prepare_authorization(services, first).authorization.authorization_id
        == first.authorization.authorization_id
    )
    # No restart, no recomposition: the same services object resolves the new context.
    assert (
        issuer.prepare_authorization(services, second).authorization.authorization_id
        == second.authorization.authorization_id
    )


def test_a_mismatched_knowledge_context_is_refused_without_restart(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    document = tii._authorization_document()
    signed = SignedIssueAgentAuthorization.from_json(document)
    services = _services(
        tmp_path,
        monkeypatch,
        {signed.authorization.authorization_id: "new"},
        {signed.authorization.authorization_id: document},
        signed_marker_by_authorization={signed.authorization.authorization_id: "old"},
    )
    with pytest.raises(IssueAgentExecutionError):
        issuer.prepare_authorization(services, signed)


def test_an_unavailable_knowledge_context_is_refused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    document = tii._authorization_document()
    signed = SignedIssueAgentAuthorization.from_json(document)
    services = _services(
        tmp_path,
        monkeypatch,
        {signed.authorization.authorization_id: None},
        {signed.authorization.authorization_id: document},
    )
    with pytest.raises(IssueAgentExecutionError):
        issuer.prepare_authorization(services, signed)
