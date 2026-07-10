from __future__ import annotations

from itertools import combinations

from hunter.intelligence.fusion.configuration import FusionConfig
from hunter.intelligence.fusion.models import DependencyAssessment, FusionInput


def assess_dependencies(inputs: tuple[FusionInput, ...], config: FusionConfig) -> DependencyAssessment:
    edges: set[tuple[str, str, str]] = set()
    for left, right in combinations(inputs, 2):
        if left.engine_id == right.engine_id:
            continue
        shared_refs = set(left.evidence_references).intersection(item for item in right.evidence_references if item)
        shared_ids = set(left.evidence_ids).intersection(right.evidence_ids)
        shared_lineage = set(left.evidence_lineage_keys).intersection(item for item in right.evidence_lineage_keys if item)
        if shared_refs or shared_ids or shared_lineage:
            source, target = sorted((left.engine_id, right.engine_id))
            reason = "shared-evidence-lineage" if shared_lineage else "shared-evidence-reference" if shared_refs else "shared-evidence-id"
            edges.add((source, target, reason))
    dependent_engines = {engine for edge in edges for engine in edge[:2]}
    penalty = min(len(edges) * config.weighting.dependency_penalty, 1.0)
    explanation = "No evidence dependencies detected" if not edges else f"{len(edges)} dependency edge(s) detected"
    return DependencyAssessment(
        dependent_engine_ids=tuple(dependent_engines),
        dependency_edges=tuple(edges),
        penalty=penalty,
        explanation=explanation,
    )
