#!/usr/bin/env python3
"""Trusted issuer HTTP edge for governed Issue agent execution.

This is the deployable endpoint that consumes
``hunter-issue-agent-signed-authorization-v2`` from the GitHub trigger,
verifies the authorization, invokes the production SmartPromptMachine
composition root, persists the canonical build, issues the signed
``PromptAutomationEnvelopeHandoff``, and forwards it unchanged into the
governed fallback runtime, which executes it in an isolated per-authorization
workspace on the branch and base derived from the signed authorization
(``docs/ISSUE_AGENT_EXECUTION_CONTRACT.md``).

Execution outcomes after the ACK are served read-only at
``GET /issue-agent/status/<authorization_id>``: only the ledger state, the
execution target, the verified outcome and a fixed-vocabulary failure code,
never the handoff, the prompt or free-form failure text.

It is deployed behind the ``HUNTER_ISSUE_AGENT_WEBHOOK_URL`` and operates
entirely from trusted repository-owned configuration. No secret material is
ever committed; all secrets are environment/repository secrets only.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import secrets
import signal
import threading
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

from issue_agent_edge_transport import (
    MAX_CONCURRENT_REQUEST_WORKERS,
    REQUEST_READ_TIMEOUT_SECONDS,
    BoundedThreadingHTTPServer,
    IssueAgentEdgeRequestHandler,
    setup_logging,
)

from hunter.automation.agent_fallback_runtime import AgentFallbackRuntimeReceipt
from hunter.automation.issue_agent_execution import (
    EVIDENCE_DATABASE_ENV,
    ISSUE_AGENT_EXECUTION_RECEIPT_SCHEMA_VERSION,
    ISSUE_AGENT_PROFILE_REGISTRY,
    ISSUE_AGENT_ROUTE_REGISTRY,
    ISSUE_AGENT_VERIFYING_KEY_ENV,
    OWNER_LOGIN_ENV,
    REPOSITORY_CHECKOUT_ENV,
    REPOSITORY_ENV,
    SOURCE_HANDLING_GENESIS_RULE_SHA256_ENV,
    SOURCE_HANDLING_VERIFICATION_KEY_ENV,
    SOURCE_HANDLING_VERIFICATION_KEY_SHA256_ENV,
    IssueAgentAuthorization,
    IssueAgentAuthorizationError,
    IssueAgentAuthorizationVerifier,
    IssueAgentConfigurationError,
    IssueAgentExecutionError,
    IssueAgentExecutionLedger,
    IssueAgentExecutionReceipt,
    IssueAgentExecutionTarget,
    IssueAgentFallbackRuntime,
    IssueAgentIssuerError,
    IssueAgentLedgerEntry,
    IssueAgentReplayError,
    IssueAgentRuntimeReceiptError,
    SignedIssueAgentAuthorization,
    SourceHandlingBlockedError,
    build_production_source_handling_resolver,
    derive_execution_target,
    issue_agent_document_id,
    issue_agent_failure_attempts,
    issue_agent_failure_code,
    issue_agent_intake_reference,
    issue_agent_task_request,
)
from hunter.automation.issue_agent_workspace import IssueAgentWorkspaceRuntime
from hunter.automation.n8n_handoff import serialize_prompt_automation_handoff
from hunter.evidence_intelligence.engineering_task_ingress import GovernedEngineeringTaskIngress
from hunter.evidence_intelligence.intake import EvidenceIntelligenceIntakeService
from hunter.evidence_intelligence.pre_model import PreModelInvariantError
from hunter.evidence_intelligence.repository import EvidenceIntelligenceRepository
from hunter.evidence_intelligence.smart_prompt_routing import (
    _PROMPT_AUTOMATION_SIGNING_KEY_ENV,
    _PROMPT_AUTOMATION_VERIFYING_KEY_ENV,
    PromptAutomationVerifier,
    SmartPromptMachine,
    SmartPromptMachineError,
)
from hunter.evidence_intelligence.source_handling_persistence import (
    IssueSourceTransientIntakeBoundary,
    ProductionSourceHandlingAuthorityResolver,
    ProvenanceResolver,
    SourceHandlingOperatorRoot,
)
from hunter.execution import Clock, SystemClock

#: Maximum request body size and transport bound are inherited from the shared
#: issue-agent edge transport; the issuer-specific execution bound stays here.
_MAX_CONCURRENT_EXECUTIONS: Final[int] = 2
_EXECUTION_SLOTS = threading.BoundedSemaphore(_MAX_CONCURRENT_EXECUTIONS)
_ISSUE_AGENT_ACCEPTED_SCHEMA = "hunter-issue-agent-accepted-v1"
_ISSUE_AGENT_STATUS_SCHEMA = "hunter-issue-agent-status-v1"
STATUS_PATH_PREFIX: Final[str] = "/issue-agent/status/"
_STATUS_PATH_RE = re.compile(r"/issue-agent/status/(hunter-issue-agent-authorization:[0-9a-f]{64})")

#: How often the issuer renews the durable execution lease of every accepted
#: execution it is still genuinely running. The ledger lease window is far
#: larger than this interval, so a single missed tick can never strand a live
#: execution behind an expired lease.
_LEASE_RENEWAL_INTERVAL_SECONDS: Final[float] = 30.0

_ISSUER_INSTANCE_PREFIX = "hunter-issue-agent-issuer"


def _new_instance_id() -> str:
    """A stable-for-process-lifetime issuer instance identity.

    Each issuer process carries one instance id, generated at trusted bootstrap
    and used as the durable lease owner for every ledger row it claims. A
    second live instance therefore has a different owner identity and can never
    mistake another instance's live execution for its own leftover work.
    """
    return f"{_ISSUER_INSTANCE_PREFIX}-{secrets.token_hex(6)}"


#: Required environment variables for operational configuration
_REQUIRED_ENV: Final[tuple[str, ...]] = (
    REPOSITORY_ENV,
    OWNER_LOGIN_ENV,
    EVIDENCE_DATABASE_ENV,
    REPOSITORY_CHECKOUT_ENV,
    SOURCE_HANDLING_VERIFICATION_KEY_ENV,
    SOURCE_HANDLING_VERIFICATION_KEY_SHA256_ENV,
    SOURCE_HANDLING_GENESIS_RULE_SHA256_ENV,
    _PROMPT_AUTOMATION_VERIFYING_KEY_ENV,
    _PROMPT_AUTOMATION_SIGNING_KEY_ENV,
    ISSUE_AGENT_VERIFYING_KEY_ENV,
)


@dataclass(frozen=True, slots=True)
class IssuerConfiguration:
    """Operational configuration captured once at startup."""

    repository: str
    owner_login: str
    evidence_database: Path
    repository_checkout: Path
    source_handling_verification_key: bytes
    source_handling_operator_root: SourceHandlingOperatorRoot
    issuer_verifier: IssueAgentAuthorizationVerifier
    prompt_verifier: PromptAutomationVerifier
    provenance_resolver: ProvenanceResolver
    clock: Clock
    instance_id: str

    @classmethod
    def from_environment(
        cls,
        *,
        environ: Mapping[str, str] | None = None,
        provenance_resolver: ProvenanceResolver,
        clock: Clock | None = None,
    ) -> IssuerConfiguration:
        source = os.environ if environ is None else environ

        # Required configuration
        missing = [var for var in _REQUIRED_ENV if not source.get(var, "").strip()]
        if missing:
            raise IssueAgentConfigurationError(
                f"missing required operational configuration: {', '.join(sorted(missing))}"
            )

        # Source Handling authority
        key_hex = source.get(SOURCE_HANDLING_VERIFICATION_KEY_ENV, "").strip()
        try:
            verification_key = bytes.fromhex(key_hex)
        except ValueError:
            raise IssueAgentConfigurationError(
                f"{SOURCE_HANDLING_VERIFICATION_KEY_ENV} must be a hex-encoded Ed25519 public key"
            ) from None

        operator_root = SourceHandlingOperatorRoot(
            genesis_rule_sha256=source.get(SOURCE_HANDLING_GENESIS_RULE_SHA256_ENV, "").strip(),
            verification_key_sha256=source.get(SOURCE_HANDLING_VERIFICATION_KEY_SHA256_ENV, "").strip(),
        )

        # Issuer authorization verifier (public half only)
        issuer_verifier = IssueAgentAuthorizationVerifier.from_environment(environ=source)

        # Prompt automation verifier
        prompt_verifier = PromptAutomationVerifier.from_environment(environ=source)

        return cls(
            repository=source.get(REPOSITORY_ENV, "").strip(),
            owner_login=source.get(OWNER_LOGIN_ENV, "").strip(),
            evidence_database=Path(source.get(EVIDENCE_DATABASE_ENV, "").strip()),
            repository_checkout=Path(source.get(REPOSITORY_CHECKOUT_ENV, "").strip()),
            source_handling_verification_key=verification_key,
            source_handling_operator_root=operator_root,
            issuer_verifier=issuer_verifier,
            prompt_verifier=prompt_verifier,
            provenance_resolver=provenance_resolver,
            clock=clock or SystemClock(),
            instance_id=_new_instance_id(),
        )


@dataclass(frozen=True, slots=True)
class IssuerServices:
    """Composed services for the trusted issuer edge."""

    configuration: IssuerConfiguration
    repository: EvidenceIntelligenceRepository
    source_handling_resolver: ProductionSourceHandlingAuthorityResolver
    ledger: IssueAgentExecutionLedger
    fallback: IssueAgentFallbackRuntime
    ingress: GovernedEngineeringTaskIngress
    boundary: IssueSourceTransientIntakeBoundary


def compose_services(configuration: IssuerConfiguration) -> IssuerServices:
    """Compose all services from captured bootstrap configuration."""
    resolver = build_production_source_handling_resolver(
        configuration,
        provenance_resolver=configuration.provenance_resolver,
    )
    repository = EvidenceIntelligenceRepository(configuration.evidence_database)
    ledger = IssueAgentExecutionLedger(
        configuration.evidence_database,
        instance_id=configuration.instance_id,
    )
    # Startup recovery is owner/lease-aware: only incomplete rows whose lease has
    # lapsed are failed closed, never a row another live instance is still
    # executing under a valid foreign lease.
    ledger.recover_expired_on_startup(failed_at=configuration.clock.now())
    boundary = IssueSourceTransientIntakeBoundary(
        intake=EvidenceIntelligenceIntakeService(repository),
        resolver=resolver,
        clock=configuration.clock,
    )
    machine = SmartPromptMachine(
        repository=repository,
        profiles=ISSUE_AGENT_PROFILE_REGISTRY,
        routes=ISSUE_AGENT_ROUTE_REGISTRY,
        source_handling_resolver=resolver,
        clock=configuration.clock,
    )
    ingress = GovernedEngineeringTaskIngress(
        machine=machine,
        routes=ISSUE_AGENT_ROUTE_REGISTRY,
        profiles=ISSUE_AGENT_PROFILE_REGISTRY,
    )
    fallback = IssueAgentWorkspaceRuntime(
        workspace_root=configuration.repository_checkout,
        repository=configuration.repository,
        environ=os.environ,
    )
    return IssuerServices(
        configuration=configuration,
        repository=repository,
        source_handling_resolver=resolver,
        ledger=ledger,
        fallback=fallback,
        ingress=ingress,
        boundary=boundary,
    )


@dataclass(frozen=True, slots=True)
class PreparedIssueAgentExecution:
    """Durably prepared work whose provider phase may safely outlive HTTP."""

    authorization: IssueAgentAuthorization
    document_id: str
    build_record_id: str
    envelope_id: str
    handoff_document: str
    target: IssueAgentExecutionTarget


def prepare_authorization(
    services: IssuerServices,
    signed: SignedIssueAgentAuthorization,
) -> PreparedIssueAgentExecution:
    """Validate, claim, compile and durably record dispatch before HTTP ACK."""
    services.configuration.issuer_verifier.verify(signed)
    authorization = signed.authorization
    if signed.implementation_scope.task_id != authorization.authorization_id:
        raise IssueAgentAuthorizationError("implementation scope task_id must bind authorization identity")

    if authorization.repository != services.configuration.repository:
        raise IssueAgentAuthorizationError("authorization names a different repository than this deployment")
    if authorization.authorized_by != services.configuration.owner_login:
        raise IssueAgentAuthorizationError("only the configured repository owner may authorize execution")

    # Pure: an underivable target is refused before the identity is consumed.
    target = derive_execution_target(signed)
    reference = issue_agent_intake_reference(authorization)
    document_id = issue_agent_document_id(authorization)
    request = issue_agent_task_request(authorization)
    if request.document_id != document_id:
        raise IssueAgentExecutionError("Issue task request does not bind the ingested document identity")

    services.boundary.preflight(
        reference,
        processing_run_id=authorization.authorization_id,
        processed_at=services.configuration.clock.now(),
    )

    services.ledger.claim(
        authorization,
        claimed_at=services.configuration.clock.now(),
    )

    try:
        services.boundary.ingest(
            reference,
            processing_run_id=authorization.authorization_id,
            processed_at=services.configuration.clock.now(),
        )

        compiled = services.ingress.compile(request, implementation_scope=signed.implementation_scope)
        envelope = compiled.envelope
        envelope.verify_issuer_signature(services.configuration.prompt_verifier)
        if envelope.build_record_id != compiled.compilation.manifest.build_record_id:
            raise IssueAgentExecutionError("signed envelope and persisted build refer to different lineage")

        handoff_document = serialize_prompt_automation_handoff(envelope)

        services.ledger.record_dispatch(
            authorization,
            document_id=document_id,
            build_record_id=envelope.build_record_id,
            envelope_id=envelope.envelope_id,
            handoff_document=handoff_document,
            target=target,
            dispatched_at=services.configuration.clock.now(),
        )
    except BaseException as error:
        _record_failure(services, authorization, error)
        raise

    return PreparedIssueAgentExecution(
        authorization=authorization,
        document_id=document_id,
        build_record_id=envelope.build_record_id,
        envelope_id=envelope.envelope_id,
        handoff_document=handoff_document,
        target=target,
    )


def _record_failure(services: IssuerServices, authorization: IssueAgentAuthorization, error: BaseException) -> None:
    services.ledger.fail(
        authorization,
        failed_at=services.configuration.clock.now(),
        failure_type=type(error).__name__,
        failure_message=str(error),
        failure_code=issue_agent_failure_code(error),
        failure_attempts=issue_agent_failure_attempts(error),
    )


def finish_prepared_authorization(
    services: IssuerServices,
    prepared: PreparedIssueAgentExecution,
) -> IssueAgentExecutionReceipt:
    """Run only the slow provider phase and persist a terminal outcome."""
    authorization = prepared.authorization
    try:
        receipt = services.fallback.dispatch(prepared.handoff_document, prepared.target)
        if not isinstance(receipt, AgentFallbackRuntimeReceipt):
            raise IssueAgentRuntimeReceiptError("fallback runtime did not return a canonical execution receipt")

        services.ledger.complete(
            authorization,
            completed_at=services.configuration.clock.now(),
            provider=receipt.provider,
            head_after=receipt.head_after,
        )

        return IssueAgentExecutionReceipt(
            authorization_id=authorization.authorization_id,
            document_id=prepared.document_id,
            build_record_id=prepared.build_record_id,
            envelope_id=prepared.envelope_id,
            handoff_document=prepared.handoff_document,
            fallback=receipt,
        )
    except BaseException as error:
        _record_failure(services, authorization, error)
        raise


def execute_authorization(
    services: IssuerServices,
    signed: SignedIssueAgentAuthorization,
) -> IssueAgentExecutionReceipt:
    """Synchronous compatibility path used by direct composition tests."""
    prepared = prepare_authorization(services, signed)
    return finish_prepared_authorization(services, prepared)


class ExecutionWorkerRegistry:
    """Tracks accepted, still-running provider execution workers.

    A worker is registered before it starts and unregistered by the worker
    itself as its final act, so the registry and the shutdown drain agree about
    what is still genuinely running. Each registered worker also names the
    authorization it owns, which is what the lease renewer refreshes while the
    provider runs. Registry operations are lock-guarded, but shutdown snapshots
    the live set and joins outside the lock, so one stalled provider can never
    hold the lock while every other worker's teardown waits on it.
    """

    __slots__ = ("_lock", "_entries")

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._entries: list[tuple[threading.Thread, PreparedIssueAgentExecution]] = []

    def register(self, worker: threading.Thread, prepared: PreparedIssueAgentExecution) -> None:
        """Record an accepted execution worker *before* it is started."""
        with self._lock:
            self._entries.append((worker, prepared))

    def unregister(self, worker: threading.Thread) -> None:
        """Drop one worker; a worker that already finished is already absent."""
        with self._lock:
            self._entries = [(candidate, prepared) for candidate, prepared in self._entries if candidate is not worker]

    def active(self) -> list[tuple[threading.Thread, PreparedIssueAgentExecution]]:
        """Snapshot of every still-registered execution worker and its work."""
        with self._lock:
            return list(self._entries)


def _run_prepared_in_background(
    services: IssuerServices,
    prepared: PreparedIssueAgentExecution,
    registry: ExecutionWorkerRegistry | None = None,
) -> None:
    worker = threading.current_thread()
    try:
        finish_prepared_authorization(services, prepared)
    except BaseException:
        logging.getLogger(__name__).exception(
            "Issue Agent background execution failed for %s",
            prepared.authorization.authorization_id,
        )
    finally:
        if registry is not None:
            registry.unregister(worker)
        _EXECUTION_SLOTS.release()


def _run_lease_renewer(
    services: IssuerServices,
    registry: ExecutionWorkerRegistry,
    *,
    interval: float,
    stop_event: threading.Event,
) -> None:
    """Extend the durable lease of every accepted execution still running here.

    A live provider keeps its ledger row non-terminal forever, so a second
    issuer instance booting while this one runs can never fail the row closed.
    The loop runs until shutdown: once a worker's row becomes terminal the
    renewal raises and the worker is dropped from the registry.
    """
    logger = logging.getLogger(__name__)
    while not stop_event.wait(interval):
        for worker, prepared in registry.active():
            try:
                services.ledger.renew_lease(
                    prepared.authorization,
                    health_at=services.configuration.clock.now(),
                )
            except IssueAgentExecutionError as error:
                logger.warning(
                    "execution %s is no longer renewable by this instance: %s",
                    prepared.authorization.authorization_id,
                    error,
                )
                registry.unregister(worker)


def status_payload(entry: IssueAgentLedgerEntry) -> dict[str, Any]:
    """The non-secret execution status of one ledger row.

    Only the lifecycle state, the execution target, the verified outcome and a
    fixed-vocabulary failure classification. The handoff document, the prompt
    and free-form failure text are never part of it.
    """
    attempts: Any = None
    if entry.failure_attempts:
        try:
            attempts = json.loads(entry.failure_attempts)
        except json.JSONDecodeError:
            attempts = None
    return {
        "authorization_id": entry.authorization_id,
        "state": entry.state,
        "claimed_at": entry.claimed_at,
        "dispatched_at": entry.dispatched_at,
        "completed_at": entry.completed_at,
        "failed_at": entry.failed_at,
        "execution_branch": entry.execution_branch,
        "base_sha": entry.base_sha,
        "provider": entry.provider,
        "head_after": entry.head_after,
        "failure_type": entry.failure_type,
        "failure_code": entry.failure_code,
        "failure_attempts": attempts,
        "schema_version": _ISSUE_AGENT_STATUS_SCHEMA,
    }


def _accepted_payload(prepared: PreparedIssueAgentExecution) -> dict[str, Any]:
    return {
        "authorization_id": prepared.authorization.authorization_id,
        "document_id": prepared.document_id,
        "build_record_id": prepared.build_record_id,
        "envelope_id": prepared.envelope_id,
        "handoff_document": prepared.handoff_document,
        "state": "DISPATCHED",
        "schema_version": _ISSUE_AGENT_ACCEPTED_SCHEMA,
    }


class _IssuerRequestHandler(IssueAgentEdgeRequestHandler):
    """HTTP handler for the trusted issuer edge's admission of one authorization.

    ``timeout`` is set per-server on the concrete subclass: it is the finite
    socket read deadline applied by ``setup()`` before any byte is trusted, so
    a connection that stalls while awaiting its declared body is bounded instead
    of holding a worker forever.  The bounded body parsing and canonical JSON
    responses come from the shared issue-agent edge transport.
    """

    services: IssuerServices | None = None
    shutdown_event: threading.Event | None = None
    execution_registry: ExecutionWorkerRegistry | None = None
    execution_admission_enabled: bool = False

    endpoint = "/issue-agent/authorize"
    service_name = "hunter-issue-agent-issuer"
    error_schema_version = ISSUE_AGENT_EXECUTION_RECEIPT_SCHEMA_VERSION

    def do_GET(self) -> None:
        """Health, plus the read-only execution status of one authorization."""
        if not self.path.startswith(STATUS_PATH_PREFIX):
            super().do_GET()
            return
        match = _STATUS_PATH_RE.fullmatch(self.path)
        assert self.services is not None
        entry = self.services.ledger.entry(match.group(1)) if match is not None else None
        if entry is None:
            self._send_error(404, "Not Found")
            return
        self._send_json(200, status_payload(entry))

    def handle_authorization(self, signed: SignedIssueAgentAuthorization) -> None:
        """Admit one verified, durably prepared authorization.

        The transport already parsed the canonical signed document; this is the
        issuer's own trust boundary: claim execution ownership and persist the
        exact handoff durably, then ACK before the slow provider phase so
        admission is bounded independently of provider duration.
        """
        # PR-A: retire Railway execution before claim/dispatch/provider reachability.
        if not self.execution_admission_enabled:
            self._send_error(503, "Issue Agent execution backend is unavailable")
            return
        # PR-A: retire Railway execution before claim/dispatch/provider reachability.
        if not self.execution_admission_enabled:
            self._send_error(503, "Issue Agent execution backend is unavailable")
            return
        if not _EXECUTION_SLOTS.acquire(blocking=False):
            self._send_error(503, "Issue Agent execution capacity is saturated")
            return

        assert self.services is not None
        try:
            prepared = prepare_authorization(self.services, signed)
        except IssueAgentReplayError as error:
            _EXECUTION_SLOTS.release()
            self._send_error(409, str(error))
            return
        except IssueAgentIssuerError as error:
            _EXECUTION_SLOTS.release()
            self._send_error(401, str(error))
            return
        except IssueAgentAuthorizationError as error:
            _EXECUTION_SLOTS.release()
            self._send_error(403, str(error))
            return
        except SourceHandlingBlockedError as error:
            _EXECUTION_SLOTS.release()
            self._send_error(422, str(error))
            return
        except IssueAgentConfigurationError as error:
            _EXECUTION_SLOTS.release()
            self._send_error(500, str(error))
            return
        except IssueAgentExecutionError as error:
            _EXECUTION_SLOTS.release()
            self._send_error(500, str(error))
            return
        except SmartPromptMachineError as error:
            _EXECUTION_SLOTS.release()
            self._send_error(422, str(error))
            return
        except PreModelInvariantError as error:
            _EXECUTION_SLOTS.release()
            self._send_error(
                422,
                f"pre-model invariant rejected execution preparation: {error.reason_code}",
            )
            return
        except Exception as error:  # noqa: BLE001
            _EXECUTION_SLOTS.release()
            self._send_error(
                500,
                f"unexpected execution preparation failure: {type(error).__name__}",
            )
            return

        worker = threading.Thread(
            target=_run_prepared_in_background,
            args=(self.services, prepared, self.execution_registry),
            name=f"issue-agent-{prepared.authorization.issue_number}",
            daemon=False,
        )
        try:
            # Register before starting: the registry and the shutdown drain must
            # agree about every accepted worker before it can begin running.
            assert self.execution_registry is not None
            self.execution_registry.register(worker, prepared)
            worker.start()
        except Exception as error:  # noqa: BLE001
            assert self.execution_registry is not None
            self.execution_registry.unregister(worker)
            try:
                self.services.ledger.fail(
                    prepared.authorization,
                    failed_at=self.services.configuration.clock.now(),
                    failure_type=type(error).__name__,
                    failure_message=str(error),
                )
            finally:
                _EXECUTION_SLOTS.release()
            self._send_error(500, "unable to start accepted Issue Agent execution")
            return

        # ACK is intentionally independent of provider duration. At this point
        # authorization ownership and the exact handoff are already durable.
        self._send_json(200, _accepted_payload(prepared))


class IssuerServer:
    """Bounded concurrent HTTP server for the trusted issuer edge.

    Requests run in workers drawn from a small explicit pool (a busy transport
    is told 503 at the socket, never queued behind an unbounded thread), and
    every connection carries a finite read deadline, so a client that opens a
    connection and withholds its body terminates within a bounded time instead
    of occupying a worker indefinitely.

    Accepted executions are owned by this instance, tracked as non-daemon
    workers, and their durable leases are renewed while the providers run.
    Shutdown stops new admissions, then drains the tracked execution workers to
    a durable terminal outcome; a drain timeout expires without ever
    fabricating a FAILED row for a provider that is still genuinely running.
    """

    def __init__(
        self,
        host: str,
        port: int,
        services: IssuerServices,
        *,
        shutdown_event: threading.Event | None = None,
        read_timeout: float = REQUEST_READ_TIMEOUT_SECONDS,
        max_workers: int = MAX_CONCURRENT_REQUEST_WORKERS,
        lease_renewal_interval: float = _LEASE_RENEWAL_INTERVAL_SECONDS,
        execution_admission_enabled: bool = False,
    ) -> None:
        self._host = host
        self._port = port
        self._services = services
        self._shutdown_event = shutdown_event or threading.Event()
        self._executions = ExecutionWorkerRegistry()
        self._lease_renewal_interval = lease_renewal_interval
        self._shutdown_complete = False

        class Handler(_IssuerRequestHandler):
            shutdown_event = self._shutdown_event
            timeout = read_timeout
            execution_registry = self._executions

        Handler.services = services
        Handler.execution_admission_enabled = execution_admission_enabled
        Handler.execution_admission_enabled = execution_admission_enabled
        self._server = BoundedThreadingHTTPServer(
            (host, port),
            Handler,
            max_workers=max_workers,
        )
        self._thread: threading.Thread | None = None
        self._renewer: threading.Thread | None = None

    def start(self) -> None:
        """Start the server in a background thread."""
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        self._renewer = threading.Thread(
            target=_run_lease_renewer,
            args=(
                self._services,
                self._executions,
            ),
            kwargs={
                "interval": self._lease_renewal_interval,
                "stop_event": self._shutdown_event,
            },
            name="issue-agent-lease-renewer",
            daemon=True,
        )
        self._renewer.start()
        logging.getLogger(__name__).info("Trusted issuer edge listening on %s:%d", self._host, self._port)

    def shutdown(self, timeout: float = 30.0) -> None:
        """Shutdown the server gracefully.

        Idempotent: a second call returns immediately. New admissions stop
        first (the accept loop is halted), then every accepted execution worker
        is joined to a durable terminal outcome. The join happens outside the
        registry lock so one still-running provider cannot stall the registry
        or any other worker's teardown, and a provider that outlives the drain
        timeout is left genuinely running rather than falsely marked FAILED.
        """
        if self._shutdown_complete:
            return
        self._shutdown_complete = True
        logger = logging.getLogger(__name__)
        logger.info("stopping trusted issuer edge")
        self._shutdown_event.set()
        self._server.shutdown()
        self._server.server_close()
        if self._thread is not None:
            self._thread.join(timeout=timeout)
        workers = self._executions.active()
        if workers:
            logger.info(
                "draining %d accepted execution worker(s) with %.1fs timeout",
                len(workers),
                timeout,
            )
        for worker, _prepared in workers:
            worker.join(timeout=timeout)
        logger.info("Trusted issuer edge stopped")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="hunter_issue_agent_issuer")
    parser.add_argument("--host", default="0.0.0.0", help="bind address")
    parser.add_argument("--port", type=int, default=8080, help="bind port")
    parser.add_argument("--provenance-resolver", required=True, help="import path to ProvenanceResolver callable")
    parser.add_argument("--verbose", action="store_true", help="enable debug logging")
    return parser


def _import_provenance_resolver(path: str) -> ProvenanceResolver:
    """Import a ProvenanceResolver callable from a dotted path."""
    module_name, _, attr_name = path.rpartition(".")
    if not module_name:
        raise ValueError(f"invalid provenance resolver path: {path}")
    module = __import__(module_name, fromlist=[attr_name])
    resolver = getattr(module, attr_name, None)
    if resolver is None or not callable(resolver):
        raise ValueError(f"{path} is not a callable ProvenanceResolver")
    return resolver


def main(argv: list[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    setup_logging(arguments.verbose)
    logger = logging.getLogger(__name__)

    try:
        provenance_resolver = _import_provenance_resolver(arguments.provenance_resolver)
    except (ImportError, AttributeError, ValueError) as error:
        logger.error("failed to import provenance resolver: %s", error)
        return 2

    try:
        configuration = IssuerConfiguration.from_environment(
            provenance_resolver=provenance_resolver,
        )
    except IssueAgentConfigurationError as error:
        logger.error("configuration error: %s", error)
        return 2

    try:
        services = compose_services(configuration)
    except (IssueAgentConfigurationError, OSError) as error:
        logger.error("service composition failed: %s", error)
        return 2

    server = IssuerServer(arguments.host, arguments.port, services)

    def _signal_handler(signum: int, frame: Any) -> None:
        logger.info("received signal %d, shutting down", signum)
        server.shutdown()

    signal.signal(signal.SIGTERM, _signal_handler)
    signal.signal(signal.SIGINT, _signal_handler)

    try:
        server.start()
        # Wait for shutdown
        server._shutdown_event.wait()
    except KeyboardInterrupt:
        pass
    finally:
        server.shutdown()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
