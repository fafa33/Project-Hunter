from __future__ import annotations

from typing import Protocol, runtime_checkable

from hunter.acquisition.models import (
    AcquisitionCheckpoint,
    AcquisitionRequest,
    AcquisitionRun,
    EvidenceValidation,
    NormalizedEvidence,
    ProviderHealth,
    ProviderMetadata,
    RawEvidence,
)


@runtime_checkable
class EvidenceProvider(Protocol):
    @property
    def metadata(self) -> ProviderMetadata:
        raise NotImplementedError

    def health(self) -> ProviderHealth:
        raise NotImplementedError

    def fetch(self, request: AcquisitionRequest) -> tuple[RawEvidence, ...]:
        raise NotImplementedError


@runtime_checkable
class EvidenceCollector(Protocol):
    id: str

    def collect(self, provider: EvidenceProvider, request: AcquisitionRequest) -> tuple[RawEvidence, ...]:
        raise NotImplementedError


@runtime_checkable
class EvidenceNormalizer(Protocol):
    def normalize(self, raw: tuple[RawEvidence, ...], request: AcquisitionRequest) -> tuple[NormalizedEvidence, ...]:
        raise NotImplementedError


@runtime_checkable
class EvidenceValidator(Protocol):
    def validate(
        self,
        evidence: tuple[NormalizedEvidence, ...],
        *,
        as_of: object,
    ) -> tuple[EvidenceValidation, ...]:
        raise NotImplementedError


@runtime_checkable
class AcquisitionRepository(Protocol):
    def save_raw(self, raw: tuple[RawEvidence, ...]) -> tuple[RawEvidence, ...]:
        raise NotImplementedError

    def save_normalized(self, evidence: tuple[NormalizedEvidence, ...]) -> tuple[NormalizedEvidence, ...]:
        raise NotImplementedError

    def save_validations(self, validations: tuple[EvidenceValidation, ...]) -> tuple[EvidenceValidation, ...]:
        raise NotImplementedError

    def save_run(self, run: AcquisitionRun) -> AcquisitionRun:
        raise NotImplementedError

    def save_checkpoint(self, checkpoint: AcquisitionCheckpoint) -> AcquisitionCheckpoint:
        raise NotImplementedError

    def latest_checkpoint(self, provider: str, domain: str, target_id: str) -> AcquisitionCheckpoint | None:
        raise NotImplementedError
