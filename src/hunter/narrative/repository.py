from __future__ import annotations

from collections import Counter
from dataclasses import dataclass

from hunter.acquisition.repositories import FileAcquisitionRepository, InMemoryAcquisitionRepository


class NarrativeRepository:
    def __init__(self, acquisition_repository: InMemoryAcquisitionRepository | None = None) -> None:
        self.acquisition_repository = acquisition_repository or FileAcquisitionRepository()

    def valid_evidence_ids(self) -> tuple[str, ...]:
        return tuple(
            sorted(
                evidence_id
                for evidence_id, validation in self.acquisition_repository.validations.items()
                if validation.status == "valid"
                and (evidence := self.acquisition_repository.normalized.get(evidence_id)) is not None
                and evidence.provider == "narrative"
            )
        )


@dataclass(frozen=True)
class NarrativeStatistics:
    raw: int
    normalized: int
    valid: int
    duplicate: int
    stale: int
    invalid: int
    projects: int
    providers: tuple[str, ...]


def narrative_statistics(repository: InMemoryAcquisitionRepository) -> NarrativeStatistics:
    raw = tuple(item for item in repository.raw.values() if item.provider == "narrative")
    normalized = tuple(item for item in repository.normalized.values() if item.provider == "narrative")
    normalized_ids = {item.evidence_id for item in normalized}
    validations = tuple(item for item in repository.validations.values() if item.evidence_id in normalized_ids)
    counts = Counter(item.status for item in validations)
    valid_ids = {item.evidence_id for item in validations if item.status == "valid"}
    projects = {item.target_id for item in normalized if item.evidence_id in valid_ids}
    providers = {
        str(item.payload.get("provider"))
        for item in raw
        if isinstance(item.payload.get("provider"), str) and item.payload.get("provider")
    }
    return NarrativeStatistics(
        raw=len(raw),
        normalized=len(normalized),
        valid=counts["valid"],
        duplicate=counts["duplicate"],
        stale=counts["stale"],
        invalid=counts["invalid"],
        projects=len(projects),
        providers=tuple(sorted(providers)),
    )
