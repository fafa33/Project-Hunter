from __future__ import annotations

from datetime import datetime

from hunter.intelligence.confidence import Confidence
from hunter.intelligence.evidence import Evidence
from hunter.intelligence.exceptions import IntelligenceValidationError
from hunter.intelligence.insight import Insight
from hunter.intelligence.intelligence import Intelligence
from hunter.intelligence.metadata import IntelligenceMetadata
from hunter.intelligence.observation import Observation
from hunter.intelligence.signal import Signal


class IntelligenceValidator:
    def validate(self, intelligence: Intelligence) -> None:
        self._required("intelligence", intelligence.id, intelligence.project, intelligence.engine)
        self._timestamp("generated_at", intelligence.generated_at)
        self._confidence_model(intelligence.confidence)
        self._duplicates(
            [
                *[signal.id for signal in intelligence.signals],
                *[evidence.id for evidence in intelligence.evidence],
                *[observation.id for observation in intelligence.observations],
                *[insight.id for insight in intelligence.insights],
            ]
        )
        for signal in intelligence.signals:
            self.signal(signal)
        for evidence in intelligence.evidence:
            self.evidence(evidence)
        for observation in intelligence.observations:
            self.observation(observation)
        for insight in intelligence.insights:
            self.insight(insight)
        if not intelligence.evidence:
            raise IntelligenceValidationError("Intelligence must include evidence")

    def signal(self, signal: Signal) -> None:
        self._required("signal", signal.id, signal.source, signal.category)
        self._timestamp("signal.timestamp", signal.timestamp)
        self._range("signal.strength", signal.strength)
        self._range("signal.confidence", signal.confidence)
        self._range("signal.severity", signal.severity)
        self._metadata(signal.metadata)

    def evidence(self, evidence: Evidence) -> None:
        self._required("evidence", evidence.id, evidence.source, evidence.reference)
        self._timestamp("evidence.collected_at", evidence.collected_at)
        self._range("evidence.reliability", evidence.reliability)
        self._range("evidence.freshness", evidence.freshness)
        self._metadata(evidence.metadata)

    def observation(self, observation: Observation) -> None:
        self._required("observation", observation.id, observation.engine, observation.project, observation.description)
        self._range("observation.importance", observation.importance)
        if not observation.evidence:
            raise IntelligenceValidationError(f"Observation {observation.id} must include evidence")
        for evidence in observation.evidence:
            self.evidence(evidence)
        self._metadata(observation.metadata)

    def insight(self, insight: Insight) -> None:
        self._required("insight", insight.id, insight.title, insight.explanation)
        self._range("insight.confidence", insight.confidence)
        self._range("insight.priority", insight.priority)
        if not insight.supporting_observations:
            raise IntelligenceValidationError(f"Insight {insight.id} must include supporting observations")
        for observation in insight.supporting_observations:
            self.observation(observation)

    def _confidence_model(self, confidence: Confidence) -> None:
        self._range("confidence.score", confidence.score)
        self._range("confidence.completeness", confidence.completeness)
        self._range("confidence.evidence_quality", confidence.evidence_quality)
        self._range("confidence.freshness", confidence.freshness)
        self._range("confidence.uncertainty", confidence.uncertainty)

    def _required(self, object_name: str, *values: str) -> None:
        if any(not str(value).strip() for value in values):
            raise IntelligenceValidationError(f"{object_name} has missing required fields")

    def _range(self, field_name: str, value: float) -> None:
        if value < 0.0 or value > 1.0:
            raise IntelligenceValidationError(f"{field_name} must be between 0.0 and 1.0")

    def _timestamp(self, field_name: str, value: datetime) -> None:
        if not isinstance(value, datetime):
            raise IntelligenceValidationError(f"{field_name} must be a datetime")

    def _metadata(self, metadata: IntelligenceMetadata) -> None:
        if not isinstance(metadata.values, dict):
            raise IntelligenceValidationError("metadata must be a mapping")

    def _duplicates(self, ids: list[str]) -> None:
        seen: set[str] = set()
        for item_id in ids:
            if item_id in seen:
                raise IntelligenceValidationError(f"Duplicate intelligence object id: {item_id}")
            seen.add(item_id)

