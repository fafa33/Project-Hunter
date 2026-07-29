from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from hunter.evidence_assembly.models import EvidenceShape


class EvidenceShapeRegistryError(ValueError):
    pass


@dataclass(frozen=True)
class EvidenceShapeRegistry:
    version: str
    shapes: tuple[EvidenceShape, ...]
    effective_at: datetime
    recorded_at: datetime
    known_at: datetime
    quality_state: str
    conflict_state: str
    content_hash: str

    def __post_init__(self) -> None:
        if not self.version.strip():
            raise EvidenceShapeRegistryError("registry version is required")
        if not self.shapes:
            raise EvidenceShapeRegistryError("registry must contain governed reference data")
        identifiers = [shape.shape_id for shape in self.shapes]
        if len(set(identifiers)) != len(identifiers):
            raise EvidenceShapeRegistryError("shape IDs must be unique within a registry version")
        if any(shape.registry_version != self.version for shape in self.shapes):
            raise EvidenceShapeRegistryError("shape entry version does not match registry version")
        if self.quality_state != "accepted" or self.conflict_state not in {"none", "resolved"}:
            raise EvidenceShapeRegistryError("registry snapshot is not authoritative")
        if not self.content_hash.strip():
            raise EvidenceShapeRegistryError("registry content hash is required")

    def require_exact_sum_compatible(self, shape_ids: tuple[str, ...]) -> tuple[EvidenceShape, ...]:
        by_id = {shape.shape_id: shape for shape in self.shapes}
        resolved: list[EvidenceShape] = []
        for shape_id in shape_ids:
            shape = by_id.get(shape_id)
            if shape is None:
                raise EvidenceShapeRegistryError(f"unknown evidence shape: {shape_id}")
            if not shape.active:
                raise EvidenceShapeRegistryError(f"inactive evidence shape: {shape_id}")
            if shape.composition_operation != "exact_sum":
                raise EvidenceShapeRegistryError(f"evidence shape is not exact-sum compatible: {shape_id}")
            resolved.append(shape)
        if len({shape.accounting_meaning for shape in resolved}) != 1:
            raise EvidenceShapeRegistryError("evidence shapes have incompatible accounting meanings")
        return tuple(resolved)
