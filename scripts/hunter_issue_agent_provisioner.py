#!/usr/bin/env python3
"""Trusted repository-owned provisioning edge for the governed Issue-agent path.

Issue #497: eliminate the hidden manual per-Issue Source Handling authority
provisioning step. The GitHub Actions trigger can read the Issue and mint the
signed authorization, but it cannot reach the Railway ``/data`` volume, so it
cannot provision authority records itself. This process is the designated
trusted minting boundary: it runs on Railway beside the issuer, shares the same
persistent evidence database, and keeps the Source Handling signing key for its
whole lifetime so that every dispatch provisions the canonical per-Issue
``FACT``, ``FIELD_CATEGORY_REGISTRY`` and ``POLICY`` records automatically.

The boundary is deliberately separate from the execution issuer:

-   It verifies the canonical signed authorization exactly as the issuer does
    (same public issuer key, same canonical parser, same fail-closed discipline)
    before it will provision anything.
-   It derives document identity only through the existing canonical functions
    (``issue_agent_document_id``), never from Issue body/title/caller selection.
-   It provisions only repository-owned contracts: fact and policy options come
    from ``provision_source_handling_issue_authority._REPOSITORY_DEFAULTS``.
-   It delegates every record to the existing canonical operator machinery
    (``provision_source_handling_issue_authority._run``), which is idempotent,
    preserves the operator root / provenance / signature / canonical-head rules,
    and fails closed on any mismatch.
-   An issuer dispatch can never outrun provisioning: the trigger orders
    provisioning before the webhook POST, so a failed or missing provisioning
    boundary blocks issuer dispatch entirely.

The issuer process keeps its existing read-only relationship to Source Handling
authority and still never holds ``HUNTER_SOURCE_HANDLING_SIGNING_KEY``; only
this process and the operator bootstrap may hold it.
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

import bootstrap_source_handling_authority as bootstrap
import provision_source_handling_issue_authority as provisioning

from hunter.automation.issue_agent_execution import (
    EVIDENCE_DATABASE_ENV,
    ISSUE_AGENT_VERIFYING_KEY_ENV,
    OWNER_LOGIN_ENV,
    REPOSITORY_ENV,
    IssueAgentAuthorizationError,
    IssueAgentAuthorizationVerifier,
    IssueAgentConfigurationError,
    IssueAgentIssuerError,
    SignedIssueAgentAuthorization,
    issue_agent_document_id,
)
from hunter.evidence_intelligence.source_handling import AUTHORITY_COMPONENT_ID
from hunter.evidence_intelligence.source_handling_persistence import SourceHandlingBlockedError

#: Maximum request body size in bytes (256 KiB), matching the trigger envelope cap.
_MAX_REQUEST_BYTES: Final[int] = 256 * 1024

#: Small explicit bound on concurrent request workers (modeled on the issuer edge).
_MAX_CONCURRENT_REQUEST_WORKERS: Final[int] = 8

#: Finite socket read deadline applied before the body is read.
_REQUEST_READ_TIMEOUT_SECONDS: Final[float] = 15.0

PROVISION_RESPONSE_SCHEMA_VERSION = "hunter-issue-agent-provision-response-v1"
_SOURCE_HANDLING_SIGNING_KEY_ENV = bootstrap.SIGNING_KEY_ENV


def _repository_default_options() -> tuple[provisioning._FactOptions, provisioning._PolicyOptions]:
    """The repository-owned Fact/Policy options powering every provisioning run.

    Only canonical contracts from ``_REPOSITORY_DEFAULTS`` (and the CLI's empty
    restriction/presence defaults with ``ALLOW`` dispositions) are ever used.
    Nothing in the authorization document -- body, title, or caller selection --
    may influence a derived record's classification.
    """
    defaults = provisioning._REPOSITORY_DEFAULTS
    fact_options = provisioning._FactOptions(
        sensitivity=defaults["sensitivity"],
        operation_restrictions=(),
        persistence_restriction=defaults["persistence_restriction"],
        secret_presence=(),
    )
    policy_options = provisioning._PolicyOptions(
        processing_decision=defaults["processing_decision"],
        retention_decision=defaults["retention_decision"],
        reconstruction_decision=defaults["reconstruction_decision"],
        access_decision=defaults["access_decision"],
        deletion_lifecycle_decision=defaults["deletion_lifecycle_decision"],
        persist_disposition="ALLOW",
        read_access_disposition="ALLOW",
        reconstruct_disposition="ALLOW",
        delete_or_expire_disposition="ALLOW",
    )
    return fact_options, policy_options


@dataclass(frozen=True, slots=True)
class ProvisionerConfiguration:
    """Operational configuration captured once at trusted startup.

    The Source Handling signing key is held on this process for its whole
    lifetime: this boundary exists specifically to mint per-Issue authority
    records, so (unlike the issuer) it never scrubs that key.
    """

    repository: str
    owner_login: str
    evidence_database: Path
    signing_key: bytes
    production_rule: Mapping[str, Any]
    issuer_verifier: IssueAgentAuthorizationVerifier

    @classmethod
    def from_environment(
        cls,
        *,
        environ: Mapping[str, str] | None = None,
        signing_key_file: str | None = None,
    ) -> ProvisionerConfiguration:
        source = os.environ if environ is None else environ

        required = (REPOSITORY_ENV, OWNER_LOGIN_ENV, EVIDENCE_DATABASE_ENV, ISSUE_AGENT_VERIFYING_KEY_ENV)
        missing = [var for var in required if not source.get(var, "").strip()]
        if missing:
            raise IssueAgentConfigurationError(
                f"missing required operational configuration: {', '.join(sorted(missing))}"
            )

        signing_key = bootstrap._load_signing_key(environ=source, signing_key_file=signing_key_file)
        rule = bootstrap._load_production_rule()
        database = Path(source.get(EVIDENCE_DATABASE_ENV, "").strip())
        provisioning._validate_operator_configuration(source, str(database), signing_key, rule)
        issuer_verifier = IssueAgentAuthorizationVerifier.from_environment(environ=source)

        return cls(
            repository=source.get(REPOSITORY_ENV, "").strip(),
            owner_login=source.get(OWNER_LOGIN_ENV, "").strip(),
            evidence_database=database,
            signing_key=signing_key,
            production_rule=rule,
            issuer_verifier=issuer_verifier,
        )


def provision_issue_authority(
    configuration: ProvisionerConfiguration,
    signed: SignedIssueAgentAuthorization,
) -> dict[str, Any]:
    """Verify the signed authorization and provision its canonical authority.

    Verification of the canonical authorization happens here, before any write,
    exactly as the issuer performs it: a document the trusted issuer did not
    mint is refused before a single record can be derived. Then only the
    repository-owned record families derived by the canonical operator machinery
    are provisioned, bound to the exact document identity the issuer runtime
    will re-derive from the same claims.
    """
    configuration.issuer_verifier.verify(signed)
    authorization = signed.authorization

    if authorization.repository != configuration.repository:
        raise IssueAgentAuthorizationError("authorization names a different repository than this deployment")
    if authorization.authorized_by != configuration.owner_login:
        raise IssueAgentAuthorizationError("only the configured repository owner may authorize execution")

    document_id = issue_agent_document_id(authorization)
    fact_options, policy_options = _repository_default_options()
    outcome = provisioning._run(
        database=str(configuration.evidence_database),
        signing_key=configuration.signing_key,
        rule=configuration.production_rule,
        authorization=authorization,
        fact_options=fact_options,
        policy_options=policy_options,
        provenance_authority_identity=AUTHORITY_COMPONENT_ID,
        as_of=None,
    )
    if outcome["document_id"] != document_id:
        raise SourceHandlingBlockedError("provisioned authority records do not bind the derived document identity")
    return outcome


def _canonical_json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


class _ProvisionerRequestHandler(BaseHTTPRequestHandler):
    """HTTP handler for the trusted provisioning edge."""

    configuration: ProvisionerConfiguration | None = None

    def do_POST(self) -> None:
        """Handle a provisioning request carrying one signed authorization."""
        if self.path != "/issue-agent/provision":
            self._send_error(404, "Not Found")
            return

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

        try:
            signed = SignedIssueAgentAuthorization.from_json(body)
        except IssueAgentIssuerError as error:
            self._send_error(401, str(error))
            return
        except IssueAgentAuthorizationError as error:
            self._send_error(400, str(error))
            return

        assert self.configuration is not None
        try:
            outcome = provision_issue_authority(self.configuration, signed)
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
        except Exception as error:  # noqa: BLE001
            self._send_error(
                500,
                f"unexpected provisioning failure: {type(error).__name__}",
            )
            return

        self._send_json(
            200,
            {
                "authorization_id": signed.authorization.authorization_id,
                "document_id": outcome["document_id"],
                "status": outcome["status"],
                "as_of": outcome["as_of"],
                "records": outcome["records"],
                "schema_version": PROVISION_RESPONSE_SCHEMA_VERSION,
            },
        )

    def do_GET(self) -> None:
        """Health check endpoint."""
        if self.path == "/healthz":
            self._send_json(200, {"status": "ok", "service": "hunter-issue-agent-provisioner"})
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
        body = _canonical_json({"error": message, "schema_version": PROVISION_RESPONSE_SCHEMA_VERSION}).encode("utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: Any) -> None:
        logging.getLogger(__name__).info("%s - %s", self.address_string(), format % args)


class _BoundedThreadingHTTPServer(ThreadingMixIn, HTTPServer):
    """HTTPServer with a small, explicit concurrency bound (modeled on the issuer)."""

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


class ProvisionerServer:
    """Bounded concurrent HTTP server for the trusted provisioning edge."""

    def __init__(
        self,
        host: str,
        port: int,
        configuration: ProvisionerConfiguration,
        *,
        read_timeout: float = _REQUEST_READ_TIMEOUT_SECONDS,
        max_workers: int = _MAX_CONCURRENT_REQUEST_WORKERS,
    ) -> None:
        self._host = host
        self._port = port
        self._configuration = configuration
        self._shutdown_complete = False

        class Handler(_ProvisionerRequestHandler):
            timeout = read_timeout

        Handler.configuration = configuration
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
        logging.getLogger(__name__).info("trusted provisioning edge listening on %s:%d", self._host, self._port)

    def shutdown(self, timeout: float = 30.0) -> None:
        """Shutdown the server gracefully (idempotent)."""
        if self._shutdown_complete:
            return
        self._shutdown_complete = True
        logging.getLogger(__name__).info("stopping trusted provisioning edge")
        self._server.shutdown()
        self._server.server_close()
        if self._thread is not None:
            self._thread.join(timeout=timeout)
        logging.getLogger(__name__).info("trusted provisioning edge stopped")


def _setup_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S%z",
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="hunter_issue_agent_provisioner")
    parser.add_argument("--host", default="0.0.0.0", help="bind address")
    parser.add_argument("--port", type=int, default=8081, help="bind port")
    parser.add_argument("--signing-key-file", default=None, help="path to the Source Handling signing key file")
    parser.add_argument("--verbose", action="store_true", help="enable debug logging")
    return parser


def main(argv: list[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    _setup_logging(arguments.verbose)
    logger = logging.getLogger(__name__)

    try:
        configuration = ProvisionerConfiguration.from_environment(
            signing_key_file=arguments.signing_key_file,
        )
    except (IssueAgentConfigurationError, ValueError, OSError, SourceHandlingBlockedError) as error:
        logger.error("configuration error: %s", error)
        return 2

    server = ProvisionerServer(arguments.host, arguments.port, configuration)

    def _signal_handler(signum: int, frame: Any) -> None:
        logger.info("received signal %d, shutting down", signum)
        server.shutdown()

    signal.signal(signal.SIGTERM, _signal_handler)
    signal.signal(signal.SIGINT, _signal_handler)

    try:
        server.start()
        signal.pause()
    except KeyboardInterrupt:
        pass
    finally:
        server.shutdown()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
