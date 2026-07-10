from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from hunter.intelligence.confidence import Confidence
from hunter.intelligence.exceptions import IntelligenceAggregationError
from hunter.intelligence.intelligence import Intelligence
from hunter.intelligence.validator import IntelligenceValidator


@dataclass(frozen=True)
class IntelligenceCollection:
    intelligence: tuple[Intelligence, ...]
    confidence: Confidence

    @property
    def projects(self) -> tuple[str, ...]:
        return tuple(sorted({item.project for item in self.intelligence}))

    @property
    def engines(self) -> tuple[str, ...]:
        return tuple(sorted({item.engine for item in self.intelligence}))


class IntelligenceAggregator:
    def __init__(self, validator: IntelligenceValidator | None = None) -> None:
        self._validator = validator or IntelligenceValidator()

    def aggregate(self, intelligence: Iterable[Intelligence]) -> IntelligenceCollection:
        items = tuple(intelligence)
        if not items:
            raise IntelligenceAggregationError("Cannot aggregate empty intelligence")
        for item in items:
            self._validator.validate(item)
        confidence = Confidence.calculate(
            completeness=sum(item.confidence.completeness for item in items) / len(items),
            evidence_quality=sum(item.confidence.evidence_quality for item in items) / len(items),
            freshness=sum(item.confidence.freshness for item in items) / len(items),
            uncertainty=sum(item.confidence.uncertainty for item in items) / len(items),
        )
        return IntelligenceCollection(intelligence=items, confidence=confidence)

