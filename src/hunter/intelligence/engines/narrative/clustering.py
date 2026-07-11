from __future__ import annotations

from hunter.intelligence.engines.narrative.models import NarrativeCluster, NarrativeDataset, NarrativeEvidence


class NarrativeClusterer:
    def cluster(self, dataset: NarrativeDataset) -> tuple[NarrativeCluster, ...]:
        grouped: dict[str, list[NarrativeEvidence]] = {}
        for evidence in dataset.evidence:
            grouped.setdefault(evidence.category, []).append(evidence)
        clusters: list[NarrativeCluster] = []
        for category, items in sorted(grouped.items()):
            institutional = tuple(item for item in items if item.institutional)
            retail = tuple(item for item in items if item.retail)
            if institutional and retail and len(items) >= 4:
                clusters.append(_cluster(f"{category}-institutional", category, institutional, parent=category))
                clusters.append(_cluster(f"{category}-retail", category, retail, parent=category))
                clusters.append(
                    _cluster(
                        category, category, tuple(items), children=(f"{category}-institutional", f"{category}-retail")
                    )
                )
            else:
                clusters.append(_cluster(category, category, tuple(items)))
        return tuple(clusters)


def _cluster(
    cluster_id: str,
    category: str,
    evidence: tuple[NarrativeEvidence, ...],
    *,
    parent: str | None = None,
    children: tuple[str, ...] = (),
) -> NarrativeCluster:
    return NarrativeCluster(
        id=f"narrative-cluster-{cluster_id}",
        category=category,
        evidence=tuple(sorted(evidence, key=lambda item: item.id)),
        parent=f"narrative-{parent}" if parent else None,
        children=tuple(f"narrative-{child}" for child in children),
    )
