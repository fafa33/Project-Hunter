"""Regression coverage for Codex findings on PR #785."""

from __future__ import annotations

import threading
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import hunter_issue_agent_issuer as issuer
import pytest

from hunter.automation.issue_agent_state import FAILURE_CODES, PUBLICATION_REFUSAL_CODES
from hunter.automation.workspace_authority import WorkspaceAuthorityMismatch


def test_workspace_refusal_is_a_durable_closed_publication_outcome() -> None:
    assert "WORKSPACE_AUTHORITY_MISMATCH" in PUBLICATION_REFUSAL_CODES
    assert "WORKSPACE_AUTHORITY_MISMATCH" in FAILURE_CODES


def test_workspace_renewal_outage_does_not_orphan_running_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Ledger renewal and shutdown tracking survive repeated API outages."""
    monkeypatch.setenv("HUNTER_WORKSPACE_API_URL", "http://127.0.0.1:8765")
    monkeypatch.delenv("HUNTER_WORKSPACE_REGISTRY_PATH", raising=False)
    monkeypatch.delenv("HUNTER_WORKSPACE_AUTHORITY_KEY_HEX", raising=False)
    monkeypatch.setenv("HUNTER_WORKSPACE_LEASE_TOKEN", "lease")
    attempts: list[int] = []

    class OfflineAuthority:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            pass

        def renew_owned_lease(self, **kwargs: Any) -> None:
            attempts.append(kwargs["issue"])
            raise WorkspaceAuthorityMismatch("transient outage")

    monkeypatch.setattr(issuer, "WorkspaceAuthorityClient", OfflineAuthority)
    ledger_renewals: list[int] = []

    class Ledger:
        def renew_lease(self, authorization: Any, *, health_at: datetime) -> None:
            ledger_renewals.append(authorization.authorization_id)

    services = SimpleNamespace(
        ledger=Ledger(),
        configuration=SimpleNamespace(
            clock=SimpleNamespace(now=lambda: datetime.now(UTC)),
            instance_id="issuer",
        ),
    )
    prepared = SimpleNamespace(
        authorization=SimpleNamespace(authorization_id=42),
        target=SimpleNamespace(issue_number=575),
    )
    registry = issuer.ExecutionWorkerRegistry()
    worker = threading.Thread(target=lambda: None)
    registry.register(worker, prepared)

    class TwoCycles:
        calls = 0

        def wait(self, interval: float) -> bool:
            self.calls += 1
            return self.calls > 2

    issuer._run_lease_renewer(services, registry, interval=0.01, stop_event=TwoCycles())
    assert ledger_renewals == [42, 42]
    assert attempts == [575, 575]
    assert registry.active() == [(worker, prepared)]


@pytest.mark.parametrize("failure", [OSError(28, "No space left on device"), PermissionError(13, "Permission denied")])
def test_workspace_filesystem_renewal_failure_keeps_all_workers_tracked(
    monkeypatch: pytest.MonkeyPatch, failure: OSError
) -> None:
    """A file-authority write failure cannot stop renewal of other live executions."""
    monkeypatch.delenv("HUNTER_WORKSPACE_API_URL", raising=False)
    monkeypatch.setenv("HUNTER_WORKSPACE_REGISTRY_PATH", "/tmp/hunter-test-registry.json")
    monkeypatch.setenv("HUNTER_WORKSPACE_AUTHORITY_KEY_HEX", "ab" * 32)
    monkeypatch.setenv("HUNTER_WORKSPACE_LEASE_TOKEN", "lease")
    attempts: list[int] = []

    def fail_renewal(self: Any, **kwargs: Any) -> None:
        attempts.append(kwargs["issue"])
        raise failure

    monkeypatch.setattr(issuer.WorkspaceRegistry, "renew_owned_lease", fail_renewal)
    ledger_renewals: list[int] = []

    class Ledger:
        def renew_lease(self, authorization: Any, *, health_at: datetime) -> None:
            ledger_renewals.append(authorization.authorization_id)

    services = SimpleNamespace(
        ledger=Ledger(),
        configuration=SimpleNamespace(clock=SimpleNamespace(now=lambda: datetime.now(UTC)), instance_id="issuer"),
    )
    registry = issuer.ExecutionWorkerRegistry()
    workers = []
    for issue in (575, 586):
        worker = threading.Thread(target=lambda: None)
        prepared = SimpleNamespace(
            authorization=SimpleNamespace(authorization_id=issue),
            target=SimpleNamespace(issue_number=issue),
        )
        registry.register(worker, prepared)
        workers.append((worker, prepared))

    class TwoCycles:
        calls = 0

        def wait(self, interval: float) -> bool:
            self.calls += 1
            return self.calls > 2

    issuer._run_lease_renewer(services, registry, interval=0.01, stop_event=TwoCycles())
    assert ledger_renewals == [575, 586, 575, 586]
    assert attempts == [575, 586, 575, 586]
    assert registry.active() == workers
