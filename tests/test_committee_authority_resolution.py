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
        payload=payload or {"signal": 0.8},
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


def test_repository_backed_resolver_accepts_persisted_authoritative_input(tmp_path: Path) -> None:
    record = snapshot()
    service, session = service_with(record, tmp_path)
    try:
        champion, assessments = service.evaluate_cycle((inputs(record),))
    finally:
        session.close()

    assert assessments[0].rank == 1
    assert assessments[0].source_record_ids == (record.id,)
    assert champion.created_at == assessments[0].created_at


def test_caller_forged_snapshot_is_rejected(tmp_path: Path) -> None:
    persisted = snapshot()
    forged = replace(persisted, payload={"signal": 0.1})
    service, session = service_with(persisted, tmp_path)
    try:
        with pytest.raises(CommitteeAuthorityError, match="differs from authoritative persisted record"):
            service.evaluate_cycle((inputs(forged),))
    finally:
        session.close()


def test_missing_unavailable_and_future_inputs_fail_closed(tmp_path: Path) -> None:
    persisted = snapshot()
    service, session = service_with(persisted, tmp_path)
    try:
        missing = replace(persisted, id="snapshot:missing")
        with pytest.raises(CommitteeAuthorityError, match="not known by Hunter"):
            service.evaluate_cycle((inputs(missing),))
    finally:
        session.close()

    future = snapshot(
        record_id="snapshot:future",
        created_at=NOW + timedelta(minutes=1),
        effective_at=NOW + timedelta(minutes=1),
    )
    service, session = service_with(future, tmp_path)
    try:
        with pytest.raises(CommitteeAuthorityError, match="not known by Hunter"):
            service.evaluate_cycle((inputs(future),))
    finally:
        session.close()


def test_stale_identity_and_authority_mismatch_are_rejected(tmp_path: Path) -> None:
    stale = snapshot(effective_at=NOW - timedelta(days=31))
    service, session = service_with(stale, tmp_path)
    try:
        with pytest.raises(CommitteeAuthorityError, match="stale"):
            service.evaluate_cycle((inputs(stale),))
    finally:
        session.close()

    wrong_identity = snapshot(
        record_id="snapshot:wrong-identity",
        record_metadata={**metadata(), "project_id": "beta"},
    )
    service, session = service_with(wrong_identity, tmp_path)
    try:
        with pytest.raises(CommitteeAuthorityError, match="identity mismatch"):
            service.evaluate_cycle((inputs(wrong_identity),))
    finally:
        session.close()

    experimental = snapshot(
        record_id="snapshot:experimental",
        record_metadata=metadata(authority="experimental"),
    )
    service, session = service_with(experimental, tmp_path)
    try:
        with pytest.raises(CommitteeAuthorityError, match="experimental committee input"):
            service.evaluate_cycle((inputs(experimental),))
    finally:
        session.close()


def test_superseded_lineage_member_is_rejected(tmp_path: Path) -> None:
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
        with pytest.raises(CommitteeAuthorityError, match="unavailable snapshot metrics"):
            service.evaluate_cycle((inputs(valuation),))
    finally:
        session.close()


@pytest.mark.parametrize("family", ("opportunity", "probability", "pattern", "necessity"))
def test_unavailable_derived_families_fail_closed(tmp_path: Path, family: str) -> None:
    record = snapshot()
    service, session = service_with(record, tmp_path)
    try:
        kwargs = {family: object()}
        candidate = replace(inputs(record), **kwargs)
        with pytest.raises(CommitteeAuthorityError, match="persisted assessment IDs|not known by Hunter"):
            service.evaluate_cycle((candidate,))
    finally:
        session.close()
