from __future__ import annotations

from hunter.committee.repository import InvestmentCommitteeRepository
from hunter.committee.resolver import RepositoryBackedCommitteeInputResolver
from hunter.committee.service import AuthoritativeInvestmentCommitteeService
from hunter.persistence.sql import RepositoryFactory


def build_authoritative_committee_service(
    *,
    output_repository: InvestmentCommitteeRepository,
    persistence_repositories: RepositoryFactory,
) -> AuthoritativeInvestmentCommitteeService:
    """Production composition root; external resolvers are deliberately not accepted."""

    return AuthoritativeInvestmentCommitteeService(
        repository=output_repository,
        input_resolver=RepositoryBackedCommitteeInputResolver(persistence_repositories),
    )
