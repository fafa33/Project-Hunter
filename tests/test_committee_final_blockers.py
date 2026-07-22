from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from hunter.committee.authority import CommitteeInputIdentity
from hunter.committee.models import CommitteeInputSet
from hunter.committee.repository import InvestmentCommitteeRepository
from hunter.committee.resolver import RepositoryBackedCommitteeInputResolver
from hunter.committee.service import (
    AuthoritativeInvestmentCommitteeService,
    CommitteeAuthorityError,
)
from hunter.persistence.records import SnapshotRecord
from hunter.persistence.sql import (
    RepositoryFactory,
    SessionFactory,
    create_schema,
    create_sqlite_engine,
)

NOW = datetime(2026, 7, 22, 12, 0, tzinfo=UTC)
IDENTITY = CommitteeInputIdentity(
    project_id="alpha",
    entity_id="entity:alpha",
    representation_id="ethereum:0xalpha",
    chain_id="eip155:1",
)


def _record(
    *,
    payload: dict[str, float],
    lifecycle_state: str | None = None,
) -> SnapshotRecord:
    metadata = {
        "authority_class": "production-authoritative",
        "project_id": IDENTITY.project_id,
        "entity_id": IDENTITY.entity_id,
        "representation_id": IDENTITY.representation_id,
        "chain_id": IDENTITY.chain_id or "",
        "lineage_id": "lineage:alpha",
        "revision_id": "revision:1",
    }
    if lifecycle_state is not None:
        metadata["lifecycle_state"] = lifecycle_state
    return SnapshotRecord(
        id="snapshot:alpha:final-blockers",
        created_at=NOW - timedelta(hours=1),
        effective_at=NOW - timedelta(hours=1),
        snapshot_type="committee-input",
        target_id="alpha",
        record_ids=("evidence:alpha",),
        payload=payload,
        metadata=metadata,
    )


def _evaluate(record: SnapshotRecord, tmp_path: Path) -> None:
    engine = create_sqlite_engine()
    create_schema(engine)
    session = SessionFactory(engine).create()
    try:
        factory = RepositoryFactory(session)
        factory.snapshots().save(record)
        session.commit()
        service = AuthoritativeInvestmentCommitteeService(
            repository=InvestmentCommitteeRepository(tmp_path / "committee.sqlite"),
            input_resolver=RepositoryBackedCommitteeInputResolver(factory),
        )
        service.evaluate_cycle(
            (
                CommitteeInputSet(
                    project_id="alpha",
                    effective_at=NOW,
                    authority_identity=IDENTITY,
                    snapshots=(record,),
                ),
            )
        )
    finally:
        session.close()


@pytest.mark.parametrize(
    "lifecycle_state",
    (
        "inactive",
        "withdrawn",
        "deprecated",
        "disabled",
        "archived",
        "rejected",
        "unknown",
    ),
)
def test_non_current_lifecycle_states_fail_closed(
    tmp_path: Path,
    lifecycle_state: str,
) -> None:
    with pytest.raises(
        CommitteeAuthorityError,
        match="non-current committee input lifecycle",
    ):
        _evaluate(
            _record(payload={"signal": 0.8}, lifecycle_state=lifecycle_state),
            tmp_path,
        )


@pytest.mark.parametrize("metric", ("risk", "backtesting_reliability"))
def test_generic_snapshot_risk_metrics_fail_closed(
    tmp_path: Path,
    metric: str,
) -> None:
    with pytest.raises(
        CommitteeAuthorityError,
        match="unavailable snapshot metrics",
    ):
        _evaluate(_record(payload={metric: 0.8}), tmp_path)
