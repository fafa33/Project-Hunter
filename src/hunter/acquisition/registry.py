from __future__ import annotations

from hunter.acquisition.contracts import EvidenceProvider
from hunter.acquisition.exceptions import AcquisitionRegistryError


class ProviderRegistry:
    def __init__(self) -> None:
        self._providers: dict[str, EvidenceProvider] = {}

    def register(self, provider: EvidenceProvider) -> EvidenceProvider:
        name = provider.metadata.name
        if name in self._providers:
            raise AcquisitionRegistryError(f"Provider already registered: {name}")
        self._providers[name] = provider
        return provider

    def get(self, name: str) -> EvidenceProvider:
        try:
            return self._providers[name]
        except KeyError as exc:
            raise AcquisitionRegistryError(f"Unknown provider: {name}") from exc

    def providers(self) -> tuple[EvidenceProvider, ...]:
        return tuple(self._providers[name] for name in sorted(self._providers))

    def by_capability(self, capability: str) -> tuple[EvidenceProvider, ...]:
        return tuple(provider for provider in self.providers() if capability in provider.metadata.capabilities)

    def by_metric(self, metric: str) -> tuple[EvidenceProvider, ...]:
        return tuple(provider for provider in self.providers() if metric in provider.metadata.supported_metrics)
