from __future__ import annotations

from datetime import datetime

from hunter.acquisition.models import EvidenceValidation, NormalizedEvidence, ValidationIssue


class EvidenceAcquisitionValidator:
    def __init__(self, *, stale_after_seconds: int = 86_400, minimum_confidence: float = 0.0) -> None:
        self.stale_after_seconds = stale_after_seconds
        self.minimum_confidence = minimum_confidence

    def validate(
        self,
        evidence: tuple[NormalizedEvidence, ...],
        *,
        as_of: object,
    ) -> tuple[EvidenceValidation, ...]:
        if not isinstance(as_of, datetime):
            msg = "as_of must be a datetime"
            raise ValueError(msg)
        seen: set[tuple[str, str, str, str]] = set()
        validations = []
        for item in evidence:
            issues: list[ValidationIssue] = []
            key = (item.provider, item.domain, item.metric, item.raw_source_id)
            duplicate = key in seen
            seen.add(key)
            if duplicate:
                issues.append(ValidationIssue("duplicate", "raw_source_id", "duplicate provider source id"))
            if item.retrieved_at > as_of:
                issues.append(ValidationIssue("timestamp", "retrieved_at", "retrieval timestamp is in the future"))
            age = (as_of - item.retrieved_at).total_seconds()
            if age > self.stale_after_seconds:
                issues.append(ValidationIssue("stale", "retrieved_at", "evidence is stale"))
            if item.confidence <= 0.0 or item.confidence < self.minimum_confidence:
                issues.append(ValidationIssue("confidence", "confidence", "confidence is below required threshold"))
            if item.freshness <= 0.0:
                issues.append(ValidationIssue("freshness", "freshness", "freshness must be positive"))
            if not item.normalized_metrics:
                issues.append(ValidationIssue("schema", "normalized_metrics", "normalized metrics are required"))
            status = "valid"
            if any(issue.code == "duplicate" for issue in issues):
                status = "duplicate"
            elif any(issue.code == "stale" for issue in issues):
                status = "stale"
            elif issues:
                status = "invalid"
            validations.append(
                EvidenceValidation(
                    evidence_id=item.evidence_id,
                    status=status,  # type: ignore[arg-type]
                    validated_at=as_of,
                    confidence=0.0 if status == "invalid" else item.confidence,
                    freshness=0.0 if status == "invalid" else item.freshness,
                    issues=tuple(issues),
                )
            )
        return tuple(sorted(validations, key=lambda item: item.evidence_id))
