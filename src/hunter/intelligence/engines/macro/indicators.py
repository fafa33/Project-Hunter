from __future__ import annotations

from statistics import mean

from hunter.intelligence.engines.macro.models import MacroDataPoint, MacroIndicator


class MacroIndicatorCalculator:
    def calculate(self, points: dict[str, MacroDataPoint]) -> tuple[MacroIndicator, ...]:
        indicators = [
            self._indicator("liquidity_expansion", "global_liquidity", points.get("global_liquidity")),
            self._inverse_indicator("liquidity_contraction", "global_liquidity", points.get("global_liquidity")),
            self._inverse_indicator("interest_rate_pressure", "interest_rates", points.get("interest_rates")),
            self._inverse_indicator("inflation_pressure", "inflation", points.get("inflation")),
            self._indicator("institutional_flow", "institutional_adoption", points.get("institutional_adoption")),
            self._indicator("stablecoin_momentum", "stablecoin_supply", points.get("stablecoin_supply")),
            self._indicator("risk_appetite", "eth_btc_ratio", points.get("eth_btc_ratio")),
            self._trend_strength(points),
            self._sector_rotation(points),
            self._market_cycle(points),
        ]
        return tuple(indicator for indicator in indicators if indicator is not None)

    def _indicator(self, name: str, domain: str, point: MacroDataPoint | None) -> MacroIndicator | None:
        if point is None or point.value is None:
            return None
        return MacroIndicator(
            name=name,
            domain=domain,
            value=point.value,
            direction=_direction(point.value, point.previous_value),
            confidence=point.reliability,
        )

    def _inverse_indicator(self, name: str, domain: str, point: MacroDataPoint | None) -> MacroIndicator | None:
        if point is None or point.value is None:
            return None
        value = 1.0 - point.value
        previous = None if point.previous_value is None else 1.0 - point.previous_value
        return MacroIndicator(
            name=name,
            domain=domain,
            value=value,
            direction=_direction(value, previous),
            confidence=point.reliability,
        )

    def _trend_strength(self, points: dict[str, MacroDataPoint]) -> MacroIndicator | None:
        values = [point.value for point in points.values() if point.value is not None]
        if not values:
            return None
        return MacroIndicator(
            name="trend_strength",
            domain="global",
            value=mean(values),
            direction="strengthening" if mean(values) >= 0.55 else "weakening",
            confidence=mean([point.reliability for point in points.values()]),
        )

    def _sector_rotation(self, points: dict[str, MacroDataPoint]) -> MacroIndicator | None:
        sectors = [
            "layer_1_ecosystem",
            "layer_2_ecosystem",
            "ai_sector",
            "depin_sector",
            "rwa_sector",
            "defi_sector",
            "gaming_sector",
            "infrastructure_sector",
            "privacy_sector",
            "interoperability_sector",
            "oracle_sector",
        ]
        known = [points[domain].value for domain in sectors if domain in points and points[domain].value is not None]
        if not known:
            return None
        return MacroIndicator(
            name="sector_rotation",
            domain="sectors",
            value=mean(known),
            direction="strengthening" if mean(known) >= 0.55 else "weakening",
            confidence=mean([points[domain].reliability for domain in sectors if domain in points]),
        )

    def _market_cycle(self, points: dict[str, MacroDataPoint]) -> MacroIndicator | None:
        domains = ["global_liquidity", "stablecoin_supply", "eth_btc_ratio", "institutional_adoption"]
        known = [points[domain].value for domain in domains if domain in points and points[domain].value is not None]
        if not known:
            return None
        return MacroIndicator(
            name="market_cycle",
            domain="global",
            value=mean(known),
            direction="risk_on" if mean(known) >= 0.55 else "risk_off",
            confidence=mean([points[domain].reliability for domain in domains if domain in points]),
        )


def _direction(value: float, previous: float | None) -> str:
    if previous is None:
        return "unknown"
    delta = value - previous
    if delta >= 0.05:
        return "strengthening"
    if delta <= -0.05:
        return "weakening"
    return "stable"
