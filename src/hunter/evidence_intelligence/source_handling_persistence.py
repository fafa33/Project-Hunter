"""Production ADR 0036 Source Handling authority persistence and resolver seam.

The in-memory ``AuthorityStore`` remains the deterministic test double.  This
module is the production path: it owns the publication capability, persists an
append-only linear authority history in the Evidence Intelligence SQLite
database, consumes signed authorizations transactionally, and exposes only a
read-only view to consumers.
"""

from __future__ import annotations

import contextlib
import copy
import hashlib
import json
import secrets
import sqlite3
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

from hunter.evidence_intelligence.intake import (
    EvidenceIntakeReference,
    EvidenceIntakeResult,
    EvidenceIntelligenceIntakeService,
    PreparedEvidenceIntake,
    evidence_document_id,
)
from hunter.evidence_intelligence.pre_model import (
    EvidencePreModelSourceHandlingAuthority,
    resolve_pre_model_source_handling,
)
from hunter.evidence_intelligence.source_handling import (
    AUTHORITY_COMPONENT_ID,
    GENESIS_RULE_ID,
    PublicationAuthorization,
    SourceHandlingAuthorityReadView,
    SourceHandlingBlockedError,
    _fact_has_restriction,
    _released_fact_restrictions,
    _supersedes_id,
    canonical_publication_digest,
    publication_authorization,
    resolve_canonical_head,
    strict_known_eligible,
    strict_known_head,
    validate_durable_payload,
    validate_permission_evidence,
    verify_publication,
)
from hunter.execution import Clock, SystemClock

SOURCE_HANDLING_FAMILIES = frozenset({"FACT", "POLICY", "FIELD_CATEGORY_REGISTRY", "AUTHORIZATION_RULE"})
SOURCE_HANDLING_RULE_SCOPE = "SOURCE_HANDLING"
SOURCE_HANDLING_RECORD_SCHEMA_VERSION = "source-handling-authority-record-v1"
SOURCE_HANDLING_AUTHORIZATION_SCHEMA_VERSION = "source-handling-publication-authorization-v1"
SOURCE_HANDLING_OPERATOR_ROOT_SCHEMA_VERSION = "source-handling-operator-root-v1"
SOURCE_HANDLING_HISTORY_COMMITMENT_SCHEMA_VERSION = "source-handling-history-commitment-v1"

ProvenanceResolver = Callable[[str, str, datetime], Mapping[str, Any] | None]
_PUBLICATION_CAPABILITY_SENTINEL = object()
_GENESIS_CAPABILITY_SENTINEL = object()


class SourceHandlingPublicationCapability:
    """Opaque capability accepted only by its bound repository."""

    def __init__(self, sentinel: object) -> None:
        if sentinel is not _PUBLICATION_CAPABILITY_SENTINEL:
            raise SourceHandlingBlockedError("publication capability is service-bootstrap only")


class _SourceHandlingGenesisBootstrapCapability:
    """Distinct operator-bootstrap capability; never accepted for runtime publication."""

    def __init__(self, sentinel: object) -> None:
        if sentinel is not _GENESIS_CAPABILITY_SENTINEL:
            raise SourceHandlingBlockedError("genesis capability is operator-bootstrap only")


@dataclass(frozen=True)
class SourceHandlingPublicationResult:
    record_id: str
    family: str
    scope: str
    admission_time: datetime


@dataclass(frozen=True)
class SourceHandlingOperatorRoot:
    """Independently provisioned trust material for one authority database."""

    genesis_rule_sha256: str
    verification_key_sha256: str

    def __post_init__(self) -> None:
        _require_sha256("operator genesis digest", self.genesis_rule_sha256)
        _require_sha256("operator verification-key fingerprint", self.verification_key_sha256)


class SourceHandlingAuthorityRepository:
    """Capability-bound SQLite writer for the ADR 0036 authority history."""

    def __init__(
        self,
        path: str | Path,
        *,
        verification_public_key: bytes,
        operator_root: SourceHandlingOperatorRoot,
        record_integrity_signer: Callable[[bytes], bytes],
        provenance_resolver: ProvenanceResolver,
        clock: Clock | None = None,
    ) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._verification_public_key_bytes = _load_public_key(verification_public_key).public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
        if not isinstance(operator_root, SourceHandlingOperatorRoot):
            raise SourceHandlingBlockedError("operator-provisioned Source Handling root is required")
        if hashlib.sha256(self._verification_public_key_bytes).hexdigest() != operator_root.verification_key_sha256:
            raise SourceHandlingBlockedError("Source Handling verification key does not match operator root")
        if not callable(record_integrity_signer):
            raise SourceHandlingBlockedError("Source Handling record-integrity signer is required")
        self._operator_root = operator_root
        self._record_integrity_signer = record_integrity_signer
        if not callable(provenance_resolver):
            raise SourceHandlingBlockedError("canonical provenance resolver is required")
        self._provenance_resolver = provenance_resolver
        self._clock = clock or SystemClock()
        self._capability: SourceHandlingPublicationCapability | None = None
        self._genesis_capability: _SourceHandlingGenesisBootstrapCapability | None = None
        self._initialize()

    def _install_capability(self, capability: SourceHandlingPublicationCapability) -> None:
        if self._capability is not None and self._capability is not capability:
            raise SourceHandlingBlockedError("publication capability is immutable once installed")
        self._capability = capability

    def _install_genesis_capability(self, capability: _SourceHandlingGenesisBootstrapCapability) -> None:
        if self._genesis_capability is not None and self._genesis_capability is not capability:
            raise SourceHandlingBlockedError("genesis capability is immutable once installed")
        self._genesis_capability = capability

    def _require_capability(self, capability: SourceHandlingPublicationCapability) -> None:
        if self._capability is None or capability is not self._capability:
            raise SourceHandlingBlockedError("source handling publication capability required")

    def _require_genesis_capability(self, capability: _SourceHandlingGenesisBootstrapCapability) -> None:
        if self._genesis_capability is None or capability is not self._genesis_capability:
            raise SourceHandlingBlockedError("operator genesis bootstrap capability required")

    def direct_write(self, *args: object, **kwargs: object) -> None:
        del args, kwargs
        raise SourceHandlingBlockedError("direct repository authority writes are forbidden")

    def read_view(self) -> SqliteSourceHandlingAuthorityReadView:
        return SqliteSourceHandlingAuthorityReadView(
            self.path,
            verification_public_key=self._verification_public_key_bytes,
            operator_root=self._operator_root,
            provenance_resolver=self._provenance_resolver,
        )

    def _register_authorization(
        self,
        capability: SourceHandlingPublicationCapability,
        authorization: PublicationAuthorization,
    ) -> None:
        self._require_capability(capability)
        _verify_authorization_signature(authorization, self._verification_public_key_bytes)
        claims_json = _canonical_json(_authorization_claims(authorization))
        issued_at = _aware_utc("authorization issued_at", self._clock.now())
        with self._transaction() as connection:
            existing = connection.execute(
                "SELECT claims_json, issuer_signature FROM source_handling_publication_authorizations "
                "WHERE authorization_id = ?",
                (authorization.authorization_id,),
            ).fetchone()
            if existing is not None:
                raise SourceHandlingBlockedError("publication authorization identity is immutable")
            connection.execute(
                """
                INSERT INTO source_handling_publication_authorizations (
                    authorization_id, claims_json, issuer_signature, issued_at,
                    consumed_at, consumed_record_id
                ) VALUES (?, ?, ?, ?, NULL, NULL)
                """,
                (authorization.authorization_id, claims_json, authorization.issuer_signature, _time_text(issued_at)),
            )

    def _publish(
        self,
        capability: SourceHandlingPublicationCapability,
        *,
        family: str,
        scope: str,
        expected_current_head_id: str | None,
        payload: Mapping[str, Any],
        authorization: PublicationAuthorization,
    ) -> SourceHandlingPublicationResult:
        self._require_capability(capability)
        _require_family_scope(family, scope)
        if family == "AUTHORIZATION_RULE" and expected_current_head_id is None:
            raise SourceHandlingBlockedError("authorization-rule genesis requires operator bootstrap")
        normalized_payload = _plain_mapping(payload)
        _validate_payload_shape(family, scope, normalized_payload)
        _verify_authorization_signature(authorization, self._verification_public_key_bytes)
        verify_publication(authorization, family, scope, normalized_payload)
        _validate_authorization_payload_times(authorization, normalized_payload)
        admission_time = _aware_utc("admission_time", self._clock.now())
        payload_json = _canonical_json(normalized_payload)
        payload_digest = hashlib.sha256(payload_json.encode("utf-8")).hexdigest()
        supersedes_id = _supersedes_id(normalized_payload)
        if family == "AUTHORIZATION_RULE" and authorization.authorization_rule_id == payload_digest:
            raise SourceHandlingBlockedError("an authorization rule cannot authorize itself")
        integrity_signature = self._sign_record_integrity(
            record_id=payload_digest,
            family=family,
            scope=scope,
            supersedes_record_id=supersedes_id,
            effective_from=_time_text(_payload_time(normalized_payload, "effective_from")),
            recorded_at=_time_text(_payload_time(normalized_payload, "recorded_at")),
            known_at=_time_text(_payload_time(normalized_payload, "known_at")),
            admission_time=_time_text(admission_time),
            payload_sha256=payload_digest,
            authorization_id=authorization.authorization_id,
            schema_version=SOURCE_HANDLING_RECORD_SCHEMA_VERSION,
        )

        with self._transaction() as connection:
            current_row = connection.execute(
                "SELECT current_record_id, revision FROM source_handling_canonical_keys "
                "WHERE family = ? AND scope = ?",
                (family, scope),
            ).fetchone()
            current_id = str(current_row["current_record_id"]) if current_row is not None else None
            if current_id != expected_current_head_id:
                raise SourceHandlingBlockedError("canonical authority head changed; re-resolution required")
            if current_id is None:
                if supersedes_id is not None:
                    raise SourceHandlingBlockedError("genesis publication cannot supersede a record")
                expected_predecessors: tuple[str, ...] = ()
            else:
                if supersedes_id != current_id:
                    raise SourceHandlingBlockedError("successor must supersede the exact canonical head")
                expected_predecessors = (current_id,)
            if authorization.predecessor_ids != expected_predecessors:
                raise SourceHandlingBlockedError("authorization predecessor binding is stale")

            auth_row = connection.execute(
                "SELECT * FROM source_handling_publication_authorizations WHERE authorization_id = ?",
                (authorization.authorization_id,),
            ).fetchone()
            if auth_row is None:
                raise SourceHandlingBlockedError(
                    "publication authorization was not issued by source handling authority"
                )
            if auth_row["consumed_at"] is not None:
                raise SourceHandlingBlockedError("publication authorization has already been consumed")
            stored_authorization = _authorization_from_storage(auth_row)
            if stored_authorization != authorization:
                raise SourceHandlingBlockedError("publication authorization differs from the issued exact claims")
            _validate_authorization_window(authorization, admission_time)

            rule = _strict_known_rule(
                connection,
                authorization.authorization_rule_id,
                authorization.known_at,
                verification_public_key_bytes=self._verification_public_key_bytes,
                operator_root=self._operator_root,
            )
            requested_change, released_restrictions = _publication_change(
                connection,
                family=family,
                scope=scope,
                current_id=current_id,
                candidate=normalized_payload,
            )
            if authorization.released_restrictions != released_restrictions:
                raise SourceHandlingBlockedError("released restrictions do not match candidate history")
            validate_permission_evidence(
                evidence_strength=_required_text("evidence_strength", authorization.evidence_strength),
                evidence_method=_required_text("evidence_method", authorization.evidence_method),
                verifier_type=_required_text("verifier_type", authorization.verifier_type),
                requested_change=requested_change,
                authorization_rule=rule,
                released_restrictions=authorization.released_restrictions,
            )
            _validate_authorization_provenance(
                authorization,
                cutoff=authorization.known_at,
                resolver=self._provenance_resolver,
            )

            last_admission = connection.execute(
                "SELECT MAX(admission_time) FROM source_handling_authority_records"
            ).fetchone()[0]
            if last_admission is not None and admission_time < _parse_time(str(last_admission)):
                raise SourceHandlingBlockedError("repository admission clock moved backwards")

            consumed = connection.execute(
                """
                UPDATE source_handling_publication_authorizations
                SET consumed_at = ?, consumed_record_id = ?
                WHERE authorization_id = ? AND consumed_at IS NULL
                """,
                (_time_text(admission_time), payload_digest, authorization.authorization_id),
            )
            if consumed.rowcount != 1:
                raise SourceHandlingBlockedError("publication authorization replay detected")

            try:
                connection.execute(
                    """
                    INSERT INTO source_handling_authority_records (
                        record_id, family, scope, supersedes_record_id,
                        effective_from, recorded_at, known_at, admission_time,
                        payload_sha256, payload_json, authorization_id, schema_version,
                        integrity_signature
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        payload_digest,
                        family,
                        scope,
                        supersedes_id,
                        _time_text(_payload_time(normalized_payload, "effective_from")),
                        _time_text(_payload_time(normalized_payload, "recorded_at")),
                        _time_text(_payload_time(normalized_payload, "known_at")),
                        _time_text(admission_time),
                        payload_digest,
                        payload_json,
                        authorization.authorization_id,
                        SOURCE_HANDLING_RECORD_SCHEMA_VERSION,
                        integrity_signature,
                    ),
                )
            except sqlite3.IntegrityError as error:
                raise SourceHandlingBlockedError("authority record identity is immutable") from error

            if current_row is None:
                canonical_revision = 1
                try:
                    connection.execute(
                        """
                        INSERT INTO source_handling_canonical_keys (family, scope, current_record_id, revision)
                        VALUES (?, ?, ?, 1)
                        """,
                        (family, scope, payload_digest),
                    )
                except sqlite3.IntegrityError as error:
                    raise SourceHandlingBlockedError(
                        "canonical authority head changed; re-resolution required"
                    ) from error
            else:
                canonical_revision = int(current_row["revision"]) + 1
                updated = connection.execute(
                    """
                    UPDATE source_handling_canonical_keys
                    SET current_record_id = ?, revision = revision + 1
                    WHERE family = ? AND scope = ? AND current_record_id = ? AND revision = ?
                    """,
                    (payload_digest, family, scope, current_id, int(current_row["revision"])),
                )
                if updated.rowcount != 1:
                    raise SourceHandlingBlockedError("canonical authority head changed; re-resolution required")

            self._append_history_commitment(
                connection,
                record_id=payload_digest,
                family=family,
                scope=scope,
                canonical_revision=canonical_revision,
                record_integrity_signature=integrity_signature,
                authorization_id=authorization.authorization_id,
                authorization_consumed_at=_time_text(admission_time),
                authorization_consumed_record_id=payload_digest,
            )

        return SourceHandlingPublicationResult(payload_digest, family, scope, admission_time)

    def _publish_genesis_rule(
        self,
        capability: _SourceHandlingGenesisBootstrapCapability,
        rule: Mapping[str, Any],
    ) -> SourceHandlingPublicationResult:
        self._require_genesis_capability(capability)
        normalized_rule = _plain_mapping(rule)
        if (
            hashlib.sha256(_canonical_json(normalized_rule).encode("utf-8")).hexdigest()
            != self._operator_root.genesis_rule_sha256
        ):
            raise SourceHandlingBlockedError("authorization-rule bootstrap digest mismatch")
        if normalized_rule.get("authorization_rule_id") != GENESIS_RULE_ID:
            raise SourceHandlingBlockedError("unexpected authorization-rule bootstrap identity")
        payload = {**normalized_rule, "scope": SOURCE_HANDLING_RULE_SCOPE}
        _validate_payload_shape("AUTHORIZATION_RULE", SOURCE_HANDLING_RULE_SCOPE, payload)
        payload_json = _canonical_json(payload)
        record_id = hashlib.sha256(payload_json.encode("utf-8")).hexdigest()
        admission_time = _aware_utc("admission_time", self._clock.now())
        integrity_signature = self._sign_record_integrity(
            record_id=record_id,
            family="AUTHORIZATION_RULE",
            scope=SOURCE_HANDLING_RULE_SCOPE,
            supersedes_record_id=None,
            effective_from=_time_text(_payload_time(payload, "effective_from")),
            recorded_at=_time_text(_payload_time(payload, "recorded_at")),
            known_at=_time_text(_payload_time(payload, "known_at")),
            admission_time=_time_text(admission_time),
            payload_sha256=record_id,
            authorization_id=None,
            schema_version=SOURCE_HANDLING_RECORD_SCHEMA_VERSION,
        )

        with self._transaction() as connection:
            history_count = int(
                connection.execute(
                    "SELECT COUNT(*) FROM source_handling_authority_records WHERE family = 'AUTHORIZATION_RULE'"
                ).fetchone()[0]
            )
            if history_count:
                raise SourceHandlingBlockedError("authorization-rule bootstrap requires empty history")
            connection.execute(
                """
                INSERT INTO source_handling_authority_records (
                    record_id, family, scope, supersedes_record_id,
                    effective_from, recorded_at, known_at, admission_time,
                    payload_sha256, payload_json, authorization_id, schema_version,
                    integrity_signature
                ) VALUES (?, 'AUTHORIZATION_RULE', ?, NULL, ?, ?, ?, ?, ?, ?, NULL, ?, ?)
                """,
                (
                    record_id,
                    SOURCE_HANDLING_RULE_SCOPE,
                    _time_text(_payload_time(payload, "effective_from")),
                    _time_text(_payload_time(payload, "recorded_at")),
                    _time_text(_payload_time(payload, "known_at")),
                    _time_text(admission_time),
                    record_id,
                    payload_json,
                    SOURCE_HANDLING_RECORD_SCHEMA_VERSION,
                    integrity_signature,
                ),
            )
            connection.execute(
                """
                INSERT INTO source_handling_canonical_keys (family, scope, current_record_id, revision)
                VALUES ('AUTHORIZATION_RULE', ?, ?, 1)
                """,
                (SOURCE_HANDLING_RULE_SCOPE, record_id),
            )
            self._append_history_commitment(
                connection,
                record_id=record_id,
                family="AUTHORIZATION_RULE",
                scope=SOURCE_HANDLING_RULE_SCOPE,
                canonical_revision=1,
                record_integrity_signature=integrity_signature,
                authorization_id=None,
                authorization_consumed_at=None,
                authorization_consumed_record_id=None,
            )
        return SourceHandlingPublicationResult(
            record_id, "AUTHORIZATION_RULE", SOURCE_HANDLING_RULE_SCOPE, admission_time
        )

    def _sign_record_integrity(self, **claims: str | None) -> str:
        message = _canonical_json(_record_integrity_claims(**claims)).encode("utf-8")
        try:
            signature = self._record_integrity_signer(message)
        except Exception as error:
            raise SourceHandlingBlockedError("Source Handling record integrity signing failed") from error
        if not isinstance(signature, bytes) or len(signature) != 64:
            raise SourceHandlingBlockedError("Source Handling record integrity signature is malformed")
        try:
            _load_public_key(self._verification_public_key_bytes).verify(signature, message)
        except InvalidSignature as error:
            raise SourceHandlingBlockedError("Source Handling record integrity signature is invalid") from error
        return signature.hex()

    def _append_history_commitment(
        self,
        connection: sqlite3.Connection,
        *,
        record_id: str,
        family: str,
        scope: str,
        canonical_revision: int,
        record_integrity_signature: str,
        authorization_id: str | None,
        authorization_consumed_at: str | None,
        authorization_consumed_record_id: str | None,
    ) -> None:
        previous = connection.execute(
            "SELECT sequence, commitment_sha256 FROM source_handling_history_commitments "
            "ORDER BY sequence DESC LIMIT 1"
        ).fetchone()
        sequence = int(previous["sequence"]) + 1 if previous is not None else 1
        previous_commitment_sha256 = str(previous["commitment_sha256"]) if previous is not None else None
        claims = _history_commitment_claims(
            sequence=sequence,
            previous_commitment_sha256=previous_commitment_sha256,
            record_id=record_id,
            family=family,
            scope=scope,
            canonical_head_id=record_id,
            canonical_revision=canonical_revision,
            record_integrity_signature=record_integrity_signature,
            authorization_id=authorization_id,
            authorization_consumed_at=authorization_consumed_at,
            authorization_consumed_record_id=authorization_consumed_record_id,
        )
        claims_json = _canonical_json(claims)
        commitment_sha256 = hashlib.sha256(claims_json.encode("utf-8")).hexdigest()
        try:
            signature = self._record_integrity_signer(claims_json.encode("utf-8"))
        except Exception as error:
            raise SourceHandlingBlockedError("Source Handling history commitment signing failed") from error
        if not isinstance(signature, bytes) or len(signature) != 64:
            raise SourceHandlingBlockedError("Source Handling history commitment signature is malformed")
        try:
            _load_public_key(self._verification_public_key_bytes).verify(signature, claims_json.encode("utf-8"))
        except InvalidSignature as error:
            raise SourceHandlingBlockedError("Source Handling history commitment signature is invalid") from error
        connection.execute(
            """
            INSERT INTO source_handling_history_commitments (
                sequence, commitment_sha256, previous_commitment_sha256,
                claims_json, issuer_signature
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (sequence, commitment_sha256, previous_commitment_sha256, claims_json, signature.hex()),
        )

    def authorization_consumed(self, authorization_id: str) -> bool:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT consumed_at FROM source_handling_publication_authorizations WHERE authorization_id = ?",
                (authorization_id,),
            ).fetchone()
            return row is not None and row["consumed_at"] is not None

    def _initialize(self) -> None:
        with self._connect(verify_operator_root=False, verify_history=False) as connection:
            connection.executescript("""
                CREATE TABLE IF NOT EXISTS source_handling_operator_root (
                    singleton_id TEXT PRIMARY KEY CHECK (singleton_id = 'SOURCE_HANDLING'),
                    genesis_rule_sha256 TEXT NOT NULL,
                    verification_key_sha256 TEXT NOT NULL,
                    schema_version TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS source_handling_publication_authorizations (
                    authorization_id TEXT PRIMARY KEY,
                    claims_json TEXT NOT NULL,
                    issuer_signature TEXT NOT NULL,
                    issued_at TEXT NOT NULL,
                    consumed_at TEXT,
                    consumed_record_id TEXT
                );
                CREATE TABLE IF NOT EXISTS source_handling_authority_records (
                    record_id TEXT PRIMARY KEY,
                    family TEXT NOT NULL,
                    scope TEXT NOT NULL,
                    supersedes_record_id TEXT,
                    effective_from TEXT NOT NULL,
                    recorded_at TEXT NOT NULL,
                    known_at TEXT NOT NULL,
                    admission_time TEXT NOT NULL,
                    payload_sha256 TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    authorization_id TEXT,
                    schema_version TEXT NOT NULL,
                    integrity_signature TEXT,
                    FOREIGN KEY (supersedes_record_id)
                        REFERENCES source_handling_authority_records(record_id),
                    FOREIGN KEY (authorization_id)
                        REFERENCES source_handling_publication_authorizations(authorization_id)
                );
                CREATE INDEX IF NOT EXISTS source_handling_records_scope_cutoff_idx
                    ON source_handling_authority_records(
                        family, scope, effective_from, recorded_at, known_at, admission_time
                    );
                CREATE TABLE IF NOT EXISTS source_handling_canonical_keys (
                    family TEXT NOT NULL,
                    scope TEXT NOT NULL,
                    current_record_id TEXT NOT NULL UNIQUE,
                    revision INTEGER NOT NULL CHECK (revision > 0),
                    PRIMARY KEY (family, scope),
                    FOREIGN KEY (current_record_id)
                        REFERENCES source_handling_authority_records(record_id)
                );
                CREATE TABLE IF NOT EXISTS source_handling_history_commitments (
                    sequence INTEGER PRIMARY KEY CHECK (sequence > 0),
                    commitment_sha256 TEXT NOT NULL UNIQUE,
                    previous_commitment_sha256 TEXT,
                    claims_json TEXT NOT NULL,
                    issuer_signature TEXT NOT NULL,
                    FOREIGN KEY (previous_commitment_sha256)
                        REFERENCES source_handling_history_commitments(commitment_sha256)
                );
                CREATE TRIGGER IF NOT EXISTS source_handling_history_commitments_no_update
                BEFORE UPDATE ON source_handling_history_commitments
                BEGIN
                    SELECT RAISE(ABORT, 'Source Handling history commitments are append-only');
                END;
                CREATE TRIGGER IF NOT EXISTS source_handling_history_commitments_no_delete
                BEFORE DELETE ON source_handling_history_commitments
                BEGIN
                    SELECT RAISE(ABORT, 'Source Handling history commitments are append-only');
                END;
                """)
            columns = {
                str(row["name"])
                for row in connection.execute("PRAGMA table_info(source_handling_authority_records)").fetchall()
            }
            if "integrity_signature" not in columns:
                connection.execute("ALTER TABLE source_handling_authority_records ADD COLUMN integrity_signature TEXT")
            pinned = connection.execute(
                "SELECT * FROM source_handling_operator_root WHERE singleton_id = 'SOURCE_HANDLING'"
            ).fetchone()
            if pinned is None:
                existing_history = int(
                    connection.execute("SELECT COUNT(*) FROM source_handling_authority_records").fetchone()[0]
                )
                if existing_history:
                    raise SourceHandlingBlockedError(
                        "operator root cannot be retroactively attached to existing Source Handling history"
                    )
                connection.execute(
                    """
                    INSERT INTO source_handling_operator_root (
                        singleton_id, genesis_rule_sha256, verification_key_sha256, schema_version
                    ) VALUES ('SOURCE_HANDLING', ?, ?, ?)
                    """,
                    (
                        self._operator_root.genesis_rule_sha256,
                        self._operator_root.verification_key_sha256,
                        SOURCE_HANDLING_OPERATOR_ROOT_SCHEMA_VERSION,
                    ),
                )
            else:
                _verify_operator_root_row(pinned, self._operator_root)
            _verify_authenticated_history(
                connection,
                verification_public_key_bytes=self._verification_public_key_bytes,
                operator_root=self._operator_root,
            )
            connection.commit()

    @contextlib.contextmanager
    def _connect(
        self,
        *,
        verify_operator_root: bool = True,
        verify_history: bool = True,
    ) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=30.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        try:
            if verify_operator_root:
                row = connection.execute(
                    "SELECT * FROM source_handling_operator_root WHERE singleton_id = 'SOURCE_HANDLING'"
                ).fetchone()
                if row is None:
                    raise SourceHandlingBlockedError("pinned Source Handling operator root is unavailable")
                _verify_operator_root_row(row, self._operator_root)
            if verify_history:
                _verify_authenticated_history(
                    connection,
                    verification_public_key_bytes=self._verification_public_key_bytes,
                    operator_root=self._operator_root,
                )
            yield connection
        finally:
            connection.close()

    @contextlib.contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                yield connection
            except Exception:
                connection.rollback()
                raise
            else:
                connection.commit()


class SqliteSourceHandlingAuthorityReadView(SourceHandlingAuthorityReadView):
    """Read-only, tamper-verifying view over durable Source Handling authority."""

    def __init__(
        self,
        path: str | Path,
        *,
        verification_public_key: bytes,
        operator_root: SourceHandlingOperatorRoot,
        provenance_resolver: ProvenanceResolver,
    ) -> None:
        self._path = Path(path)
        self._verification_public_key_bytes = _load_public_key(verification_public_key).public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
        if not isinstance(operator_root, SourceHandlingOperatorRoot):
            raise SourceHandlingBlockedError("operator-provisioned Source Handling root is required")
        if hashlib.sha256(self._verification_public_key_bytes).hexdigest() != operator_root.verification_key_sha256:
            raise SourceHandlingBlockedError("Source Handling verification key does not match operator root")
        self._operator_root = operator_root
        if not callable(provenance_resolver):
            raise SourceHandlingBlockedError("canonical provenance resolver is required")
        self._provenance_resolver = provenance_resolver
        with self._connect():
            pass

    def canonical_records(self, family: str, scope: str) -> tuple[dict[str, Any], ...]:
        _require_family_scope(family, scope)
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM source_handling_authority_records
                WHERE family = ? AND scope = ?
                ORDER BY admission_time, record_id
                """,
                (family, scope),
            ).fetchall()
            if not rows:
                return ()
            records = tuple(self._decode_record(connection, row) for row in rows)
            head_row = connection.execute(
                "SELECT current_record_id FROM source_handling_canonical_keys WHERE family = ? AND scope = ?",
                (family, scope),
            ).fetchone()
            if head_row is None:
                raise SourceHandlingBlockedError("canonical authority key is missing")
            _validate_complete_chain(records, expected_head_id=str(head_row["current_record_id"]))
            return records

    def canonical_record_by_id(self, family: str, record_id: str) -> dict[str, Any] | None:
        _require_family_scope(family, "identity-lookup")
        if not record_id:
            raise SourceHandlingBlockedError("authority record identity is required")
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM source_handling_authority_records WHERE family = ? AND record_id = ?",
                (family, record_id),
            ).fetchone()
            if row is not None:
                scopes = (str(row["scope"]),)
            else:
                rows = connection.execute(
                    "SELECT scope, payload_json FROM source_handling_authority_records WHERE family = ?",
                    (family,),
                ).fetchall()
                identity_field = {
                    "FIELD_CATEGORY_REGISTRY": "field_category_registry_id",
                    "AUTHORIZATION_RULE": "authorization_rule_id",
                }.get(family)
                if identity_field is None:
                    return None
                scopes = tuple(
                    str(candidate["scope"])
                    for candidate in rows
                    if isinstance((payload := json.loads(str(candidate["payload_json"]))), dict)
                    and payload.get(identity_field) == record_id
                )
                if len(scopes) > 1:
                    raise SourceHandlingBlockedError("canonical authority identity is ambiguous")
                if not scopes:
                    return None
            scope = scopes[0]
        records = self.canonical_records(family, scope)
        matches = [
            record
            for record in records
            if record.get("id") == record_id
            or record.get("field_category_registry_id") == record_id
            or record.get("authorization_rule_id") == record_id
        ]
        if len(matches) != 1:
            raise SourceHandlingBlockedError("canonical authority identity is ambiguous")
        return matches[0]

    def current_canonical_head_id(self, family: str, scope: str) -> str | None:
        _require_family_scope(family, scope)
        with self._connect() as connection:
            row = connection.execute(
                "SELECT current_record_id FROM source_handling_canonical_keys WHERE family = ? AND scope = ?",
                (family, scope),
            ).fetchone()
            return str(row["current_record_id"]) if row is not None else None

    def verify_canonical_record(
        self,
        *,
        family: str,
        scope: str,
        record: Mapping[str, Any],
        cutoff: datetime,
    ) -> None:
        record_id = record.get("id")
        if not isinstance(record_id, str) or not record_id:
            raise SourceHandlingBlockedError("authority record identity is required")
        canonical = self.canonical_record_by_id(family, record_id)
        if canonical is None or _canonical_json(canonical) != _canonical_json(record):
            raise SourceHandlingBlockedError("record was not published through canonical authority")
        if not strict_known_eligible(canonical, cutoff):
            raise SourceHandlingBlockedError("authority record was not strict-known at cutoff")
        if family == "AUTHORIZATION_RULE" and canonical.get("publication_authorization") is None:
            return
        authorization = canonical.get("publication_authorization")
        if not isinstance(authorization, PublicationAuthorization):
            raise SourceHandlingBlockedError("canonical publication authorization is missing")
        if not strict_known_eligible(_authorization_times(authorization), cutoff):
            raise SourceHandlingBlockedError("publication authorization was not strict-known at cutoff")
        if family == "AUTHORIZATION_RULE":
            predecessor_id = _supersedes_id(canonical)
            if predecessor_id is None or predecessor_id != authorization.authorization_rule_id:
                raise SourceHandlingBlockedError(
                    "successor authorization rule was not authorized by its exact predecessor"
                )
            if authorization.authorization_rule_id == record_id:
                raise SourceHandlingBlockedError("an authorization rule cannot authorize itself")
            rule = self.canonical_record_by_id("AUTHORIZATION_RULE", predecessor_id)
            if rule is None:
                raise SourceHandlingBlockedError("successor authorizing rule is unavailable")
            self.verify_canonical_record(
                family="AUTHORIZATION_RULE",
                scope=SOURCE_HANDLING_RULE_SCOPE,
                record=rule,
                cutoff=authorization.known_at,
            )
        else:
            rule = resolve_canonical_head(
                self,
                family="AUTHORIZATION_RULE",
                scope=SOURCE_HANDLING_RULE_SCOPE,
                cutoff=authorization.known_at,
            )
        if rule.get("id") != authorization.authorization_rule_id:
            raise SourceHandlingBlockedError("authorization names a stale authorization rule")
        _validate_authorization_provenance(
            authorization,
            cutoff=authorization.known_at,
            resolver=self._provenance_resolver,
        )

    def _decode_record(self, connection: sqlite3.Connection, row: sqlite3.Row) -> dict[str, Any]:
        return _decode_durable_record(
            connection,
            row,
            verification_public_key_bytes=self._verification_public_key_bytes,
            operator_root=self._operator_root,
        )

    @contextlib.contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        if not self._path.exists():
            raise SourceHandlingBlockedError("Source Handling authority database is unavailable")
        connection = sqlite3.connect(f"file:{self._path.resolve()}?mode=ro", uri=True, timeout=30.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        try:
            row = connection.execute(
                "SELECT * FROM source_handling_operator_root WHERE singleton_id = 'SOURCE_HANDLING'"
            ).fetchone()
            if row is None:
                raise SourceHandlingBlockedError("pinned Source Handling operator root is unavailable")
            _verify_operator_root_row(row, self._operator_root)
            _verify_authenticated_history(
                connection,
                verification_public_key_bytes=self._verification_public_key_bytes,
                operator_root=self._operator_root,
            )
            yield connection
        finally:
            connection.close()


class ProductionSourceHandlingAuthorityResolver:
    """Callable production resolver holding only a read-only authority view."""

    def __init__(self, view: SqliteSourceHandlingAuthorityReadView) -> None:
        if not isinstance(view, SqliteSourceHandlingAuthorityReadView):
            raise TypeError("production resolver requires a read-only SQLite authority view")
        self._view = view

    def __call__(self, document_id: str, cutoff: datetime) -> EvidencePreModelSourceHandlingAuthority:
        if not isinstance(document_id, str) or not document_id.strip():
            raise SourceHandlingBlockedError("document identity is required")
        normalized_cutoff = _aware_utc("Source Handling cutoff", cutoff)
        return EvidencePreModelSourceHandlingAuthority(
            store=self._view,
            fact_scope=document_id,
            policy_scope=f"policy:{document_id}:v1",
            cutoff=normalized_cutoff,
        )


class SourceHandlingAuthorityService:
    """Sole production issuer and publisher for all four authority families."""

    def __init__(
        self,
        path: str | Path,
        *,
        signing_private_key: bytes,
        operator_root: SourceHandlingOperatorRoot,
        provenance_resolver: ProvenanceResolver,
        clock: Clock | None = None,
    ) -> None:
        self._clock = clock or SystemClock()
        self._signing_key = _load_private_key(signing_private_key)
        public_key = self._signing_key.public_key().public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
        self._capability = SourceHandlingPublicationCapability(_PUBLICATION_CAPABILITY_SENTINEL)
        self._genesis_capability = _SourceHandlingGenesisBootstrapCapability(_GENESIS_CAPABILITY_SENTINEL)
        self._repository = SourceHandlingAuthorityRepository(
            path,
            verification_public_key=public_key,
            operator_root=operator_root,
            record_integrity_signer=self._signing_key.sign,
            provenance_resolver=provenance_resolver,
            clock=self._clock,
        )
        self._repository._install_capability(self._capability)
        self._repository._install_genesis_capability(self._genesis_capability)
        self._provenance_resolver = provenance_resolver

    @property
    def path(self) -> Path:
        return self._repository.path

    def resolver(self) -> ProductionSourceHandlingAuthorityResolver:
        return ProductionSourceHandlingAuthorityResolver(self._repository.read_view())

    def publish_genesis_rule(self, rule: Mapping[str, Any]) -> SourceHandlingPublicationResult:
        return self._repository._publish_genesis_rule(
            self._genesis_capability,
            rule,
        )

    def issue_authorization(
        self,
        *,
        publication_kind: str,
        governed_subject_scope: str,
        payload: Mapping[str, Any],
        authorization_rule_id: str,
        expected_current_head_id: str | None,
        evidence_ids: Sequence[str],
        evidence_strength: str,
        evidence_method: str,
        verifier_ids: Sequence[str],
        verifier_type: str,
        effective_from: datetime,
        recorded_at: datetime,
        known_at: datetime,
        expires_at: datetime,
        authorization_id: str | None = None,
    ) -> PublicationAuthorization:
        _require_family_scope(publication_kind, governed_subject_scope)
        normalized_payload = _plain_mapping(payload)
        _validate_payload_shape(publication_kind, governed_subject_scope, normalized_payload)
        now = _aware_utc("authorization issuance time", self._clock.now())
        normalized_expires = _aware_utc("authorization expires_at", expires_at)
        normalized_times = tuple(
            _aware_utc(name, value)
            for name, value in (
                ("effective_from", effective_from),
                ("recorded_at", recorded_at),
                ("known_at", known_at),
            )
        )
        if normalized_times[0] > normalized_times[2] or normalized_times[1] > normalized_times[2]:
            raise SourceHandlingBlockedError("authorization temporal claims are not strict-known")
        if normalized_times[2] > now or normalized_expires <= now:
            raise SourceHandlingBlockedError("authorization issuance window is invalid")
        if not evidence_ids or not verifier_ids:
            raise SourceHandlingBlockedError("authorization provenance identities are required")
        payload_times = tuple(
            _payload_time(normalized_payload, field) for field in ("effective_from", "recorded_at", "known_at")
        )
        if payload_times != normalized_times:
            raise SourceHandlingBlockedError("authorization temporal claims do not match the exact payload")
        evidence_tuple = tuple(sorted(set(_nonblank_values("evidence_ids", evidence_ids))))
        verifier_tuple = tuple(sorted(set(_nonblank_values("verifier_ids", verifier_ids))))

        view = self._repository.read_view()
        current_id = view.current_canonical_head_id(publication_kind, governed_subject_scope)
        if current_id != expected_current_head_id:
            raise SourceHandlingBlockedError("canonical authority head changed; re-resolution required")
        rule = resolve_canonical_head(
            view,
            family="AUTHORIZATION_RULE",
            scope=SOURCE_HANDLING_RULE_SCOPE,
            cutoff=normalized_times[2],
        )
        if rule.get("id") != authorization_rule_id:
            raise SourceHandlingBlockedError("authorization names a stale authorization rule")
        if view.current_canonical_head_id("AUTHORIZATION_RULE", SOURCE_HANDLING_RULE_SCOPE) != authorization_rule_id:
            raise SourceHandlingBlockedError("authorization rule is no longer the canonical head")

        requested_change, released_restrictions = _view_publication_change(
            view,
            family=publication_kind,
            scope=governed_subject_scope,
            current_id=current_id,
            candidate=normalized_payload,
        )
        validate_permission_evidence(
            evidence_strength=evidence_strength,
            evidence_method=evidence_method,
            verifier_type=verifier_type,
            requested_change=requested_change,
            authorization_rule=rule,
            released_restrictions=released_restrictions,
        )
        unsigned = publication_authorization(
            publication_kind=publication_kind,
            governed_subject_scope=governed_subject_scope,
            authorized_payload_sha256=canonical_publication_digest(
                publication_kind,
                governed_subject_scope,
                normalized_payload,
            ),
            authorization_rule_id=authorization_rule_id,
            effective_from=normalized_times[0],
            recorded_at=normalized_times[1],
            known_at=normalized_times[2],
            authorization_id=authorization_id or secrets.token_hex(32),
            authority_component_id=AUTHORITY_COMPONENT_ID,
            evidence_ids=evidence_tuple,
            evidence_strength=evidence_strength,
            evidence_method=evidence_method,
            verifier_ids=verifier_tuple,
            verifier_type=verifier_type,
            released_restrictions=released_restrictions,
            predecessor_ids=(current_id,) if current_id is not None else (),
            expires_at=normalized_expires,
        )
        _validate_authorization_provenance(unsigned, cutoff=unsigned.known_at, resolver=self._provenance_resolver)
        signature = self._signing_key.sign(_authorization_message(unsigned)).hex()
        authorization = replace(unsigned, issuer_signature=signature)
        self._repository._register_authorization(self._capability, authorization)
        return authorization

    def publish(
        self,
        *,
        family: str,
        scope: str,
        expected_current_head_id: str | None,
        payload: Mapping[str, Any],
        authorization: PublicationAuthorization,
    ) -> SourceHandlingPublicationResult:
        return self._repository._publish(
            self._capability,
            family=family,
            scope=scope,
            expected_current_head_id=expected_current_head_id,
            payload=payload,
            authorization=authorization,
        )

    def authorization_consumed(self, authorization_id: str) -> bool:
        return self._repository.authorization_consumed(authorization_id)


_CONTENT_DERIVED_INTAKE_FIELDS = frozenset(
    {
        "chunk_id",
        "content_hash",
        "document_id",
        "event_id",
        "link_id",
        "normalized_content_hash",
        "rendition_id",
        "span_id",
        "text_hash",
        "verification_id",
        "version_id",
    }
)
_LOCATOR_INTAKE_FIELDS = frozenset({"locator", "source_url"})
_SOURCE_DERIVED_TEXT_INTAKE_FIELDS = frozenset({"excerpt", "section_title", "title"})


def _issue_intake_durable_payload(
    reference: EvidenceIntakeReference,
    prepared: PreparedEvidenceIntake,
) -> dict[str, Any]:
    categorized: dict[str, dict[str, dict[str, Any]]] = {
        "content_derived_ids": {},
        "locator_urls": {},
        "source_derived_text": {},
        "intake_metadata": {},
    }
    for table, records in prepared.persisted_artifacts().items():
        for position, artifact in enumerate(records):
            artifact_key = f"{table}:{position}"
            for field, value in artifact.items():
                if field in _CONTENT_DERIVED_INTAKE_FIELDS:
                    category = "content_derived_ids"
                elif field in _LOCATOR_INTAKE_FIELDS:
                    category = "locator_urls"
                elif field in _SOURCE_DERIVED_TEXT_INTAKE_FIELDS:
                    category = "source_derived_text"
                else:
                    category = "intake_metadata"
                categorized[category].setdefault(artifact_key, {})[field] = value
    if any(not fields for fields in categorized.values()):
        raise SourceHandlingBlockedError("complete Issue Source durable artifact categorization is unavailable")
    return {"issue_content": reference.content, **categorized}


class IssueSourceTransientIntakeBoundary:
    """ADR 0036 gate that keeps raw Issue content transient until retention is allowed."""

    def __init__(
        self,
        *,
        intake: EvidenceIntelligenceIntakeService,
        resolver: ProductionSourceHandlingAuthorityResolver,
    ) -> None:
        if not isinstance(intake, EvidenceIntelligenceIntakeService):
            raise TypeError("Issue Source boundary requires EvidenceIntelligenceIntakeService")
        if not isinstance(resolver, ProductionSourceHandlingAuthorityResolver):
            raise TypeError("Issue Source boundary requires the production read-only resolver")
        self._intake = intake
        self._resolver = resolver

    def ingest(
        self,
        reference: EvidenceIntakeReference,
        *,
        processing_run_id: str,
        processed_at: datetime,
    ) -> EvidenceIntakeResult:
        cutoff = _aware_utc("Issue Source intake cutoff", processed_at)
        document_id = evidence_document_id(reference)
        resolved = resolve_pre_model_source_handling(self._resolver(document_id, cutoff))
        decision = resolved.decision
        if decision.get("retention_decision") != "ALLOW":
            raise SourceHandlingBlockedError("Issue Source retention is not allowed")
        if decision.get("deletion_lifecycle_decision") in {"DELETE", "BLOCKED"}:
            raise SourceHandlingBlockedError("Issue Source deletion lifecycle blocks durable intake")
        dispositions = decision.get("durable_dispositions")
        if not isinstance(dispositions, Mapping) or not dispositions:
            raise SourceHandlingBlockedError("Issue Source durable dispositions are unavailable")
        fact = resolved.fact_record.get("fact")
        if not isinstance(fact, Mapping):
            raise SourceHandlingBlockedError("Issue Source fact authority is unavailable")
        secret_presence = set(_string_values(fact.get("secret_presence")))
        prepared = self._intake.prepare(
            reference,
            processing_run_id=processing_run_id,
            processed_at=cutoff,
        )
        validate_durable_payload(
            decision=decision,
            registry=resolved.registry_record,
            payload=_issue_intake_durable_payload(reference, prepared),
            secret_presence=secret_presence,
        )
        return self._intake.ingest_prepared(prepared)


def _decode_durable_record(
    connection: sqlite3.Connection,
    row: sqlite3.Row,
    *,
    verification_public_key_bytes: bytes,
    operator_root: SourceHandlingOperatorRoot,
) -> dict[str, Any]:
    if row["schema_version"] != SOURCE_HANDLING_RECORD_SCHEMA_VERSION:
        raise SourceHandlingBlockedError("unknown durable Source Handling record schema")
    _verify_record_integrity(row, verification_public_key_bytes)
    payload_json = str(row["payload_json"])
    payload_digest = hashlib.sha256(payload_json.encode("utf-8")).hexdigest()
    if payload_digest != row["payload_sha256"] or payload_digest != row["record_id"]:
        raise SourceHandlingBlockedError("TAMPER_DETECTED: authority payload digest mismatch")
    try:
        payload = json.loads(payload_json)
    except json.JSONDecodeError as error:
        raise SourceHandlingBlockedError("TAMPER_DETECTED: authority payload is malformed") from error
    if not isinstance(payload, dict):
        raise SourceHandlingBlockedError("TAMPER_DETECTED: authority payload is not an object")
    family, scope = str(row["family"]), str(row["scope"])
    _validate_payload_shape(family, scope, payload)
    if family == "AUTHORIZATION_RULE" and payload.get("authorization_rule_id") == GENESIS_RULE_ID:
        operator_payload = copy.deepcopy(payload)
        operator_payload.pop("scope", None)
        if (
            hashlib.sha256(_canonical_json(operator_payload).encode("utf-8")).hexdigest()
            != operator_root.genesis_rule_sha256
        ):
            raise SourceHandlingBlockedError("TAMPER_DETECTED: genesis rule does not match operator root")
    if _supersedes_id(payload) != row["supersedes_record_id"]:
        raise SourceHandlingBlockedError("TAMPER_DETECTED: supersession index mismatch")
    for field in ("effective_from", "recorded_at", "known_at"):
        if _time_text(_payload_time(payload, field)) != row[field]:
            raise SourceHandlingBlockedError("TAMPER_DETECTED: temporal index mismatch")

    authorization: PublicationAuthorization | None = None
    authorization_id = row["authorization_id"]
    if authorization_id is not None:
        auth_row = connection.execute(
            "SELECT * FROM source_handling_publication_authorizations WHERE authorization_id = ?",
            (authorization_id,),
        ).fetchone()
        if auth_row is None:
            raise SourceHandlingBlockedError("TAMPER_DETECTED: publication authorization is missing")
        authorization = _authorization_from_storage(auth_row)
        _verify_authorization_signature(authorization, verification_public_key_bytes)
        if auth_row["consumed_at"] is None or auth_row["consumed_record_id"] != row["record_id"]:
            raise SourceHandlingBlockedError("TAMPER_DETECTED: authorization consumption mismatch")
        verify_publication(authorization, family, scope, payload)
        expected_predecessors = (str(row["supersedes_record_id"]),) if row["supersedes_record_id"] else ()
        if authorization.predecessor_ids != expected_predecessors:
            raise SourceHandlingBlockedError("TAMPER_DETECTED: authorization predecessor mismatch")
    elif family != "AUTHORIZATION_RULE" or payload.get("authorization_rule_id") != GENESIS_RULE_ID:
        raise SourceHandlingBlockedError("canonical publication authorization is missing")

    return {
        "id": str(row["record_id"]),
        **copy.deepcopy(payload),
        "publication_payload": copy.deepcopy(payload),
        "publication_authorization": authorization,
        "admission_time": _parse_time(str(row["admission_time"])),
    }


def _record_integrity_claims(
    *,
    record_id: str,
    family: str,
    scope: str,
    supersedes_record_id: str | None,
    effective_from: str,
    recorded_at: str,
    known_at: str,
    admission_time: str,
    payload_sha256: str,
    authorization_id: str | None,
    schema_version: str,
) -> dict[str, str | None]:
    return {
        "record_id": record_id,
        "family": family,
        "scope": scope,
        "supersedes_record_id": supersedes_record_id,
        "effective_from": effective_from,
        "recorded_at": recorded_at,
        "known_at": known_at,
        "admission_time": admission_time,
        "payload_sha256": payload_sha256,
        "authorization_id": authorization_id,
        "schema_version": schema_version,
    }


def _record_integrity_claims_from_row(row: sqlite3.Row) -> dict[str, str | None]:
    return _record_integrity_claims(
        record_id=str(row["record_id"]),
        family=str(row["family"]),
        scope=str(row["scope"]),
        supersedes_record_id=(str(row["supersedes_record_id"]) if row["supersedes_record_id"] is not None else None),
        effective_from=str(row["effective_from"]),
        recorded_at=str(row["recorded_at"]),
        known_at=str(row["known_at"]),
        admission_time=str(row["admission_time"]),
        payload_sha256=str(row["payload_sha256"]),
        authorization_id=(str(row["authorization_id"]) if row["authorization_id"] is not None else None),
        schema_version=str(row["schema_version"]),
    )


def _verify_record_integrity(row: sqlite3.Row, verification_public_key_bytes: bytes) -> None:
    signature = row["integrity_signature"]
    if not isinstance(signature, str) or len(signature) != 128 or signature.lower() != signature:
        raise SourceHandlingBlockedError("TAMPER_DETECTED: record integrity signature is missing or malformed")
    message = _canonical_json(_record_integrity_claims_from_row(row)).encode("utf-8")
    try:
        _load_public_key(verification_public_key_bytes).verify(bytes.fromhex(signature), message)
    except (ValueError, InvalidSignature) as error:
        raise SourceHandlingBlockedError("TAMPER_DETECTED: record integrity signature is invalid") from error


def _verify_operator_root_row(row: sqlite3.Row, operator_root: SourceHandlingOperatorRoot) -> None:
    if row["schema_version"] != SOURCE_HANDLING_OPERATOR_ROOT_SCHEMA_VERSION:
        raise SourceHandlingBlockedError("TAMPER_DETECTED: unknown Source Handling operator-root schema")
    if (
        row["genesis_rule_sha256"] != operator_root.genesis_rule_sha256
        or row["verification_key_sha256"] != operator_root.verification_key_sha256
    ):
        raise SourceHandlingBlockedError("TAMPER_DETECTED: durable Source Handling operator root mismatch")


def _history_commitment_claims(
    *,
    sequence: int,
    previous_commitment_sha256: str | None,
    record_id: str,
    family: str,
    scope: str,
    canonical_head_id: str,
    canonical_revision: int,
    record_integrity_signature: str,
    authorization_id: str | None,
    authorization_consumed_at: str | None,
    authorization_consumed_record_id: str | None,
) -> dict[str, Any]:
    return {
        "schema_version": SOURCE_HANDLING_HISTORY_COMMITMENT_SCHEMA_VERSION,
        "sequence": sequence,
        "previous_commitment_sha256": previous_commitment_sha256,
        "record_id": record_id,
        "family": family,
        "scope": scope,
        "canonical_head_id": canonical_head_id,
        "canonical_revision": canonical_revision,
        "record_integrity_signature": record_integrity_signature,
        "authorization_id": authorization_id,
        "authorization_consumed_at": authorization_consumed_at,
        "authorization_consumed_record_id": authorization_consumed_record_id,
    }


def _history_commitment_claim_keys() -> set[str]:
    return set(
        _history_commitment_claims(
            sequence=1,
            previous_commitment_sha256=None,
            record_id="record",
            family="FACT",
            scope="scope",
            canonical_head_id="record",
            canonical_revision=1,
            record_integrity_signature="signature",
            authorization_id=None,
            authorization_consumed_at=None,
            authorization_consumed_record_id=None,
        )
    )


def _verify_authenticated_history(
    connection: sqlite3.Connection,
    *,
    verification_public_key_bytes: bytes,
    operator_root: SourceHandlingOperatorRoot,
) -> None:
    commitment_rows = connection.execute(
        "SELECT * FROM source_handling_history_commitments ORDER BY sequence"
    ).fetchall()
    record_rows = connection.execute("SELECT * FROM source_handling_authority_records").fetchall()
    canonical_rows = connection.execute("SELECT * FROM source_handling_canonical_keys").fetchall()
    authorization_rows = connection.execute("SELECT * FROM source_handling_publication_authorizations").fetchall()
    if not commitment_rows:
        if (
            record_rows
            or canonical_rows
            or any(
                row["consumed_at"] is not None or row["consumed_record_id"] is not None for row in authorization_rows
            )
        ):
            raise SourceHandlingBlockedError("TAMPER_DETECTED: authenticated authority history is missing")
        return

    records_by_id = {str(row["record_id"]): row for row in record_rows}
    if len(records_by_id) != len(record_rows):
        raise SourceHandlingBlockedError("TAMPER_DETECTED: durable authority record identity is duplicated")
    expected_previous: str | None = None
    expected_sequence = 1
    committed_record_ids: set[str] = set()
    committed_consumptions: dict[str, tuple[str, str]] = {}
    expected_heads: dict[tuple[str, str], tuple[str, int]] = {}

    for commitment_row in commitment_rows:
        try:
            claims = json.loads(str(commitment_row["claims_json"]))
        except (json.JSONDecodeError, TypeError) as error:
            raise SourceHandlingBlockedError("TAMPER_DETECTED: history commitment claims are malformed") from error
        if not isinstance(claims, dict) or set(claims) != _history_commitment_claim_keys():
            raise SourceHandlingBlockedError("TAMPER_DETECTED: history commitment claim set is incomplete")
        if claims.get("schema_version") != SOURCE_HANDLING_HISTORY_COMMITMENT_SCHEMA_VERSION:
            raise SourceHandlingBlockedError("TAMPER_DETECTED: unknown history commitment schema")
        if _canonical_json(claims) != commitment_row["claims_json"]:
            raise SourceHandlingBlockedError("TAMPER_DETECTED: history commitment claims are not canonical")
        if claims.get("sequence") != expected_sequence or int(commitment_row["sequence"]) != expected_sequence:
            raise SourceHandlingBlockedError("TAMPER_DETECTED: history commitment sequence is not contiguous")
        if (
            claims.get("previous_commitment_sha256") != expected_previous
            or commitment_row["previous_commitment_sha256"] != expected_previous
        ):
            raise SourceHandlingBlockedError("TAMPER_DETECTED: history commitment chain is broken")
        claims_json = str(commitment_row["claims_json"])
        commitment_sha256 = hashlib.sha256(claims_json.encode("utf-8")).hexdigest()
        if commitment_sha256 != commitment_row["commitment_sha256"]:
            raise SourceHandlingBlockedError("TAMPER_DETECTED: history commitment digest mismatch")
        signature = commitment_row["issuer_signature"]
        if not isinstance(signature, str) or len(signature) != 128 or signature.lower() != signature:
            raise SourceHandlingBlockedError("TAMPER_DETECTED: history commitment signature is malformed")
        try:
            _load_public_key(verification_public_key_bytes).verify(
                bytes.fromhex(signature), claims_json.encode("utf-8")
            )
        except (ValueError, InvalidSignature) as error:
            raise SourceHandlingBlockedError("TAMPER_DETECTED: history commitment signature is invalid") from error

        record_id = _required_text("history commitment record_id", claims.get("record_id"))
        family = _required_text("history commitment family", claims.get("family"))
        scope = _required_text("history commitment scope", claims.get("scope"))
        _require_family_scope(family, scope)
        if record_id in committed_record_ids:
            raise SourceHandlingBlockedError("TAMPER_DETECTED: authority record is committed more than once")
        record_row = records_by_id.get(record_id)
        if record_row is None:
            raise SourceHandlingBlockedError("TAMPER_DETECTED: committed authority record is missing")
        record = _decode_durable_record(
            connection,
            record_row,
            verification_public_key_bytes=verification_public_key_bytes,
            operator_root=operator_root,
        )
        if record_row["family"] != family or record_row["scope"] != scope:
            raise SourceHandlingBlockedError("TAMPER_DETECTED: committed authority identity mismatch")
        if claims.get("canonical_head_id") != record_id:
            raise SourceHandlingBlockedError("TAMPER_DETECTED: committed canonical head identity mismatch")
        if claims.get("record_integrity_signature") != record_row["integrity_signature"]:
            raise SourceHandlingBlockedError("TAMPER_DETECTED: committed record integrity proof mismatch")

        head_key = (family, scope)
        previous_head = expected_heads.get(head_key)
        expected_revision = previous_head[1] + 1 if previous_head is not None else 1
        if claims.get("canonical_revision") != expected_revision:
            raise SourceHandlingBlockedError("TAMPER_DETECTED: committed canonical revision is not monotonic")
        predecessor_id = _supersedes_id(record)
        expected_predecessor = previous_head[0] if previous_head is not None else None
        if predecessor_id != expected_predecessor:
            raise SourceHandlingBlockedError("TAMPER_DETECTED: committed authority chain is not linear")
        expected_heads[head_key] = (record_id, expected_revision)

        authorization_id = claims.get("authorization_id")
        consumed_at = claims.get("authorization_consumed_at")
        consumed_record_id = claims.get("authorization_consumed_record_id")
        if authorization_id is None:
            if (
                consumed_at is not None
                or consumed_record_id is not None
                or record.get("publication_authorization") is not None
            ):
                raise SourceHandlingBlockedError("TAMPER_DETECTED: genesis commitment consumption is invalid")
        else:
            normalized_authorization_id = _required_text("history authorization_id", authorization_id)
            normalized_consumed_at = _required_text("history authorization consumed_at", consumed_at)
            normalized_consumed_record_id = _required_text(
                "history authorization consumed_record_id", consumed_record_id
            )
            if normalized_consumed_record_id != record_id:
                raise SourceHandlingBlockedError("TAMPER_DETECTED: committed authorization record mismatch")
            publication_authorization = record.get("publication_authorization")
            if (
                not isinstance(publication_authorization, PublicationAuthorization)
                or publication_authorization.authorization_id != normalized_authorization_id
            ):
                raise SourceHandlingBlockedError("TAMPER_DETECTED: committed authorization identity mismatch")
            if normalized_authorization_id in committed_consumptions:
                raise SourceHandlingBlockedError("TAMPER_DETECTED: authorization was committed more than once")
            committed_consumptions[normalized_authorization_id] = (
                normalized_consumed_at,
                normalized_consumed_record_id,
            )

        committed_record_ids.add(record_id)
        expected_previous = commitment_sha256
        expected_sequence += 1

    if committed_record_ids != set(records_by_id):
        raise SourceHandlingBlockedError("TAMPER_DETECTED: uncommitted authority record exists")
    actual_heads = {
        (str(row["family"]), str(row["scope"])): (str(row["current_record_id"]), int(row["revision"]))
        for row in canonical_rows
    }
    if actual_heads != expected_heads:
        raise SourceHandlingBlockedError("TAMPER_DETECTED: canonical authority state does not match committed history")

    actual_consumptions: dict[str, tuple[str, str]] = {}
    for authorization_row in authorization_rows:
        authorization_id = str(authorization_row["authorization_id"])
        consumed_at = authorization_row["consumed_at"]
        consumed_record_id = authorization_row["consumed_record_id"]
        if (consumed_at is None) != (consumed_record_id is None):
            raise SourceHandlingBlockedError("TAMPER_DETECTED: authorization consumption state is incomplete")
        if consumed_at is not None and consumed_record_id is not None:
            actual_consumptions[authorization_id] = (str(consumed_at), str(consumed_record_id))
    if actual_consumptions != committed_consumptions:
        raise SourceHandlingBlockedError("TAMPER_DETECTED: authorization consumption does not match committed history")


def _authorization_claims(authorization: PublicationAuthorization) -> dict[str, Any]:
    if authorization.expires_at is None:
        raise SourceHandlingBlockedError("publication authorization expiry is required")
    return {
        "schema_version": SOURCE_HANDLING_AUTHORIZATION_SCHEMA_VERSION,
        "authorization_id": authorization.authorization_id,
        "publication_kind": authorization.publication_kind,
        "governed_subject_scope": authorization.governed_subject_scope,
        "authorized_payload_sha256": authorization.authorized_payload_sha256,
        "authorization_rule_id": authorization.authorization_rule_id,
        "effective_from": _time_text(authorization.effective_from),
        "recorded_at": _time_text(authorization.recorded_at),
        "known_at": _time_text(authorization.known_at),
        "expires_at": _time_text(authorization.expires_at),
        "authority_component_id": authorization.authority_component_id,
        "evidence_ids": list(authorization.evidence_ids),
        "evidence_strength": authorization.evidence_strength,
        "evidence_method": authorization.evidence_method,
        "verifier_ids": list(authorization.verifier_ids),
        "verifier_type": authorization.verifier_type,
        "released_restrictions": sorted(authorization.released_restrictions),
        "predecessor_ids": list(authorization.predecessor_ids),
    }


def _authorization_message(authorization: PublicationAuthorization) -> bytes:
    return _canonical_json(_authorization_claims(authorization)).encode("utf-8")


def _authorization_from_storage(row: sqlite3.Row) -> PublicationAuthorization:
    try:
        claims = json.loads(str(row["claims_json"]))
    except (json.JSONDecodeError, TypeError) as error:
        raise SourceHandlingBlockedError("TAMPER_DETECTED: authorization claims are malformed") from error
    expected_keys = set(_authorization_claims_template())
    if not isinstance(claims, dict) or set(claims) != expected_keys:
        raise SourceHandlingBlockedError("TAMPER_DETECTED: authorization claim set is incomplete")
    if claims.get("schema_version") != SOURCE_HANDLING_AUTHORIZATION_SCHEMA_VERSION:
        raise SourceHandlingBlockedError("unknown publication authorization schema")
    authorization = publication_authorization(
        publication_kind=_required_text("publication_kind", claims.get("publication_kind")),
        governed_subject_scope=_required_text("governed_subject_scope", claims.get("governed_subject_scope")),
        authorized_payload_sha256=_required_text("authorized_payload_sha256", claims.get("authorized_payload_sha256")),
        authorization_rule_id=_required_text("authorization_rule_id", claims.get("authorization_rule_id")),
        effective_from=_parse_time(_required_text("effective_from", claims.get("effective_from"))),
        recorded_at=_parse_time(_required_text("recorded_at", claims.get("recorded_at"))),
        known_at=_parse_time(_required_text("known_at", claims.get("known_at"))),
        authorization_id=_required_text("authorization_id", claims.get("authorization_id")),
        authority_component_id=_required_text("authority_component_id", claims.get("authority_component_id")),
        evidence_ids=_string_values(claims.get("evidence_ids")),
        evidence_strength=_optional_text(claims.get("evidence_strength")),
        evidence_method=_optional_text(claims.get("evidence_method")),
        verifier_ids=_string_values(claims.get("verifier_ids")),
        verifier_type=_optional_text(claims.get("verifier_type")),
        released_restrictions=frozenset(_string_values(claims.get("released_restrictions"))),
        predecessor_ids=_string_values(claims.get("predecessor_ids")),
        expires_at=_parse_time(_required_text("expires_at", claims.get("expires_at"))),
        issuer_signature=_required_text("issuer_signature", row["issuer_signature"]),
    )
    if authorization.authorization_id != row["authorization_id"]:
        raise SourceHandlingBlockedError("TAMPER_DETECTED: authorization identity index mismatch")
    if _canonical_json(_authorization_claims(authorization)) != row["claims_json"]:
        raise SourceHandlingBlockedError("TAMPER_DETECTED: authorization claims are not canonical")
    return authorization


def _authorization_claims_template() -> dict[str, None]:
    return {
        key: None
        for key in (
            "schema_version",
            "authorization_id",
            "publication_kind",
            "governed_subject_scope",
            "authorized_payload_sha256",
            "authorization_rule_id",
            "effective_from",
            "recorded_at",
            "known_at",
            "expires_at",
            "authority_component_id",
            "evidence_ids",
            "evidence_strength",
            "evidence_method",
            "verifier_ids",
            "verifier_type",
            "released_restrictions",
            "predecessor_ids",
        )
    }


def _verify_authorization_signature(authorization: PublicationAuthorization, public_key_bytes: bytes) -> None:
    signature = authorization.issuer_signature
    if len(signature) != 128 or signature.lower() != signature:
        raise SourceHandlingBlockedError("publication authorization signature is malformed")
    try:
        signature_bytes = bytes.fromhex(signature)
        _load_public_key(public_key_bytes).verify(signature_bytes, _authorization_message(authorization))
    except (ValueError, InvalidSignature) as error:
        raise SourceHandlingBlockedError("publication authorization signature is invalid") from error


def _validate_authorization_window(authorization: PublicationAuthorization, admission_time: datetime) -> None:
    if authorization.expires_at is None:
        raise SourceHandlingBlockedError("publication authorization expiry is required")
    if not strict_known_eligible(_authorization_times(authorization), admission_time):
        raise SourceHandlingBlockedError("publication authorization is not strict-known at admission")
    if admission_time > authorization.expires_at:
        raise SourceHandlingBlockedError("publication authorization has expired")


def _validate_authorization_payload_times(
    authorization: PublicationAuthorization,
    payload: Mapping[str, Any],
) -> None:
    payload_times = tuple(_payload_time(payload, field) for field in ("effective_from", "recorded_at", "known_at"))
    authorization_times = (authorization.effective_from, authorization.recorded_at, authorization.known_at)
    if payload_times != authorization_times:
        raise SourceHandlingBlockedError("authorization temporal claims do not match the exact payload")


def _authorization_times(authorization: PublicationAuthorization) -> dict[str, datetime]:
    return {
        "effective_from": authorization.effective_from,
        "recorded_at": authorization.recorded_at,
        "known_at": authorization.known_at,
    }


def _validate_authorization_provenance(
    authorization: PublicationAuthorization,
    *,
    cutoff: datetime,
    resolver: ProvenanceResolver,
) -> None:
    evidence = [resolver(identity, "EVIDENCE", cutoff) for identity in authorization.evidence_ids]
    verifiers = [resolver(identity, "VERIFIER", cutoff) for identity in authorization.verifier_ids]
    if not evidence or not verifiers or any(not isinstance(item, Mapping) for item in (*evidence, *verifiers)):
        raise SourceHandlingBlockedError("canonical publication provenance is incomplete")
    for item in evidence:
        assert isinstance(item, Mapping)
        if item.get("provenance_id") not in authorization.evidence_ids or item.get("provenance_kind") != "EVIDENCE":
            raise SourceHandlingBlockedError("canonical evidence provenance identity mismatch")
        if not strict_known_eligible(item, cutoff):
            raise SourceHandlingBlockedError("canonical evidence was not strict-known at cutoff")
        if item.get("evidence_strength") != authorization.evidence_strength:
            raise SourceHandlingBlockedError("evidence strength does not match canonical provenance")
        if item.get("evidence_method") != authorization.evidence_method:
            raise SourceHandlingBlockedError("evidence method does not match canonical provenance")
    for item in verifiers:
        assert isinstance(item, Mapping)
        if item.get("provenance_id") not in authorization.verifier_ids or item.get("provenance_kind") != "VERIFIER":
            raise SourceHandlingBlockedError("canonical verifier provenance identity mismatch")
        if not strict_known_eligible(item, cutoff):
            raise SourceHandlingBlockedError("canonical verifier was not strict-known at cutoff")
        if item.get("verifier_type") != authorization.verifier_type:
            raise SourceHandlingBlockedError("verifier type does not match canonical provenance")


def _strict_known_rule(
    connection: sqlite3.Connection,
    rule_id: str,
    cutoff: datetime,
    *,
    verification_public_key_bytes: bytes,
    operator_root: SourceHandlingOperatorRoot,
) -> Mapping[str, Any]:
    rows = connection.execute(
        """
        SELECT * FROM source_handling_authority_records
        WHERE family = 'AUTHORIZATION_RULE' AND scope = ?
        ORDER BY admission_time, record_id
        """,
        (SOURCE_HANDLING_RULE_SCOPE,),
    ).fetchall()
    records = [
        _decode_durable_record(
            connection,
            row,
            verification_public_key_bytes=verification_public_key_bytes,
            operator_root=operator_root,
        )
        for row in rows
    ]
    if not records:
        raise SourceHandlingBlockedError("authorization-rule history is unavailable")
    head_row = connection.execute(
        "SELECT current_record_id FROM source_handling_canonical_keys "
        "WHERE family = 'AUTHORIZATION_RULE' AND scope = ?",
        (SOURCE_HANDLING_RULE_SCOPE,),
    ).fetchone()
    if head_row is None:
        raise SourceHandlingBlockedError("authorization-rule canonical head is unavailable")
    current_rule_id = str(head_row["current_record_id"])
    _validate_complete_chain(records, expected_head_id=current_rule_id)
    if current_rule_id != rule_id:
        raise SourceHandlingBlockedError("authorization names a stale authorization rule")
    rule = strict_known_head(records, cutoff=cutoff, scope=SOURCE_HANDLING_RULE_SCOPE)
    if rule.get("id") != rule_id:
        raise SourceHandlingBlockedError("authorization names a stale authorization rule")
    return rule


def _publication_change(
    connection: sqlite3.Connection,
    *,
    family: str,
    scope: str,
    current_id: str | None,
    candidate: Mapping[str, Any],
) -> tuple[str, frozenset[str]]:
    if family != "FACT":
        return "PERMISSIVE_GENESIS", frozenset()
    candidate_fact = candidate.get("fact")
    if not isinstance(candidate_fact, Mapping):
        raise SourceHandlingBlockedError("fact publication payload lacks normalized fact")
    if current_id is None:
        return ("MORE_RESTRICTIVE" if _fact_has_restriction(candidate_fact) else "PERMISSIVE_GENESIS"), frozenset()
    row = connection.execute(
        "SELECT payload_json FROM source_handling_authority_records WHERE record_id = ? AND family = 'FACT' AND scope = ?",
        (current_id, scope),
    ).fetchone()
    if row is None:
        raise SourceHandlingBlockedError("canonical predecessor fact is unavailable")
    predecessor = json.loads(str(row["payload_json"]))
    predecessor_fact = predecessor.get("fact") if isinstance(predecessor, Mapping) else None
    if not isinstance(predecessor_fact, Mapping):
        raise SourceHandlingBlockedError("canonical predecessor fact is unavailable")
    releases = _released_fact_restrictions(predecessor_fact, candidate_fact)
    return ("LESS_RESTRICTIVE", releases) if releases else ("MORE_RESTRICTIVE", frozenset())


def _view_publication_change(
    view: SqliteSourceHandlingAuthorityReadView,
    *,
    family: str,
    scope: str,
    current_id: str | None,
    candidate: Mapping[str, Any],
) -> tuple[str, frozenset[str]]:
    if family != "FACT":
        return "PERMISSIVE_GENESIS", frozenset()
    candidate_fact = candidate.get("fact")
    if not isinstance(candidate_fact, Mapping):
        raise SourceHandlingBlockedError("fact publication payload lacks normalized fact")
    if current_id is None:
        return ("MORE_RESTRICTIVE" if _fact_has_restriction(candidate_fact) else "PERMISSIVE_GENESIS"), frozenset()
    predecessor = view.canonical_record_by_id("FACT", current_id)
    predecessor_fact = predecessor.get("fact") if predecessor is not None else None
    if not isinstance(predecessor_fact, Mapping):
        raise SourceHandlingBlockedError("canonical predecessor fact is unavailable")
    releases = _released_fact_restrictions(predecessor_fact, candidate_fact)
    return ("LESS_RESTRICTIVE", releases) if releases else ("MORE_RESTRICTIVE", frozenset())


def _validate_complete_chain(records: Sequence[Mapping[str, Any]], *, expected_head_id: str) -> None:
    by_id: dict[str, Mapping[str, Any]] = {}
    children: dict[str, int] = {}
    roots: list[str] = []
    for record in records:
        record_id = record.get("id")
        if not isinstance(record_id, str) or not record_id or record_id in by_id:
            raise SourceHandlingBlockedError("canonical authority identity is missing or duplicated")
        by_id[record_id] = record
    for record_id, record in by_id.items():
        predecessor = _supersedes_id(record)
        if predecessor is None:
            roots.append(record_id)
            continue
        if predecessor not in by_id:
            raise SourceHandlingBlockedError("canonical authority predecessor is missing")
        children[predecessor] = children.get(predecessor, 0) + 1
        if children[predecessor] > 1:
            raise SourceHandlingBlockedError("canonical authority history has divergent heads")
    heads = [record_id for record_id in by_id if children.get(record_id, 0) == 0]
    if len(roots) != 1 or heads != [expected_head_id]:
        raise SourceHandlingBlockedError("canonical authority history is not one complete chain")
    visited: set[str] = set()
    cursor: str | None = expected_head_id
    while cursor is not None:
        if cursor in visited:
            raise SourceHandlingBlockedError("canonical authority history contains a cycle")
        visited.add(cursor)
        cursor = _supersedes_id(by_id[cursor])
    if visited != set(by_id):
        raise SourceHandlingBlockedError("canonical authority history is not one complete chain")


def _validate_payload_shape(family: str, scope: str, payload: Mapping[str, Any]) -> None:
    if payload.get("scope") != scope:
        raise SourceHandlingBlockedError("authority payload scope does not match publication scope")
    for field in ("effective_from", "recorded_at", "known_at"):
        _payload_time(payload, field)
    if family == "FACT":
        fact = payload.get("fact")
        if not isinstance(fact, Mapping):
            raise SourceHandlingBlockedError("fact publication payload is incomplete")
        for field in (
            "sensitivity_known",
            "operation_restrictions_known",
            "persistence_restriction_known",
            "secret_presence_known",
        ):
            if fact.get(field) is not True:
                raise SourceHandlingBlockedError(f"FACT dimension is unknown: {field}")
        if fact.get("sensitivity") not in {"PUBLIC", "INTERNAL", "CONFIDENTIAL", "RESTRICTED"}:
            raise SourceHandlingBlockedError("FACT sensitivity is unknown or unsupported")
        if fact.get("persistence_restriction") not in {
            "FULL_CONTENT_ALLOWED",
            "DERIVED_ONLY",
            "METADATA_ONLY",
            "NO_PERSISTENCE",
        }:
            raise SourceHandlingBlockedError("FACT persistence restriction is unknown or unsupported")
        if not isinstance(fact.get("operation_restrictions"), list):
            raise SourceHandlingBlockedError("FACT operation restrictions are unknown")
        if not isinstance(fact.get("secret_presence"), list):
            raise SourceHandlingBlockedError("FACT secret presence is unknown")


def _require_family_scope(family: str, scope: str) -> None:
    if family not in SOURCE_HANDLING_FAMILIES:
        raise SourceHandlingBlockedError("unknown Source Handling authority family")
    if not isinstance(scope, str) or not scope.strip():
        raise SourceHandlingBlockedError("Source Handling governed scope is required")


def _payload_time(payload: Mapping[str, Any], field: str) -> datetime:
    value = payload.get(field)
    if isinstance(value, datetime):
        return _aware_utc(field, value)
    if isinstance(value, str):
        return _parse_time(value)
    raise SourceHandlingBlockedError(f"authority payload {field} is required")


def _plain_mapping(value: Mapping[str, Any]) -> dict[str, Any]:
    try:
        result = json.loads(_canonical_json(value))
    except (TypeError, ValueError, json.JSONDecodeError) as error:
        raise SourceHandlingBlockedError("authority payload is not canonical JSON") from error
    if not isinstance(result, dict):
        raise SourceHandlingBlockedError("authority payload must be an object")
    return result


def _canonical_json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=_json_default)


def _json_default(value: object) -> object:
    if isinstance(value, datetime):
        return _time_text(value)
    if isinstance(value, (set, frozenset, tuple)):
        return sorted(value) if not isinstance(value, tuple) else list(value)
    if isinstance(value, PublicationAuthorization):
        return {**_authorization_claims(value), "issuer_signature": value.issuer_signature}
    raise TypeError(f"value is not JSON serializable: {type(value).__name__}")


def _aware_utc(name: str, value: datetime) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise SourceHandlingBlockedError(f"{name} must be timezone-aware")
    return value.astimezone(UTC)


def _time_text(value: datetime) -> str:
    return _aware_utc("datetime", value).isoformat().replace("+00:00", "Z")


def _parse_time(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError) as error:
        raise SourceHandlingBlockedError("authority timestamp is malformed") from error
    return _aware_utc("authority timestamp", parsed)


def _load_private_key(value: bytes) -> Ed25519PrivateKey:
    if not isinstance(value, bytes) or len(value) != 32:
        raise SourceHandlingBlockedError("Ed25519 signing key material is missing or malformed")
    try:
        return Ed25519PrivateKey.from_private_bytes(value)
    except ValueError as error:
        raise SourceHandlingBlockedError("Ed25519 signing key material is missing or malformed") from error


def _load_public_key(value: bytes) -> Ed25519PublicKey:
    if not isinstance(value, bytes) or len(value) != 32:
        raise SourceHandlingBlockedError("Ed25519 verification key material is missing or malformed")
    try:
        return Ed25519PublicKey.from_public_bytes(value)
    except ValueError as error:
        raise SourceHandlingBlockedError("Ed25519 verification key material is missing or malformed") from error


def _require_sha256(name: str, value: object) -> str:
    if not isinstance(value, str) or len(value) != 64 or value.lower() != value:
        raise SourceHandlingBlockedError(f"{name} is missing or malformed")
    try:
        bytes.fromhex(value)
    except ValueError as error:
        raise SourceHandlingBlockedError(f"{name} is missing or malformed") from error
    return value


def _required_text(name: str, value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise SourceHandlingBlockedError(f"{name} is required")
    return value


def _optional_text(value: object) -> str | None:
    if value is None:
        return None
    return _required_text("authorization claim", value)


def _nonblank_values(name: str, values: Sequence[str]) -> tuple[str, ...]:
    normalized = tuple(value for value in values if isinstance(value, str) and value.strip())
    if len(normalized) != len(values):
        raise SourceHandlingBlockedError(f"{name} must contain only non-blank identities")
    return normalized


def _string_values(value: object) -> tuple[str, ...]:
    if not isinstance(value, list) or any(not isinstance(item, str) or not item for item in value):
        raise SourceHandlingBlockedError("authorization string-list claim is malformed")
    return tuple(value)
