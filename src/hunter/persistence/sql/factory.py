from __future__ import annotations

from sqlalchemy.orm import Session

from hunter.persistence.sql.repositories import (
    SQLConfigurationRepository,
    SQLEngineManifestRepository,
    SQLEvidenceRepository,
    SQLInsightRepository,
    SQLIntelligenceRepository,
    SQLObservationRepository,
    SQLOperationalAttemptRepository,
    SQLPipelineRunRepository,
    SQLSignalRepository,
    SQLSnapshotRepository,
)


class RepositoryFactory:
    def __init__(self, session: Session) -> None:
        self._session = session

    def pipeline_runs(self) -> SQLPipelineRunRepository:
        return SQLPipelineRunRepository(self._session)

    def operational_attempts(self) -> SQLOperationalAttemptRepository:
        return SQLOperationalAttemptRepository(self._session)

    def evidence(self) -> SQLEvidenceRepository:
        return SQLEvidenceRepository(self._session)

    def signals(self) -> SQLSignalRepository:
        return SQLSignalRepository(self._session)

    def observations(self) -> SQLObservationRepository:
        return SQLObservationRepository(self._session)

    def insights(self) -> SQLInsightRepository:
        return SQLInsightRepository(self._session)

    def intelligence(self) -> SQLIntelligenceRepository:
        return SQLIntelligenceRepository(self._session)

    def snapshots(self) -> SQLSnapshotRepository:
        return SQLSnapshotRepository(self._session)

    def configurations(self) -> SQLConfigurationRepository:
        return SQLConfigurationRepository(self._session)

    def engine_manifests(self) -> SQLEngineManifestRepository:
        return SQLEngineManifestRepository(self._session)
