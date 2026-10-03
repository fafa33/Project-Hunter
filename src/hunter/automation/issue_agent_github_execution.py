"""Issue #557 production cutover: GitHub-hosted execution behind the Railway authority.

PR #522 retired Railway model execution and PR #524 added the credential-isolated
replacement result contract and publisher, but nothing connected the production
``issues:labeled`` trigger to them. This module is that connection on the
Railway side and nothing else. It owns no prompt, route, profile, ledger,
TaskScope or publication authority:

- the exact handoff it serves is the one ``GovernedEngineeringTaskIngress`` ->
  ``SmartPromptMachine`` (which consumes ``EngineeringContextAuthority``/DPM)
  already compiled, signed and durably recorded by the existing issuer path
  before the HTTP ACK; nothing reaches an executor unless that happened;
- it replaces only the retired fallback runtime seam
  (``IssueAgentFallbackRuntime.dispatch``): instead of running a provider on
  Railway, it waits for the GitHub-hosted executor's hostile closed-schema
  result and validates it with the #524 ``validation_receipt`` primitive;
- the GitHub workflow authenticates with GitHub Actions OIDC. Every request is
  bound to this repository, the trusted default-branch trigger workflow, an
  owner-triggered ``issues`` run on a GitHub-hosted runner, and to the single
  workflow run (id + attempt) that first claimed the authorization. Every role
  operation is single-use, so replay, retry and cross-run reuse fail closed;
- the sensitive material (exact prompt, hostile result) travels only in the
  authenticated response bodies; nothing here is ever logged.

State is held in this issuer instance only, exactly like the execution lease
it lives under: an issuer restart lapses the lease, the ledger fails the row
closed (``PROCESS_RESTART``), and the authorization is never replayed.
"""

from __future__ import annotations

import base64
import binascii
import json
import re
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPublicKey, RSAPublicNumbers

from hunter.automation.agent_fallback_runtime import AgentFallbackRuntimeReceipt
from hunter.automation.issue_agent_execution import (
    IssueAgentExecutionTarget,
    IssueAgentRemoteExecutionError,
    IssueAgentRuntimeReceiptError,
    SignedIssueAgentAuthorization,
    derive_execution_target,
)
from hunter.automation.issue_agent_replacement_executor import (
    ReplacementExecutorError,
    ReplacementResultLedger,
    ReplacementValidationReceipt,
    validation_receipt,
)
from hunter.automation.n8n_handoff import PromptAutomationEnvelopeHandoff, PromptAutomationHandoffError
from hunter.evidence_intelligence.pre_model_persistence import EvidencePreModelPersistenceRepository
from hunter.evidence_intelligence.repository import EvidenceIntelligenceRepository
from hunter.evidence_intelligence.smart_prompt_routing import PromptAutomationVerifier

GITHUB_OIDC_ISSUER = "https://token.actions.githubusercontent.com"
GITHUB_OIDC_JWKS_URL = f"{GITHUB_OIDC_ISSUER}/.well-known/jwks"
TRIGGER_WORKFLOW_PATH = ".github/workflows/hunter-issue-agent-trigger.yml"
TRUSTED_REF = "refs/heads/main"

EXECUTION_FETCH_PATH = "/issue-agent/execution/fetch"
EXECUTION_RESULT_PATH = "/issue-agent/execution/result"
EXECUTION_REQUEST_SCHEMA_VERSION = "hunter-issue-agent-execution-request-v1"
EXECUTION_HANDOFF_SCHEMA_VERSION = "hunter-issue-agent-execution-handoff-v1"
EXECUTION_CANDIDATE_SCHEMA_VERSION = "hunter-issue-agent-execution-candidate-v1"
EXECUTION_RESULT_ACK_SCHEMA_VERSION = "hunter-issue-agent-execution-result-accepted-v1"

#: The validation-definition identity bound into every validation receipt.
VALIDATION_DEFINITION = "hunter-issue-agent-replacement-validation:github-hosted-v1"
EXECUTOR_PROVIDER = "github-hosted-executor"

ROLE_EXECUTOR = "executor"
ROLE_VALIDATOR = "validator"
ROLE_PUBLISHER = "publisher"
EXECUTION_ROLES = (ROLE_EXECUTOR, ROLE_VALIDATOR, ROLE_PUBLISHER)

DEFAULT_RESULT_TIMEOUT_SECONDS = 90 * 60.0
DEFAULT_CANDIDATE_RETENTION_SECONDS = 2 * 60 * 60.0
MAX_OIDC_TOKEN_BYTES = 8 * 1024
MAX_JWKS_BYTES = 64 * 1024
_JWKS_REFRESH_SECONDS = 60.0
_LEEWAY_SECONDS = 60
_AUTHORIZATION_ID_RE = re.compile(r"hunter-issue-agent-authorization:[0-9a-f]{64}")
_DIGITS_RE = re.compile(r"[0-9]{1,20}")
_B64URL_RE = re.compile(r"[A-Za-z0-9_-]+")


def oidc_audience(repository: str) -> str:
    """The one OIDC audience the issuer accepts for this repository."""
    return f"hunter-issue-agent-execution:{repository}"


class GitHubExecutionError(RuntimeError):
    """A refused execution-channel request; ``status`` is the HTTP answer."""

    status = 400


class ExecutionIdentityError(GitHubExecutionError):
    status = 401


class ExecutionBindingError(GitHubExecutionError):
    status = 403


class ExecutionNotFoundError(GitHubExecutionError):
    status = 404


class ExecutionReplayError(GitHubExecutionError):
    status = 409


class ExecutionResultRejectedError(GitHubExecutionError):
    status = 422


@dataclass(frozen=True, slots=True)
class GitHubOidcClaims:
    """The verified identity of one GitHub Actions job request."""

    repository: str
    run_id: str
    run_attempt: str
    actor: str

    @property
    def run(self) -> tuple[str, str]:
        return (self.run_id, self.run_attempt)


JwksFetcher = Callable[[], Mapping[str, Any]]


class _RejectRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[no-untyped-def]
        raise ExecutionIdentityError("GitHub OIDC key discovery redirect refused")


def fetch_github_jwks() -> Mapping[str, Any]:
    """Fetch GitHub's OIDC signing keys from the fixed issuer endpoint."""
    opener = urllib.request.build_opener(_RejectRedirects)
    try:
        with opener.open(GITHUB_OIDC_JWKS_URL, timeout=10) as response:
            raw = response.read(MAX_JWKS_BYTES + 1)
    except (urllib.error.URLError, OSError, TimeoutError):
        raise ExecutionIdentityError("GitHub OIDC signing keys are unavailable") from None
    if len(raw) > MAX_JWKS_BYTES:
        raise ExecutionIdentityError("GitHub OIDC signing key document is too large")
    try:
        decoded = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        raise ExecutionIdentityError("GitHub OIDC signing key document is malformed") from None
    if not isinstance(decoded, Mapping):
        raise ExecutionIdentityError("GitHub OIDC signing key document is malformed")
    return decoded


def _b64url_decode(value: str) -> bytes:
    if not value or _B64URL_RE.fullmatch(value) is None:
        raise ExecutionIdentityError("OIDC token segment is not base64url")
    try:
        return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
    except (binascii.Error, ValueError):
        raise ExecutionIdentityError("OIDC token segment is not base64url") from None


def _json_segment(value: str) -> dict[str, Any]:
    try:
        decoded = json.loads(_b64url_decode(value).decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        raise ExecutionIdentityError("OIDC token segment is not JSON") from None
    if not isinstance(decoded, dict):
        raise ExecutionIdentityError("OIDC token segment is not a JSON object")
    return decoded


def _rsa_key(jwk: Mapping[str, Any]) -> RSAPublicKey:
    if jwk.get("kty") != "RSA" or jwk.get("alg", "RS256") != "RS256" or jwk.get("use", "sig") != "sig":
        raise ExecutionIdentityError("OIDC signing key is not an RS256 signature key")
    try:
        n = int.from_bytes(_b64url_decode(str(jwk["n"])), "big")
        e = int.from_bytes(_b64url_decode(str(jwk["e"])), "big")
    except KeyError:
        raise ExecutionIdentityError("OIDC signing key is incomplete") from None
    return RSAPublicNumbers(e, n).public_key()


def _audiences(value: object) -> tuple[str, ...]:
    """RFC 7519 ``aud``: one string, or a non-empty array of only strings."""
    if isinstance(value, str) and value:
        return (value,)
    if isinstance(value, list) and value and all(isinstance(item, str) and item for item in value):
        return tuple(value)
    raise ExecutionIdentityError("OIDC token audience claim is malformed")


class GitHubActionsOidcVerifier:
    """Verify a GitHub Actions OIDC token as the trusted Issue trigger workflow.

    The token must be RS256-signed by GitHub's issuer key, carry this
    repository's audience, be time-valid, and name: this repository and owner,
    the trusted trigger workflow on the default branch (``workflow_ref``; a
    present ``job_workflow_ref`` must name that same workflow, so a reusable
    workflow is refused), an ``issues`` event triggered by the owner, and a
    GitHub-hosted runner. Anything else fails closed before any state is read.
    """

    def __init__(
        self,
        *,
        repository: str,
        owner_login: str,
        jwks_fetcher: JwksFetcher = fetch_github_jwks,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._repository = repository
        self._owner = owner_login
        self._fetch = jwks_fetcher
        self._monotonic = monotonic
        self._keys: dict[str, RSAPublicKey] = {}
        self._refreshed_at: float | None = None
        self._lock = threading.Lock()
        self._workflow_ref = f"{repository}/{TRIGGER_WORKFLOW_PATH}@{TRUSTED_REF}"

    def _key(self, kid: str) -> RSAPublicKey:
        with self._lock:
            key = self._keys.get(kid)
            now = self._monotonic()
            if key is None and (self._refreshed_at is None or now - self._refreshed_at >= _JWKS_REFRESH_SECONDS):
                self._refreshed_at = now
                document = self._fetch()
                keys = document.get("keys")
                if not isinstance(keys, list):
                    raise ExecutionIdentityError("GitHub OIDC signing key document is malformed")
                refreshed: dict[str, RSAPublicKey] = {}
                for entry in keys:
                    if isinstance(entry, Mapping) and isinstance(entry.get("kid"), str):
                        try:
                            refreshed[entry["kid"]] = _rsa_key(entry)
                        except (ExecutionIdentityError, ValueError):
                            continue
                self._keys = refreshed
                key = refreshed.get(kid)
        if key is None:
            raise ExecutionIdentityError("OIDC token signing key is unknown")
        return key

    def verify(self, token: object, *, now: datetime) -> GitHubOidcClaims:
        if not isinstance(token, str) or not token or len(token.encode("utf-8")) > MAX_OIDC_TOKEN_BYTES:
            raise ExecutionIdentityError("OIDC token is missing or oversized")
        parts = token.split(".")
        if len(parts) != 3:
            raise ExecutionIdentityError("OIDC token is not a compact JWS")
        header = _json_segment(parts[0])
        if header.get("alg") != "RS256" or not isinstance(header.get("kid"), str) or "crit" in header:
            raise ExecutionIdentityError("OIDC token header is not an RS256 GitHub token")
        signature = _b64url_decode(parts[2])
        try:
            self._key(header["kid"]).verify(
                signature, f"{parts[0]}.{parts[1]}".encode("ascii"), padding.PKCS1v15(), hashes.SHA256()
            )
        except InvalidSignature:
            raise ExecutionIdentityError("OIDC token signature is invalid") from None
        claims = _json_segment(parts[1])
        return self._check_claims(claims, now=now)

    def _check_claims(self, claims: Mapping[str, Any], *, now: datetime) -> GitHubOidcClaims:
        if claims.get("iss") != GITHUB_OIDC_ISSUER:
            raise ExecutionIdentityError("OIDC token issuer is not GitHub Actions")
        if oidc_audience(self._repository) not in _audiences(claims.get("aud")):
            raise ExecutionIdentityError("OIDC token audience is not this execution boundary")
        moment = int(now.timestamp())
        for name in ("exp", "iat"):
            if type(claims.get(name)) is not int:
                raise ExecutionIdentityError(f"OIDC token {name} is missing")
        # A present nbf must be well formed; a malformed one is never skipped.
        if "nbf" in claims and type(claims["nbf"]) is not int:
            raise ExecutionIdentityError("OIDC token nbf is malformed")
        if claims["exp"] <= moment - _LEEWAY_SECONDS:
            raise ExecutionIdentityError("OIDC token has expired")
        if claims["iat"] > moment + _LEEWAY_SECONDS or claims.get("nbf", moment) > moment + _LEEWAY_SECONDS:
            raise ExecutionIdentityError("OIDC token is not yet valid")
        expected = {
            "repository": self._repository,
            "repository_owner": self._owner,
            "actor": self._owner,
            "ref": TRUSTED_REF,
            "ref_type": "branch",
            "event_name": "issues",
            "workflow_ref": self._workflow_ref,
            "runner_environment": "github-hosted",
        }
        for name, value in expected.items():
            if claims.get(name) != value:
                raise ExecutionBindingError(f"OIDC token {name} is not the trusted execution identity")
        # GitHub documents job_workflow_ref only for jobs running a reusable
        # workflow; the trigger's jobs run directly, so workflow_ref above is the
        # applicable binding. When the claim is present it must name the same
        # trusted workflow, so a job inside any reusable workflow still fails closed.
        if "job_workflow_ref" in claims and claims["job_workflow_ref"] != self._workflow_ref:
            raise ExecutionBindingError("OIDC token job_workflow_ref is not the trusted execution identity")
        run_id, run_attempt = claims.get("run_id"), claims.get("run_attempt")
        if not isinstance(run_id, str) or not isinstance(run_attempt, str):
            raise ExecutionBindingError("OIDC token does not name an exact workflow run")
        if _DIGITS_RE.fullmatch(run_id) is None or _DIGITS_RE.fullmatch(run_attempt) is None:
            raise ExecutionBindingError("OIDC token does not name an exact workflow run")
        return GitHubOidcClaims(self._repository, run_id, run_attempt, self._owner)


class _DuplicateKeyError(ValueError):
    pass


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    values: dict[str, Any] = {}
    for key, value in pairs:
        if key in values:
            raise _DuplicateKeyError(key)
        values[key] = value
    return values


def _parse_request(body: bytes, *, expected: set[str]) -> dict[str, Any]:
    try:
        decoded = json.loads(body.decode("utf-8"), object_pairs_hook=_reject_duplicate_keys)
    except (UnicodeDecodeError, ValueError):
        raise GitHubExecutionError("execution request must be canonical UTF-8 JSON") from None
    if not isinstance(decoded, dict) or set(decoded) != expected:
        raise GitHubExecutionError("execution request schema mismatch")
    if decoded["schema_version"] != EXECUTION_REQUEST_SCHEMA_VERSION:
        raise GitHubExecutionError("unknown execution request schema version")
    authorization_id = decoded["authorization_id"]
    if not isinstance(authorization_id, str) or _AUTHORIZATION_ID_RE.fullmatch(authorization_id) is None:
        raise GitHubExecutionError("execution request names no canonical authorization identity")
    return decoded


@dataclass(slots=True)
class _Execution:
    signed: SignedIssueAgentAuthorization
    handoff_document: str
    target: IssueAgentExecutionTarget
    expires_at: float
    run: tuple[str, str] | None = None
    used_roles: set[str] = field(default_factory=set)
    #: AUTHORIZED -> HANDOFF_IN_FLIGHT (executor role used) -> HANDOFF_DELIVERED
    #: (prompt resolved) -> RESULT_ALLOWED. Only a delivered handoff admits a result.
    handoff_delivered: bool = False
    result_document: str | None = None
    receipt: ReplacementValidationReceipt | None = None
    failure: BaseException | None = None
    done: threading.Event = field(default_factory=threading.Event)


PromptResolver = Callable[[str], str]


def evidence_prompt_resolver(
    evidence_database: Path,
    *,
    prompt_verifier: PromptAutomationVerifier,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> PromptResolver:
    """Resolve the exact prompt for a recorded handoff from the canonical build.

    The handoff's issuer signature is re-verified first, so only a handoff the
    Smart Prompt Machine itself signed can release a prompt.
    """

    def resolve(handoff_document: str) -> str:
        handoff = PromptAutomationEnvelopeHandoff.from_json(handoff_document)
        handoff.to_envelope().verify_issuer_signature(prompt_verifier)
        repository = EvidenceIntelligenceRepository(evidence_database)
        reconstruction = EvidencePreModelPersistenceRepository(repository).strict_known_reconstruction(
            handoff.build_record_id, clock()
        )
        prompt = reconstruction.exact_prompt
        if not prompt:
            raise PromptAutomationHandoffError(
                f"exact prompt reconstruction unavailable for the signed build ({reconstruction.reason_code})"
            )
        return prompt

    return resolve


class GitHubHostedExecutionRuntime:
    """The production ``IssueAgentFallbackRuntime``: execution happens on GitHub.

    ``admit`` is called by the issuer after the existing path has claimed the
    authorization, compiled it through SPM/DPM and recorded the exact handoff,
    and before the HTTP ACK. ``dispatch`` (the existing worker seam, under the
    existing lease renewal) then waits for one admissible result.
    """

    def __init__(
        self,
        *,
        repository: str,
        owner_login: str,
        evidence_database: Path,
        oidc_verifier: GitHubActionsOidcVerifier,
        prompt_resolver: PromptResolver,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        monotonic: Callable[[], float] = time.monotonic,
        result_timeout_seconds: float = DEFAULT_RESULT_TIMEOUT_SECONDS,
        retention_seconds: float = DEFAULT_CANDIDATE_RETENTION_SECONDS,
    ) -> None:
        self._repository = repository
        self._owner = owner_login
        self._results = ReplacementResultLedger(evidence_database)
        self._oidc = oidc_verifier
        self._resolve_prompt = prompt_resolver
        self._clock = clock
        self._monotonic = monotonic
        self._result_timeout = result_timeout_seconds
        self._retention = retention_seconds
        self._lock = threading.Lock()
        self._executions: dict[str, _Execution] = {}

    # -- issuer-side seam ---------------------------------------------------

    def admit(self, signed: SignedIssueAgentAuthorization, handoff_document: str) -> None:
        """Make one durably prepared authorization fetchable by its executor."""
        target = derive_execution_target(signed)
        if target.repository != self._repository:
            raise IssueAgentRuntimeReceiptError("admitted authorization names a different repository")
        PromptAutomationEnvelopeHandoff.from_json(handoff_document)
        with self._lock:
            self._purge()
            if target.authorization_id in self._executions:
                raise IssueAgentRuntimeReceiptError("authorization is already admitted for execution")
            self._executions[target.authorization_id] = _Execution(
                signed=signed,
                handoff_document=handoff_document,
                target=target,
                expires_at=self._monotonic() + self._result_timeout + self._retention,
            )

    def dispatch(self, document: str | bytes, target: IssueAgentExecutionTarget) -> AgentFallbackRuntimeReceipt:
        handoff = document.decode("utf-8") if isinstance(document, bytes) else document
        with self._lock:
            execution = self._executions.get(target.authorization_id)
        if execution is None or execution.target != target or execution.handoff_document != handoff:
            raise IssueAgentRuntimeReceiptError("dispatched handoff is not the admitted execution")
        if not execution.done.wait(self._result_timeout):
            with self._lock:
                if execution.receipt is None and execution.failure is None:
                    execution.failure = IssueAgentRemoteExecutionError(
                        "EXECUTOR_RESULT_TIMEOUT", "no admissible executor result arrived in time"
                    )
                    execution.done.set()
        if execution.failure is not None:
            raise execution.failure
        assert execution.receipt is not None
        return AgentFallbackRuntimeReceipt(
            provider=EXECUTOR_PROVIDER,
            head_before=target.base_sha,
            head_after="",
            attempts=({"provider": EXECUTOR_PROVIDER, "result_sha256": execution.receipt.result_sha256},),
            validation_succeeded=True,
        )

    # -- OIDC-authenticated channel ----------------------------------------

    def _purge(self) -> None:
        now = self._monotonic()
        for key in [k for k, v in self._executions.items() if v.expires_at < now]:
            execution = self._executions.pop(key)
            if execution.failure is None and execution.receipt is None:
                execution.failure = IssueAgentRemoteExecutionError(
                    "EXECUTOR_RESULT_TIMEOUT", "execution identity expired before a result"
                )
                execution.done.set()

    def _bound(self, request: Mapping[str, Any], role: str) -> _Execution:
        """Verify the caller and bind/compare its exact workflow run. Caller holds the lock."""
        claims = self._oidc.verify(request["oidc_token"], now=self._clock())
        self._purge()
        execution = self._executions.get(request["authorization_id"])
        if execution is None:
            raise ExecutionNotFoundError("no admitted execution for this authorization identity")
        if execution.failure is not None:
            raise ExecutionReplayError("execution identity is already terminal")
        if execution.run is None:
            if role != ROLE_EXECUTOR:
                raise ExecutionBindingError("execution identity is not yet claimed by an executor run")
            execution.run = claims.run
        elif execution.run != claims.run:
            raise ExecutionBindingError("execution identity is bound to a different workflow run")
        return execution

    def handle_fetch(self, body: bytes) -> dict[str, Any]:
        request = _parse_request(body, expected={"schema_version", "authorization_id", "role", "oidc_token"})
        role = request["role"]
        if role not in EXECUTION_ROLES:
            raise GitHubExecutionError("unknown execution role")
        with self._lock:
            execution = self._bound(request, role)
            if role in execution.used_roles:
                raise ExecutionReplayError(f"the {role} fetch for this execution was already used")
            if role == ROLE_EXECUTOR:
                execution.used_roles.add(role)
            elif execution.receipt is None or execution.result_document is None:
                raise ExecutionNotFoundError("no validated result exists for this execution yet")
            else:
                execution.used_roles.add(role)
                return {
                    "schema_version": EXECUTION_CANDIDATE_SCHEMA_VERSION,
                    "authorization_id": execution.target.authorization_id,
                    "signed_authorization": execution.signed.to_json(),
                    "result_document": execution.result_document,
                    "validation_receipt": execution.receipt.to_json(),
                    "validation_definition": VALIDATION_DEFINITION,
                }
        # HANDOFF_IN_FLIGHT: the executor role is consumed, but no result is
        # admissible until the prompt is resolved and the handoff is delivered.
        try:
            prompt = self._resolve_prompt(execution.handoff_document)
        except Exception as error:  # noqa: BLE001 - any unresolvable handoff is terminal
            self._fail(execution, error)
            raise ExecutionResultRejectedError("the signed handoff cannot release an exact prompt") from None
        with self._lock:
            if execution.failure is not None:
                raise ExecutionReplayError("execution identity became terminal before handoff delivery")
            execution.handoff_delivered = True
        scope = execution.signed.implementation_scope
        target = execution.target
        return {
            "schema_version": EXECUTION_HANDOFF_SCHEMA_VERSION,
            "authorization_id": target.authorization_id,
            "repository": target.repository,
            "issue_number": target.issue_number,
            "branch": target.branch,
            "base_sha": target.base_sha,
            "allowed_paths": list(scope.allowed_paths),
            "prohibited_paths": list(scope.prohibited_paths),
            "handoff_document": execution.handoff_document,
            "exact_prompt": prompt,
        }

    def handle_result(self, body: bytes) -> dict[str, Any]:
        request = _parse_request(
            body, expected={"schema_version", "authorization_id", "role", "oidc_token", "result_document"}
        )
        if request["role"] != ROLE_EXECUTOR:
            raise ExecutionBindingError("only the executor returns a result")
        document = request["result_document"]
        if not isinstance(document, str):
            raise GitHubExecutionError("result_document must be the exact result JSON text")
        with self._lock:
            execution = self._bound(request, ROLE_EXECUTOR)
            if not execution.handoff_delivered:
                raise ExecutionBindingError("a result requires the executor's successfully delivered handoff first")
            if execution.receipt is not None:
                raise ExecutionReplayError("this execution already returned its result")
            try:
                receipt = validation_receipt(
                    document, signed_authorization=execution.signed, validation_definition=VALIDATION_DEFINITION
                )
                self._results.record_validated(receipt)
            except ReplacementExecutorError as error:
                self._fail_locked(execution, IssueAgentRemoteExecutionError("EXECUTOR_RESULT_REJECTED", str(error)))
                raise ExecutionResultRejectedError(f"hostile result rejected: {error}") from None
            execution.result_document = document
            execution.receipt = receipt
            execution.done.set()
        return {
            "schema_version": EXECUTION_RESULT_ACK_SCHEMA_VERSION,
            "authorization_id": receipt.authorization_id,
            "result_sha256": receipt.result_sha256,
        }

    def _fail(self, execution: _Execution, error: BaseException) -> None:
        with self._lock:
            self._fail_locked(execution, error)

    @staticmethod
    def _fail_locked(execution: _Execution, error: BaseException) -> None:
        if execution.failure is None and execution.receipt is None:
            execution.failure = error
            execution.done.set()


__all__ = [
    "DEFAULT_RESULT_TIMEOUT_SECONDS",
    "EXECUTION_CANDIDATE_SCHEMA_VERSION",
    "EXECUTION_FETCH_PATH",
    "EXECUTION_HANDOFF_SCHEMA_VERSION",
    "EXECUTION_REQUEST_SCHEMA_VERSION",
    "EXECUTION_RESULT_ACK_SCHEMA_VERSION",
    "EXECUTION_RESULT_PATH",
    "EXECUTION_ROLES",
    "EXECUTOR_PROVIDER",
    "GITHUB_OIDC_ISSUER",
    "GitHubActionsOidcVerifier",
    "GitHubExecutionError",
    "GitHubHostedExecutionRuntime",
    "GitHubOidcClaims",
    "ROLE_EXECUTOR",
    "ROLE_PUBLISHER",
    "ROLE_VALIDATOR",
    "TRIGGER_WORKFLOW_PATH",
    "VALIDATION_DEFINITION",
    "evidence_prompt_resolver",
    "fetch_github_jwks",
    "oidc_audience",
]
