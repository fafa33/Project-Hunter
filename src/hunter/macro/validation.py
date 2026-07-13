from __future__ import annotations

from datetime import UTC, datetime

from hunter.execution.identity import identity
from hunter.macro.models import MacroEvidence, MacroMetric

METRIC_FRESHNESS_HOURS: dict[str, int] = {
    "federal_funds_rate": 24 * 120,
    "ecb_interest_rate": 24 * 120,
    "us_cpi": 24 * 75,
    "us_ppi": 24 * 75,
    "pmi": 24 * 75,
    "us_unemployment": 24 * 75,
    "dxy": 24 * 7,
    "treasury_10y": 24 * 7,
    "treasury_2y": 24 * 7,
    "yield_curve_spread": 24 * 7,
    "global_m2_liquidity": 24 * 45,
    "bitcoin_dominance": 24,
    "total_crypto_market_cap": 24,
    "stablecoin_market_cap": 24 * 7,
    "fear_greed_index": 24,
    "vix": 24 * 7,
    "oil_wti": 24 * 7,
    "gold": 24 * 7,
    "dollar_liquidity_indicators": 24 * 21,
}


def freshness_threshold_hours(metric: str, fallback: int) -> int:
    return METRIC_FRESHNESS_HOURS.get(metric, fallback)


def validate_metric(metric: MacroMetric, *, now: datetime, stale_after_hours: int) -> MacroEvidence:
    errors: list[str] = []
    future_seconds = (metric.timestamp - now.astimezone(UTC)).total_seconds()
    if future_seconds > 60:
        errors.append("future_timestamp")
    age_hours = (now.astimezone(UTC) - metric.timestamp).total_seconds() / 3600
    threshold = freshness_threshold_hours(metric.name, stale_after_hours)
    if age_hours > threshold:
        errors.append("stale")
    if not metric.source_url.startswith("https://"):
        errors.append("unverifiable_source_url")
    status = "VALID" if not errors else "STALE" if errors == ["stale"] else "INVALID"
    return MacroEvidence(
        evidence_id=identity(
            "macro-evidence",
            {
                "metric": metric.name,
                "provider": metric.provider,
                "timestamp": metric.timestamp,
                "value": metric.value,
            },
        ),
        repository_id=f"macro:{metric.provider}:{metric.name}",
        metric=metric,
        normalized_value=normalize_metric(metric.name, metric.value),
        validation_status=status,
        validation_errors=tuple(errors),
    )


def normalize_metric(metric: str, value: float) -> float:
    if metric in {"federal_funds_rate", "ecb_interest_rate", "treasury_10y", "treasury_2y"}:
        return _inverse(value, low=0.0, high=8.0)
    if metric in {"us_cpi", "us_ppi", "us_unemployment", "vix"}:
        return _inverse(value, low=0.0, high=12.0 if metric != "vix" else 40.0)
    if metric in {"pmi", "fear_greed_index"}:
        return _scale(value, low=0.0, high=100.0)
    if metric == "bitcoin_dominance":
        return _scale(value, low=30.0, high=70.0)
    if metric in {
        "total_crypto_market_cap",
        "stablecoin_market_cap",
        "global_m2_liquidity",
        "bitcoin_etf_net_flows",
        "ethereum_etf_net_flows",
        "dollar_liquidity_indicators",
    }:
        return _scale(value, low=0.0, high=max(value, 1.0))
    if metric in {"oil_wti", "gold", "dxy"}:
        return _scale(value, low=0.0, high=max(value * 1.5, 1.0))
    return _scale(value, low=0.0, high=max(value, 1.0))


def _scale(value: float, *, low: float, high: float) -> float:
    if high <= low:
        return 0.0
    return round(max(0.0, min(1.0, (value - low) / (high - low))), 4)


def _inverse(value: float, *, low: float, high: float) -> float:
    return round(1.0 - _scale(value, low=low, high=high), 4)
