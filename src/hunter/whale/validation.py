from __future__ import annotations

from datetime import UTC, datetime

from hunter.execution.identity import identity
from hunter.whale.models import WhaleEvidence, WhaleMetric


def validate_metric(metric: WhaleMetric, *, now: datetime, stale_after_hours: int) -> WhaleEvidence:
    errors: list[str] = []
    now_utc = now.astimezone(UTC)
    if (metric.timestamp - now_utc).total_seconds() > 60:
        errors.append("future_timestamp")
    if metric.retrieval_time < metric.timestamp and (metric.timestamp - metric.retrieval_time).total_seconds() > 60:
        errors.append("retrieval_before_event")
    age_hours = (now_utc - metric.timestamp).total_seconds() / 3600
    if age_hours > stale_after_hours:
        errors.append("stale")
    if not metric.source_url.startswith("https://"):
        errors.append("unverifiable_source_url")
    status = "VALID" if not errors else "STALE" if errors == ["stale"] else "INVALID"
    return WhaleEvidence(
        evidence_id=identity(
            "whale-evidence",
            {
                "metric": metric.name,
                "provider": metric.provider,
                "asset": metric.asset,
                "timestamp": metric.timestamp,
                "value": metric.value,
            },
        ),
        repository_id=f"whale:{metric.provider}:{metric.asset}:{metric.name}",
        metric=metric,
        normalized_value=normalize_metric(metric.name, metric.value),
        validation_status=status,
        validation_errors=tuple(errors),
    )


def normalize_metric(metric: str, value: float) -> float:
    if metric == "funding_rate":
        return _scale(value, low=-0.001, high=0.001)
    if metric == "open_interest":
        return _scale(value, low=0.0, high=max(value, 1.0))
    if metric in {"exchange_inflows", "stablecoin_inflows", "liquidation_pressure"}:
        return _scale(value, low=0.0, high=max(value, 1.0))
    if metric in {"exchange_outflows", "stablecoin_outflows", "whale_accumulation"}:
        return _scale(value, low=0.0, high=max(value, 1.0))
    return _scale(value, low=0.0, high=max(value, 1.0))


def _scale(value: float, *, low: float, high: float) -> float:
    if high <= low:
        return 0.0
    return round(max(0.0, min(1.0, (value - low) / (high - low))), 4)
