from __future__ import annotations

from hunter.intelligence.engines.narrative.configuration import NarrativeEngineConfiguration
from hunter.intelligence.engines.narrative.models import (
    NARRATIVE_CATEGORIES,
    NarrativeDataset,
    NarrativeEvidence,
    NarrativeRecord,
)


class NarrativeNormalizer:
    def __init__(self, configuration: NarrativeEngineConfiguration | None = None) -> None:
        self.configuration = configuration or NarrativeEngineConfiguration()

    def normalize(self, records: tuple[NarrativeRecord, ...]) -> NarrativeDataset:
        by_key: dict[str, NarrativeEvidence] = {}
        duplicates: list[str] = []
        filtered: list[str] = []
        for record in sorted(records, key=lambda item: (item.timestamp.isoformat(), item.id)):
            key = record.duplicate_key or _evidence_key(record)
            quality = record.reliability * record.strength
            if (
                record.category not in NARRATIVE_CATEGORIES
                or record.promotional
                or record.spam
                or quality < self.configuration.minimum_evidence_quality
            ):
                filtered.append(record.id)
                continue
            existing = by_key.get(key)
            if existing is None or record.reliability > existing.reliability:
                if existing is not None:
                    duplicates.append(existing.id)
                by_key[key] = record
            else:
                duplicates.append(record.id)
        evidence = tuple(sorted(by_key.values(), key=lambda item: (item.timestamp.isoformat(), item.id)))
        return NarrativeDataset(
            project=sorted({item.project for item in evidence})[0] if evidence else self.configuration.project,
            evidence=evidence,
            duplicates=tuple(sorted(set(duplicates))),
            filtered=tuple(sorted(set(filtered))),
            missing_fields=() if evidence else ("evidence",),
        )


def _evidence_key(evidence: NarrativeEvidence) -> str:
    normalized_text = " ".join(evidence.text.lower().split())
    return f"{evidence.category}|{evidence.source.lower()}|{normalized_text}"
