"""Production provenance persistence and resolution for Source Handling (Issue #426).

The trusted Issue-agent issuer names evidence and verifier identities in every
publication authorization it issues, and the Source Handling read path resolves
that provenance with a strict-known resolver.  Before this module the resolver
was the only seam never satisfied by repository-owned code: the issuer received
whatever resolver the operator supplied, and the deployment documentation
pointed at a placeholder module that does not exist.

This module makes that seam real without widening it.  Provenance about the
identities a publication authorization may name is a supporting fact about the
Source Handling Authority, not a new authoritative family: ADR 0033 and ADR 0036
bind the ``AUTHORIZATION_RULE`` history to exactly four families (FACT, POLICY,
FIELD_CATEGORY_REGISTRY and AUTHORIZATION_RULE), so this module persists
provenance in dedicated append-only tables *alongside* the authority records in
the same Evidence Intelligence database, bound to the same pinned operator root
and signed with the same Ed25519 key material that signs authority records.

Trust does not flow from the caller anywhere in this module:

* ``SourceHandlingProvenanceAuthorityRepository`` is an operator-owned writer
  that requires the pinned operator root to already exist, verifies that the
  signing key corresponds to that root, and never runs in the issuer runtime.
* ``SourceHandlingProvenanceView`` is a read-only, tamper-verifying view that
  re-derives every provenance row from stored columns (digest, signature,
  schema, kind shape, canonical timestamps), validates the history commitment
  invariants (exactly one genesis, linear supersession, strictly advancing
  ``known_at``, signed head), and resolves the last strict-known record.
* ``production_provenance_resolver`` is the module-level dotted-path target the
  issuer's ``--provenance-resolver`` wraps; it lazily builds the view once from
  the operator-provided environment and fails closed when the configuration is
  missing, malformed or not pinned to the operator root.

Fail-closed behaviour is invariant: a missing table, an unsigned row, a broken
chain or a record not strict-known at the cutoff raises
``SourceHandlingBlockedError`` instead of degrading to a latest-record fallback.
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import os
import sqlite3
import sys
import threading
from collections.abc import Iterator, Mapping, Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import quote

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization

from hunter.evidence_intelligence.source_handling_persistence import (
    SourceHandlingBlockedError,
    SourceHandlingOperatorRoot,
    _aware_utc,
    _canonical_json,
    _load_private_key,
    _load_public_key,
    _parse_time,
    _time_text,
    _verify_operator_root_row,
)

PROVENANCE_RECORD_SCHEMA_VERSION = "hunter-source-handling-provenance-record-v1"
SOURCE_HANDLING_PROVENANCE_RECORDS = "source_handling_provenance_records"
SOURCE_HANDLING_PROVENANCE_HEADS = "source_handling_provenance_heads"

EVIDENCE_DATABASE_ENV = "HUNTER_ISSUE_AGENT_EVIDENCE_DB"
VERIFICATION_KEY_ENV = "HUNTER_SOURCE_HANDLING_VERIFICATION_KEY"
VERIFICATION_KEY_SHA256_ENV = "HUNTER_SOURCE_HANDLING_VERIFICATION_KEY_SHA256"
GENESIS_RULE_SHA256_ENV = "HUNTER_SOURCE_HANDLING_GENESIS_RULE_SHA256"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS source_handling_provenance_records (
    record_id TEXT PRIMARY KEY,
    provenance_id TEXT NOT NULL,
    provenance_kind TEXT NOT NULL
        CHECK (provenance_kind IN ('EVIDENCE', 'VERIFIER')),
    evidence_strength TEXT,
    evidence_method TEXT,
    verifier_type TEXT,
    authority_identity TEXT NOT NULL,
    supersedes_record_id TEXT,
    effective_from TEXT NOT NULL,
    recorded_at TEXT NOT NULL,
    known_at TEXT NOT NULL,
    admission_time TEXT NOT NULL,
    schema_version TEXT NOT NULL,
    integrity_signature TEXT NOT NULL,
    FOREIGN KEY (supersedes_record_id)
        REFERENCES source_handling_provenance_records(record_id)
);
CREATE INDEX IF NOT EXISTS source_handling_provenance_cutoff_idx
    ON source_handling_provenance_records(
        provenance_id, provenance_kind, effective_from, recorded_at, known_at, admission_time
    );
CREATE TRIGGER IF NOT EXISTS source_handling_provenance_no_update
    BEFORE UPDATE ON source_handling_provenance_records
    BEGIN
        SELECT RAISE(ABORT, 'Source Handling provenance records are append-only');
    END;
CREATE TRIGGER IF NOT EXISTS source_handling_provenance_no_delete
    BEFORE DELETE ON source_handling_provenance_records
    BEGIN
        SELECT RAISE(ABORT, 'Source Handling provenance records are append-only');
    END;
CREATE TABLE IF NOT EXISTS source_handling_provenance_heads (
    provenance_id TEXT NOT NULL,
    provenance_kind TEXT NOT NULL,
    current_record_id TEXT NOT NULL UNIQUE,
    revision INTEGER NOT NULL CHECK (revision > 0),
    integrity_signature TEXT NOT NULL,
    PRIMARY KEY (provenance_id, provenance_kind),
    FOREIGN KEY (current_record_id)
        REFERENCES source_handling_provenance_records(record_id)
);
CREATE TRIGGER IF NOT EXISTS source_handling_provenance_head_no_delete
    BEFORE DELETE ON source_handling_provenance_heads
    BEGIN
        SELECT RAISE(ABORT, 'Source Handling provenance heads are append-only');
    END;
"""


class _SystemClock:
    def now(self) -> datetime:
        return datetime.now(UTC)


def _required_text(name: str, value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise SourceHandlingBlockedError(f"{name} is required")
    return value


def _nullable_text(name: str, value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise SourceHandlingBlockedError(f"{name} is malformed")
    return value


def _kind(value: str) -> str:
    if value in {"EVIDENCE", "VERIFIER"}:
        return value
    raise SourceHandlingBlockedError("unknown Source Handling provenance kind")


def _sha256_identity(name: str, value: object) -> str:
    if not isinstance(value, str) or len(value) != 64 or value.lower() != value:
        raise SourceHandlingBlockedError(f"TAMPER_DETECTED: {name} is malformed")
    return value


def _verified_verification_key(hex_value: str, expected_sha256: str) -> bytes:
    try:
        key_bytes = bytes.fromhex(hex_value)
    except ValueError:
        raise SourceHandlingBlockedError("provenance verification key must be hex-encoded") from None
    _load_public_key(key_bytes)
    if hashlib.sha256(key_bytes).hexdigest() != expected_sha256:
        raise SourceHandlingBlockedError("provenance verification key does not match its pinned digest")
    return key_bytes


def _provenance_payload(
    *,
    provenance_id: str,
    provenance_kind: str,
    evidence_strength: str | None,
    evidence_method: str | None,
    verifier_type: str | None,
    authority_identity: str,
    effective_from: str,
    recorded_at: str,
    known_at: str,
) -> dict[str, str | None]:
    """Content that is bound into the content-addressed provenance identity.

    ``supersedes_record_id`` is deliberately excluded: re-recording identical
    content after the chain has advanced must be detectable as the same
    identity, while the supersession link itself remains bound by the signed
    integrity claims and enforced by the linear-history validation.
    """

    return {
        "provenance_id": provenance_id,
        "provenance_kind": provenance_kind,
        "evidence_strength": evidence_strength,
        "evidence_method": evidence_method,
        "verifier_type": verifier_type,
        "authority_identity": authority_identity,
        "effective_from": effective_from,
        "recorded_at": recorded_at,
        "known_at": known_at,
    }


def _record_integrity_claims(row: sqlite3.Row) -> dict[str, str | None]:
    return {
        "schema_version": str(row["schema_version"]),
        "record_id": str(row["record_id"]),
        "provenance_id": str(row["provenance_id"]),
        "provenance_kind": str(row["provenance_kind"]),
        "evidence_strength": (str(row["evidence_strength"]) if row["evidence_strength"] is not None else None),
        "evidence_method": (str(row["evidence_method"]) if row["evidence_method"] is not None else None),
        "verifier_type": (str(row["verifier_type"]) if row["verifier_type"] is not None else None),
        "authority_identity": str(row["authority_identity"]),
        "supersedes_record_id": (str(row["supersedes_record_id"]) if row["supersedes_record_id"] is not None else None),
        "effective_from": str(row["effective_from"]),
        "recorded_at": str(row["recorded_at"]),
        "known_at": str(row["known_at"]),
        "admission_time": str(row["admission_time"]),
    }


def _verify_record_integrity(row: sqlite3.Row, verification_public_key_bytes: bytes) -> None:
    signature = row["integrity_signature"]
    if not isinstance(signature, str) or len(signature) != 128 or signature.lower() != signature:
        raise SourceHandlingBlockedError("TAMPER_DETECTED: provenance signature is missing or malformed")
    message = _canonical_json(_record_integrity_claims(row)).encode("utf-8")
    try:
        _load_public_key(verification_public_key_bytes).verify(bytes.fromhex(signature), message)
    except (ValueError, InvalidSignature) as error:
        raise SourceHandlingBlockedError("TAMPER_DETECTED: provenance signature is invalid") from error


def _verify_head_integrity(row: sqlite3.Row, verification_public_key_bytes: bytes) -> None:
    signature = row["integrity_signature"]
    if not isinstance(signature, str) or len(signature) != 128 or signature.lower() != signature:
        raise SourceHandlingBlockedError("TAMPER_DETECTED: provenance head signature is missing or malformed")
    claims = {
        "provenance_id": str(row["provenance_id"]),
        "provenance_kind": str(row["provenance_kind"]),
        "current_record_id": str(row["current_record_id"]),
        "revision": int(row["revision"]),
    }
    message = _canonical_json(claims).encode("utf-8")
    try:
        _load_public_key(verification_public_key_bytes).verify(bytes.fromhex(signature), message)
    except (ValueError, InvalidSignature) as error:
        raise SourceHandlingBlockedError("TAMPER_DETECTED: provenance head signature is invalid") from error


def _decode_record(row: sqlite3.Row, verification_public_key_bytes: bytes) -> dict[str, Any]:
    if str(row["schema_version"]) != PROVENANCE_RECORD_SCHEMA_VERSION:
        raise SourceHandlingBlockedError("unknown Source Handling provenance schema")
    record_id = _sha256_identity("provenance record identity", row["record_id"])
    provenance_id = _required_text("provenance_id", row["provenance_id"])
    provenance_kind = _kind(_required_text("provenance_kind", row["provenance_kind"]))
    authority_identity = _required_text("authority_identity", row["authority_identity"])
    supersedes_record_id = (
        _sha256_identity("provenance predecessor identity", row["supersedes_record_id"])
        if row["supersedes_record_id"] is not None
        else None
    )
    if provenance_kind == "EVIDENCE":
        evidence_strength = _required_text("evidence_strength", row["evidence_strength"])
        evidence_method = _required_text("evidence_method", row["evidence_method"])
        verifier_type = _nullable_text("verifier_type", row["verifier_type"])
        if verifier_type is not None:
            raise SourceHandlingBlockedError("EVIDENCE provenance must not name a verifier type")
    else:
        evidence_strength = _nullable_text("evidence_strength", row["evidence_strength"])
        evidence_method = _nullable_text("evidence_method", row["evidence_method"])
        verifier_type = _required_text("verifier_type", row["verifier_type"])
        if evidence_strength is not None or evidence_method is not None:
            raise SourceHandlingBlockedError("VERIFIER provenance must not name evidence strength or method")
    temporal: dict[str, datetime] = {}
    stored_texts: dict[str, str] = {}
    for field in ("effective_from", "recorded_at", "known_at", "admission_time"):
        stored_texts[field] = _required_text(field, row[field])
        parsed = _parse_time(stored_texts[field])
        if _time_text(parsed) != stored_texts[field]:
            raise SourceHandlingBlockedError(f"TAMPER_DETECTED: provenance {field} is not canonical")
        temporal[field] = parsed
    _verify_record_integrity(row, verification_public_key_bytes)
    payload = _provenance_payload(
        provenance_id=provenance_id,
        provenance_kind=provenance_kind,
        evidence_strength=evidence_strength,
        evidence_method=evidence_method,
        verifier_type=verifier_type,
        authority_identity=authority_identity,
        effective_from=stored_texts["effective_from"],
        recorded_at=stored_texts["recorded_at"],
        known_at=stored_texts["known_at"],
    )
    if hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest() != record_id:
        raise SourceHandlingBlockedError("TAMPER_DETECTED: provenance record identity mismatch")
    if temporal["recorded_at"] > temporal["known_at"] or temporal["effective_from"] > temporal["known_at"]:
        raise SourceHandlingBlockedError("provenance record is not strict-known at its own known_at")
    return {
        "schema_version": PROVENANCE_RECORD_SCHEMA_VERSION,
        "record_id": record_id,
        "provenance_id": provenance_id,
        "provenance_kind": provenance_kind,
        "evidence_strength": evidence_strength,
        "evidence_method": evidence_method,
        "verifier_type": verifier_type,
        "authority_identity": authority_identity,
        "supersedes_record_id": supersedes_record_id,
        "effective_from": temporal["effective_from"],
        "recorded_at": temporal["recorded_at"],
        "known_at": temporal["known_at"],
        "admission_time": temporal["admission_time"],
    }


class SourceHandlingProvenanceAuthorityRepository:
    """Operator-owned writer for the Source Handling provenance ledger.

    The issuer runtime never holds this class: it requires the signing private
    key, validates that the key corresponds to the pinned operator root, and
    therefore can only be used by the operator while provisioning the evidence
    database.  Mirrors ``SourceHandlingAuthorityRepository``'s fail-closed
    discipline so a missing or mismatched operator root never degrades to an
    unowned write.
    """

    def __init__(
        self,
        path: str | Path,
        *,
        signing_private_key: bytes,
        operator_root: SourceHandlingOperatorRoot,
        clock: Any | None = None,
    ) -> None:
        self.path = Path(path)
        self._signing_key = _load_private_key(signing_private_key)
        self._verification_public_key_bytes = self._signing_key.public_key().public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
        self._operator_root = operator_root
        self._clock = clock if clock is not None else _SystemClock()
        if hashlib.sha256(self._verification_public_key_bytes).hexdigest() != operator_root.verification_key_sha256:
            raise SourceHandlingBlockedError("provenance signing key does not match the pinned operator root")

    def _sign(self, claims: Mapping[str, object]) -> str:
        message = _canonical_json(claims).encode("utf-8")
        try:
            signature = self._signing_key.sign(message)
        except Exception as error:
            raise SourceHandlingBlockedError("Source Handling provenance signing failed") from error
        if not isinstance(signature, bytes) or len(signature) != 64:
            raise SourceHandlingBlockedError("Source Handling provenance signature is malformed")
        try:
            _load_public_key(self._verification_public_key_bytes).verify(signature, message)
        except InvalidSignature as error:
            raise SourceHandlingBlockedError("Source Handling provenance signature is invalid") from error
        return signature.hex()

    def _next_admission_time(self, connection: sqlite3.Connection) -> datetime:
        candidate = _aware_utc("provenance admission_time", self._clock.now())
        row = connection.execute(f"SELECT MAX(admission_time) FROM {SOURCE_HANDLING_PROVENANCE_RECORDS}").fetchone()
        if row is None or row[0] is None:
            return candidate
        last = _parse_time(str(row[0]))
        return candidate if candidate > last else last + timedelta(microseconds=1)

    @contextlib.contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=30.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        try:
            connection.execute("BEGIN IMMEDIATE")
            try:
                pinned = connection.execute(
                    "SELECT * FROM source_handling_operator_root WHERE singleton_id = 'SOURCE_HANDLING'"
                ).fetchone()
            except sqlite3.OperationalError as error:
                raise SourceHandlingBlockedError("pinned Source Handling operator root is unavailable") from error
            if pinned is None:
                raise SourceHandlingBlockedError("pinned Source Handling operator root is unavailable")
            _verify_operator_root_row(pinned, self._operator_root)
            connection.executescript(_SCHEMA)
            yield connection
        except Exception:
            connection.rollback()
            raise
        else:
            connection.commit()
        finally:
            connection.close()

    def record_provenance(
        self,
        *,
        provenance_id: str,
        provenance_kind: str,
        authority_identity: str,
        effective_from: datetime,
        recorded_at: datetime,
        known_at: datetime,
        evidence_strength: str | None = None,
        evidence_method: str | None = None,
        verifier_type: str | None = None,
    ) -> str:
        """Provision one strict-known provenance record and return its identity.

        The first record for an identity is its genesis.  A later record must
        supersede the exact current head and be knowable strictly later than it,
        so a conflicting identity can never be re-provisioned once the head has
        advanced; re-recording identical content is an idempotent no-op that
        returns the existing head identity.  ``known_at`` must be at or before
        the repository clock for the record to be strict-known to the issuer.
        """

        provenance_id = _required_text("provenance_id", provenance_id)
        provenance_kind = _kind(_required_text("provenance_kind", provenance_kind))
        authority_identity = _required_text("authority_identity", authority_identity)
        if provenance_kind == "EVIDENCE":
            evidence_strength = _required_text("evidence_strength", evidence_strength)
            evidence_method = _required_text("evidence_method", evidence_method)
            if verifier_type is not None:
                raise SourceHandlingBlockedError("EVIDENCE provenance must not name a verifier type")
            verifier_type = None
        else:
            if evidence_strength is not None or evidence_method is not None:
                raise SourceHandlingBlockedError("VERIFIER provenance must not name evidence strength or method")
            evidence_strength = None
            evidence_method = None
            verifier_type = _required_text("verifier_type", verifier_type)
        timestamps = {
            "effective_from": _aware_utc("effective_from", effective_from),
            "recorded_at": _aware_utc("recorded_at", recorded_at),
            "known_at": _aware_utc("known_at", known_at),
        }
        if timestamps["recorded_at"] > timestamps["known_at"] or timestamps["effective_from"] > timestamps["known_at"]:
            raise SourceHandlingBlockedError("provenance record is not strict-known at its own known_at")

        with self._transaction() as connection:
            head_row = connection.execute(
                f"SELECT * FROM {SOURCE_HANDLING_PROVENANCE_HEADS} WHERE provenance_id = ? AND provenance_kind = ?",
                (provenance_id, provenance_kind),
            ).fetchone()
            head_known_at: datetime | None = None
            if head_row is None:
                existing = connection.execute(
                    f"SELECT COUNT(*) FROM {SOURCE_HANDLING_PROVENANCE_RECORDS} "
                    "WHERE provenance_id = ? AND provenance_kind = ?",
                    (provenance_id, provenance_kind),
                ).fetchone()[0]
                if existing:
                    raise SourceHandlingBlockedError("provenance head is missing for existing records")
                supersedes_record_id = None
                revision = 1
            else:
                supersedes_record_id = _sha256_identity("provenance head identity", head_row["current_record_id"])
                head_record = connection.execute(
                    f"SELECT * FROM {SOURCE_HANDLING_PROVENANCE_RECORDS} WHERE record_id = ?",
                    (supersedes_record_id,),
                ).fetchone()
                if head_record is None:
                    raise SourceHandlingBlockedError("provenance head record is missing")
                head_known_at = _parse_time(str(head_record["known_at"]))
                revision = int(head_row["revision"]) + 1

            effective_text = _time_text(timestamps["effective_from"])
            recorded_text = _time_text(timestamps["recorded_at"])
            known_text = _time_text(timestamps["known_at"])
            record_id = hashlib.sha256(
                _canonical_json(
                    _provenance_payload(
                        provenance_id=provenance_id,
                        provenance_kind=provenance_kind,
                        evidence_strength=evidence_strength,
                        evidence_method=evidence_method,
                        verifier_type=verifier_type,
                        authority_identity=authority_identity,
                        effective_from=effective_text,
                        recorded_at=recorded_text,
                        known_at=known_text,
                    )
                ).encode("utf-8")
            ).hexdigest()
            if supersedes_record_id is not None and record_id == supersedes_record_id:
                return record_id
            if head_known_at is not None and timestamps["known_at"] <= head_known_at:
                raise SourceHandlingBlockedError(
                    "provenance correction must be knowable strictly later than the current head"
                )
            admission_time = self._next_admission_time(connection)
            admission_text = _time_text(admission_time)
            integrity_signature = self._sign(
                {
                    "schema_version": PROVENANCE_RECORD_SCHEMA_VERSION,
                    "record_id": record_id,
                    "provenance_id": provenance_id,
                    "provenance_kind": provenance_kind,
                    "evidence_strength": evidence_strength,
                    "evidence_method": evidence_method,
                    "verifier_type": verifier_type,
                    "authority_identity": authority_identity,
                    "supersedes_record_id": supersedes_record_id,
                    "effective_from": effective_text,
                    "recorded_at": recorded_text,
                    "known_at": known_text,
                    "admission_time": admission_text,
                }
            )
            connection.execute(
                f"INSERT INTO {SOURCE_HANDLING_PROVENANCE_RECORDS} ("
                "record_id, provenance_id, provenance_kind, evidence_strength, evidence_method, verifier_type, "
                "authority_identity, supersedes_record_id, effective_from, recorded_at, known_at, admission_time, "
                "schema_version, integrity_signature"
                ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    record_id,
                    provenance_id,
                    provenance_kind,
                    evidence_strength,
                    evidence_method,
                    verifier_type,
                    authority_identity,
                    supersedes_record_id,
                    effective_text,
                    recorded_text,
                    known_text,
                    admission_text,
                    PROVENANCE_RECORD_SCHEMA_VERSION,
                    integrity_signature,
                ),
            )
            head_signature = self._sign(
                {
                    "provenance_id": provenance_id,
                    "provenance_kind": provenance_kind,
                    "current_record_id": record_id,
                    "revision": revision,
                }
            )
            connection.execute(
                f"INSERT INTO {SOURCE_HANDLING_PROVENANCE_HEADS} ("
                "provenance_id, provenance_kind, current_record_id, revision, integrity_signature"
                ") VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT(provenance_id, provenance_kind) DO UPDATE SET "
                "current_record_id = excluded.current_record_id, "
                "revision = excluded.revision, "
                "integrity_signature = excluded.integrity_signature",
                (provenance_id, provenance_kind, record_id, revision, head_signature),
            )
            return record_id


class SourceHandlingProvenanceView:
    """Read-only, tamper-verifying view over the Source Handling provenance ledger.

    Every resolved record is re-derived from its stored columns: the row digest
    must equal the content-addressed identity, the integrity signature must
    verify, the kind-specific shape must hold, all timestamps must be canonical,
    and the history must satisfy the commitment invariants (exactly one genesis,
    linear supersession, strictly advancing ``known_at``, and a head that is
    signed and matches the latest record).  Missing or tampered provenance fails
    closed with ``SourceHandlingBlockedError``.
    """

    def __init__(
        self,
        path: str | Path,
        *,
        verification_public_key: bytes,
        operator_root: SourceHandlingOperatorRoot,
    ) -> None:
        self._path = Path(path)
        self._operator_root = operator_root
        self._verification_public_key_bytes = _load_public_key(verification_public_key).public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )

    @contextlib.contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        uri = f"file:{quote(str(self._path.resolve()))}?mode=ro"
        connection = sqlite3.connect(uri, uri=True, timeout=30.0)
        connection.row_factory = sqlite3.Row
        try:
            try:
                pinned = connection.execute(
                    "SELECT * FROM source_handling_operator_root WHERE singleton_id = 'SOURCE_HANDLING'"
                ).fetchone()
            except sqlite3.OperationalError as error:
                raise SourceHandlingBlockedError("pinned Source Handling operator root is unavailable") from error
            if pinned is None:
                raise SourceHandlingBlockedError("pinned Source Handling operator root is unavailable")
            _verify_operator_root_row(pinned, self._operator_root)
            yield connection
        finally:
            connection.close()

    def _provenance_tables_present(self, connection: sqlite3.Connection) -> bool:
        rows = connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name IN (?, ?)",
            (SOURCE_HANDLING_PROVENANCE_RECORDS, SOURCE_HANDLING_PROVENANCE_HEADS),
        ).fetchall()
        return len(rows) == 2

    def _validate_chain(
        self,
        connection: sqlite3.Connection,
        provenance_id: str,
        provenance_kind: str,
        chain: Sequence[Mapping[str, Any]],
    ) -> None:
        genesis = [record for record in chain if record["supersedes_record_id"] is None]
        if len(genesis) != 1:
            raise SourceHandlingBlockedError("TAMPER_DETECTED: provenance history lacks exactly one genesis")
        if genesis[0] is not chain[0]:
            raise SourceHandlingBlockedError("TAMPER_DETECTED: provenance genesis is not the first record")
        if len(chain) >= 2:
            for previous, record in zip(chain, chain[1:], strict=False):
                if record["supersedes_record_id"] != previous["record_id"]:
                    raise SourceHandlingBlockedError("TAMPER_DETECTED: provenance history is not linear")
                if record["known_at"] <= previous["known_at"]:
                    raise SourceHandlingBlockedError(
                        "TAMPER_DETECTED: provenance correction is not strictly knowable later"
                    )
        head_row = connection.execute(
            f"SELECT * FROM {SOURCE_HANDLING_PROVENANCE_HEADS} WHERE provenance_id = ? AND provenance_kind = ?",
            (provenance_id, provenance_kind),
        ).fetchone()
        if head_row is None:
            raise SourceHandlingBlockedError("TAMPER_DETECTED: provenance head is missing")
        _verify_head_integrity(head_row, self._verification_public_key_bytes)
        if str(head_row["current_record_id"]) != chain[-1]["record_id"]:
            raise SourceHandlingBlockedError("TAMPER_DETECTED: provenance head does not match the latest record")
        if int(head_row["revision"]) != len(chain):
            raise SourceHandlingBlockedError("TAMPER_DETECTED: provenance head revision is inconsistent")

    def resolve(
        self,
        provenance_id: str,
        provenance_kind: str,
        cutoff: datetime,
    ) -> Mapping[str, Any] | None:
        """Resolve the strict-known provenance record for an identity at ``cutoff``.

        Returns the last record whose ``effective_from``, ``recorded_at``,
        ``known_at`` and ``admission_time`` are all at or before ``cutoff`` and
        whose entire history is completely knowable at ``cutoff``.  Returns
        ``None`` only when the identity is genuinely unresolved; an identity
        with a gap between knowable records fails closed instead of returning a
        record that was never strict-known at ``cutoff``.
        """

        provenance_id = _required_text("provenance_id", provenance_id)
        provenance_kind = _kind(_required_text("provenance_kind", provenance_kind))
        normalized_cutoff = _aware_utc("provenance cutoff", cutoff)
        with self._connect() as connection:
            if not self._provenance_tables_present(connection):
                raise SourceHandlingBlockedError("Source Handling provenance tables are unavailable")
            rows = connection.execute(
                f"SELECT * FROM {SOURCE_HANDLING_PROVENANCE_RECORDS} "
                "WHERE provenance_id = ? AND provenance_kind = ? ORDER BY admission_time, record_id",
                (provenance_id, provenance_kind),
            ).fetchall()
            if not rows:
                return None
            chain = [_decode_record(row, self._verification_public_key_bytes) for row in rows]
            self._validate_chain(connection, provenance_id, provenance_kind, chain)
            known_prefix = 0
            for record in chain:
                if not _strict_known_eligible(record, normalized_cutoff):
                    break
                known_prefix += 1
            if known_prefix == 0:
                return None
            for record in chain[known_prefix:]:
                if _strict_known_eligible(record, normalized_cutoff):
                    raise SourceHandlingBlockedError("provenance history is not completely knowable at cutoff")
            return chain[known_prefix - 1]


def _strict_known_eligible(record: Mapping[str, Any], cutoff: datetime) -> bool:
    for field in ("effective_from", "recorded_at", "known_at", "admission_time"):
        value = record.get(field)
        if not isinstance(value, datetime) or value > cutoff:
            return False
    return True


_production_view: SourceHandlingProvenanceView | None = None
_production_view_lock = threading.Lock()


def _build_production_provenance_view() -> SourceHandlingProvenanceView:
    database = os.environ.get(EVIDENCE_DATABASE_ENV)
    verification_key_hex = os.environ.get(VERIFICATION_KEY_ENV)
    verification_key_sha256 = os.environ.get(VERIFICATION_KEY_SHA256_ENV)
    genesis_rule_sha256 = os.environ.get(GENESIS_RULE_SHA256_ENV)
    if not database or not verification_key_hex or not verification_key_sha256 or not genesis_rule_sha256:
        raise SourceHandlingBlockedError("Source Handling provenance operator configuration is incomplete")
    verification_key = _verified_verification_key(verification_key_hex, verification_key_sha256)
    operator_root = SourceHandlingOperatorRoot(
        genesis_rule_sha256=genesis_rule_sha256,
        verification_key_sha256=verification_key_sha256,
    )
    return SourceHandlingProvenanceView(
        database,
        verification_public_key=verification_key,
        operator_root=operator_root,
    )


def production_provenance_resolver(
    provenance_id: str,
    provenance_kind: str,
    cutoff: datetime,
) -> Mapping[str, Any] | None:
    """Resolver target for the issuer's ``--provenance-resolver`` dotted path.

    Built once from the operator-provided environment described by
    ``EVIDENCE_DATABASE_ENV``, ``VERIFICATION_KEY_ENV``,
    ``VERIFICATION_KEY_SHA256_ENV`` and ``GENESIS_RULE_SHA256_ENV``.  A missing,
    malformed or operator-root-mismatched configuration raises
    ``SourceHandlingBlockedError``; resolution never degrades to an unowned or
    latest-record fallback.
    """

    global _production_view
    if _production_view is None:
        with _production_view_lock:
            if _production_view is None:
                _production_view = _build_production_provenance_view()
    return _production_view.resolve(provenance_id, provenance_kind, cutoff)


def _record_argument(raw: str) -> dict[str, Any]:
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as error:
        raise ValueError("provenance record JSON is malformed") from error
    if not isinstance(payload, dict):
        raise ValueError("provenance record must be a JSON object")
    for field in ("provenance_id", "provenance_kind", "authority_identity"):
        _required_text(field, payload.get(field))
    for field in ("effective_from", "recorded_at", "known_at"):
        payload[field] = _parse_time(_required_text(field, payload.get(field)))
    for field in ("evidence_strength", "evidence_method", "verifier_type"):
        if payload.get(field) is not None:
            payload[field] = _required_text(field, payload[field])
    return payload


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m hunter.evidence_intelligence.source_handling_provenance",
        description="Provision Source Handling provenance records into the evidence database.",
    )
    parser.add_argument(
        "--database",
        default=os.environ.get(EVIDENCE_DATABASE_ENV),
        help=f"evidence database path (default: ${EVIDENCE_DATABASE_ENV})",
    )
    parser.add_argument("--signing-key-hex", required=True, help="hex-encoded Ed25519 signing private key")
    parser.add_argument("--verification-key-hex", required=True, help="hex-encoded Ed25519 verification public key")
    parser.add_argument("--verification-key-sha256", required=True, help="sha256 of the verification public key")
    parser.add_argument(
        "--genesis-rule-sha256",
        required=True,
        help="sha256 of the Source Handling genesis authorization rule",
    )
    parser.add_argument(
        "--record", action="append", required=True, metavar="JSON", help="one provenance record to provision"
    )
    args = parser.parse_args(argv)
    if not args.database:
        parser.error(f"{EVIDENCE_DATABASE_ENV} is not configured")
    try:
        _verified_verification_key(args.verification_key_hex, args.verification_key_sha256)
        signing_private_key = bytes.fromhex(args.signing_key_hex)
    except (ValueError, SourceHandlingBlockedError) as error:
        parser.error(str(error))
    operator_root = SourceHandlingOperatorRoot(
        genesis_rule_sha256=args.genesis_rule_sha256,
        verification_key_sha256=args.verification_key_sha256,
    )
    try:
        repository = SourceHandlingProvenanceAuthorityRepository(
            args.database,
            signing_private_key=signing_private_key,
            operator_root=operator_root,
        )
    except SourceHandlingBlockedError as error:
        parser.error(str(error))
    for raw in args.record:
        try:
            payload = _record_argument(raw)
        except (ValueError, SourceHandlingBlockedError) as error:
            parser.error(str(error))
        try:
            record_id = repository.record_provenance(**payload)
        except SourceHandlingBlockedError as error:
            parser.error(str(error))
        print(record_id)
    return 0


if __name__ == "__main__":
    sys.exit(main())
