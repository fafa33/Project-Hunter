from __future__ import annotations

from hunter.execution.identity import identity
from hunter.intelligence.fusion.models import CanonicalEvidence, FusionInput


def canonicalize_evidence(inputs: tuple[FusionInput, ...]) -> tuple[CanonicalEvidence, ...]:
    groups: list[dict[str, set[str]]] = []
    for item in inputs:
        count = max(len(item.evidence_ids), len(item.evidence_references), len(item.evidence_lineage_keys))
        for index in range(count):
            evidence_id = _at(item.evidence_ids, index)
            reference = _at(item.evidence_references, index)
            lineage_key = _at(item.evidence_lineage_keys, index)
            keys = {key for key in (f"id:{evidence_id}" if evidence_id else "", f"reference:{reference}" if reference else "", f"lineage:{lineage_key}" if lineage_key else "") if key}
            if not keys:
                continue
            group = _matching_group(groups, keys)
            group["keys"].update(keys)
            if evidence_id:
                group["ids"].add(evidence_id)
            if reference:
                group["references"].add(reference)
            if lineage_key:
                group["lineage"].add(lineage_key)
            group["intelligence"].add(item.intelligence_id)
    canonical: list[CanonicalEvidence] = []
    for group in groups:
        key = _canonical_key(group)
        canonical.append(
            CanonicalEvidence(
                canonical_key=key,
                evidence_ids=tuple(group["ids"]),
                references=tuple(group["references"]),
                lineage_keys=tuple(group["lineage"]),
                source_intelligence_ids=tuple(group["intelligence"]),
            )
        )
    return tuple(sorted(canonical, key=lambda item: item.canonical_key))


def deduplicate_evidence(inputs: tuple[FusionInput, ...]) -> tuple[str, ...]:
    return tuple(item.canonical_key for item in canonicalize_evidence(inputs))


def deduplicate_sources(inputs: tuple[FusionInput, ...]) -> tuple[FusionInput, ...]:
    by_id = {item.intelligence_id: item for item in inputs}
    return tuple(by_id[key] for key in sorted(by_id))


def _at(values: tuple[str, ...], index: int) -> str:
    if index >= len(values):
        return ""
    return values[index]


def _matching_group(groups: list[dict[str, set[str]]], keys: set[str]) -> dict[str, set[str]]:
    matches = [group for group in groups if group["keys"].intersection(keys)]
    if not matches:
        group = {"keys": set(), "ids": set(), "references": set(), "lineage": set(), "intelligence": set()}
        groups.append(group)
        return group
    primary = matches[0]
    for extra in matches[1:]:
        primary["keys"].update(extra["keys"])
        primary["ids"].update(extra["ids"])
        primary["references"].update(extra["references"])
        primary["lineage"].update(extra["lineage"])
        primary["intelligence"].update(extra["intelligence"])
        groups.remove(extra)
    return primary


def _canonical_key(group: dict[str, set[str]]) -> str:
    if group["lineage"]:
        prefix = "lineage"
        values = sorted(group["lineage"])
    elif group["references"]:
        prefix = "reference"
        values = sorted(group["references"])
    else:
        prefix = "id"
        values = sorted(group["ids"])
    return identity("fusion-evidence", {"type": prefix, "values": values})
