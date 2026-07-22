from __future__ import annotations

from hunter.committee.composition import build_authoritative_committee_service
from hunter.committee.repository import InvestmentCommitteeRepository
from hunter.committee.resolver import RepositoryBackedCommitteeInputResolver
from hunter.persistence.sql import RepositoryFactory, SessionFactory, create_schema, create_sqlite_engine


def test_production_composition_uses_only_repository_backed_resolver(tmp_path) -> None:
    engine = create_sqlite_engine()
    create_schema(engine)
    session = SessionFactory(engine).create()
    try:
        service = build_authoritative_committee_service(
            output_repository=InvestmentCommitteeRepository(tmp_path / "committee.sqlite"),
            persistence_repositories=RepositoryFactory(session),
        )
        assert isinstance(service.input_resolver, RepositoryBackedCommitteeInputResolver)
    finally:
        session.close()
