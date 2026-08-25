"""Mechanical Phase A persistence for ADR 0035 ResponseValidator contracts.

This repository is deliberately non-authoritative. It provides insert-only
profile history, atomic create-if-absent validation-event allocation, uniqueness,
and exact payload verification. Profile publication semantics, profile
applicability, trusted clocks, event identity, and cutoff allocation remain owned
by the service boundaries in ``response_validator``.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import sqlite3
from collections.abc import Callable, Iterator, Mapping
from dataclasses import asdict
from datetime import UTC, datetime
from typing import Any

from hunter.evidence_intelligence.repository import EvidenceIntelligenceRepository
from hunter.evidence_intelligence.response_validator import (
    BaseValidationKey,
    ResponseValidationProfile,
    ResponseValidationProfileAuthority,
    ResponseValidationProfileSpec,
    ResponseValidator,
    ResponseValidatorFoundation,
    ResponseValidatorFoundationError,
    ValidationEventAllocation,
    ValidationEventAllocationError,
)


class ResponseValidatorPersistenceError(RuntimeError):
    """Base class for mechanical ResponseValidator persistence failures."""


class ResponseValidatorPersistenceConflict(ResponseValidatorPersistenceError):
    """Raised for append-only identity, lineage, or allocation conflicts."""


class ResponseValidatorPersistenceCorruption(ResponseValidatorPersistenceError):
    """Raised when durable bytes no longer match their identity or hash."""


class ResponseValidatorDirectWriteForbidden(ResponseValidatorPersistenceError):
    """Raised when a caller tries to bypass the owning authority/service."""


class ResponseValidatorPersistenceRepository:
    """Insert-only storage and atomic allocation mechanics for ADR 0035 Phase A."""

    def __init__(self, evidence_repository: EvidenceIntelligenceRepository) -> None:
        self.path = evidence_repository.path
        self.__profile_authority_owner: ResponseValidationProfileAuthority | None = None
        self.__profile_authority_capability: object | None = None
        self.__event_allocator_owner: ResponseValidatorFoundation | None = None
        self.__event_allocator_capability: object | None = None
        self.__validation_authority_capabilities: set[object] = set()
        self._initialize()

    def _bind_profile_authority(
        self,
        owner: object,
        installer: Callable[[object], None],
    ) -> None:
        """Install one opaque write capability into the exact profile authority."""
        expected_installer = vars(ResponseValidationProfileAuthority)[
            "_ResponseValidationProfileAuthority__install_persistence_capability"
        ]
        if (
            type(owner) is not ResponseValidationProfileAuthority
            or getattr(owner, "_repository", None) is not self
            or getattr(installer, "__self__", None) is not owner
            or getattr(installer, "__func__", None) is not expected_installer
        ):
            raise ResponseValidatorDirectWriteForbidden("profile persistence capability owner is not canonical")
        if self.__profile_authority_owner is not None:
            raise ResponseValidatorDirectWriteForbidden("profile persistence capability is already bound")
        capability = object()
        self.__profile_authority_owner = owner
        self.__profile_authority_capability = capability
        installer(capability)

    def _bind_event_allocator(
        self,
        owner: object,
        installer: Callable[[object], None],
    ) -> None:
        """Install one opaque write capability into the exact event allocator."""
        expected_installer = vars(ResponseValidatorFoundation)[
            "_ResponseValidatorFoundation__install_persistence_capability"
        ]
        if (
            type(owner) is not ResponseValidatorFoundation
            or getattr(owner, "_repository", None) is not self
            or getattr(installer, "__self__", None) is not owner
            or getattr(installer, "__func__", None) is not expected_installer
        ):
            raise ResponseValidatorDirectWriteForbidden(
                "event-allocation persistence capability owner is not canonical"
            )
        if self.__event_allocator_owner is not None:
            raise ResponseValidatorDirectWriteForbidden("event-allocation persistence capability is already bound")
        capability = object()
        self.__event_allocator_owner = owner
        self.__event_allocator_capability = capability
        installer(capability)

    def _bind_validation_authority(
        self,
        owner: object,
        installer: Callable[[object], None],
    ) -> None:
        """Install an opaque reservation-write capability into an exact ResponseValidator."""
        expected_installer = vars(ResponseValidator)[
            "_ResponseValidator__install_reservation_persistence_capability"
        ]
        if (
            type(owner) is not ResponseValidator
            or getattr(getattr(owner, "_foundation", None), "_repository", None) is not self
            or getattr(installer, "__self__", None) is not owner
            or getattr(installer, "__func__", None) is not expected_installer
        ):
            raise ResponseValidatorDirectWriteForbidden(
                "transient reservation persistence capability owner is not canonical"
            )
        capability = object()
        self.__validation_authority_capabilities.add(capability)
        installer(capability)

    def _require_profile_authority_capability(self, capability: object | None) -> None:
        if capability is None or capability is not self.__profile_authority_capability:
            raise ResponseValidatorDirectWriteForbidden("profile persistence requires its owning authority capability")

    def _require_event_allocator_capability(self, capability: object | None) -> None:
        if capability is None or capability is not self.__event_allocator_capability:
            raise ResponseValidatorDirectWriteForbidden("event allocation requires its owning service capability")

    def _require_validation_authority_capability(self, capability: object | None) -> None:
        if capability is None or capability not in self.__validation_authority_capabilities:
            raise ResponseValidatorDirectWriteForbidden(
                "transient capture reservation requires its owning ResponseValidator capability"
            )

    def _publish_profile_authorized(
        self,
        *,
        authority_capability: object | None = None,
        applicability_key: str,
        factory: Callable[[ResponseValidationProfile | None], ResponseValidationProfile],
    ) -> ResponseValidationProfile:
        """Append a service-created profile while holding the history claim."""
        self._require_profile_authority_capability(authority_capability)
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            history = self._profile_history(connection, applicability_key)
            predecessor = _profile_head(history)
            profile = factory(predecessor)
            if profile.spec.applicability_key != applicability_key:
                raise ResponseValidatorPersistenceConflict("profile applicability does not match claimed history")
            if predecessor is None:
                if history or profile.profile_version != 1 or profile.supersedes_publication_id is not None:
                    raise ResponseValidatorPersistenceConflict("invalid genesis profile history")
            else:
                if profile.profile_version != predecessor.profile_version + 1:
                    raise ResponseValidatorPersistenceConflict("profile version must advance exactly once")
                if profile.supersedes_publication_id != predecessor.publication_id:
                    raise ResponseValidatorPersistenceConflict("profile successor must name the exact current head")

            payload = _profile_payload(profile)
            payload_hash = _sha256(payload)
            try:
                connection.execute(
                    """
                    INSERT INTO response_validation_profiles (
                        publication_id, applicability_key, profile_version,
                        applicable_from, published_at, known_at,
                        supersedes_publication_id, payload_hash, payload_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        profile.publication_id,
                        applicability_key,
                        profile.profile_version,
                        profile.applicable_from.isoformat(),
                        profile.published_at.isoformat(),
                        profile.known_at.isoformat(),
                        profile.supersedes_publication_id,
                        payload_hash,
                        payload,
                    ),
                )
            except sqlite3.IntegrityError as error:
                raise ResponseValidatorPersistenceConflict(
                    "append-only profile identity or lineage conflict"
                ) from error
        return profile

    def profile_history(self, applicability_key: str) -> tuple[ResponseValidationProfile, ...]:
        with self._connect() as connection:
            return self._profile_history(connection, applicability_key)

    def _profile_history(
        self,
        connection: sqlite3.Connection,
        applicability_key: str,
    ) -> tuple[ResponseValidationProfile, ...]:
        rows = connection.execute(
            """
            SELECT publication_id, applicability_key, profile_version,
                   applicable_from, published_at, known_at,
                   supersedes_publication_id, payload_hash, payload_json
            FROM response_validation_profiles
            WHERE applicability_key = ?
            ORDER BY known_at, profile_version, publication_id
            """,
            (applicability_key,),
        ).fetchall()
        return tuple(_profile_from_row(row) for row in rows)

    def _allocate_base_event_authorized(
        self,
        *,
        authority_capability: object | None = None,
        key: BaseValidationKey,
        factory: Callable[[], ValidationEventAllocation],
    ) -> ValidationEventAllocation:
        """Create the base allocation once; concurrent/repeated callers join it."""
        self._require_event_allocator_capability(authority_capability)
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                """
                SELECT validation_event_id, base_validation_key_id,
                       revalidation_generation, predecessor_validation_event_id,
                       validation_cutoff, payload_hash, payload_json
                FROM response_validation_event_allocations
                WHERE base_validation_key_id = ? AND revalidation_generation = 0
                """,
                (key.base_validation_key_id,),
            ).fetchone()
            if existing is not None:
                allocation = _allocation_from_row(existing)
                if allocation.base_validation_key != key:
                    raise ResponseValidatorPersistenceCorruption("base-validation key identity has conflicting bytes")
                return allocation

            allocation = factory()
            if allocation.base_validation_key != key or allocation.revalidation_generation != 0:
                raise ResponseValidatorPersistenceConflict("base allocator returned mismatched canonical coordinates")
            self._insert_allocation(connection, allocation)
            return allocation

    def _allocate_revalidation_event_authorized(
        self,
        *,
        authority_capability: object | None = None,
        predecessor_validation_event_id: str,
        factory: Callable[[ValidationEventAllocation], ValidationEventAllocation],
    ) -> ValidationEventAllocation:
        """Create or join the unique child allocation of the exact predecessor."""
        self._require_event_allocator_capability(authority_capability)
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            predecessor_row = connection.execute(
                """
                SELECT validation_event_id, base_validation_key_id,
                       revalidation_generation, predecessor_validation_event_id,
                       validation_cutoff, payload_hash, payload_json
                FROM response_validation_event_allocations
                WHERE validation_event_id = ?
                """,
                (predecessor_validation_event_id,),
            ).fetchone()
            if predecessor_row is None:
                raise ValidationEventAllocationError("re-validation predecessor is unknown")
            predecessor = _allocation_from_row(predecessor_row)

            existing_child = connection.execute(
                """
                SELECT validation_event_id, base_validation_key_id,
                       revalidation_generation, predecessor_validation_event_id,
                       validation_cutoff, payload_hash, payload_json
                FROM response_validation_event_allocations
                WHERE predecessor_validation_event_id = ?
                """,
                (predecessor_validation_event_id,),
            ).fetchone()
            if existing_child is not None:
                return _allocation_from_row(existing_child)

            allocation = factory(predecessor)
            if allocation.base_validation_key != predecessor.base_validation_key:
                raise ResponseValidatorPersistenceConflict("re-validation changed the stable base key")
            if allocation.predecessor_validation_event_id != predecessor.validation_event_id:
                raise ResponseValidatorPersistenceConflict("re-validation does not name the exact predecessor")
            if allocation.revalidation_generation != predecessor.revalidation_generation + 1:
                raise ResponseValidatorPersistenceConflict("re-validation generation must advance exactly once")
            self._insert_allocation(connection, allocation)
            return allocation

    def validation_event(self, validation_event_id: str) -> ValidationEventAllocation | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT validation_event_id, base_validation_key_id,
                       revalidation_generation, predecessor_validation_event_id,
                       validation_cutoff, payload_hash, payload_json
                FROM response_validation_event_allocations
                WHERE validation_event_id = ?
                """,
                (validation_event_id,),
            ).fetchone()
        return _allocation_from_row(row) if row is not None else None

    def validation_events(self, base_validation_key_id: str) -> tuple[ValidationEventAllocation, ...]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT validation_event_id, base_validation_key_id,
                       revalidation_generation, predecessor_validation_event_id,
                       validation_cutoff, payload_hash, payload_json
                FROM response_validation_event_allocations
                WHERE base_validation_key_id = ?
                ORDER BY revalidation_generation, validation_event_id
                """,
                (base_validation_key_id,),
            ).fetchall()
        return tuple(_allocation_from_row(row) for row in rows)

    def _reserve_transient_capture_authorized(
        self,
        *,
        authority_capability: object | None,
        response_capture_identity: str,
        validation_event_id: str,
    ) -> bool:
        """Atomically reserve one transient capture for one canonical validation event."""
        self._require_validation_authority_capability(authority_capability)
        if not response_capture_identity or not validation_event_id:
            raise ResponseValidatorPersistenceConflict(
                "transient capture reservation requires canonical non-blank identities"
            )
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                """
                SELECT validation_event_id
                FROM response_validation_transient_capture_reservations
                WHERE response_capture_identity = ?
                """,
                (response_capture_identity,),
            ).fetchone()
            if existing is not None:
                return str(existing["validation_event_id"]) == validation_event_id
            try:
                connection.execute(
                    """
                    INSERT INTO response_validation_transient_capture_reservations (
                        response_capture_identity, validation_event_id
                    ) VALUES (?, ?)
                    """,
                    (response_capture_identity, validation_event_id),
                )
            except sqlite3.IntegrityError as error:
                raise ResponseValidatorPersistenceConflict(
                    "transient capture reservation identity conflict"
                ) from error
            return True

    def transient_capture_owner(self, response_capture_identity: str) -> str | None:
        """Read durable non-content ownership metadata for one transient capture."""
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT validation_event_id
                FROM response_validation_transient_capture_reservations
                WHERE response_capture_identity = ?
                """,
                (response_capture_identity,),
            ).fetchone()
        return str(row["validation_event_id"]) if row is not None else None

    def _insert_allocation(self, connection: sqlite3.Connection, allocation: ValidationEventAllocation) -> None:
        payload = _allocation_payload(allocation)
        try:
            connection.execute(
                """
                INSERT INTO response_validation_event_allocations (
                    validation_event_id, base_validation_key_id,
                    revalidation_generation, predecessor_validation_event_id,
                    validation_cutoff, payload_hash, payload_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    allocation.validation_event_id,
                    allocation.base_validation_key.base_validation_key_id,
                    allocation.revalidation_generation,
                    allocation.predecessor_validation_event_id,
                    allocation.validation_cutoff.isoformat(),
                    _sha256(payload),
                    payload,
                ),
            )
        except sqlite3.IntegrityError as error:
            raise ResponseValidatorPersistenceConflict("canonical validation-event allocation conflict") from error

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS response_validation_profiles (
                    publication_id TEXT PRIMARY KEY,
                    applicability_key TEXT NOT NULL,
                    profile_version INTEGER NOT NULL,
                    applicable_from TEXT NOT NULL,
                    published_at TEXT NOT NULL,
                    known_at TEXT NOT NULL,
                    supersedes_publication_id TEXT,
                    payload_hash TEXT NOT NULL,
                    payload_json TEXT NOT NULL
                );
                CREATE UNIQUE INDEX IF NOT EXISTS response_validation_profile_version_unique_idx
                    ON response_validation_profiles(applicability_key, profile_version);
                CREATE UNIQUE INDEX IF NOT EXISTS response_validation_profile_successor_unique_idx
                    ON response_validation_profiles(supersedes_publication_id)
                    WHERE supersedes_publication_id IS NOT NULL;
                CREATE INDEX IF NOT EXISTS response_validation_profile_strict_known_idx
                    ON response_validation_profiles(applicability_key, known_at, published_at, applicable_from);

                CREATE TABLE IF NOT EXISTS response_validation_event_allocations (
                    validation_event_id TEXT PRIMARY KEY,
                    base_validation_key_id TEXT NOT NULL,
                    revalidation_generation INTEGER NOT NULL,
                    predecessor_validation_event_id TEXT,
                    validation_cutoff TEXT NOT NULL,
                    payload_hash TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    UNIQUE(base_validation_key_id, revalidation_generation),
                    FOREIGN KEY (predecessor_validation_event_id)
                        REFERENCES response_validation_event_allocations(validation_event_id)
                );
                CREATE UNIQUE INDEX IF NOT EXISTS response_validation_base_event_unique_idx
                    ON response_validation_event_allocations(base_validation_key_id)
                    WHERE revalidation_generation = 0;
                CREATE UNIQUE INDEX IF NOT EXISTS response_validation_revalidation_predecessor_unique_idx
                    ON response_validation_event_allocations(predecessor_validation_event_id)
                    WHERE predecessor_validation_event_id IS NOT NULL;
                CREATE INDEX IF NOT EXISTS response_validation_event_cutoff_idx
                    ON response_validation_event_allocations(validation_cutoff, validation_event_id);

                CREATE TABLE IF NOT EXISTS response_validation_transient_capture_reservations (
                    response_capture_identity TEXT PRIMARY KEY,
                    validation_event_id TEXT NOT NULL,
                    FOREIGN KEY (validation_event_id)
                        REFERENCES response_validation_event_allocations(validation_event_id)
                );
                CREATE INDEX IF NOT EXISTS response_validation_transient_capture_owner_idx
                    ON response_validation_transient_capture_reservations(validation_event_id);
                """
            )

    @contextlib.contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=30.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        try:
            with connection:
                yield connection
        finally:
            connection.close()


def _profile_head(history: tuple[ResponseValidationProfile, ...]) -> ResponseValidationProfile | None:
    if not history:
        return None
    superseded = {item.supersedes_publication_id for item in history if item.supersedes_publication_id is not None}
    heads = tuple(item for item in history if item.publication_id not in superseded)
    if len(heads) != 1:
        raise ResponseValidatorPersistenceCorruption("profile history has ambiguous canonical head")
    return heads[0]


def _profile_payload(profile: ResponseValidationProfile) -> str:
    return _canonical_json(
        {
            **_jsonable(asdict(profile)),
            "publication_id": profile.publication_id,
            "content_hash": profile.spec.content_hash,
        }
    )


def _profile_from_row(row: sqlite3.Row) -> ResponseValidationProfile:
    payload = str(row["payload_json"])
    if _sha256(payload) != str(row["payload_hash"]):
        raise ResponseValidatorPersistenceCorruption("profile payload hash mismatch")
    try:
        item = json.loads(payload)
        publication_id = str(item.pop("publication_id"))
        content_hash = str(item.pop("content_hash"))
        spec = ResponseValidationProfileSpec(**item.pop("spec"))
        for name in ("applicable_from", "published_at", "known_at"):
            item[name] = _parse_time(str(item[name]))
        profile = ResponseValidationProfile(spec=spec, **item)
    except (KeyError, TypeError, ValueError, ResponseValidatorFoundationError) as error:
        raise ResponseValidatorPersistenceCorruption("profile payload is not canonical") from error
    if profile.publication_id != publication_id or profile.spec.content_hash != content_hash:
        raise ResponseValidatorPersistenceCorruption("profile identity or content hash mismatch")
    _require_row_metadata(
        row,
        {
            "applicability_key": profile.spec.applicability_key,
            "publication_id": profile.publication_id,
            "profile_version": profile.profile_version,
            "applicable_from": profile.applicable_from.isoformat(),
            "published_at": profile.published_at.isoformat(),
            "known_at": profile.known_at.isoformat(),
            "supersedes_publication_id": profile.supersedes_publication_id,
        },
        record_kind="profile",
    )
    return profile


def _allocation_payload(allocation: ValidationEventAllocation) -> str:
    return _canonical_json(
        {
            **_jsonable(asdict(allocation)),
            "validation_event_id": allocation.validation_event_id,
            "base_validation_key_id": allocation.base_validation_key.base_validation_key_id,
        }
    )


def _allocation_from_row(row: sqlite3.Row) -> ValidationEventAllocation:
    payload = str(row["payload_json"])
    if _sha256(payload) != str(row["payload_hash"]):
        raise ResponseValidatorPersistenceCorruption("validation allocation payload hash mismatch")
    try:
        item = json.loads(payload)
        validation_event_id = str(item.pop("validation_event_id"))
        base_validation_key_id = str(item.pop("base_validation_key_id"))
        key = BaseValidationKey(**item.pop("base_validation_key"))
        item["validation_cutoff"] = _parse_time(str(item["validation_cutoff"]))
        allocation = ValidationEventAllocation(base_validation_key=key, **item)
    except (KeyError, TypeError, ValueError, ResponseValidatorFoundationError) as error:
        raise ResponseValidatorPersistenceCorruption("validation allocation payload is not canonical") from error
    if allocation.validation_event_id != validation_event_id:
        raise ResponseValidatorPersistenceCorruption("validation event identity mismatch")
    if allocation.base_validation_key.base_validation_key_id != base_validation_key_id:
        raise ResponseValidatorPersistenceCorruption("base-validation key identity mismatch")
    _require_row_metadata(
        row,
        {
            "validation_event_id": allocation.validation_event_id,
            "base_validation_key_id": allocation.base_validation_key.base_validation_key_id,
            "revalidation_generation": allocation.revalidation_generation,
            "predecessor_validation_event_id": allocation.predecessor_validation_event_id,
            "validation_cutoff": allocation.validation_cutoff.isoformat(),
        },
        record_kind="validation allocation",
    )
    return allocation


def _require_row_metadata(
    row: sqlite3.Row,
    expected: Mapping[str, object],
    *,
    record_kind: str,
) -> None:
    """Fail closed when indexed SQL coordinates diverge from canonical bytes."""
    for name, expected_value in expected.items():
        try:
            actual_value = row[name]
        except (IndexError, KeyError) as error:
            raise ResponseValidatorPersistenceCorruption(f"{record_kind} SQL metadata is incomplete") from error
        if actual_value != expected_value:
            raise ResponseValidatorPersistenceCorruption(
                f"{record_kind} SQL metadata does not match canonical payload: {name}"
            )


def _canonical_json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _jsonable(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.astimezone(UTC).isoformat()
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    return value


def _parse_time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        raise ResponseValidatorPersistenceCorruption("persisted timestamp must be timezone-aware")
    return parsed.astimezone(UTC)
