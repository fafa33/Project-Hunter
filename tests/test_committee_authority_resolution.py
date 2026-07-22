from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy.orm import Session

from hunter.committee.authority import CommitteeInputIdentity
from hunter.committee.models import CommitteeInputSet
from hunter.committee.repository import InvestmentCommitteeRepository
from hunter.committee.resolver import RepositoryBackedCommitteeInputResolver
from hunter.committee.service import AuthoritativeInvestmentCommitteeService, CommitteeAuthorityError
from hunter.persistence.records import SnapshotRecord
from hunter.persistence.sql import RepositoryFactory, SessionFactory, create_schema, create_sqlite_engine

NOW = datetime(2026, 7, 22, 12, 0, tzinfo=UTC)
IDENTITY = CommitteeInputIdentity(
    project_id="alpha",
    entity_id="entity:alpha",
    representation_id="ethereum:0xalpha",
    chain_id="eip155:1",
)


def metadata(*, revision: str = "revision:1", authority: str = "production-authoritative") -> dict[str, str]:
    return {
        "authority_class": authority,
        "project_id": IDENTITY.project_id,
        "entity_id": IDENTITY.entity_id,
        "representation_id": IDENTITY.representation_id,
        "chain_id": IDENTITY.chain_id or "",
        "lineage_id": "lineage:alpha",
        "revision_id": revision,
    }


def snapshot(
    record_id: str = "snapshot:alpha:1",
    *,
    created_at: datetime = NOW - timedelta(hours=1),
    effective_at: datetime = NOW - timedelta(hours=1),
    payload: dict[str, float] | None = None,
    record_metadata: dict[str, str] | None = None,
) -> SnapshotRecord:
    return SnapshotRecord(
        id=record_id,
        created_at=created_at,
        effective_at=effective_at,
        snapshot_type="committee-input",
        target_id="alpha",
        record_ids=("evidence:alpha",),
        payload=payload or {"risk": 0.2, "backtesting_reliability": 0.8},
        metadata=record_metadata or metadata(),
    )


def service_with(record: SnapshotRecord, tmp_path: Path) -> tuple[AuthoritativeInvestmentCommitteeService, Session]:
    engine = create_sqlite_engine()
    create_schema(engine)
    session = SessionFactory(engine).create()
    factory = RepositoryFactory(session)
    factory.snapshots().save(record)
    session.commit()
    resolver = RepositoryBackedCommitteeInputResolver(factory)
    database_name = f"committee-{record.id.replace(':', '-')}.sqlite"
    service = AuthoritativeInvestmentCommitteeService(
        repository=InvestmentCommitteeRepository(tmp_path / database_name),
        input_resolver=resolver,
    )
    return service, session


def inputs(record: SnapshotRecord, *, alerts: tuple[str, ...] = ()) -> CommitteeInputSet:
    return CommitteeInputSet(
        project_id="alpha",
        effective_at=NOW,
        authority_identity=IDENTITY,
        snapshots=(record,),
        alerts=alerts,
    )


def test_repository_backed_resolver_drives_authoritative_cycle(tmp_path: Path) -> None:
    record = snapshot()
    service, session = service_with(record, tmp_path)
    try:
        champion, assessments = service.evaluate_cycle((inputs(record),))
    finally:
        session.close()

    assert assessments[0].rank == 1
    assert assessments[0].source_record_ids == (record.id,)
    assert champion.created_at == assessments[0].created_at


def test_forged_caller_value_and_unknown_id_reject(tmp_path: Path) -> None:
    record = snapshot()
    service, session = service_with(record, tmp_path)
    try:
        forged = replace(record, payload={"risk": 0.9})
        with pytest.raises(CommitteeAuthorityError, match="differs from authoritative persisted record"):
            service.evaluate_cycle((inputs(forged),))
        unknown = replace(record, id="snapshot:unknown")
        with pytest.raises(CommitteeAuthorityError, match="not known by Hunter"):
            service.evaluate_cycle((inputs(unknown),))
    finally:
        session.close()


def test_missing_or_nonproduction_authority_rejects(tmp_path: Path) -> None:
    for authority in ("", "experimental", "descriptive-only", "unavailable"):
        record = snapshot(
            record_id=f"snapshot:{authority or 'missing'}",
            record_metadata=metadata(authority=authority),
        )
        service, session = service_with(record, tmp_path)
        try:
            with pytest.raises(CommitteeAuthorityError):
                service.evaluate_cycle((inputs(record),))
        finally:
            session.close()


def test_canonical_current_lineage_is_selected_from_persistence(tmp_path: Path) -> None:
    old = snapshot(record_id="snapshot:alpha:1", record_metadata=metadata(revision="revision:1"))
    current = snapshot(
        record_id="snapshot:alpha:2",
        created_at=NOW - timedelta(minutes=30),
        effective_at=NOW - timedelta(minutes=30),
        record_metadata=metadata(revision="revision:2"),
    )
    engine = create_sqlite_engine()
    create_schema(engine)
    session = SessionFactory(engine).create()
    try:
        factory = RepositoryFactory(session)
        factory.snapshots().save(old)
        factory.snapshots().save(current)
        session.commit()
        service = AuthoritativeInvestmentCommitteeService(
            repository=InvestmentCommitteeRepository(tmp_path / "committee-lineage.sqlite"),
            input_resolver=RepositoryBackedCommitteeInputResolver(factory),
        )
        with pytest.raises(CommitteeAuthorityError, match="superseded correction-lineage member"):
            service.evaluate_cycle((inputs(old),))
        service.evaluate_cycle((inputs(current),))
    finally:
        session.close()


def test_lineage_resolution_respects_historical_cutoff(tmp_path: Path) -> None:
    invalidated_at = (NOW + timedelta(days=1)).isoformat()
    old_metadata = {**metadata(revision="revision:1"), "invalidated_at": invalidated_at}
    old = snapshot(record_id="snapshot:alpha:1", record_metadata=old_metadata)
    future = snapshot(
        record_id="snapshot:alpha:2",
        created_at=NOW - timedelta(minutes=30),
        effective_at=NOW + timedelta(days=1),
        record_metadata=metadata(revision="revision:2"),
    )
    engine = create_sqlite_engine()
    create_schema(engine)
    session = SessionFactory(engine).create()
    try:
        factory = RepositoryFactory(session)
        factory.snapshots().save(old)
        factory.snapshots().save(future)
        session.commit()
        service = AuthoritativeInvestmentCommitteeService(
            repository=InvestmentCommitteeRepository(tmp_path / "committee-historical-lineage.sqlite"),
            input_resolver=RepositoryBackedCommitteeInputResolver(factory),
        )
        service.evaluate_cycle((inputs(old),))
    finally:
        session.close()


def test_alerts_and_unavailable_valuation_snapshot_metrics_are_blocked(tmp_path: Path) -> None:
    record = snapshot()
    service, session = service_with(record, tmp_path)
    try:
        with pytest.raises(CommitteeAuthorityError, match="critical alerts"):
            service.evaluate_cycle((inputs(record, alerts=("critical",)),))
    finally:
        session.close()

    valuation = snapshot(record_id="snapshot:valuation", payload={"valuation": 0.9})
    service, session = service_with(valuation, tmp_path)
    try:
        with pytest.raises(CommitteeAuthorityError, match="unavailable valuation-family"):
            service.evaluate_cycle((inputs(valuation),))
    finally:
        session.close()


def test_stale_future_known_and_cross_identity_inputs_reject(tmp_path: Path) -> None:
    stale = snapshot(effective_at=NOW - timedelta(days=8))
    service, session = service_with(stale, tmp_path)
    try:
        with pytest.raises(CommitteeAuthorityError, match="stale"):
            service.evaluate_cycle((inputs(stale),))
    finally:
        session.close()

    future = snapshot(created_at=NOW + timedelta(minutes=1), effective_at=NOW + timedelta(minutes=1))
    service, session = service_with(future, tmp_path)
    try:
        with pytest.raises(CommitteeAuthorityError, match="not known by Hunter"):
            service.evaluate_cycle((inputs(future),))
    finally:
        session.close()

    wrong_metadata = {
        **metadata(),
        "representation_id": "base:0xalpha",
        "chain_id": "eip155:8453",
    }
    wrong = snapshot(record_metadata=wrong_metadata)
    service, session = service_with(wrong, tmp_path)
    try:
        with pytest.raises(CommitteeAuthorityError, match="identity mismatch"):
            service.evaluate_cycle((inputs(wrong),))
    finally:
        session.close()
