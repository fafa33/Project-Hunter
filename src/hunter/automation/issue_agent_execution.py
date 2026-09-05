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

``hunter-issue-agent-authorization-v1``
    parsed and revalidated here against trusted operational configuration. The
    document's own ``authorization_id`` is recomputed from its exact claims, so
    replay identity is bound to content rather than to a caller-chosen label.
``IssueAgentExecutionLedger``
    durable execution ownership, claimed *before* any execution begins and
    advanced across the dispatch boundary, so a crash or retry cannot execute
    the same authorization twice.
``IssueSourceTransientIntakeBoundary`` (ADR 0036 s7)
    the only path by which Issue-sourced content becomes a durable
    ``EvidenceDocument``. Missing or non-permissive Source Handling authority
    fails closed here, before a build exists.
``SmartPromptMachine`` (ADR 0031/0032 route + profile registries)
    the only issuer of a build and of the signed ``PromptAutomationEnvelope``.
``serialize_prompt_automation_handoff``
    the exact non-content wire bytes, recorded durably and then passed to the
    fallback runtime **unchanged**; the runtime re-verifies the signature and
    keeps its own fixed provider order and remote-HEAD success contract.

Issue text is caller data at every step. It selects no route, no provider, no
destination, no branch and no merge behaviour: the task key, the prompt profile
and the route registry are repository-owned constants, and the execution branch
and repository checkout come from operational configuration only.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from collections.abc import Mapping
from contextlib import closing
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

from hunter.automation.agent_fallback_runtime import (
    AgentFallbackRuntimeReceipt,
    OperationalAgentFallbackRuntime,
)
from hunter.automation.n8n_handoff import serialize_prompt_automation_handoff
from hunter.evidence_intelligence.intake import (
    EvidenceIntakeReference,
    EvidenceIntelligenceIntakeService,
    evidence_document_id,
)
from hunter.evidence_intelligence.repository import EvidenceIntelligenceRepository
from hunter.evidence_intelligence.smart_prompt_routing import (
    ENGINEERING_REVIEW_FIX_PROFILE,
    ENGINEERING_REVIEW_FIX_ROUTE,
    ENGINEERING_REVIEW_FIX_TASK_KEY,
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

ISSUE_AGENT_AUTHORIZATION_SCHEMA_VERSION = "hunter-issue-agent-authorization-v1"
ISSUE_AGENT_AUTHORIZATION_LABEL = "hunter-agent-execute"
ISSUE_AGENT_AUTHORIZATION_IDENTITY_PREFIX = "hunter-issue-agent-authorization"
ISSUE_AGENT_EXECUTION_RECEIPT_SCHEMA_VERSION = "hunter-issue-agent-execution-receipt-v1"

#: The governed task key for every authorized Issue. Fixed by the repository,
#: never derived from Issue text, so an Issue cannot select its own route.
ISSUE_AGENT_TASK_KEY = ENGINEERING_REVIEW_FIX_TASK_KEY

#: The exact registries this composition root routes through. Building them once
#: as module constants makes the route/profile pair a repository-owned fact
#: rather than something a caller assembles per execution.
ISSUE_AGENT_PROFILE_REGISTRY = PromptMachineProfileRegistry((ENGINEERING_REVIEW_FIX_PROFILE,))
ISSUE_AGENT_ROUTE_REGISTRY = PromptTaskRouteRegistry(
    (ENGINEERING_REVIEW_FIX_ROUTE,),
    profiles=ISSUE_AGENT_PROFILE_REGISTRY,
)

REPOSITORY_ENV = "HUNTER_ISSUE_AGENT_REPOSITORY"
OWNER_LOGIN_ENV = "HUNTER_ISSUE_AGENT_OWNER_LOGIN"
EVIDENCE_DATABASE_ENV = "HUNTER_ISSUE_AGENT_EVIDENCE_DB"
EXECUTION_BRANCH_ENV = "HUNTER_ISSUE_AGENT_EXECUTION_BRANCH"
REPOSITORY_CHECKOUT_ENV = "HUNTER_ISSUE_AGENT_REPO_DIR"
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
    completed_at TEXT
)
"""
_STATE_CLAIMED = "CLAIMED"
_STATE_DISPATCHED = "DISPATCHED"
_STATE_COMPLETED = "COMPLETED"


class IssueAgentExecutionError(RuntimeError):
    """Raised when the governed Issue execution path cannot proceed safely."""


class IssueAgentAuthorizationError(IssueAgentExecutionError):
    """Raised when an authorization document is malformed or not authorized here."""


class IssueAgentReplayError(IssueAgentExecutionError):
    """Raised when an authorization was already claimed by some earlier execution."""


class IssueAgentConfigurationError(IssueAgentExecutionError):
    """Raised when required operational configuration is absent or malformed."""


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


@dataclass(frozen=True, slots=True)
class IssueAgentAuthorization:
    """One parsed ``hunter-issue-agent-authorization-v1`` document.

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
    def derived_authorization_id(self) -> str:
        """Recompute the trigger's deterministic identity over the exact claims."""
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
        """Parse one bounded exact-schema authorization document or fail closed."""
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
            "issue_body": authorization.issue_body,
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
    if not authorization.issue_body.strip():
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
        content=authorization.issue_body,
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
class IssueAgentLedgerEntry:
    """The durable execution-ownership row for one authorization."""

    authorization_id: str
    authorization_digest: str
    state: str
    document_id: str | None
    build_record_id: str | None
    envelope_id: str | None
    handoff_document: str | None


class IssueAgentExecutionLedger:
    """Durable, restart-surviving execution ownership for Issue authorizations.

    Ownership is claimed before any execution work happens and is never released
    on failure. A crash between the claim and the dispatch therefore leaves a row
    that refuses the next attempt, which is the fail-closed choice: a duplicate
    execution can push commits, while a refused retry cannot.
    """

    __slots__ = ("_path",)

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as connection:
            connection.execute(_LEDGER_SCHEMA)
            connection.commit()

    @property
    def path(self) -> Path:
        return self._path

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
        )

    def claim(self, authorization: IssueAgentAuthorization, *, claimed_at: datetime) -> None:
        """Take durable ownership of one authorization or refuse the execution."""
        moment = _aware_utc("ledger claim time", claimed_at).isoformat()
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
                    "(authorization_id, authorization_digest, state, claimed_at) VALUES (?, ?, ?, ?)",
                    (authorization.authorization_id, authorization.content_digest, _STATE_CLAIMED, moment),
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
        dispatched_at: datetime,
    ) -> None:
        """Record the exact handoff durably *before* it is handed to the runtime."""
        moment = _aware_utc("ledger dispatch time", dispatched_at).isoformat()
        self._advance(
            authorization,
            sql=(
                f"UPDATE {_LEDGER_TABLE} SET state = ?, document_id = ?, build_record_id = ?, "
                "envelope_id = ?, handoff_document = ?, dispatched_at = ? "
                "WHERE authorization_id = ? AND authorization_digest = ? AND state = ?"
            ),
            parameters=(
                _STATE_DISPATCHED,
                document_id,
                build_record_id,
                envelope_id,
                handoff_document,
                moment,
                authorization.authorization_id,
                authorization.content_digest,
                _STATE_CLAIMED,
            ),
            failure="ledger dispatch state is not the exact claimed authorization",
        )

    def complete(self, authorization: IssueAgentAuthorization, *, completed_at: datetime) -> None:
        """Mark one authorization finished after the runtime returned a receipt."""
        moment = _aware_utc("ledger completion time", completed_at).isoformat()
        self._advance(
            authorization,
            sql=(
                f"UPDATE {_LEDGER_TABLE} SET state = ?, completed_at = ? "
                "WHERE authorization_id = ? AND authorization_digest = ? AND state = ?"
            ),
            parameters=(
                _STATE_COMPLETED,
                moment,
                authorization.authorization_id,
                authorization.content_digest,
                _STATE_DISPATCHED,
            ),
            failure="ledger completion state is not the exact dispatched authorization",
        )

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
    """The existing fallback runtime seam; the handoff is passed unchanged."""

    def dispatch(self, document: str | bytes) -> AgentFallbackRuntimeReceipt: ...


@dataclass(frozen=True, slots=True)
class IssueAgentExecutionConfiguration:
    """Operational configuration captured once at trusted bootstrap.

    Nothing here is reachable from Issue text, and nothing here is re-read from
    the environment later: a mid-run environment mutation cannot move the
    execution to another repository, branch, checkout or authority database.
    """

    repository: str
    owner_login: str
    evidence_database: Path
    execution_branch: str
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
            execution_branch=_required_text(EXECUTION_BRANCH_ENV, source.get(EXECUTION_BRANCH_ENV)),
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


class GovernedIssueAgentExecutionService:
    """The Issue #390 production composition root.

    One method, one authorization, one execution. It composes existing
    authorities in a fixed order and adds exactly one thing of its own: durable
    execution ownership, taken before work begins and advanced across the
    dispatch boundary.
    """

    __slots__ = ("_configuration", "_ledger", "_machine", "_boundary", "_fallback", "_verifier", "_clock")

    def __init__(
        self,
        *,
        configuration: IssueAgentExecutionConfiguration,
        repository: EvidenceIntelligenceRepository,
        source_handling_resolver: ProductionSourceHandlingAuthorityResolver,
        ledger: IssueAgentExecutionLedger,
        fallback: IssueAgentFallbackRuntime,
        verifier: PromptAutomationVerifier,
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
        if not callable(getattr(fallback, "dispatch", None)):
            raise IssueAgentConfigurationError("the composition root requires the existing fallback runtime seam")
        self._configuration = configuration
        self._ledger = ledger
        self._fallback = fallback
        self._verifier = verifier
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
            fallback=OperationalAgentFallbackRuntime(
                repo_dir=configuration.repository_checkout,
                branch=configuration.execution_branch,
                environ=source,
            ),
            verifier=PromptAutomationVerifier.from_environment(environ=source),
            clock=clock,
        )

    def execute(self, document: str | bytes) -> IssueAgentExecutionReceipt:
        """Run one authorized Issue through the existing governed runtime."""
        authorization = IssueAgentAuthorization.from_json(document)
        if authorization.repository != self._configuration.repository:
            raise IssueAgentAuthorizationError("authorization names a different repository than this deployment")
        if authorization.authorized_by != self._configuration.owner_login:
            raise IssueAgentAuthorizationError("only the configured repository owner may authorize execution")

        # Deterministic mapping is pure and reaches nothing durable or external,
        # so it is done before ownership is taken. A document that could never
        # execute does not burn its own authorization identity, and the claim
        # still precedes every step that can actually run something.
        reference = issue_agent_intake_reference(authorization)
        document_id = evidence_document_id(reference)
        request = issue_agent_task_request(authorization)
        if request.document_id != document_id:
            raise IssueAgentExecutionError("Issue task request does not bind the ingested document identity")

        self._ledger.claim(authorization, claimed_at=self._clock.now())

        self._boundary.ingest(
            reference,
            processing_run_id=authorization.authorization_id,
            processed_at=_aware_utc("Issue execution intake time", self._clock.now()),
        )

        compiled = self._machine.compile_task(request)
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
            dispatched_at=self._clock.now(),
        )

        receipt = self._fallback.dispatch(handoff_document)
        if not isinstance(receipt, AgentFallbackRuntimeReceipt):
            raise IssueAgentExecutionError("fallback runtime did not return a canonical execution receipt")
        self._ledger.complete(authorization, completed_at=self._clock.now())
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
    "ISSUE_AGENT_AUTHORIZATION_LABEL",
    "ISSUE_AGENT_AUTHORIZATION_SCHEMA_VERSION",
    "ISSUE_AGENT_EXECUTION_RECEIPT_SCHEMA_VERSION",
    "ISSUE_AGENT_PROFILE_REGISTRY",
    "ISSUE_AGENT_ROUTE_REGISTRY",
    "ISSUE_AGENT_TASK_KEY",
    "IssueAgentAuthorization",
    "IssueAgentAuthorizationError",
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
    "SourceHandlingBlockedError",
    "build_production_source_handling_resolver",
    "issue_agent_document_id",
    "issue_agent_intake_reference",
    "issue_agent_task_request",
    "issue_agent_task_text",
]
