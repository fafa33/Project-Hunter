from __future__ import annotations

from hunter.acquisition.contracts import EvidenceProvider
from hunter.acquisition.models import AcquisitionRequest, RawEvidence


class ProviderCollector:
    id = "provider-collector"

    def collect(self, provider: EvidenceProvider, request: AcquisitionRequest) -> tuple[RawEvidence, ...]:
        return provider.fetch(request)
