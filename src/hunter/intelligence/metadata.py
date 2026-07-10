from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

MetadataValue = str | int | float | bool | None


@dataclass(frozen=True)
class IntelligenceMetadata:
    values: dict[str, MetadataValue] = field(default_factory=dict)

    def get(self, key: str, default: MetadataValue = None) -> MetadataValue:
        return self.values.get(key, default)

    def as_dict(self) -> dict[str, MetadataValue]:
        return dict(self.values)


def normalize_metadata(metadata: dict[str, Any] | IntelligenceMetadata | None) -> IntelligenceMetadata:
    if metadata is None:
        return IntelligenceMetadata()
    if isinstance(metadata, IntelligenceMetadata):
        return metadata
    normalized: dict[str, MetadataValue] = {}
    for key, value in metadata.items():
        if isinstance(value, str | int | float | bool) or value is None:
            normalized[str(key)] = value
        else:
            normalized[str(key)] = str(value)
    return IntelligenceMetadata(normalized)
