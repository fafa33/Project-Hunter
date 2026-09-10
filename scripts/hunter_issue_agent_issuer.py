#!/usr/bin/env python3
"""Trusted issuer HTTP edge for governed Issue agent execution.

This is the deployable endpoint that consumes
``hunter-issue-agent-signed-authorization-v1`` from the GitHub trigger,
verifies the authorization, invokes the production SmartPromptMachine
composition root, persists the canonical build, issues the signed
``PromptAutomationEnvelopeHandoff``, and forwards it unchanged into the
existing fallback runtime.

It is deployed behind the ``HUNTER_ISSUE_AGENT_WEBHOOK_URL`` and operates
entirely from trusted repository-owned configuration. No secret material is
ever committed; all secrets are environment/repository secrets only.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import signal
import threading
from collections.abc import Mapping
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from socketserver import ThreadingMixIn
from typing import Any, Final

from hunter.automation.agent_fallback_runtime import (
    AgentFallbackRuntimeReceipt,
    OperationalAgentFallbackRuntime,
)
from hunter.automation.issue_agent_execution import (
    EVIDENCE_DATABASE_ENV,
    EXECUTION_BRANCH_ENV,
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
    IssueAgentAuthorizationError,
    IssueAgentAuthorizationVerifier,
    IssueAgentConfigurationError,
    IssueAgentExecutionError,
    IssueAgentExecutionLedger,
    IssueAgentExecutionReceipt,
    IssueAgentIssuerError,
    IssueAgentReplayError,
    SignedIssueAgentAuthorization,
    SourceHandlingBlockedError,
    build_production_source_handling_resolver,
    issue_agent_document_id,
    issue_agent_intake_reference,
    issue_agent_task_request,
)
from hunter.automation.n8n_handoff import serialize_prompt_automation_handoff
from hunter.evidence_intelligence.engineering_task_ingress import GovernedEngineeringTaskIngress
from hunter.evidence_intelligence.intake import EvidenceIntelligenceIntakeService
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

#: Maximum request body size in bytes (256 KiB)
_MAX_REQUEST_BYTES: Final[int] = 256 * 1024

#: Small explicit bound on concurrent request workers. A stalled body holds
#: exactly one slot and is released by the read deadline below, so an
#: unauthenticated client can never monopolize the transport with one-thread
#: starvation or unbounded thread churn.
_MAX_CONCURRENT_REQUEST_WORKERS: Final[int] = 8

#: Finite socket read deadline applied to every request connection before the
#: body is read, so a client that withholds its declared body terminates within
#: a bounded time (fail-closed 408) instead of occupying a worker indefinitely.
_REQUEST_READ_TIMEOUT_SECONDS: Final[float] = 15.0

#: Required environment variables for operational configuration
_REQUIRED_ENV: Final[tuple[str, ...]] = (
    REPOSITORY_ENV,
    OWNER_LOGIN_ENV,
    EVIDENCE_DATABASE_ENV,
    EXECUTION_BRANCH_ENV,
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
    execution_branch: str
    repository_checkout: Path
    source_handling_verification_key: bytes
    source_handling_operator_root: SourceHandlingOperatorRoot
    issuer_verifier: IssueAgentAuthorizationVerifier
    prompt_verifier: PromptAutomationVerifier
    provenance_resolver: ProvenanceResolver
    clock: Clock

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
            execution_branch=source.get(EXECUTION_BRANCH_ENV, "").strip(),
            repository_checkout=Path(source.get(REPOSITORY_CHECKOUT_ENV, "").strip()),
            source_handling_verification_key=verification_key,
            source_handling_operator_root=operator_root,
            issuer_verifier=issuer_verifier,
            prompt_verifier=prompt_verifier,
            provenance_resolver=provenance_resolver,
            clock=clock or SystemClock(),
        )


@dataclass(frozen=True, slots=True)
class IssuerServices:
    """Composed services for the trusted issuer edge."""

    configuration: IssuerConfiguration
    repository: EvidenceIntelligenceRepository
    source_handling_resolver: ProductionSourceHandlingAuthorityResolver
    ledger: IssueAgentExecutionLedger
    fallback: OperationalAgentFallbackRuntime
    ingress: GovernedEngineeringTaskIngress
    boundary: IssueSourceTransientIntakeBoundary


def compose_services(configuration: IssuerConfiguration) -> IssuerServices:
    """Compose all services from captured bootstrap configuration."""
    resolver = build_production_source_handling_resolver(
        configuration,
        provenance_resolver=configuration.provenance_resolver,
    )
    repository = EvidenceIntelligenceRepository(configuration.evidence_database)
    ledger = IssueAgentExecutionLedger(configuration.evidence_database)
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
    fallback = OperationalAgentFallbackRuntime(
        repo_dir=configuration.repository_checkout,
        branch=configuration.execution_branch,
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


def execute_authorization(
    services: IssuerServices,
    signed: SignedIssueAgentAuthorization,
) -> IssueAgentExecutionReceipt:
    """Execute one signed authorization through the governed path."""
    # 1. Verify issuer signature (trusted origin)
    services.configuration.issuer_verifier.verify(signed)
    authorization = signed.authorization

    # 2. Verify repository and owner match deployment
    if authorization.repository != services.configuration.repository:
        raise IssueAgentAuthorizationError("authorization names a different repository than this deployment")
    if authorization.authorized_by != services.configuration.owner_login:
        raise IssueAgentAuthorizationError("only the configured repository owner may authorize execution")

    # 3. Deterministic mapping (pure, before ownership)
    reference = issue_agent_intake_reference(authorization)
    document_id = issue_agent_document_id(authorization)
    request = issue_agent_task_request(authorization)
    if request.document_id != document_id:
        raise IssueAgentExecutionError("Issue task request does not bind the ingested document identity")

    # 4. Source Handling preflight: validate authority before claiming ownership.
    # This is side-effect free -- no ledger row, no persisted artifacts, no
    # dispatch. A failed preflight allows retry once authority is corrected.
    services.boundary.preflight(
        reference,
        processing_run_id=authorization.authorization_id,
        processed_at=services.configuration.clock.now(),
    )

    # 5. Claim durable execution ownership
    services.ledger.claim(authorization, claimed_at=services.configuration.clock.now())

    # 6. Ingest through ADR 0036 boundary
    services.boundary.ingest(
        reference,
        processing_run_id=authorization.authorization_id,
        processed_at=services.configuration.clock.now(),
    )

    # 7. Compile through the one canonical engineering-task ingress
    compiled = services.ingress.compile(request)
    envelope = compiled.envelope
    envelope.verify_issuer_signature(services.configuration.prompt_verifier)
    if envelope.build_record_id != compiled.compilation.manifest.build_record_id:
        raise IssueAgentExecutionError("signed envelope and persisted build refer to different lineage")

    # 8. Serialize exact non-content handoff
    handoff_document = serialize_prompt_automation_handoff(envelope)

    # 9. Record handoff durably BEFORE dispatch
    services.ledger.record_dispatch(
        authorization,
        document_id=document_id,
        build_record_id=envelope.build_record_id,
        envelope_id=envelope.envelope_id,
        handoff_document=handoff_document,
        dispatched_at=services.configuration.clock.now(),
    )

    # 10. Dispatch to fallback runtime (unchanged handoff)
    receipt = services.fallback.dispatch(handoff_document)
    if not isinstance(receipt, AgentFallbackRuntimeReceipt):
        raise IssueAgentExecutionError("fallback runtime did not return a canonical execution receipt")

    # 11. Complete ledger
    services.ledger.complete(authorization, completed_at=services.configuration.clock.now())

    return IssueAgentExecutionReceipt(
        authorization_id=authorization.authorization_id,
        document_id=document_id,
        build_record_id=envelope.build_record_id,
        envelope_id=envelope.envelope_id,
        handoff_document=handoff_document,
        fallback=receipt,
    )


def _canonical_json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


class _IssuerRequestHandler(BaseHTTPRequestHandler):
    """HTTP handler for the trusted issuer edge.

    ``timeout`` is set per-server on the concrete subclass: it is the finite
    socket read deadline applied by ``setup()`` before any byte is trusted, so
    a connection that stalls while awaiting its declared body is bounded instead
    of holding a worker forever.
    """

    services: IssuerServices | None = None
    shutdown_event: threading.Event | None = None

    def do_POST(self) -> None:
        """Handle POST request with signed authorization."""
        if self.path != "/issue-agent/authorize":
            self._send_error(404, "Not Found")
            return

        # Read and validate request
        content_length = self.headers.get("Content-Length")
        if content_length is None:
            self._send_error(411, "Length Required")
            return
        try:
            length = int(content_length)
        except ValueError:
            self._send_error(400, "Invalid Content-Length")
            return
        if length > _MAX_REQUEST_BYTES:
            self._send_error(413, "Payload Too Large")
            return

        try:
            body = self.rfile.read(length)
        except TimeoutError:
            self._send_error(408, "Request body read timed out")
            return
        if len(body) != length:
            self._send_error(400, "Incomplete request body")
            return

        # Parse and validate the signed authorization (bytes only: the canonical
        # parser enforces bounded size, UTF-8, duplicate-key refusal and object
        # shape, so the transport duplicates none of that trust logic).
        try:
            signed = SignedIssueAgentAuthorization.from_json(body)
        except IssueAgentIssuerError as error:
            self._send_error(401, str(error))
            return
        except IssueAgentAuthorizationError as error:
            self._send_error(400, str(error))
            return

        # Execute through governed path
        assert self.services is not None
        try:
            receipt = execute_authorization(self.services, signed)
        except IssueAgentReplayError as error:
            self._send_error(409, str(error))
            return
        # IssuerError subclasses AuthorizationError, so it must be matched first:
        # a signature the trusted issuer did not mint is an origin failure (401),
        # not an authorization-policy failure (403).
        except IssueAgentIssuerError as error:
            self._send_error(401, str(error))
            return
        except IssueAgentAuthorizationError as error:
            self._send_error(403, str(error))
            return
        except SourceHandlingBlockedError as error:
            self._send_error(422, str(error))
            return
        except IssueAgentConfigurationError as error:
            self._send_error(500, str(error))
            return
        except IssueAgentExecutionError as error:
            self._send_error(500, str(error))
            return
        except SmartPromptMachineError as error:
            self._send_error(422, str(error))
            return
        except Exception as error:  # noqa: BLE001 - fail closed, never hang the transport
            self._send_error(500, f"unexpected execution failure: {type(error).__name__}")
            return

        # Success response
        self._send_json(200, json.loads(receipt.to_json()))

    def do_GET(self) -> None:
        """Health check endpoint."""
        if self.path == "/healthz":
            self._send_json(200, {"status": "ok", "service": "hunter-issue-agent-issuer"})
        else:
            self._send_error(404, "Not Found")

    def _send_json(self, status: int, payload: dict[str, Any]) -> None:
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        body = _canonical_json(payload).encode("utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_error(self, status: int, message: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        body = _canonical_json(
            {"error": message, "schema_version": ISSUE_AGENT_EXECUTION_RECEIPT_SCHEMA_VERSION}
        ).encode("utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: Any) -> None:
        logging.getLogger(__name__).info("%s - %s", self.address_string(), format % args)


class _BoundedThreadingHTTPServer(ThreadingMixIn, HTTPServer):
    """HTTPServer with a small, explicit concurrency bound.

    A request is admitted to a worker only while a slot is free; when every
    worker is busy the transport answers with a deterministic 503 at the
    socket instead of queueing behind an unbounded thread. Workers are daemon
    threads so shutdown is never blocked by a stalled peer, and every admitted
    request runs under the connection's finite read deadline, so a withheld
    body can hold a slot only until that deadline fires.
    """

    daemon_threads = True
    allow_reuse_address = True

    def __init__(
        self,
        server_address: tuple[str, int],
        RequestHandlerClass: type[BaseHTTPRequestHandler],
        *,
        max_workers: int,
    ) -> None:
        if max_workers < 1:
            raise ValueError("concurrent worker bound must be a positive integer")
        super().__init__(server_address, RequestHandlerClass)
        self._worker_slots = threading.Semaphore(max_workers)

    def process_request(self, request: Any, client_address: Any) -> None:
        if not self._worker_slots.acquire(blocking=False):
            self._reject_saturated(request)
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except BaseException:
            self._worker_slots.release()
            self.shutdown_request(request)
            raise

    def process_request_thread(self, request: Any, client_address: Any) -> None:
        try:
            super().process_request_thread(request, client_address)
        finally:
            self._worker_slots.release()

    def _reject_saturated(self, request: Any) -> None:
        """Fail closed on a bounded 503 without leaking internal detail."""
        try:
            request.settimeout(5.0)
            request.sendall(
                b"HTTP/1.1 503 Service Unavailable\r\n"
                b"Content-Type: application/json\r\n"
                b"Content-Length: 0\r\n"
                b"Connection: close\r\n\r\n"
            )
        except OSError:
            pass


class IssuerServer:
    """Bounded concurrent HTTP server for the trusted issuer edge.

    Requests run in workers drawn from a small explicit pool (a busy transport
    is told 503 at the socket, never queued behind an unbounded thread), and
    every connection carries a finite read deadline, so a client that opens a
    connection and withholds its body terminates within a bounded time instead
    of occupying a worker indefinitely. Shutdown stays deterministic: the
    accept loop is stopped and any in-flight worker is a daemon.
    """

    def __init__(
        self,
        host: str,
        port: int,
        services: IssuerServices,
        *,
        shutdown_event: threading.Event | None = None,
        read_timeout: float = _REQUEST_READ_TIMEOUT_SECONDS,
        max_workers: int = _MAX_CONCURRENT_REQUEST_WORKERS,
    ) -> None:
        self._host = host
        self._port = port
        self._shutdown_event = shutdown_event or threading.Event()

        class Handler(_IssuerRequestHandler):
            shutdown_event = self._shutdown_event
            timeout = read_timeout

        Handler.services = services
        self._server = _BoundedThreadingHTTPServer(
            (host, port),
            Handler,
            max_workers=max_workers,
        )
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        """Start the server in a background thread."""
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        logging.getLogger(__name__).info("Trusted issuer edge listening on %s:%d", self._host, self._port)

    def shutdown(self, timeout: float = 30.0) -> None:
        """Shutdown the server gracefully."""
        self._shutdown_event.set()
        self._server.shutdown()
        self._server.server_close()
        if self._thread:
            self._thread.join(timeout=timeout)
        logging.getLogger(__name__).info("Trusted issuer edge stopped")


def _setup_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S%z",
    )


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
    _setup_logging(arguments.verbose)
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
