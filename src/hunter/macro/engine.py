from __future__ import annotations

from datetime import UTC, datetime

from hunter.execution.identity import identity
from hunter.macro.configuration import MacroAcquisitionConfig, load_macro_config
from hunter.macro.models import MacroEvidence, MacroSnapshot
from hunter.macro.providers import MacroProviderRegistry
from hunter.macro.repository import MacroRepository
from hunter.macro.validation import validate_metric

REQUIRED_MACRO_METRICS: tuple[str, ...] = (
    "federal_funds_rate",
    "ecb_interest_rate",
    "us_cpi",
    "us_ppi",
    "pmi",
    "us_unemployment",
    "dxy",
    "treasury_10y",
    "treasury_2y",
    "global_m2_liquidity",
    "bitcoin_dominance",
    "total_crypto_market_cap",
    "stablecoin_market_cap",
    "bitcoin_etf_net_flows",
    "ethereum_etf_net_flows",
    "fear_greed_index",
    "vix",
    "oil_wti",
    "gold",
    "dollar_liquidity_indicators",
)


class MacroIntelligenceEvidenceEngine:
    def __init__(
        self,
        config: MacroAcquisitionConfig | None = None,
        repository: MacroRepository | None = None,
        registry: MacroProviderRegistry | None = None,
    ) -> None:
        self.config = config or load_macro_config()
        self.repository = repository or MacroRepository()
        self.registry = registry or MacroProviderRegistry(self.config.providers)

    def sync(self, *, now: datetime | None = None) -> MacroSnapshot:
        timestamp = (now or datetime.now(tz=UTC)).astimezone(UTC)
        evidence: list[MacroEvidence] = []
        failures = []
        if self.config.enabled:
            for provider in self.registry.providers():
                try:
                    metrics = provider.collect()
                except Exception:
                    metrics = ()
                failures.extend(getattr(provider, "failures", ()))
                for metric in metrics:
                    evidence.append(
                        validate_metric(metric, now=timestamp, stale_after_hours=self.config.stale_after_hours)
                    )
        saved = self.repository.save_evidence(tuple(evidence))
        self.repository.save_failures(tuple(failures))
        snapshot = self.build_snapshot((*self.repository.evidence(), *saved), generated_at=timestamp)
        return self.repository.save_snapshot(snapshot)

    def build_snapshot(
        self, evidence: tuple[MacroEvidence, ...] | None = None, *, generated_at: datetime | None = None
    ) -> MacroSnapshot:
        timestamp = (generated_at or datetime.now(tz=UTC)).astimezone(UTC)
        rows = tuple(item for item in (evidence or self.repository.evidence()) if item.validation_status == "VALID")
        latest = _latest_by_metric(rows)
        normalized = {name: item.normalized_value for name, item in latest.items()}
        raw = {name: item.metric.value for name, item in latest.items()}
        if "treasury_10y" in raw and "treasury_2y" in raw:
            raw["yield_curve_spread"] = round(raw["treasury_10y"] - raw["treasury_2y"], 4)
            normalized["yield_curve_spread"] = _spread_score(raw["yield_curve_spread"])
        stablecoin_history = tuple(
            sorted(
                (item for item in rows if item.metric.name == "stablecoin_market_cap"),
                key=lambda item: item.metric.timestamp,
            )
        )
        if len(stablecoin_history) >= 2:
            previous = stablecoin_history[-2].metric.value
            current = stablecoin_history[-1].metric.value
            if previous > 0:
                raw["stablecoin_growth"] = round((current - previous) / previous, 6)
                normalized["stablecoin_growth"] = _growth_score(raw["stablecoin_growth"])
        evidence_quality = round(len(latest) / len(REQUIRED_MACRO_METRICS), 4)
        confidence = _mean(tuple(item.metric.confidence for item in latest.values())) * evidence_quality
        freshness = _mean(tuple(item.metric.freshness for item in latest.values()))
        liquidity = _mean(
            tuple(
                normalized[name]
                for name in (
                    "global_m2_liquidity",
                    "total_crypto_market_cap",
                    "stablecoin_market_cap",
                    "stablecoin_growth",
                )
                if name in normalized
            )
        )
        inflation = _mean(tuple(normalized[name] for name in ("us_cpi", "us_ppi") if name in normalized))
        policy = _mean(
            tuple(
                normalized[name]
                for name in (
                    "federal_funds_rate",
                    "ecb_interest_rate",
                    "treasury_10y",
                    "treasury_2y",
                    "yield_curve_spread",
                )
                if name in normalized
            )
        )
        recession = _mean(tuple(1.0 - normalized[name] for name in ("us_unemployment", "pmi") if name in normalized))
        crypto_liquidity = _mean(
            tuple(
                normalized[name]
                for name in (
                    "bitcoin_dominance",
                    "total_crypto_market_cap",
                    "stablecoin_market_cap",
                    "bitcoin_etf_net_flows",
                    "ethereum_etf_net_flows",
                )
                if name in normalized
            )
        )
        risk_off = _mean(tuple(1.0 - normalized[name] for name in ("vix", "fear_greed_index") if name in normalized))
        risk_on = _mean((liquidity, policy, crypto_liquidity, 1.0 - risk_off))
        return MacroSnapshot(
            snapshot_id=identity(
                "macro-snapshot",
                {
                    "generated_at": timestamp,
                    "evidence": tuple(sorted(item.evidence_id for item in latest.values())),
                },
            ),
            generated_at=timestamp,
            evidence=tuple(latest.values()),
            liquidity_score=liquidity,
            inflation_score=inflation,
            monetary_policy_score=policy,
            recession_probability=recession,
            risk_on_score=risk_on,
            risk_off_score=risk_off,
            crypto_liquidity_score=crypto_liquidity,
            macro_confidence=round(confidence, 4),
            freshness=freshness,
            evidence_quality=evidence_quality,
            raw_metrics=raw,
            normalized_metrics=normalized,
        )


def _latest_by_metric(evidence: tuple[MacroEvidence, ...]) -> dict[str, MacroEvidence]:
    rows: dict[str, MacroEvidence] = {}
    for item in evidence:
        current = rows.get(item.metric.name)
        if current is None or item.metric.timestamp > current.metric.timestamp:
            rows[item.metric.name] = item
    return rows


def _mean(values: tuple[float, ...]) -> float:
    if not values:
        return 0.0
    return round(sum(values) / len(values), 4)


def _spread_score(value: float) -> float:
    return round(max(0.0, min(1.0, (value + 1.0) / 3.0)), 4)


def _growth_score(value: float) -> float:
    return round(max(0.0, min(1.0, (value + 0.05) / 0.15)), 4)
