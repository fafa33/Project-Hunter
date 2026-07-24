from __future__ import annotations

from hunter.committee.repository import InvestmentCommitteeRepository
from hunter.committee.resolver import RepositoryBackedCommitteeInputResolver
from hunter.committee.service import AuthoritativeInvestmentCommitteeService
from hunter.committee.sql_output import GenericSQLCommitteeOutput
from hunter.persistence.sql import RepositoryFactory


def build_authoritative_committee_service(
    *,
    persistence_repositories: RepositoryFactory,
    pipeline_run_id: str | None = None,
    output_repository: InvestmentCommitteeRepository | None = None,
) -> AuthoritativeInvestmentCommitteeService:
    """Production composition root; external resolvers are deliberately not accepted."""

    resolver = RepositoryBackedCommitteeInputResolver(persistence_repositories)
    if pipeline_run_id is not None:
        return AuthoritativeInvestmentCommitteeService(
            output=GenericSQLCommitteeOutput(persistence_repositories, pipeline_run_id),
            input_resolver=resolver,
        )
    if output_repository is None:
        raise ValueError("pipeline_run_id is required for canonical production committee output")
    return AuthoritativeInvestmentCommitteeService(
        repository=output_repository,
        input_resolver=resolver,
    )
