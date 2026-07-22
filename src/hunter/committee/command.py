from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any

from hunter.committee.authority import CommitteeInputIdentity
from hunter.committee.composition import build_authoritative_committee_service
from hunter.committee.models import CommitteeInputSet
from hunter.committee.repository import InvestmentCommitteeRepository
from hunter.persistence.sql import RepositoryFactory, SessionFactory, create_schema, create_sqlite_engine

_APPLICATION_ROOT_ENV = "HUNTER_APPLICATION_ROOT"
_CANONICAL_PERSISTENCE_DATABASE = Path("data/data_ops.sqlite")
_CANONICAL_COMMITTEE_DATABASE = Path("data/committee/runtime/investment_committee.sqlite")


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        print("usage: hunter committee-authority MANIFEST.json")
        return 1
    manifest_path = Path(argv[0]).resolve()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    inputs = _load_inputs(manifest)
    application_root = _application_root()
    persistence_path = _canonical_path(application_root, _CANONICAL_PERSISTENCE_DATABASE)
    committee_path = _canonical_path(application_root, _CANONICAL_COMMITTEE_DATABASE)
    engine = create_sqlite_engine(persistence_path)
    create_schema(engine)
    session = SessionFactory(engine).create()
    try:
        repositories = RepositoryFactory(session)
        hydrated = tuple(_hydrate_input(item, repositories) for item in inputs)
        service = build_authoritative_committee_service(
            output_repository=InvestmentCommitteeRepository(committee_path),
            persistence_repositories=repositories,
        )
        champion, assessments = service.evaluate_cycle(hydrated)
    finally:
        session.close()
        engine.dispose()
    print(
        json.dumps(
            {
                "champion_id": champion.id,
                "selected_project_id": champion.selected_project_id,
                "decision": champion.decision,
                "assessment_ids": [assessment.id for assessment in assessments],
                "committee_database": str(committee_path),
            },
            sort_keys=True,
        )
    )
    return 0


def _application_root() -> Path:
    configured = os.environ.get(_APPLICATION_ROOT_ENV, "").strip()
    if not configured:
        raise ValueError(f"{_APPLICATION_ROOT_ENV} must identify the approved Hunter application root")
    root = Path(configured).expanduser()
    if not root.is_absolute():
        raise ValueError(f"{_APPLICATION_ROOT_ENV} must be an absolute path")
    return root.resolve()


def _canonical_path(root: Path, relative: Path) -> Path:
    candidate = (root / relative).resolve()
    if root != candidate and root not in candidate.parents:
        raise ValueError("canonical committee runtime path escaped application root")
    return candidate


def _load_inputs(manifest: Any) -> tuple[dict[str, Any], ...]:
    if not isinstance(manifest, dict) or not isinstance(manifest.get("inputs"), list):
        raise ValueError("committee manifest requires an inputs list")
    rows = tuple(item for item in manifest["inputs"] if isinstance(item, dict))
    if len(rows) != len(manifest["inputs"]) or not rows:
        raise ValueError("committee manifest inputs must be non-empty objects")
    return rows


def _hydrate_input(item: dict[str, Any], repositories: RepositoryFactory) -> CommitteeInputSet:
    identity = item.get("identity")
    if not isinstance(identity, dict):
        raise ValueError("committee manifest input requires identity")
    effective_at = datetime.fromisoformat(str(item["effective_at"]))
    if effective_at.tzinfo is None:
        raise ValueError("committee manifest effective_at must be timezone-aware")
    return CommitteeInputSet(
        project_id=str(item["project_id"]),
        effective_at=effective_at,
        authority_identity=CommitteeInputIdentity(
            project_id=str(identity["project_id"]),
            entity_id=str(identity["entity_id"]),
            representation_id=str(identity["representation_id"]),
            chain_id=str(identity["chain_id"]) if identity.get("chain_id") else None,
        ),
        intelligence=_records(repositories.intelligence(), item.get("intelligence_ids")),
        fused_intelligence=_records(repositories.fused_intelligence(), item.get("fused_intelligence_ids")),
        evidence=_records(repositories.evidence(), item.get("evidence_ids")),
        snapshots=_records(repositories.snapshots(), item.get("snapshot_ids")),
    )


def _records(repository: Any, raw_ids: Any) -> tuple[Any, ...]:
    if raw_ids is None:
        return ()
    if not isinstance(raw_ids, list) or any(not isinstance(record_id, str) for record_id in raw_ids):
        raise ValueError("committee manifest record IDs must be a list of strings")
    records = []
    for record_id in raw_ids:
        record = repository.load(record_id)
        if record is None:
            raise ValueError(f"committee manifest record not found: {record_id}")
        records.append(record)
    return tuple(records)
