from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from hunter.evidence_assembly.models import Cadence, EvidenceShape
from hunter.persistence.models import QuerySpec
from hunter.persistence.records import SnapshotRecord
from hunter.persistence.sql import RepositoryFactory, SessionFactory, create_schema, create_sqlite_engine
from hunter.persistence.sql.exceptions import PersistenceIdentityConflictError

DEFAULT_REGISTRY_DB = Path("data/data_ops.sqlite")
REGISTRY_MIGRATION_ID = "generic-sql-evidence-shape-registry-v1"
REGISTRY_SNAPSHOT_TYPE = "evidence-shape-registry"
REGISTRY_SCHEMA_VERSION = "evidence-shape-registry-v1"
REGISTRY_AUTHORITY_ID = "canonical-evidence-shape-registry-authority-v1"
REGISTRY_AUTHORIZING_ADR = "ADR-0025"
REGISTRY_AUTHORIZING_ADR_HASH = "d952fd4ef03378a73264b54e1ae048a697ba41126f262c2895b0764505be5d2b"
CANONICAL_REGISTRY_ID = "canonical-valuation-evidence-shapes"
CANONICAL_REGISTRY_VERSION = "1"
_CADENCE_RANK: dict[Cadence, int] = {
    "daily": 1,
    "monthly": 2,
    "quarterly": 3,
    "semiannual": 4,
    "annual": 5,
}
_CALENDAR_CADENCES: tuple[Cadence, ...] = (
    "daily",
    "monthly",
    "quarterly",
    "semiannual",
    "annual",
)
CANONICAL_INITIAL_SHAPES: tuple[EvidenceShape, ...] = tuple(
    EvidenceShape(
        shape_id=f"{cadence}-revenue",
        evidence_type="audited_financial_disclosure",
        source_methodology=f"reported-{cadence}" if cadence != "semiannual" else "reported",
        accounting_meaning="period_specific",
        cadence=cadence,
        composition_operation="none" if cadence == "annual" else "exact_sum",
        active=True,
        currency="USD",
        unit="USD",
        compatible_cadences=tuple(item for item in _CALENDAR_CADENCES if item != cadence),
    )
    for cadence in _CALENDAR_CADENCES
) + (
    EvidenceShape(
        shape_id="irregular",
        evidence_type="audited_financial_disclosure",
        source_methodology="reported-irregular",
        accounting_meaning="period_specific",
        cadence="irregular",
        composition_operation="exact_sum",
        active=True,
        currency="USD",
        unit="USD",
    ),
    EvidenceShape(
        shape_id="event-driven",
        evidence_type="audited_financial_disclosure",
        source_methodology="reported-event",
        accounting_meaning="event",
        cadence="event_driven",
        composition_operation="none",
        active=False,
        currency="USD",
        unit="USD",
    ),
    EvidenceShape(
        shape_id="epoch-based",
        evidence_type="onchain_observation",
        source_methodology="reported-epoch",
        accounting_meaning="cumulative",
        cadence="epoch_based",
        composition_operation="none",
        active=False,
        currency="USD",
        unit="USD",
    ),
)


class EvidenceShapeRegistryError(ValueError):
    pass


@dataclass(frozen=True)
class EvidenceShapeRegistrySnapshot:
    record_id: str
    registry_id: str
    registry_version: str
    schema_version: str
    shapes: tuple[EvidenceShape, ...]
    effective_start: datetime
    effective_end: datetime
    recorded_at: datetime
    known_at: datetime
    active: bool
    quality_state: str
    conflict_state: str
    authorized_by: str
    authorizing_adr_reference: str
    content_hash: str
    supersedes_record_id: str | None = None
    correction_reason: str = ""

    def __post_init__(self) -> None:
        for name in (
            "record_id",
            "registry_id",
            "registry_version",
            "schema_version",
            "authorized_by",
            "authorizing_adr_reference",
            "content_hash",
        ):
            if not str(getattr(self, name)).strip():
                raise EvidenceShapeRegistryError(f"{name} is required")
        for name in ("effective_start", "effective_end", "recorded_at", "known_at"):
            value = getattr(self, name)
            if value.tzinfo is None or value.utcoffset() is None:
                raise EvidenceShapeRegistryError(f"{name} must be timezone-aware")
            object.__setattr__(self, name, value.astimezone(UTC))
        if self.effective_start >= self.effective_end:
            raise EvidenceShapeRegistryError("registry effective interval must be positive")
        if self.recorded_at > self.known_at:
            raise EvidenceShapeRegistryError("registry recorded_at must not follow known_at")
        if not self.shapes:
            raise EvidenceShapeRegistryError("registry must contain governed shapes")
        ordered = tuple(sorted(self.shapes, key=lambda shape: shape.shape_id))
        if len({shape.shape_id for shape in ordered}) != len(ordered):
            raise EvidenceShapeRegistryError("registry shape IDs must be unique")
        object.__setattr__(self, "shapes", ordered)
        if self.quality_state != "accepted" or self.conflict_state not in {"none", "resolved"}:
            raise EvidenceShapeRegistryError("registry snapshot is not authoritative")
        if self.authorized_by != REGISTRY_AUTHORITY_ID:
            raise EvidenceShapeRegistryError("registry snapshot lacks canonical authority attestation")
        if self.authorizing_adr_reference != REGISTRY_AUTHORIZING_ADR:
            raise EvidenceShapeRegistryError("registry snapshot lacks accepted-ADR authorization")
        if bool(self.supersedes_record_id) != bool(self.correction_reason.strip()):
            raise EvidenceShapeRegistryError("registry correction predecessor and reason must be paired")

    def shape(self, shape_id: str) -> EvidenceShape:
        match = next((shape for shape in self.shapes if shape.shape_id == shape_id), None)
        if match is None:
            raise EvidenceShapeRegistryError(f"unknown evidence shape: {shape_id}")
        if not match.active:
            raise EvidenceShapeRegistryError(f"inactive evidence shape: {shape_id}")
        return match

    def cadence_rank(self, shape: EvidenceShape) -> int:
        rank = _CADENCE_RANK.get(shape.cadence)
        if rank is None:
            raise EvidenceShapeRegistryError(f"cadence is not intrinsically comparable: {shape.cadence}")
        return rank

    def compare_cadence(self, left: EvidenceShape, right: EvidenceShape) -> int:
        if left.cadence == right.cadence:
            return 0
        if right.cadence not in left.compatible_cadences or left.cadence not in right.compatible_cadences:
            raise EvidenceShapeRegistryError("cadence compatibility is not governed")
        return -1 if self.cadence_rank(left) < self.cadence_rank(right) else 1


class EvidenceShapeRegistryRepository:
    def __init__(self, path: str | Path = DEFAULT_REGISTRY_DB) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        engine = create_sqlite_engine(self.path)
        try:
            create_schema(engine)
        finally:
            engine.dispose()

    def migration_ids(self) -> tuple[str, ...]:
        return (REGISTRY_MIGRATION_ID,)

    def get(self, record_id: str) -> EvidenceShapeRegistrySnapshot | None:
        engine = create_sqlite_engine(self.path)
        session = SessionFactory(engine).create()
        try:
            item = RepositoryFactory(session).snapshots().load(record_id)
            if item is None or item.snapshot_type != REGISTRY_SNAPSHOT_TYPE:
                return None
            result = _from_payload(item.payload)
            if _content_hash(result) != result.content_hash:
                raise EvidenceShapeRegistryError("registry canonical content hash mismatch")
            return result
        finally:
            session.close()
            engine.dispose()

    def history(self, registry_id: str) -> tuple[EvidenceShapeRegistrySnapshot, ...]:
        records = [item for item in self._records() if item.registry_id == registry_id]
        records.sort(key=lambda item: (item.effective_start, item.recorded_at, item.known_at, item.record_id))
        return tuple(records)

    def strict_known(
        self,
        *,
        registry_id: str,
        registry_version: str,
        effective_as_of: datetime,
        known_by: datetime,
    ) -> EvidenceShapeRegistrySnapshot | None:
        effective_as_of, known_by = _aware(effective_as_of), _aware(known_by)
        eligible = [
            item
            for item in self.history(registry_id)
            if item.registry_version == registry_version
            and item.active
            and item.effective_start <= effective_as_of < item.effective_end
            and item.recorded_at <= known_by
            and item.known_at <= known_by
        ]
        superseded = {item.supersedes_record_id for item in eligible if item.supersedes_record_id}
        tips = [item for item in eligible if item.record_id not in superseded]
        tips.sort(key=lambda item: (item.recorded_at, item.known_at, item.record_id), reverse=True)
        if not tips:
            return None
        result = tips[0]
        if _content_hash(result) != result.content_hash:
            raise EvidenceShapeRegistryError("registry canonical content hash mismatch")
        return result

    def _records(self) -> tuple[EvidenceShapeRegistrySnapshot, ...]:
        engine = create_sqlite_engine(self.path)
        session = SessionFactory(engine).create()
        try:
            rows = RepositoryFactory(session).snapshots().query(QuerySpec(record_kind="snapshot"))
            result = []
            for row in rows:
                if row.snapshot_type != REGISTRY_SNAPSHOT_TYPE:
                    continue
                try:
                    result.append(_from_payload(row.payload))
                except (KeyError, TypeError, ValueError) as exc:
                    raise EvidenceShapeRegistryError(f"malformed canonical registry snapshot: {row.id}") from exc
            return tuple(result)
        finally:
            session.close()
            engine.dispose()


class EvidenceShapeRegistryAuthority:
    """Canonical reference-data write and strict-known resolution boundary."""

    def __init__(self, repository: EvidenceShapeRegistryRepository, *, application_root: Path) -> None:
        if not application_root.is_absolute():
            raise EvidenceShapeRegistryError("application_root must be absolute")
        self.repository = repository
        self._application_root = application_root.resolve()
        adr_path = self._application_root / "docs" / "ADR" / "0025-canonical-valuation-evidence-assembly-authority.md"
        if not adr_path.is_file():
            raise EvidenceShapeRegistryError("accepted Registry-authorizing ADR is unavailable")
        if hashlib.sha256(adr_path.read_bytes()).hexdigest() != REGISTRY_AUTHORIZING_ADR_HASH:
            raise EvidenceShapeRegistryError("Registry-authorizing ADR content is not the accepted version")

    def persist(
        self,
        *,
        registry_id: str,
        registry_version: str,
        effective_start: datetime,
        effective_end: datetime,
        recorded_at: datetime,
        known_at: datetime,
        authorizing_adr_reference: str,
        active: bool = True,
        supersedes_record_id: str | None = None,
        correction_reason: str = "",
    ) -> EvidenceShapeRegistrySnapshot:
        if registry_id != CANONICAL_REGISTRY_ID or registry_version != CANONICAL_REGISTRY_VERSION:
            raise EvidenceShapeRegistryError("registry identity or version is not governed by an accepted ADR")
        if authorizing_adr_reference != REGISTRY_AUTHORIZING_ADR:
            raise EvidenceShapeRegistryError("registry amendment lacks accepted-ADR authorization")
        if supersedes_record_id is not None:
            raise EvidenceShapeRegistryError(
                "ADR-0025 authorizes only the initial taxonomy; amendments require a newly accepted ADR"
            )
        pending = EvidenceShapeRegistrySnapshot(
            record_id="pending",
            registry_id=registry_id,
            registry_version=registry_version,
            schema_version=REGISTRY_SCHEMA_VERSION,
            shapes=CANONICAL_INITIAL_SHAPES,
            effective_start=effective_start,
            effective_end=effective_end,
            recorded_at=recorded_at,
            known_at=known_at,
            active=active,
            quality_state="accepted",
            conflict_state="none",
            authorized_by=REGISTRY_AUTHORITY_ID,
            authorizing_adr_reference=authorizing_adr_reference,
            content_hash="pending",
            supersedes_record_id=supersedes_record_id,
            correction_reason=correction_reason,
        )
        digest = _content_hash(pending)
        record = replace(pending, record_id=f"evidence-shape-registry:{digest}", content_hash=digest)
        engine = create_sqlite_engine(self.repository.path)
        session = SessionFactory(engine).create()
        try:
            session.connection().exec_driver_sql("BEGIN IMMEDIATE")
            snapshots = RepositoryFactory(session).snapshots()
            self._authorize_lineage(record, snapshots=snapshots)
            snapshots.save(_snapshot(record))
            session.commit()
        except PersistenceIdentityConflictError as exc:
            session.rollback()
            raise EvidenceShapeRegistryError(str(exc)) from exc
        finally:
            session.close()
            engine.dispose()
        return record

    def strict_known_registry(self, **kwargs: Any) -> EvidenceShapeRegistrySnapshot | None:
        return self.repository.strict_known(**kwargs)

    def _authorize_lineage(self, record: EvidenceShapeRegistrySnapshot, *, snapshots: Any) -> None:
        history = tuple(
            _from_payload(item.payload)
            for item in snapshots.query(QuerySpec(record_kind="snapshot"))
            if item.snapshot_type == REGISTRY_SNAPSHOT_TYPE and item.target_id == record.registry_id
        )
        if record.supersedes_record_id is None:
            if any(
                item.registry_version == record.registry_version and item.record_id != record.record_id
                for item in history
            ):
                raise EvidenceShapeRegistryError("registry version already has a root; correction required")
            return
        predecessor = next(
            (item for item in history if item.record_id == record.supersedes_record_id),
            None,
        )
        if predecessor is None or predecessor.registry_id != record.registry_id:
            raise EvidenceShapeRegistryError("registry correction predecessor is invalid")
        if predecessor.recorded_at >= record.recorded_at or predecessor.known_at >= record.known_at:
            raise EvidenceShapeRegistryError("registry correction clocks must advance")
        if any(
            item.supersedes_record_id == predecessor.record_id and item.record_id != record.record_id
            for item in history
        ):
            raise EvidenceShapeRegistryError("branching registry correction")


def _snapshot(record: EvidenceShapeRegistrySnapshot) -> SnapshotRecord:
    return SnapshotRecord(
        id=record.record_id,
        created_at=record.recorded_at,
        effective_at=record.effective_start,
        snapshot_type=REGISTRY_SNAPSHOT_TYPE,
        target_id=record.registry_id,
        record_ids=(record.record_id,),
        payload=_json_safe(asdict(record)),
        metadata={"known_at": record.known_at.isoformat(), "content_hash": record.content_hash},
    )


def _from_payload(payload: dict[str, Any]) -> EvidenceShapeRegistrySnapshot:
    result = dict(payload)
    result["shapes"] = tuple(EvidenceShape(**shape) for shape in result["shapes"])
    for name in ("effective_start", "effective_end", "recorded_at", "known_at"):
        result[name] = datetime.fromisoformat(str(result[name]))
    return EvidenceShapeRegistrySnapshot(**result)


def _content_hash(record: EvidenceShapeRegistrySnapshot) -> str:
    payload = asdict(record)
    payload["record_id"] = ""
    payload["content_hash"] = ""
    raw = json.dumps(_json_safe(payload), sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(raw).hexdigest()


def _json_safe(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.astimezone(UTC).isoformat()
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    return value


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise EvidenceShapeRegistryError("datetime must be timezone-aware")
    return value.astimezone(UTC)
