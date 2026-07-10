from __future__ import annotations

from hunter.intelligence.fusion.models import FusionInput


def deduplicate_evidence(inputs: tuple[FusionInput, ...]) -> tuple[str, ...]:
    keys: set[str] = set()
    for item in inputs:
        for reference in item.evidence_references:
            keys.add(f"reference:{reference}")
        for evidence_id in item.evidence_ids:
            keys.add(f"id:{evidence_id}")
    return tuple(sorted(keys))


def deduplicate_sources(inputs: tuple[FusionInput, ...]) -> tuple[FusionInput, ...]:
    by_id = {item.intelligence_id: item for item in inputs}
    return tuple(by_id[key] for key in sorted(by_id))
