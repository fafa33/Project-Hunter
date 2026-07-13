from __future__ import annotations

from datetime import UTC, datetime

from hunter.execution.identity import identity
from hunter.whale.configuration import WhaleAcquisitionConfig, load_whale_config
from hunter.whale.models import WhaleEvidence, WhaleSnapshot
from hunter.whale.providers import WhaleProviderRegistry
from hunter.whale.repository import WhaleRepository
from hunter.whale.validation import validate_metric

REQUIRED_WHALE_METRICS: tuple[str, ...] = (
    "exchange_inflows",
    "exchange_outflows",
    "net_exchange_flows",
    "large_transaction_activity",
    "whale_accumulation",
    "whale_distribution",
    "smart_money_activity",
    "top_holder_concentration",
    "holder_growth",
    "long_term_holder_supply",
    "short_term_holder_supply",
    "supply_on_exchanges",
    "supply_off_exchanges",
    "stablecoin_inflows",
    "stablecoin_outflows",
    "open_interest",
    "funding_rate",
    "liquidation_pressure",
    "large_wallet_balance_changes",
    "institutional_accumulation",
)

WHALE_ENGINE_TARGETS: tuple[str, ...] = (
    "whale_intelligence",
    "probability",
    "risk",
    "capital_rotation",
    "future_demand",
    "pattern_matching",
    "committee",
)


class WhaleIntelligenceEvidenceEngine:
    def __init__(
        self,
        config: WhaleAcquisitionConfig | None = None,
        repository: WhaleRepository | None = None,
        registry: WhaleProviderRegistry | None = None,
    ) -> None:
        self.config = config or load_whale_config()
        self.repository = repository or WhaleRepository()
        self.registry = registry or WhaleProviderRegistry(self.config.providers)

    def sync(self, *, now: datetime | None = None) -> WhaleSnapshot:
        timestamp = (now or datetime.now(tz=UTC)).astimezone(UTC)
        evidence: list[WhaleEvidence] = []
        failures = []
        if self.config.enabled:
            for provider in self.registry.providers():
                try:
                    metrics = provider.collect()
                except Exception:
                    metrics = ()
                failures.extend(getattr(provider, "failures", ()))
                for metric in metrics:
                    validation_time = max(timestamp, metric.retrieval_time)
                    evidence.append(
                        validate_metric(metric, now=validation_time, stale_after_hours=self.config.stale_after_hours)
                    )
        saved = self.repository.save_evidence(tuple(evidence))
        self.repository.save_failures(tuple(failures))
        snapshot = self.build_snapshot((*self.repository.evidence(), *saved), generated_at=timestamp)
        return self.repository.save_snapshot(snapshot)

    def build_snapshot(
        self, evidence: tuple[WhaleEvidence, ...] | None = None, *, generated_at: datetime | None = None
    ) -> WhaleSnapshot:
        timestamp = (generated_at or datetime.now(tz=UTC)).astimezone(UTC)
        rows = tuple(item for item in (evidence or self.repository.evidence()) if item.validation_status == "VALID")
        latest = _latest_by_metric_asset(rows)
        if not latest:
            return _empty_snapshot(timestamp)
        normalized = _average_by_metric(latest)
        raw = _raw_by_metric(latest)
        if "exchange_inflows" in raw and "exchange_outflows" in raw:
            net = raw["exchange_inflows"] - raw["exchange_outflows"]
            raw["net_exchange_flows"] = round(net, 6)
            normalized["net_exchange_flows"] = _pressure_score(net)
        if "exchange_inflows" in normalized and "exchange_outflows" in normalized:
            normalized["exchange_pressure"] = _mean(
                (normalized["exchange_inflows"], 1.0 - normalized["exchange_outflows"])
            )
            raw["exchange_pressure"] = normalized["exchange_pressure"]
        if "stablecoin_inflows" in normalized and "stablecoin_outflows" in normalized:
            normalized["stablecoin_buying_pressure"] = _mean(
                (normalized["stablecoin_inflows"], 1.0 - normalized["stablecoin_outflows"])
            )
            raw["stablecoin_buying_pressure"] = normalized["stablecoin_buying_pressure"]
        for metric in ("open_interest", "funding_rate"):
            disagreement = _provider_disagreement(latest, metric)
            if disagreement is not None:
                raw[f"{metric}_provider_disagreement"] = disagreement
                normalized[f"{metric}_provider_disagreement"] = disagreement
        evidence_quality = round(len({item.metric.name for item in latest.values()}) / len(REQUIRED_WHALE_METRICS), 4)
        confidence = _mean(tuple(item.metric.confidence for item in latest.values())) * evidence_quality
        freshness = _mean(tuple(item.metric.freshness for item in latest.values()))
        accumulation = _mean(
            tuple(
                normalized[name]
                for name in ("whale_accumulation", "large_wallet_balance_changes")
                if name in normalized
            )
        )
        distribution = _mean(
            tuple(normalized[name] for name in ("whale_distribution", "exchange_pressure") if name in normalized)
        )
        exchange_pressure = normalized.get("exchange_pressure", normalized.get("net_exchange_flows", 0.0))
        smart_money = normalized.get("smart_money_activity", 0.0)
        stablecoin = normalized.get("stablecoin_buying_pressure", 0.0)
        institutional = _mean(
            tuple(
                normalized[name]
                for name in ("institutional_accumulation", "open_interest", "funding_rate")
                if name in normalized
            )
        )
        market_participation = _mean(
            tuple(
                normalized[name]
                for name in ("open_interest", "funding_rate", "large_transaction_activity")
                if name in normalized
            )
        )
        whale_score = _mean(
            (accumulation, 1.0 - distribution, 1.0 - exchange_pressure, smart_money, stablecoin, institutional)
        )
        return WhaleSnapshot(
            snapshot_id=identity(
                "whale-snapshot",
                {
                    "generated_at": timestamp,
                    "evidence": tuple(sorted(item.evidence_id for item in latest.values())),
                },
            ),
            generated_at=timestamp,
            evidence=tuple(latest.values()),
            whale_score=whale_score,
            accumulation_score=accumulation,
            distribution_score=distribution,
            exchange_pressure=exchange_pressure,
            smart_money_score=smart_money,
            stablecoin_pressure=stablecoin,
            institutional_score=institutional,
            market_participation=market_participation,
            confidence=round(confidence, 4),
            freshness=freshness,
            evidence_quality=evidence_quality,
            raw_metrics=raw,
            normalized_metrics=normalized,
        )


def _latest_by_metric_asset(evidence: tuple[WhaleEvidence, ...]) -> dict[str, WhaleEvidence]:
    rows: dict[str, WhaleEvidence] = {}
    for item in evidence:
        key = f"{item.metric.provider}:{item.metric.name}:{item.metric.asset}"
        current = rows.get(key)
        if current is None or item.metric.timestamp > current.metric.timestamp:
            rows[key] = item
    return rows


def _empty_snapshot(timestamp: datetime) -> WhaleSnapshot:
    return WhaleSnapshot(
        snapshot_id=identity("whale-snapshot", {"generated_at": timestamp, "evidence": ()}),
        generated_at=timestamp,
        evidence=(),
        whale_score=0.0,
        accumulation_score=0.0,
        distribution_score=0.0,
        exchange_pressure=0.0,
        smart_money_score=0.0,
        stablecoin_pressure=0.0,
        institutional_score=0.0,
        market_participation=0.0,
        confidence=0.0,
        freshness=0.0,
        evidence_quality=0.0,
        raw_metrics={},
        normalized_metrics={},
    )


def _average_by_metric(evidence: dict[str, WhaleEvidence]) -> dict[str, float]:
    grouped: dict[str, list[float]] = {}
    for item in evidence.values():
        grouped.setdefault(item.metric.name, []).append(item.normalized_value)
    return {metric: _mean(tuple(values)) for metric, values in grouped.items()}


def _raw_by_metric(evidence: dict[str, WhaleEvidence]) -> dict[str, float]:
    return {
        f"{item.metric.provider}.{item.metric.asset}.{item.metric.name}": item.metric.value
        for item in sorted(evidence.values(), key=lambda row: (row.metric.provider, row.metric.asset, row.metric.name))
    }


def _provider_disagreement(evidence: dict[str, WhaleEvidence], metric: str) -> float | None:
    values = tuple(item.normalized_value for item in evidence.values() if item.metric.name == metric)
    if len(values) < 2:
        return None
    return round(max(values) - min(values), 4)


def _mean(values: tuple[float, ...]) -> float:
    if not values:
        return 0.0
    return round(sum(values) / len(values), 4)


def _pressure_score(value: float) -> float:
    return round(max(0.0, min(1.0, (value + abs(value)) / max(abs(value) * 2, 1.0))), 4)
