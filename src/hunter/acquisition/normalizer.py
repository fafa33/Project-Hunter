from __future__ import annotations

from hunter.acquisition.models import AcquisitionRequest, NormalizedEvidence, RawEvidence
from hunter.execution.identity import identity


class CanonicalEvidenceNormalizer:
    def normalize(self, raw: tuple[RawEvidence, ...], request: AcquisitionRequest) -> tuple[NormalizedEvidence, ...]:
        normalized = []
        for item in raw:
            value = item.payload.get("value")
            if not isinstance(value, int | float | str | bool):
                value = str(value) if value is not None else ""
            normalized_metrics = _normalized_metrics(item.payload)
            evidence_id = identity(
                "acquisition-evidence",
                {
                    "provider": item.provider,
                    "collector": item.collector,
                    "raw_source_id": item.raw_source_id,
                    "domain": item.domain,
                    "metric": item.metric,
                    "target_id": item.target_id,
                    "retrieved_at": item.retrieved_at,
                    "request_mode": request.mode,
                },
            )
            repository_id = item.repository_id or identity(
                "acquisition-repository",
                {
                    "provider": item.provider,
                    "domain": item.domain,
                    "target_id": item.target_id,
                    "metric": item.metric,
                },
            )
            normalized.append(
                NormalizedEvidence(
                    evidence_id=evidence_id,
                    repository_id=repository_id,
                    provider=item.provider,
                    collector=item.collector,
                    raw_source_id=item.raw_source_id,
                    domain=item.domain,
                    metric=item.metric,
                    target_id=item.target_id,
                    value=value,
                    raw_metrics=dict(item.payload),
                    normalized_metrics=normalized_metrics,
                    source_url=item.source_url,
                    retrieved_at=item.retrieved_at,
                    normalized_at=request.requested_at,
                    confidence=_score(item.payload.get("confidence"), 1.0),
                    freshness=_score(item.payload.get("freshness"), 1.0),
                    raw_evidence_id=item.raw_source_id,
                )
            )
        return tuple(sorted(normalized, key=lambda item: item.evidence_id))


def _normalized_metrics(payload: dict[str, object]) -> dict[str, float]:
    metrics = payload.get("normalized_metrics")
    if isinstance(metrics, dict):
        return {str(key): _score(value, 0.0) for key, value in metrics.items() if isinstance(value, int | float)}
    value = payload.get("value")
    return {"value": _score(value, 0.0)} if isinstance(value, int | float) else {}


def _score(value: object, default: float) -> float:
    if isinstance(value, int | float):
        return round(max(0.0, min(1.0, float(value))), 4)
    return default
