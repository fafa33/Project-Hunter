"""Production composition root for the governed GitHub Issue execution path.

Issue #390. PR #391 delivered the authorization edge only: it proves *who*
requested execution and emits a deterministic
``hunter-issue-agent-authorization-v1`` document. Nothing in the repository
consumed that document, so an authorized Issue could not reach the existing
Smart Prompt Machine -> signed handoff -> fallback runtime without someone
building a parallel path around the authorities that already own each decision.

This module is that missing consumer and nothing else. It owns no routing, no
prompt profile, no signing key, no transport, no provider order and no merge
authority; every one of those stays with the component that already holds it:

``hunter-issue-agent-signed-authorization-v2``
    the only executable input. It carries the canonical
    ``hunter-issue-agent-authorization-v1`` payload named by accepted ADR 0036
    s7 verbatim -- this contribution wraps that document, it never redefines it
    -- plus the Ed25519 issuer proof. The signature is verified first, against a
    public key captured at trusted bootstrap, because the ``authorization_id``
    digest covers only public Issue fields and so proves consistency rather than
    provenance. A bare unsigned payload is refused on the outer schema and has
    no execution path. The digest is then recomputed from the exact claims,
    binding replay identity to content rather than to a value the caller chose.
``IssueAgentExecutionLedger``
    durable execution ownership, claimed *before* any execution begins and
    advanced across the dispatch boundary, so a crash or retry cannot execute
    the same authorization twice.
``IssueSourceTransientIntakeBoundary`` (ADR 0036 s7)
    the only path by which Issue-sourced content becomes a durable
    ``EvidenceDocument``. Missing or non-permissive Source Handling authority
    fails closed here, before a build exists.
``GovernedEngineeringTaskIngress`` (Issue #436)
    the one canonical engineering-task entry point. Every authorized Issue is
    bound to a ``PromptTaskRequest`` and compiles only through this ingress,
    which resolves the exact governed route and enforces the route's hard input
    budget: normal implementation authorizations route to the governed
    ``engineering.implement`` route with a machine-readable fail-closed
    ``PromptTaskOversizeError`` before any dispatch, while the bounded
    ``engineering.review-fix`` reducer remains a governed route through the same
    ingress for review-fix work. There is deliberately no second entry into the
    machine here.
``SmartPromptMachine`` (ADR 0031/0032 route + profile registries)
    the only issuer of a build and of the signed ``PromptAutomationEnvelope``.
``serialize_prompt_automation_handoff``
    the exact non-content wire bytes, recorded durably and then passed to the
    fallback runtime **unchanged**; the runtime re-verifies the signature and
    keeps its own fixed provider order and remote-HEAD success contract.

Issue text is caller data at every step. It selects no route, no provider, no
destination, no branch and no merge behaviour: the task key, the prompt profile
and the route registry are repository-owned constants. The execution branch and
base are a pure function of the signed authorization (``derive_execution_target``,
``docs/ISSUE_AGENT_EXECUTION_CONTRACT.md``), never of Issue text or deployment
configuration, and every execution runs in its own isolated workspace.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import secrets
import sqlite3
from collections.abc import Mapping
from contextlib import closing
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from fnmatch import fnmatchcase
from pathlib import Path
from typing import Any, Protocol

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from hunter.automation.agent_fallback import AgentEnvironmentUnsuitableError, AgentFallbackExhaustedError
from hunter.automation.agent_fallback_runtime import (
    AgentFallbackRuntimeError,
    AgentFallbackRuntimeReceipt,
)
from hunter.automation.n8n_handoff import PromptAutomationHandoffError, serialize_prompt_automation_handoff
from hunter.evidence_intelligence.engineering_task_ingress import GovernedEngineeringTaskIngress
from hunter.evidence_intelligence.intake import (
    EvidenceIntakeReference,
    EvidenceIntelligenceIntakeService,
    evidence_document_id,
)
from hunter.evidence_intelligence.pre_model import PreModelInvariantError
from hunter.evidence_intelligence.repository import EvidenceIntelligenceRepository
from hunter.evidence_intelligence.smart_prompt_machine import SmartPromptMachineError
from hunter.evidence_intelligence.smart_prompt_routing import (
    ENGINEERING_IMPLEMENT_PROFILE,
    ENGINEERING_IMPLEMENT_ROUTE,
    ENGINEERING_IMPLEMENT_TASK_KEY,
    ENGINEERING_REVIEW_FIX_PROFILE,
    ENGINEERING_REVIEW_FIX_ROUTE,
    PromptAutomationVerifier,
    PromptMachineProfileRegistry,
    PromptTaskRequest,
    PromptTaskRouteRegistry,
    SmartPromptMachine,
)
from hunter.evidence_intelligence.source_handling import SourceHandlingBlockedError
from hunter.evidence_intelligence.source_handling_persistence import (
    IssueSourceTransientIntakeBoundary,
    ProductionSourceHandlingAuthorityResolver,
    ProvenanceResolver,
    SourceHandlingOperatorRoot,
    SqliteSourceHandlingAuthorityReadView,
)
from hunter.execution import Clock, SystemClock
from hunter.task_scope import TaskScopeContract, strip_task_scope_block

#: The inner payload named by accepted ADR 0036 s7, carried verbatim and never
#: redefined here.
ISSUE_AGENT_AUTHORIZATION_SCHEMA_VERSION = "hunter-issue-agent-authorization-v1"

#: The issuer-authenticated transport that carries it. Only this is executable.
ISSUE_AGENT_SIGNED_AUTHORIZATION_SCHEMA_VERSION = "hunter-issue-agent-signed-authorization-v2"

ISSUE_AGENT_AUTHORIZATION_LABEL = "hunter-agent-execute"
ISSUE_AGENT_AUTHORIZATION_IDENTITY_PREFIX = "hunter-issue-agent-authorization"

#: Domain separator the issuer mixes into the signed message. It must match
#: ``scripts/hunter_issue_agent_trigger.py`` exactly; the cross-binding test
#: pins the two together.
ISSUE_AGENT_AUTHORIZATION_SIGNATURE_DOMAIN = b"hunter-issue-agent-signed-authorization-v2:"
ISSUE_AGENT_VERIFYING_KEY_ENV = "HUNTER_ISSUE_AGENT_AUTHORIZATION_VERIFYING_KEY"
_ISSUE_AGENT_KEY_BYTES = 32
_ISSUE_AGENT_SIGNATURE_BYTES = 64
ISSUE_AGENT_EXECUTION_RECEIPT_SCHEMA_VERSION = "hunter-issue-agent-execution-receipt-v1"

#: The governed task key for normal implementation authorizations. Fixed by the
#: repository, never derived from Issue text, so an Issue cannot select its own
#: route. The bounded ``engineering.review-fix`` task stays a governed route
#: through the same ingress for review-fix work.
ISSUE_AGENT_TASK_KEY = ENGINEERING_IMPLEMENT_TASK_KEY

#: The exact registries this composition root routes through. Building them once
#: as module constants makes the route/profile pair a repository-owned fact
#: rather than something a caller assembles per execution.
ISSUE_AGENT_PROFILE_REGISTRY = PromptMachineProfileRegistry(
    (ENGINEERING_REVIEW_FIX_PROFILE, ENGINEERING_IMPLEMENT_PROFILE)
)
ISSUE_AGENT_ROUTE_REGISTRY = PromptTaskRouteRegistry(
    (ENGINEERING_REVIEW_FIX_ROUTE, ENGINEERING_IMPLEMENT_ROUTE),
    profiles=ISSUE_AGENT_PROFILE_REGISTRY,
)

REPOSITORY_ENV = "HUNTER_ISSUE_AGENT_REPOSITORY"
OWNER_LOGIN_ENV = "HUNTER_ISSUE_AGENT_OWNER_LOGIN"
EVIDENCE_DATABASE_ENV = "HUNTER_ISSUE_AGENT_EVIDENCE_DB"
#: Retired. The execution branch is derived from the signed authorization; a
#: value still present in a deployment is ignored (startup warns about it).
EXECUTION_BRANCH_ENV = "HUNTER_ISSUE_AGENT_EXECUTION_BRANCH"
#: The workspace root beneath which one isolated workspace per authorization is
#: materialized (``docs/ISSUE_AGENT_EXECUTION_CONTRACT.md`` I3).
REPOSITORY_CHECKOUT_ENV = "HUNTER_ISSUE_AGENT_REPO_DIR"

#: The only base branch the governance chain admits an Issue candidate against.
ISSUE_AGENT_BASE_REF = "main"
#: Hex characters of the authorization identity digest carried in the branch.
ISSUE_AGENT_BRANCH_DIGEST_LENGTH = 16
_AUTHORIZATION_DIGEST_RE = re.compile(r"[0-9a-f]{64}")
_COMMIT_SHA_RE = re.compile(r"[0-9a-f]{40}")
SOURCE_HANDLING_VERIFICATION_KEY_ENV = "HUNTER_SOURCE_HANDLING_VERIFICATION_KEY"
SOURCE_HANDLING_VERIFICATION_KEY_SHA256_ENV = "HUNTER_SOURCE_HANDLING_VERIFICATION_KEY_SHA256"
SOURCE_HANDLING_GENESIS_RULE_SHA256_ENV = "HUNTER_SOURCE_HANDLING_GENESIS_RULE_SHA256"

_MAX_AUTHORIZATION_BYTES = 256 * 1024
_LEDGER_TABLE = "issue_agent_execution_ledger"
_LEDGER_SCHEMA = f"""
CREATE TABLE IF NOT EXISTS {_LEDGER_TABLE} (
    authorization_id TEXT PRIMARY KEY,
    authorization_digest TEXT NOT NULL,
    state TEXT NOT NULL,
    document_id TEXT,
    build_record_id TEXT,
    envelope_id TEXT,
    handoff_document TEXT,
    claimed_at TEXT NOT NULL,
    dispatched_at TEXT,
    completed_at TEXT,
    failed_at TEXT,
    failure_type TEXT,
    failure_message TEXT,
    owner_instance_id TEXT,
    leased_at TEXT,
    lease_expires_at TEXT,
    execution_branch TEXT,
    base_sha TEXT,
    provider TEXT,
    head_after TEXT,
    failure_code TEXT,
    failure_attempts TEXT
)
"""
#: Columns added after the first ledger schema, migrated in place on open.
_LEDGER_MIGRATED_COLUMNS = (
    "failed_at",
    "failure_type",
    "failure_message",
    "owner_instance_id",
    "leased_at",
    "lease_expires_at",
    "execution_branch",
    "base_sha",
    "provider",
    "head_after",
    "failure_code",
    "failure_attempts",
)
_STATE_CLAIMED = "CLAIMED"
_STATE_DISPATCHED = "DISPATCHED"
_STATE_COMPLETED = "COMPLETED"
_STATE_FAILED = "FAILED"

#: How long an active execution lease remains valid without a renewal. Each
#: issuer instance extends the lease of every execution it is still running, so
#: the window only measures the gap between renewals; a lease that lapses is the
#: fail-closed signal that the owning instance is no longer running the
#: provider, and the row may then be recovered as failed by a later instance.
ISSUE_AGENT_LEDGER_LEASE_SECONDS = 6 * 60 * 60

_LEDGER_INSTANCE_PREFIX = "hunter-issue-agent-issuer-instance"


def _new_ledger_instance_id() -> str:
    """A fresh, stable-for-process lifetime issuer instance identity."""
    return f"{_LEDGER_INSTANCE_PREFIX}-{secrets.token_hex(6)}"


class IssueAgentExecutionError(RuntimeError):
    """Raised when the governed Issue execution path cannot proceed safely."""


class IssueAgentAuthorizationError(IssueAgentExecutionError):
    """Raised when an authorization document is malformed or not authorized here."""


class IssueAgentReplayError(IssueAgentExecutionError):
    """Raised when an authorization was already claimed by some earlier execution."""


class IssueAgentIssuerError(IssueAgentAuthorizationError):
    """Raised when a document cannot prove it came from the trusted issuer."""


class IssueAgentConfigurationError(IssueAgentExecutionError):
    """Raised when required operational configuration is absent or malformed."""


class IssueAgentRuntimeReceiptError(IssueAgentExecutionError):
    """The fallback runtime returned something other than its canonical receipt."""


class IssueAgentWorkspaceError(IssueAgentExecutionError):
    """An isolated execution workspace could not be materialized safely."""

    def __init__(self, reason_code: str, message: str) -> None:
        if reason_code not in _WORKSPACE_FAILURE_CODES:
            raise ValueError(f"unknown workspace failure code {reason_code!r}")
        self.reason_code = reason_code
        super().__init__(f"{reason_code}: {message}")


_WORKSPACE_FAILURE_CODES = frozenset({"BASE_NOT_ON_MAIN", "REMOTE_BRANCH_CONFLICT", "WORKSPACE_UNAVAILABLE"})

#: The fixed, non-secret vocabulary a terminal ledger failure is classified by.
ISSUE_AGENT_FAILURE_CODES = frozenset(
    {
        *_WORKSPACE_FAILURE_CODES,
        "ENVIRONMENT_UNSUITABLE",
        "PROVIDER_POOL_EXHAUSTED",
        "RUNTIME_FAILURE",
        "HANDOFF_INVALID",
        "NONCANONICAL_RUNTIME_RECEIPT",
        "PRE_MODEL_INVARIANT",
        "PROMPT_COMPILATION_REJECTED",
        "SOURCE_HANDLING_BLOCKED",
        "PROCESS_RESTART",
        "EXECUTION_ERROR",
    }
)


def issue_agent_failure_code(error: BaseException) -> str:
    """Classify one terminal execution failure into the fixed failure vocabulary."""
    if isinstance(error, IssueAgentWorkspaceError):
        return error.reason_code
    if isinstance(error, AgentEnvironmentUnsuitableError):
        return "ENVIRONMENT_UNSUITABLE"
    if isinstance(error, AgentFallbackExhaustedError):
        return "PROVIDER_POOL_EXHAUSTED"
    if isinstance(error, AgentFallbackRuntimeError):
        return "RUNTIME_FAILURE"
    if isinstance(error, PromptAutomationHandoffError):
        return "HANDOFF_INVALID"
    if isinstance(error, PreModelInvariantError):
        return "PRE_MODEL_INVARIANT"
    if isinstance(error, SmartPromptMachineError):
        return "PROMPT_COMPILATION_REJECTED"
    if isinstance(error, SourceHandlingBlockedError):
        return "SOURCE_HANDLING_BLOCKED"
    if isinstance(error, IssueAgentRuntimeReceiptError):
        return "NONCANONICAL_RUNTIME_RECEIPT"
    return "EXECUTION_ERROR"


def issue_agent_failure_attempts(error: BaseException) -> str | None:
    """The provider attempts behind an exhausted pool, as canonical JSON, if any.

    Attempt details are runtime-owned fixed strings (never provider output), so
    they are safe to persist and to report.
    """
    attempts = getattr(error, "attempts", None) if isinstance(error, AgentFallbackExhaustedError) else None
    if not attempts:
        return None
    return _canonical_json(
        [
            {
                "provider": attempt.provider,
                "state": attempt.state,
                "validation_passed": attempt.validation_passed,
                "detail": attempt.detail,
            }
            for attempt in attempts
        ]
    )


class _DuplicateJSONKeyError(ValueError):
    """Raised when an untrusted authorization object repeats a key."""


def _reject_duplicate_json_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    values: dict[str, Any] = {}
    for key, value in pairs:
        if key in values:
            raise _DuplicateJSONKeyError(key)
        values[key] = value
    return values


def _canonical_json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _required_text(name: str, value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise IssueAgentConfigurationError(f"{name} must be configured as a non-empty string")
    return value.strip()


def _aware_utc(name: str, value: datetime) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise IssueAgentExecutionError(f"{name} must be a timezone-aware datetime")
    return value.astimezone(UTC)


def _decode_bounded_json_object(document: str | bytes) -> dict[str, Any]:
    """Decode one bounded UTF-8 JSON object, refusing ambiguous input."""
    if isinstance(document, bytes):
        if len(document) > _MAX_AUTHORIZATION_BYTES:
            raise IssueAgentAuthorizationError("authorization document is too large")
        try:
            text = document.decode("utf-8")
        except UnicodeDecodeError:
            raise IssueAgentAuthorizationError("authorization document must be UTF-8 JSON") from None
    elif isinstance(document, str):
        if len(document.encode("utf-8")) > _MAX_AUTHORIZATION_BYTES:
            raise IssueAgentAuthorizationError("authorization document is too large")
        text = document
    else:
        raise IssueAgentAuthorizationError("authorization document must be str or bytes")

    try:
        decoded = json.loads(text, object_pairs_hook=_reject_duplicate_json_keys)
    except _DuplicateJSONKeyError:
        raise IssueAgentAuthorizationError("authorization document contains duplicate JSON keys") from None
    except (RecursionError, ValueError):
        raise IssueAgentAuthorizationError("authorization document is malformed JSON") from None
    if not isinstance(decoded, dict):
        raise IssueAgentAuthorizationError("authorization document must be a JSON object")
    return decoded


def _canonical_issuer_signature(value: object) -> str:
    """Return one ASCII lowercase Ed25519 signature or fail closed."""
    if (
        not isinstance(value, str)
        or len(value) != _ISSUE_AGENT_SIGNATURE_BYTES * 2
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise IssueAgentIssuerError("issuer_signature must be a 128-character lowercase hexadecimal Ed25519 signature")
    return value


@dataclass(frozen=True, slots=True)
class IssueAgentAuthorizationVerifier:
    """Process-bound issuer verifier captured once from trusted bootstrap.

    This is the authority that answers the question the identity digest cannot:
    did the trusted workflow that observed the owner's `issues:labeled` event
    actually mint this document? The public key is captured at bootstrap, so a
    later environment mutation cannot move the trust root, and the execution
    side holds only the public half -- it can verify an owner authorization but
    can never mint one.
    """

    _public_key_bytes: bytes

    def __post_init__(self) -> None:
        if type(self._public_key_bytes) is not bytes or len(self._public_key_bytes) != _ISSUE_AGENT_KEY_BYTES:
            raise IssueAgentConfigurationError(f"issuer verifying key must be exactly {_ISSUE_AGENT_KEY_BYTES} bytes")

    @classmethod
    def from_environment(
        cls,
        *,
        environ: Mapping[str, str] | None = None,
    ) -> IssueAgentAuthorizationVerifier:
        """Capture the verifier key once; absent or malformed material fails closed."""
        source = os.environ if environ is None else environ
        value = source.get(ISSUE_AGENT_VERIFYING_KEY_ENV, "")
        if not isinstance(value, str) or not value.strip():
            raise IssueAgentConfigurationError(f"{ISSUE_AGENT_VERIFYING_KEY_ENV} must provide the issuer verifying key")
        try:
            key = bytes.fromhex(value.strip())
        except ValueError:
            raise IssueAgentConfigurationError(
                f"{ISSUE_AGENT_VERIFYING_KEY_ENV} must be a hex-encoded byte string"
            ) from None
        if len(key) != _ISSUE_AGENT_KEY_BYTES:
            raise IssueAgentConfigurationError(
                f"{ISSUE_AGENT_VERIFYING_KEY_ENV} must decode to exactly {_ISSUE_AGENT_KEY_BYTES} bytes"
            )
        return cls(_public_key_bytes=key)

    def verify(self, signed: SignedIssueAgentAuthorization) -> None:
        """Reject any envelope the trusted issuer did not mint."""
        if not isinstance(signed, SignedIssueAgentAuthorization):
            raise IssueAgentIssuerError("issuer verification requires a parsed signed authorization")
        signature = bytes.fromhex(_canonical_issuer_signature(signed.issuer_signature))
        try:
            Ed25519PublicKey.from_public_bytes(self._public_key_bytes).verify(
                signature,
                signed.signed_message,
            )
        except InvalidSignature:
            raise IssueAgentIssuerError(
                "authorization was not issued by the trusted Issue authorization issuer"
            ) from None


@dataclass(frozen=True, slots=True)
class IssueAgentAuthorization:
    """One parsed ``hunter-issue-agent-authorization-v1`` payload.

    Every field is untrusted caller data except the schema version and the label,
    both of which must equal the repository's governed constants. The document's
    ``authorization_id`` is not believed: it is recomputed from the exact claims
    and must match, which is what binds replay identity to content instead of to
    a value the caller could choose.
    """

    repository: str
    issue_number: int
    issue_url: str
    issue_title: str
    issue_body: str
    authorized_by: str
    authorization_label: str
    issue_updated_at: str
    authorization_id: str
    schema_version: str = ISSUE_AGENT_AUTHORIZATION_SCHEMA_VERSION

    @property
    def canonical_claims(self) -> dict[str, Any]:
        """The exact claim set the trigger hashes into ``authorization_id``."""
        return {
            "repository": self.repository,
            "issue_number": self.issue_number,
            "issue_url": self.issue_url,
            "issue_title": self.issue_title,
            "issue_body": self.issue_body,
            "authorized_by": self.authorized_by,
            "authorization_label": self.authorization_label,
            "issue_updated_at": self.issue_updated_at,
            "schema_version": self.schema_version,
        }

    @property
    def signed_message(self) -> bytes:
        """The exact bytes the issuer signature covers: this whole payload.

        Signing the complete payload rather than only its claims means the
        carried ``authorization_id`` and ``schema_version`` are covered too, so
        no field of the canonical v1 document can be altered in transit.
        """
        return ISSUE_AGENT_AUTHORIZATION_SIGNATURE_DOMAIN + _canonical_json(asdict(self)).encode("utf-8")

    @property
    def derived_authorization_id(self) -> str:
        """Recompute the trigger's deterministic identity over the exact claims.

        This proves internal consistency only. Anyone holding the public Issue
        fields can recompute this digest, so it is never evidence of who minted
        the document -- that is what the issuer signature is for.
        """
        digest = hashlib.sha256(_canonical_json(self.canonical_claims).encode("utf-8")).hexdigest()
        return f"{ISSUE_AGENT_AUTHORIZATION_IDENTITY_PREFIX}:{digest}"

    @property
    def content_digest(self) -> str:
        """Digest over *every* field, including the identity the document carries.

        ``authorization_id`` alone is not a sufficient replay key for a ledger
        row: it is derived from the claims, so binding the stored row to the full
        document as well means a row can never be matched by a document that is
        not byte-identical to the one that claimed it.
        """
        return hashlib.sha256(_canonical_json(asdict(self)).encode("utf-8")).hexdigest()

    def to_json(self) -> str:
        return _canonical_json(asdict(self))

    @classmethod
    def from_json(cls, document: str | bytes) -> IssueAgentAuthorization:
        """Parse one bare canonical v1 payload.

        This is the ADR-named document, and parsing it proves nothing about who
        minted it, so it is deliberately *not* an execution entry point. The
        composition root accepts only ``SignedIssueAgentAuthorization``.
        """
        return cls._from_mapping(_decode_bounded_json_object(document))

    @classmethod
    def _from_mapping(cls, decoded: dict[str, Any]) -> IssueAgentAuthorization:
        """Validate one exact-schema payload mapping or fail closed."""
        expected = {
            "repository",
            "issue_number",
            "issue_url",
            "issue_title",
            "issue_body",
            "authorized_by",
            "authorization_label",
            "issue_updated_at",
            "authorization_id",
            "schema_version",
        }
        if set(decoded) != expected:
            raise IssueAgentAuthorizationError("authorization document schema mismatch")
        if decoded["schema_version"] != ISSUE_AGENT_AUTHORIZATION_SCHEMA_VERSION:
            raise IssueAgentAuthorizationError("unknown authorization document schema version")

        number = decoded["issue_number"]
        if type(number) is not int or number <= 0:
            raise IssueAgentAuthorizationError("authorization issue_number must be a positive integer")
        for name in expected - {"issue_number"}:
            if not isinstance(decoded[name], str):
                raise IssueAgentAuthorizationError(f"authorization {name} must be text")
        for name in ("repository", "issue_url", "issue_title", "authorized_by", "issue_updated_at", "authorization_id"):
            if not decoded[name].strip():
                raise IssueAgentAuthorizationError(f"authorization {name} must be non-empty")
        if decoded["authorization_label"] != ISSUE_AGENT_AUTHORIZATION_LABEL:
            raise IssueAgentAuthorizationError("authorization label is not the governed execution label")

        authorization = cls(**decoded)
        if authorization.authorization_id != authorization.derived_authorization_id:
            raise IssueAgentAuthorizationError("authorization identity does not bind the exact authorization claims")
        return authorization


@dataclass(frozen=True, slots=True)
class SignedIssueAgentAuthorization:
    """One canonical v1 payload plus the proof that the trusted issuer minted it.

    This is the only executable form. The payload it carries is exactly the
    document accepted ADR 0036 s7 names, unchanged and unredefined; the proof of
    origin lives out here in the transport, so authentication was added without
    touching the meaning of the inner schema.

    The signature covers the whole payload -- every claim, its
    ``authorization_id`` and its ``schema_version`` -- so no field of the
    canonical document can be altered in transit, and a signature minted for one
    payload cannot be transplanted onto another.
    """

    authorization: IssueAgentAuthorization
    implementation_scope: TaskScopeContract
    issuer_signature: str
    schema_version: str = ISSUE_AGENT_SIGNED_AUTHORIZATION_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if not isinstance(self.authorization, IssueAgentAuthorization):
            raise IssueAgentAuthorizationError("signed authorization must carry a canonical v1 payload")
        if not isinstance(self.implementation_scope, TaskScopeContract):
            raise IssueAgentAuthorizationError("signed authorization must carry canonical implementation scope")
        incomplete = self.implementation_scope.incompleteness()
        if incomplete:
            raise IssueAgentAuthorizationError(incomplete)
        if self.schema_version != ISSUE_AGENT_SIGNED_AUTHORIZATION_SCHEMA_VERSION:
            raise IssueAgentAuthorizationError("unknown signed authorization schema version")
        _canonical_issuer_signature(self.issuer_signature)

    @property
    def signed_message(self) -> bytes:
        return ISSUE_AGENT_AUTHORIZATION_SIGNATURE_DOMAIN + _canonical_json(
            {"authorization": asdict(self.authorization), "implementation_scope": asdict(self.implementation_scope)}
        ).encode("utf-8")

    @classmethod
    def from_json(cls, document: str | bytes) -> SignedIssueAgentAuthorization:
        """Parse one bounded exact-schema signed authorization or fail closed.

        A bare ``hunter-issue-agent-authorization-v1`` payload fails here on the
        outer field set: it is no longer an executable document, and the failure
        is a schema mismatch rather than a missing-signature afterthought.
        """
        decoded = _decode_bounded_json_object(document)
        expected = {"authorization", "implementation_scope", "issuer_signature", "schema_version"}
        if set(decoded) != expected:
            raise IssueAgentAuthorizationError("signed authorization document schema mismatch")
        if decoded["schema_version"] != ISSUE_AGENT_SIGNED_AUTHORIZATION_SCHEMA_VERSION:
            raise IssueAgentAuthorizationError("unknown signed authorization schema version")
        if not isinstance(decoded["issuer_signature"], str):
            raise IssueAgentIssuerError("issuer_signature must be text")
        payload = decoded["authorization"]
        if not isinstance(payload, dict):
            raise IssueAgentAuthorizationError("signed authorization must carry a JSON object payload")
        scope_payload = decoded["implementation_scope"]
        if not isinstance(scope_payload, dict):
            raise IssueAgentAuthorizationError("signed authorization implementation_scope must be an object")
        try:
            scope = TaskScopeContract.from_dict(scope_payload)
        except ValueError as error:
            raise IssueAgentAuthorizationError(str(error)) from None
        return cls(
            authorization=IssueAgentAuthorization._from_mapping(payload),
            implementation_scope=scope,
            issuer_signature=decoded["issuer_signature"],
        )

    def to_json(self) -> str:
        return _canonical_json(
            {
                "authorization": asdict(self.authorization),
                "implementation_scope": asdict(self.implementation_scope),
                "issuer_signature": self.issuer_signature,
                "schema_version": self.schema_version,
            }
        )


def issue_agent_task_text(authorization: IssueAgentAuthorization) -> str:
    """Map one authorization to its exact caller task text.

    Deterministic and lossless over the three Issue-content fields, emitted as
    canonical JSON so the untrusted parts stay individually delimited all the way
    into the Smart Prompt Machine's own ``untrusted_user_task`` wrapper. It
    carries no routing, provider, destination or merge coordinate, because no
    such coordinate is ever taken from Issue text.
    """
    return _canonical_json(
        {
            "issue_body": strip_task_scope_block(authorization.issue_body),
            "issue_title": authorization.issue_title,
            "issue_url": authorization.issue_url,
        }
    )


def issue_agent_intake_reference(authorization: IssueAgentAuthorization) -> EvidenceIntakeReference:
    """Build the deterministic ADR 0036 intake reference for one authorization.

    Identity coordinates come from the repository and Issue number, so the same
    Issue at the same content always resolves to the same governed document
    scope. Metadata is restricted to the two operational fields the Issue Source
    boundary accepts, and the label recorded is the governed constant rather than
    whatever labels the Issue happens to carry.
    """
    if not strip_task_scope_block(authorization.issue_body).strip():
        raise IssueAgentAuthorizationError("an authorized Issue must carry body content to execute")
    identity = f"github-issue:{authorization.repository}#{authorization.issue_number}"
    return EvidenceIntakeReference(
        source_evidence_id=identity,
        raw_evidence_id=f"{identity}:body",
        normalized_evidence_id=f"{identity}:body:normalized",
        candidate_id=authorization.authorization_id,
        identity_resolution_status="resolved",
        source_url=authorization.issue_url,
        source_provider="github",
        source_type="issue",
        source_claimed_authority="repository-owner",
        title=authorization.issue_title,
        content=strip_task_scope_block(authorization.issue_body),
        metadata={
            "issue_number": authorization.issue_number,
            "labels": [ISSUE_AGENT_AUTHORIZATION_LABEL],
        },
    )


def issue_agent_document_id(authorization: IssueAgentAuthorization) -> str:
    """The canonical Evidence document identity for one authorized Issue."""
    return evidence_document_id(issue_agent_intake_reference(authorization))


def issue_agent_task_request(authorization: IssueAgentAuthorization) -> PromptTaskRequest:
    """Map one authorization deterministically onto exactly one task request."""
    return PromptTaskRequest(
        document_id=issue_agent_document_id(authorization),
        execution_owner_id=authorization.authorization_id,
        task_key=ISSUE_AGENT_TASK_KEY,
        task_text=issue_agent_task_text(authorization),
    )


@dataclass(frozen=True, slots=True)
class IssueAgentExecutionTarget:
    """Where one authorization executes: its own branch, forked at its exact base.

    Every field is derived from the verified signed authorization by
    ``derive_execution_target``. Nothing here is configurable, and nothing here
    is taken from Issue text, deployment configuration or provider output.
    """

    authorization_id: str
    issue_number: int
    repository: str
    branch: str
    base_ref: str
    base_sha: str


def issue_agent_execution_branch(authorization: IssueAgentAuthorization) -> str:
    """The deterministic candidate branch ``issue-<n>-<16 hex of the identity digest>``.

    The branch binds the governing Issue in the shape the governance chain reads
    (``issue-<n>-...``), and the digest binds it to exactly one authorization: a
    changed Issue, scope or base is a new authorization and so a new branch.
    """
    prefix, separator, digest = authorization.authorization_id.partition(":")
    if (
        prefix != ISSUE_AGENT_AUTHORIZATION_IDENTITY_PREFIX
        or not separator
        or _AUTHORIZATION_DIGEST_RE.fullmatch(digest) is None
    ):
        raise IssueAgentAuthorizationError("authorization identity is not a canonical digest identity")
    return f"issue-{authorization.issue_number}-{digest[:ISSUE_AGENT_BRANCH_DIGEST_LENGTH]}"


def derive_execution_target(signed: SignedIssueAgentAuthorization) -> IssueAgentExecutionTarget:
    """Derive the execution target from the signed authorization, or refuse it.

    Pure and side-effect free, so it runs before the ledger claim: a document
    whose target cannot be derived is refused without consuming its identity.
    """
    authorization = signed.authorization
    scope = signed.implementation_scope
    if scope.task_id != authorization.authorization_id:
        raise IssueAgentAuthorizationError("implementation scope task_id must bind authorization identity")
    if _COMMIT_SHA_RE.fullmatch(scope.base_sha) is None:
        raise IssueAgentAuthorizationError("signed implementation scope must pin an exact lowercase base_sha")
    if scope.base_ref != ISSUE_AGENT_BASE_REF:
        raise IssueAgentAuthorizationError(
            f"signed implementation scope base_ref must be {ISSUE_AGENT_BASE_REF!r}, the only admitted base"
        )
    branch = issue_agent_execution_branch(authorization)
    if not fnmatchcase(branch, scope.branch_pattern):
        raise IssueAgentAuthorizationError(
            f"execution branch {branch!r} does not match the signed branch_pattern {scope.branch_pattern!r}"
        )
    return IssueAgentExecutionTarget(
        authorization_id=authorization.authorization_id,
        issue_number=authorization.issue_number,
        repository=authorization.repository,
        branch=branch,
        base_ref=scope.base_ref,
        base_sha=scope.base_sha,
    )


@dataclass(frozen=True, slots=True)
class IssueAgentLedgerEntry:
    """The durable execution-ownership row for one authorization."""

    authorization_id: str
    authorization_digest: str
    state: str
    document_id: str | None
    build_record_id: str | None
    envelope_id: str | None
    handoff_document: str | None
    failed_at: str | None
    failure_type: str | None
    failure_message: str | None
    owner_instance_id: str | None
    leased_at: str | None
    lease_expires_at: str | None
    claimed_at: str | None = None
    dispatched_at: str | None = None
    completed_at: str | None = None
    execution_branch: str | None = None
    base_sha: str | None = None
    provider: str | None = None
    head_after: str | None = None
    failure_code: str | None = None
    failure_attempts: str | None = None


class IssueAgentExecutionLedger:
    """Durable, restart-surviving execution ownership for Issue authorizations.

    Ownership is claimed before any execution work happens and is never released
    on failure. A crash between the claim and the dispatch therefore leaves a row
    that refuses the next attempt, which is the fail-closed choice: a duplicate
    execution can push commits, while a refused retry cannot.

    Every active row is owned by exactly one issuer instance and carries a
    renewable lease. The owning instance extends the lease while its provider is
    still genuinely running, so a lapsed lease is the deterministic fail-closed
    signal that the owner is no longer running the provider. Startup recovery
    therefore never touches a row whose foreign lease is still valid, which is
    what a rolling deployment of multiple issuer instances relies on: the old
    instance keeps its live executions instead of having them failed underneath
    it by the instance that happens to boot next.
    """

    __slots__ = ("_path", "_instance_id")

    def __init__(self, path: str | Path, *, instance_id: str | None = None) -> None:
        self._path = Path(path)
        self._instance_id = instance_id or _new_ledger_instance_id()
        if instance_id is None:
            logging.getLogger(__name__).warning(
                "Issue Agent execution ledger created without an explicit instance id; "
                "adopting %s for this process only",
                self._instance_id,
            )
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as connection:
            connection.execute(_LEDGER_SCHEMA)
            columns = {str(row["name"]) for row in connection.execute(f"PRAGMA table_info({_LEDGER_TABLE})").fetchall()}
            for name in _LEDGER_MIGRATED_COLUMNS:
                if name not in columns:
                    connection.execute(f"ALTER TABLE {_LEDGER_TABLE} ADD COLUMN {name} TEXT")
            connection.commit()

    @property
    def path(self) -> Path:
        return self._path

    @property
    def instance_id(self) -> str:
        """The stable issuer instance identity that owns this ledger's rows."""
        return self._instance_id

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._path, timeout=30.0, isolation_level=None)
        connection.row_factory = sqlite3.Row
        return connection

    def entry(self, authorization_id: str) -> IssueAgentLedgerEntry | None:
        with closing(self._connect()) as connection:
            row = connection.execute(
                f"SELECT * FROM {_LEDGER_TABLE} WHERE authorization_id = ?",
                (authorization_id,),
            ).fetchone()
        if row is None:
            return None
        return IssueAgentLedgerEntry(
            authorization_id=str(row["authorization_id"]),
            authorization_digest=str(row["authorization_digest"]),
            state=str(row["state"]),
            document_id=row["document_id"],
            build_record_id=row["build_record_id"],
            envelope_id=row["envelope_id"],
            handoff_document=row["handoff_document"],
            failed_at=row["failed_at"],
            failure_type=row["failure_type"],
            failure_message=row["failure_message"],
            owner_instance_id=row["owner_instance_id"],
            leased_at=row["leased_at"],
            lease_expires_at=row["lease_expires_at"],
            claimed_at=row["claimed_at"],
            dispatched_at=row["dispatched_at"],
            completed_at=row["completed_at"],
            execution_branch=row["execution_branch"],
            base_sha=row["base_sha"],
            provider=row["provider"],
            head_after=row["head_after"],
            failure_code=row["failure_code"],
            failure_attempts=row["failure_attempts"],
        )

    def claim(self, authorization: IssueAgentAuthorization, *, claimed_at: datetime) -> None:
        """Take durable ownership of one authorization or refuse the execution."""
        moment = _aware_utc("ledger claim time", claimed_at)
        expires_at = moment + timedelta(seconds=ISSUE_AGENT_LEDGER_LEASE_SECONDS)
        with closing(self._connect()) as connection:
            try:
                connection.execute("BEGIN IMMEDIATE")
                existing = connection.execute(
                    f"SELECT state FROM {_LEDGER_TABLE} WHERE authorization_id = ?",
                    (authorization.authorization_id,),
                ).fetchone()
                if existing is not None:
                    raise IssueAgentReplayError(
                        f"authorization was already claimed in state {str(existing['state'])!r}"
                    )
                connection.execute(
                    f"INSERT INTO {_LEDGER_TABLE} "
                    "(authorization_id, authorization_digest, state, claimed_at, "
                    "owner_instance_id, leased_at, lease_expires_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        authorization.authorization_id,
                        authorization.content_digest,
                        _STATE_CLAIMED,
                        moment.isoformat(),
                        self._instance_id,
                        moment.isoformat(),
                        expires_at.isoformat(),
                    ),
                )
                connection.execute("COMMIT")
            except sqlite3.IntegrityError:
                connection.execute("ROLLBACK")
                raise IssueAgentReplayError("authorization was already claimed by a concurrent execution") from None
            except BaseException:
                connection.execute("ROLLBACK")
                raise

    def record_dispatch(
        self,
        authorization: IssueAgentAuthorization,
        *,
        document_id: str,
        build_record_id: str,
        envelope_id: str,
        handoff_document: str,
        target: IssueAgentExecutionTarget,
        dispatched_at: datetime,
    ) -> None:
        """Record the exact handoff and execution target durably *before* the runtime sees them."""
        if not isinstance(target, IssueAgentExecutionTarget) or target.authorization_id != (
            authorization.authorization_id
        ):
            raise IssueAgentExecutionError("ledger dispatch requires the execution target of this authorization")
        moment = _aware_utc("ledger dispatch time", dispatched_at)
        expires_at = moment + timedelta(seconds=ISSUE_AGENT_LEDGER_LEASE_SECONDS)
        self._advance(
            authorization,
            sql=(
                f"UPDATE {_LEDGER_TABLE} SET state = ?, document_id = ?, build_record_id = ?, "
                "envelope_id = ?, handoff_document = ?, execution_branch = ?, base_sha = ?, dispatched_at = ?, "
                "leased_at = ?, lease_expires_at = ? "
                "WHERE authorization_id = ? AND authorization_digest = ? AND state = ? "
                "AND owner_instance_id = ? AND lease_expires_at > ?"
            ),
            parameters=(
                _STATE_DISPATCHED,
                document_id,
                build_record_id,
                envelope_id,
                handoff_document,
                target.branch,
                target.base_sha,
                moment.isoformat(),
                moment.isoformat(),
                expires_at.isoformat(),
                authorization.authorization_id,
                authorization.content_digest,
                _STATE_CLAIMED,
                self._instance_id,
                moment.isoformat(),
            ),
            failure="ledger dispatch state is not the exact claimed authorization owned by this instance",
        )

    def complete(
        self,
        authorization: IssueAgentAuthorization,
        *,
        completed_at: datetime,
        provider: str | None = None,
        head_after: str | None = None,
    ) -> None:
        """Mark one authorization finished after the runtime returned a receipt."""
        moment = _aware_utc("ledger completion time", completed_at).isoformat()
        self._advance(
            authorization,
            sql=(
                f"UPDATE {_LEDGER_TABLE} SET state = ?, completed_at = ?, provider = ?, head_after = ? "
                "WHERE authorization_id = ? AND authorization_digest = ? AND state = ? "
                "AND owner_instance_id = ?"
            ),
            parameters=(
                _STATE_COMPLETED,
                moment,
                provider,
                head_after,
                authorization.authorization_id,
                authorization.content_digest,
                _STATE_DISPATCHED,
                self._instance_id,
            ),
            failure="ledger completion requires the exact dispatched authorization owned by this instance",
        )

    def fail(
        self,
        authorization: IssueAgentAuthorization,
        *,
        failed_at: datetime,
        failure_type: str,
        failure_message: str,
        failure_code: str | None = None,
        failure_attempts: str | None = None,
    ) -> None:
        """Persist a terminal failure without ever releasing replay ownership."""
        moment = _aware_utc("ledger failure time", failed_at).isoformat()
        kind = str(failure_type).strip() or "ExecutionError"
        message = str(failure_message).strip() or kind
        if len(message) > 2000:
            message = message[:2000]
        code = failure_code if failure_code in ISSUE_AGENT_FAILURE_CODES else "EXECUTION_ERROR"
        attempts = failure_attempts[:4000] if isinstance(failure_attempts, str) else None

        self._advance(
            authorization,
            sql=(
                f"UPDATE {_LEDGER_TABLE} "
                "SET state = ?, failed_at = ?, failure_type = ?, failure_message = ?, "
                "failure_code = ?, failure_attempts = ? "
                "WHERE authorization_id = ? AND authorization_digest = ? "
                "AND state IN (?, ?) AND owner_instance_id = ?"
            ),
            parameters=(
                _STATE_FAILED,
                moment,
                kind,
                message,
                code,
                attempts,
                authorization.authorization_id,
                authorization.content_digest,
                _STATE_CLAIMED,
                _STATE_DISPATCHED,
                self._instance_id,
            ),
            failure="ledger failure requires an active exact authorization owned by this instance",
        )

    def renew_lease(
        self,
        authorization: IssueAgentAuthorization,
        *,
        health_at: datetime,
    ) -> None:
        """Extend the execution lease while this instance is still running it.

        A live provider keeps its row non-terminal forever, so an instance that
        boots while it runs must never fail it closed. Replay ownership is
        untouched by renewal: it never resets a terminal row and never lets a
        foreign instance reclaim the authorization.
        """
        moment = _aware_utc("ledger lease health time", health_at)
        expires_at = moment + timedelta(seconds=ISSUE_AGENT_LEDGER_LEASE_SECONDS)
        self._advance(
            authorization,
            sql=(
                f"UPDATE {_LEDGER_TABLE} SET leased_at = ?, lease_expires_at = ? "
                "WHERE authorization_id = ? AND authorization_digest = ? AND state = ? "
                "AND owner_instance_id = ?"
            ),
            parameters=(
                moment.isoformat(),
                expires_at.isoformat(),
                authorization.authorization_id,
                authorization.content_digest,
                _STATE_DISPATCHED,
                self._instance_id,
            ),
            failure="ledger lease renewal requires the exact dispatched authorization owned by this instance",
        )

    def recover_expired_on_startup(self, *, failed_at: datetime) -> int:
        """Fail closed only incomplete rows whose execution lease has lapsed.

        A row whose foreign lease is still valid is proof that another live
        issuer instance still runs that provider, so this instance must leave it
        alone; that is exactly the overlap a rolling deployment or the
        multiple-instance configuration produces. An incomplete row whose lease
        has lapsed means the owning instance stopped renewing it before a
        terminal outcome, so it may be failed closed. Rows with no recorded
        lease (pre-upgrade or otherwise unclaimable) are never guessed at and
        stay as they are. Replay ownership remains held forever: recovery never
        retries an uncertain authorization and therefore cannot double-execute
        it, and lease expiry never permits a reclaim.
        """
        moment = _aware_utc("ledger startup recovery time", failed_at).isoformat()
        with closing(self._connect()) as connection:
            try:
                connection.execute("BEGIN IMMEDIATE")
                cursor = connection.execute(
                    f"UPDATE {_LEDGER_TABLE} "
                    "SET state = ?, failed_at = ?, failure_type = ?, failure_message = ?, failure_code = ? "
                    "WHERE state IN (?, ?) "
                    "AND lease_expires_at IS NOT NULL AND lease_expires_at < ?",
                    (
                        _STATE_FAILED,
                        moment,
                        "ProcessRestart",
                        "issuer lease expired before a durable terminal outcome",
                        "PROCESS_RESTART",
                        _STATE_CLAIMED,
                        _STATE_DISPATCHED,
                        moment,
                    ),
                )
                connection.execute("COMMIT")
                return int(cursor.rowcount)
            except BaseException:
                connection.execute("ROLLBACK")
                raise

    def _advance(
        self,
        authorization: IssueAgentAuthorization,
        *,
        sql: str,
        parameters: tuple[Any, ...],
        failure: str,
    ) -> None:
        del authorization
        with closing(self._connect()) as connection:
            try:
                connection.execute("BEGIN IMMEDIATE")
                cursor = connection.execute(sql, parameters)
                if cursor.rowcount != 1:
                    raise IssueAgentExecutionError(failure)
                connection.execute("COMMIT")
            except BaseException:
                connection.execute("ROLLBACK")
                raise


@dataclass(frozen=True, slots=True)
class IssueAgentExecutionReceipt:
    """Non-secret proof of one governed Issue execution."""

    authorization_id: str
    document_id: str
    build_record_id: str
    envelope_id: str
    handoff_document: str
    fallback: AgentFallbackRuntimeReceipt
    schema_version: str = ISSUE_AGENT_EXECUTION_RECEIPT_SCHEMA_VERSION

    def to_json(self) -> str:
        payload = {
            "authorization_id": self.authorization_id,
            "build_record_id": self.build_record_id,
            "document_id": self.document_id,
            "envelope_id": self.envelope_id,
            "fallback": json.loads(self.fallback.to_json()),
            "handoff_document": self.handoff_document,
            "schema_version": self.schema_version,
        }
        return _canonical_json(payload)


class IssueAgentFallbackRuntime(Protocol):
    """The fallback runtime seam: the handoff unchanged, plus where it executes.

    The target is always the one ``derive_execution_target`` derived from the
    signed authorization and recorded in the ledger before dispatch.
    """

    def dispatch(self, document: str | bytes, target: IssueAgentExecutionTarget) -> AgentFallbackRuntimeReceipt: ...


@dataclass(frozen=True, slots=True)
class IssueAgentExecutionConfiguration:
    """Operational configuration captured once at trusted bootstrap.

    Nothing here is reachable from Issue text, and nothing here is re-read from
    the environment later: a mid-run environment mutation cannot move the
    execution to another repository, workspace root or authority database. The
    branch and base are not configuration at all; they come from the signed
    authorization.
    """

    repository: str
    owner_login: str
    evidence_database: Path
    repository_checkout: Path
    source_handling_verification_key: bytes
    source_handling_operator_root: SourceHandlingOperatorRoot

    @classmethod
    def from_environment(cls, environ: Mapping[str, str] | None = None) -> IssueAgentExecutionConfiguration:
        source = os.environ if environ is None else environ
        key_hex = _required_text(SOURCE_HANDLING_VERIFICATION_KEY_ENV, source.get(SOURCE_HANDLING_VERIFICATION_KEY_ENV))
        try:
            verification_key = bytes.fromhex(key_hex)
        except ValueError:
            raise IssueAgentConfigurationError(
                f"{SOURCE_HANDLING_VERIFICATION_KEY_ENV} must be a hex-encoded Ed25519 public key"
            ) from None
        operator_root = SourceHandlingOperatorRoot(
            genesis_rule_sha256=_required_text(
                SOURCE_HANDLING_GENESIS_RULE_SHA256_ENV,
                source.get(SOURCE_HANDLING_GENESIS_RULE_SHA256_ENV),
            ),
            verification_key_sha256=_required_text(
                SOURCE_HANDLING_VERIFICATION_KEY_SHA256_ENV,
                source.get(SOURCE_HANDLING_VERIFICATION_KEY_SHA256_ENV),
            ),
        )
        return cls(
            repository=_required_text(REPOSITORY_ENV, source.get(REPOSITORY_ENV)),
            owner_login=_required_text(OWNER_LOGIN_ENV, source.get(OWNER_LOGIN_ENV)),
            evidence_database=Path(_required_text(EVIDENCE_DATABASE_ENV, source.get(EVIDENCE_DATABASE_ENV))),
            repository_checkout=Path(_required_text(REPOSITORY_CHECKOUT_ENV, source.get(REPOSITORY_CHECKOUT_ENV))),
            source_handling_verification_key=verification_key,
            source_handling_operator_root=operator_root,
        )


def build_production_source_handling_resolver(
    configuration: IssueAgentExecutionConfiguration,
    *,
    provenance_resolver: ProvenanceResolver,
) -> ProductionSourceHandlingAuthorityResolver:
    """Bind the ADR 0036 production read-only authority seam.

    The resolver is constructed from a read-only view, never from
    ``SourceHandlingAuthorityService``: the execution path must be able to read
    published authority and must never be able to publish it. An absent,
    unreadable or tampered authority history raises ``SourceHandlingBlockedError``
    from the view's own constructor, so a missing authority can never degrade to
    an in-memory test double.
    """
    view = SqliteSourceHandlingAuthorityReadView(
        configuration.evidence_database,
        verification_public_key=configuration.source_handling_verification_key,
        operator_root=configuration.source_handling_operator_root,
        provenance_resolver=provenance_resolver,
    )
    return ProductionSourceHandlingAuthorityResolver(view)


def _workspace_runtime(
    configuration: IssueAgentExecutionConfiguration, environ: Mapping[str, str]
) -> IssueAgentFallbackRuntime:
    # Imported here: the workspace runtime builds on this module's contract.
    from hunter.automation.issue_agent_workspace import IssueAgentWorkspaceRuntime

    return IssueAgentWorkspaceRuntime(
        workspace_root=configuration.repository_checkout,
        repository=configuration.repository,
        environ=environ,
    )


class GovernedIssueAgentExecutionService:
    """The Issue #390 production composition root.

    One method, one authorization, one execution. It composes existing
    authorities in a fixed order and adds exactly one thing of its own: durable
    execution ownership, taken before work begins and advanced across the
    dispatch boundary.
    """

    __slots__ = (
        "_configuration",
        "_ledger",
        "_machine",
        "_ingress",
        "_boundary",
        "_fallback",
        "_verifier",
        "_issuer_verifier",
        "_clock",
    )

    def __init__(
        self,
        *,
        configuration: IssueAgentExecutionConfiguration,
        repository: EvidenceIntelligenceRepository,
        source_handling_resolver: ProductionSourceHandlingAuthorityResolver,
        ledger: IssueAgentExecutionLedger,
        fallback: IssueAgentFallbackRuntime,
        verifier: PromptAutomationVerifier,
        issuer_verifier: IssueAgentAuthorizationVerifier,
        clock: Clock | None = None,
    ) -> None:
        if not isinstance(configuration, IssueAgentExecutionConfiguration):
            raise IssueAgentConfigurationError("the composition root requires captured bootstrap configuration")
        if not isinstance(repository, EvidenceIntelligenceRepository):
            raise IssueAgentConfigurationError("the composition root requires the canonical Evidence repository")
        if not isinstance(source_handling_resolver, ProductionSourceHandlingAuthorityResolver):
            raise IssueAgentConfigurationError(
                "the composition root requires the ADR 0036 production read-only Source Handling resolver"
            )
        if not isinstance(ledger, IssueAgentExecutionLedger):
            raise IssueAgentConfigurationError("the composition root requires the durable execution ledger")
        if type(verifier) is not PromptAutomationVerifier:
            raise IssueAgentConfigurationError("the composition root requires the process-bound issuer verifier")
        if type(issuer_verifier) is not IssueAgentAuthorizationVerifier:
            raise IssueAgentConfigurationError(
                "the composition root requires the bootstrap-captured Issue authorization verifier"
            )
        if not callable(getattr(fallback, "dispatch", None)):
            raise IssueAgentConfigurationError("the composition root requires the existing fallback runtime seam")
        self._configuration = configuration
        self._ledger = ledger
        self._fallback = fallback
        self._verifier = verifier
        self._issuer_verifier = issuer_verifier
        self._clock = clock or SystemClock()
        self._boundary = IssueSourceTransientIntakeBoundary(
            intake=EvidenceIntelligenceIntakeService(repository),
            resolver=source_handling_resolver,
            clock=self._clock,
        )
        self._machine = SmartPromptMachine(
            repository=repository,
            profiles=ISSUE_AGENT_PROFILE_REGISTRY,
            routes=ISSUE_AGENT_ROUTE_REGISTRY,
            source_handling_resolver=source_handling_resolver,
            clock=self._clock,
        )
        self._ingress = GovernedEngineeringTaskIngress(
            machine=self._machine,
            routes=ISSUE_AGENT_ROUTE_REGISTRY,
            profiles=ISSUE_AGENT_PROFILE_REGISTRY,
        )

    @classmethod
    def from_environment(
        cls,
        *,
        provenance_resolver: ProvenanceResolver,
        environ: Mapping[str, str] | None = None,
        clock: Clock | None = None,
    ) -> GovernedIssueAgentExecutionService:
        """Compose the production path from operational configuration only.

        The canonical provenance resolver is supplied by the operator rather than
        read from the environment, because it is an authority callable and not a
        configuration value; there is deliberately no default, so an unwired
        deployment cannot silently resolve provenance as "absent but fine".
        """
        source = os.environ if environ is None else environ
        configuration = IssueAgentExecutionConfiguration.from_environment(source)
        resolver = build_production_source_handling_resolver(
            configuration,
            provenance_resolver=provenance_resolver,
        )
        return cls(
            configuration=configuration,
            repository=EvidenceIntelligenceRepository(configuration.evidence_database),
            source_handling_resolver=resolver,
            ledger=IssueAgentExecutionLedger(configuration.evidence_database),
            fallback=_workspace_runtime(configuration, source),
            verifier=PromptAutomationVerifier.from_environment(environ=source),
            issuer_verifier=IssueAgentAuthorizationVerifier.from_environment(environ=source),
            clock=clock,
        )

    def execute(self, document: str | bytes) -> IssueAgentExecutionReceipt:
        """Run one signed authorization through the existing governed runtime.

        The only accepted input is a ``hunter-issue-agent-signed-authorization-v2``
        envelope. A bare canonical v1 payload is refused on the outer schema, so
        an unsigned document has no execution path here at all.
        """
        signed = SignedIssueAgentAuthorization.from_json(document)
        # Trusted origin first. The identity digest proves only that the claims
        # are self-consistent, and every field it covers is public, so it is not
        # evidence that the owner performed the `issues:labeled` event. Only the
        # issuer signature proves that, and nothing durable or external happens
        # until it verifies.
        self._issuer_verifier.verify(signed)
        authorization = signed.authorization
        if signed.implementation_scope.task_id != authorization.authorization_id:
            raise IssueAgentAuthorizationError("implementation scope task_id must bind authorization identity")
        if authorization.repository != self._configuration.repository:
            raise IssueAgentAuthorizationError("authorization names a different repository than this deployment")
        if authorization.authorized_by != self._configuration.owner_login:
            raise IssueAgentAuthorizationError("only the configured repository owner may authorize execution")

        # Deterministic mapping is pure and reaches nothing durable or external,
        # so it is done before ownership is taken. A document that could never
        # execute does not burn its own authorization identity, and the claim
        # still precedes every step that can actually run something.
        target = derive_execution_target(signed)
        reference = issue_agent_intake_reference(authorization)
        document_id = evidence_document_id(reference)
        request = issue_agent_task_request(authorization)
        if request.document_id != document_id:
            raise IssueAgentExecutionError("Issue task request does not bind the ingested document identity")

        # Source Handling preflight: validate authority before claiming ownership.
        # This is side-effect free -- no ledger row, no persisted artifacts, no
        # dispatch. A failed preflight allows retry once authority is corrected.
        self._boundary.preflight(
            reference,
            processing_run_id=authorization.authorization_id,
            processed_at=_aware_utc("Issue execution preflight time", self._clock.now()),
        )

        self._ledger.claim(authorization, claimed_at=self._clock.now())

        self._boundary.ingest(
            reference,
            processing_run_id=authorization.authorization_id,
            processed_at=_aware_utc("Issue execution intake time", self._clock.now()),
        )

        compiled = self._ingress.compile(request, implementation_scope=signed.implementation_scope)
        envelope = compiled.envelope
        envelope.verify_issuer_signature(self._verifier)
        if envelope.build_record_id != compiled.compilation.manifest.build_record_id:
            raise IssueAgentExecutionError("signed envelope and persisted build refer to different lineage")

        handoff_document = serialize_prompt_automation_handoff(envelope)
        self._ledger.record_dispatch(
            authorization,
            document_id=document_id,
            build_record_id=envelope.build_record_id,
            envelope_id=envelope.envelope_id,
            handoff_document=handoff_document,
            target=target,
            dispatched_at=self._clock.now(),
        )

        receipt = self._fallback.dispatch(handoff_document, target)
        if not isinstance(receipt, AgentFallbackRuntimeReceipt):
            raise IssueAgentRuntimeReceiptError("fallback runtime did not return a canonical execution receipt")
        self._ledger.complete(
            authorization,
            completed_at=self._clock.now(),
            provider=receipt.provider,
            head_after=receipt.head_after,
        )
        return IssueAgentExecutionReceipt(
            authorization_id=authorization.authorization_id,
            document_id=document_id,
            build_record_id=envelope.build_record_id,
            envelope_id=envelope.envelope_id,
            handoff_document=handoff_document,
            fallback=receipt,
        )


__all__ = [
    "EVIDENCE_DATABASE_ENV",
    "EXECUTION_BRANCH_ENV",
    "GovernedIssueAgentExecutionService",
    "ISSUE_AGENT_BASE_REF",
    "ISSUE_AGENT_BRANCH_DIGEST_LENGTH",
    "ISSUE_AGENT_FAILURE_CODES",
    "IssueAgentExecutionTarget",
    "IssueAgentRuntimeReceiptError",
    "IssueAgentWorkspaceError",
    "derive_execution_target",
    "issue_agent_execution_branch",
    "issue_agent_failure_attempts",
    "issue_agent_failure_code",
    "ISSUE_AGENT_AUTHORIZATION_LABEL",
    "ISSUE_AGENT_AUTHORIZATION_SCHEMA_VERSION",
    "ISSUE_AGENT_EXECUTION_RECEIPT_SCHEMA_VERSION",
    "ISSUE_AGENT_LEDGER_LEASE_SECONDS",
    "ISSUE_AGENT_PROFILE_REGISTRY",
    "ISSUE_AGENT_ROUTE_REGISTRY",
    "ISSUE_AGENT_SIGNED_AUTHORIZATION_SCHEMA_VERSION",
    "ISSUE_AGENT_TASK_KEY",
    "ISSUE_AGENT_VERIFYING_KEY_ENV",
    "IssueAgentAuthorization",
    "IssueAgentAuthorizationError",
    "IssueAgentAuthorizationVerifier",
    "IssueAgentIssuerError",
    "IssueAgentConfigurationError",
    "IssueAgentExecutionConfiguration",
    "IssueAgentExecutionError",
    "IssueAgentExecutionLedger",
    "IssueAgentExecutionReceipt",
    "IssueAgentFallbackRuntime",
    "IssueAgentLedgerEntry",
    "IssueAgentReplayError",
    "OWNER_LOGIN_ENV",
    "REPOSITORY_CHECKOUT_ENV",
    "REPOSITORY_ENV",
    "SOURCE_HANDLING_GENESIS_RULE_SHA256_ENV",
    "SOURCE_HANDLING_VERIFICATION_KEY_ENV",
    "SOURCE_HANDLING_VERIFICATION_KEY_SHA256_ENV",
    "SignedIssueAgentAuthorization",
    "SourceHandlingBlockedError",
    "build_production_source_handling_resolver",
    "issue_agent_document_id",
    "issue_agent_intake_reference",
    "issue_agent_task_request",
    "issue_agent_task_text",
]
