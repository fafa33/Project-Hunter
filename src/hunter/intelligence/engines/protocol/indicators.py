from __future__ import annotations

from datetime import timedelta
from statistics import mean

from hunter.intelligence.engines.protocol.configuration import ProtocolEngineConfiguration
from hunter.intelligence.engines.protocol.models import ProtocolDataset, ProtocolIndicator


class ProtocolIndicatorCalculator:
    def __init__(self, configuration: ProtocolEngineConfiguration | None = None) -> None:
        self.configuration = configuration or ProtocolEngineConfiguration()

    def calculate(self, dataset: ProtocolDataset) -> tuple[ProtocolIndicator, ...]:
        return (
            self.user_growth(dataset),
            self.returning_user_ratio(dataset),
            self.retention_trend(dataset),
            self.transaction_growth(dataset),
            self.transaction_quality(dataset),
            self.fee_growth(dataset),
            self.revenue_growth(dataset),
            self.fee_to_revenue_conversion(dataset),
            self.tvl_growth(dataset),
            self.organic_tvl_ratio(dataset),
            self.liquidity_depth(dataset),
            self.liquidity_stability(dataset),
            self.capital_efficiency(dataset),
            self.utilization(dataset),
            self.application_breadth(dataset),
            self.application_concentration(dataset),
            self.network_reliability(dataset),
            self.incident_frequency(dataset),
            self.incident_severity_trend(dataset),
            self.validator_health(dataset),
            self.governance_participation(dataset),
            self.treasury_runway(dataset),
            self.incentive_dependence(dataset),
            self.emissions_dependence(dataset),
            self.value_capture_efficiency(dataset),
            self.ecosystem_expansion(dataset),
            self.protocol_resilience(dataset),
            self.protocol_acceleration(dataset),
            self.protocol_deterioration(dataset),
        )

    def user_growth(self, dataset: ProtocolDataset) -> ProtocolIndicator:
        values = [item.active_users for item in dataset.usage if item.active_users is not None]
        return _growth_indicator("user_growth", values, "Active user growth across available snapshots.")

    def returning_user_ratio(self, dataset: ProtocolDataset) -> ProtocolIndicator:
        latest = _latest(dataset.usage)
        if latest is None or latest.returning_users is None or latest.active_users in {None, 0}:
            return _missing("returning_user_ratio", "usage")
        return _indicator(
            "returning_user_ratio",
            latest.returning_users / latest.active_users,
            "positive",
            "Returning users as share of active users.",
        )

    def retention_trend(self, dataset: ProtocolDataset) -> ProtocolIndicator:
        values = [
            item.retained_users / item.active_users
            for item in dataset.usage
            if item.retained_users is not None and item.active_users not in {None, 0}
        ]
        return _growth_indicator("retention_trend", values, "Retention ratio trend.")

    def transaction_growth(self, dataset: ProtocolDataset) -> ProtocolIndicator:
        values = [item.transaction_count for item in dataset.transactions if item.transaction_count is not None]
        return _growth_indicator("transaction_growth", values, "Transaction count growth across available snapshots.")

    def transaction_quality(self, dataset: ProtocolDataset) -> ProtocolIndicator:
        latest = _latest(dataset.transactions)
        if latest is None or latest.transaction_count in {None, 0}:
            return _missing("transaction_quality", "transactions")
        meaningful = _ratio(latest.economically_meaningful_count or 0, latest.transaction_count or 0)
        penalties = mean(
            (
                latest.duplicate_ratio or 0.0,
                latest.bridge_pass_through_ratio or 0.0,
            )
        )
        return _indicator(
            "transaction_quality",
            meaningful * (1.0 - penalties),
            "positive",
            "Economically meaningful transaction share adjusted for duplicate and pass-through activity.",
        )

    def fee_growth(self, dataset: ProtocolDataset) -> ProtocolIndicator:
        return _growth_indicator(
            "fee_growth",
            [item.fees_usd for item in dataset.fees if item.fees_usd is not None],
            "Fee growth across snapshots.",
        )

    def revenue_growth(self, dataset: ProtocolDataset) -> ProtocolIndicator:
        values = [item.revenue_usd for item in dataset.revenues if item.revenue_usd is not None]
        return _growth_indicator("revenue_growth", values, "Revenue growth across snapshots.")

    def fee_to_revenue_conversion(self, dataset: ProtocolDataset) -> ProtocolIndicator:
        fee = _latest(dataset.fees)
        revenue = _latest(dataset.revenues)
        if fee is None or revenue is None or fee.fees_usd in {None, 0} or revenue.revenue_usd is None:
            return _missing("fee_to_revenue_conversion", "fees_or_revenue")
        return _indicator(
            "fee_to_revenue_conversion",
            _ratio(revenue.revenue_usd, fee.fees_usd),
            "positive",
            "Revenue captured as share of fees.",
        )

    def tvl_growth(self, dataset: ProtocolDataset) -> ProtocolIndicator:
        return _growth_indicator(
            "tvl_growth",
            [item.tvl_usd for item in dataset.tvl if item.tvl_usd is not None],
            "TVL growth across snapshots.",
        )

    def organic_tvl_ratio(self, dataset: ProtocolDataset) -> ProtocolIndicator:
        latest = _latest(dataset.tvl)
        if latest is None or latest.tvl_usd in {None, 0} or latest.organic_tvl_usd is None:
            return _missing("organic_tvl_ratio", "organic_tvl")
        value = _ratio(latest.organic_tvl_usd, latest.tvl_usd)
        direction = "negative" if value < self.configuration.organic_tvl_threshold else "positive"
        return _indicator("organic_tvl_ratio", value, direction, "Organic TVL share.")

    def liquidity_depth(self, dataset: ProtocolDataset) -> ProtocolIndicator:
        latest = _latest(dataset.liquidity)
        if latest is None or latest.depth_usd is None or latest.liquidity_usd in {None, 0}:
            return _missing("liquidity_depth", "liquidity")
        return _indicator(
            "liquidity_depth",
            _ratio(latest.depth_usd, latest.liquidity_usd),
            "positive",
            "Depth relative to total liquidity.",
        )

    def liquidity_stability(self, dataset: ProtocolDataset) -> ProtocolIndicator:
        latest = _latest(dataset.liquidity)
        if latest is None or latest.stable_liquidity_ratio is None:
            return _missing("liquidity_stability", "liquidity")
        return _indicator("liquidity_stability", latest.stable_liquidity_ratio, "positive", "Stable liquidity ratio.")

    def capital_efficiency(self, dataset: ProtocolDataset) -> ProtocolIndicator:
        latest_tx = _latest(dataset.transactions)
        latest_tvl = _latest(dataset.tvl)
        if (
            latest_tx is None
            or latest_tvl is None
            or latest_tx.economically_meaningful_count is None
            or latest_tvl.tvl_usd in {None, 0}
        ):
            return _missing("capital_efficiency", "transactions_or_tvl")
        return _indicator(
            "capital_efficiency",
            min(latest_tx.economically_meaningful_count / latest_tvl.tvl_usd * 1000, 1.0),
            "positive",
            "Meaningful activity relative to TVL.",
        )

    def utilization(self, dataset: ProtocolDataset) -> ProtocolIndicator:
        latest = _latest(dataset.liquidity)
        if latest is None or latest.utilization_ratio is None:
            return _missing("utilization", "liquidity")
        return _indicator("utilization", latest.utilization_ratio, "positive", "Protocol utilization ratio.")

    def application_breadth(self, dataset: ProtocolDataset) -> ProtocolIndicator:
        active = [item for item in dataset.applications if item.active]
        return _indicator(
            "application_breadth", min(len(active) / 5, 1.0), "positive", "Breadth of active applications."
        )

    def application_concentration(self, dataset: ProtocolDataset) -> ProtocolIndicator:
        shares = [item.volume_share for item in dataset.applications if item.volume_share is not None]
        if not shares:
            return _missing("application_concentration", "applications")
        concentration = max(shares)
        direction = "negative" if concentration >= self.configuration.concentration_risk_threshold else "positive"
        return _indicator(
            "application_concentration",
            1.0 - concentration,
            direction,
            "Lower application concentration indicates broader usage.",
        )

    def network_reliability(self, dataset: ProtocolDataset) -> ProtocolIndicator:
        recent_incidents = self._recent_incidents(dataset)
        value = 1.0 - min(sum(item.severity for item in recent_incidents) / 3, 1.0)
        return _indicator(
            "network_reliability",
            value,
            "positive" if value >= 0.6 else "negative",
            "Recent incident-adjusted reliability.",
        )

    def incident_frequency(self, dataset: ProtocolDataset) -> ProtocolIndicator:
        recent = self._recent_incidents(dataset)
        value = 1.0 - min(len(recent) / 3, 1.0)
        return _indicator(
            "incident_frequency", value, "positive" if value >= 0.6 else "negative", "Low recent incident frequency."
        )

    def incident_severity_trend(self, dataset: ProtocolDataset) -> ProtocolIndicator:
        values = [item.severity for item in dataset.incidents]
        if not values:
            return _indicator("incident_severity_trend", 1.0, "positive", "No incidents observed.")
        trend = _growth_score(values)
        return _indicator(
            "incident_severity_trend",
            1.0 - trend,
            "negative" if trend >= 0.55 else "positive",
            "Incident severity trend, inverted.",
        )

    def validator_health(self, dataset: ProtocolDataset) -> ProtocolIndicator:
        latest = _latest(dataset.validators)
        if latest is None:
            return _missing("validator_health", "validators")
        online = latest.online_ratio if latest.online_ratio is not None else 0.0
        concentration = latest.concentration_ratio if latest.concentration_ratio is not None else 1.0
        count_score = min((latest.active_validators or 0) / 100, 1.0)
        return _indicator(
            "validator_health",
            mean((online, 1.0 - concentration, count_score)),
            "positive",
            "Validator online status, decentralization, and count.",
        )

    def governance_participation(self, dataset: ProtocolDataset) -> ProtocolIndicator:
        latest = _latest(dataset.governance)
        if latest is None or latest.participation_ratio is None:
            return _missing("governance_participation", "governance")
        return _indicator(
            "governance_participation", latest.participation_ratio, "positive", "Governance participation ratio."
        )

    def treasury_runway(self, dataset: ProtocolDataset) -> ProtocolIndicator:
        latest = _latest(dataset.treasury)
        if latest is None:
            return _missing("treasury_runway", "treasury")
        runway = latest.runway_months
        if runway is None and latest.treasury_usd is not None and latest.monthly_expense_usd not in {None, 0}:
            runway = latest.treasury_usd / latest.monthly_expense_usd
        if runway is None:
            return _missing("treasury_runway", "treasury_runway")
        return _indicator(
            "treasury_runway",
            min(runway / self.configuration.treasury_runway_months_threshold, 1.0),
            "positive",
            "Treasury runway relative to configured threshold.",
        )

    def incentive_dependence(self, dataset: ProtocolDataset) -> ProtocolIndicator:
        latest = _latest(dataset.incentives)
        if latest is None or latest.incentives_usd is None:
            return _missing("incentive_dependence", "incentives")
        denominator = max(latest.revenue_usd or 0.0, latest.incentives_usd)
        dependence = _ratio(latest.incentives_usd, denominator)
        direction = "negative" if dependence >= self.configuration.incentive_dependence_threshold else "positive"
        return _indicator(
            "incentive_dependence",
            1.0 - dependence,
            direction,
            "Lower incentive dependence indicates healthier activity.",
        )

    def emissions_dependence(self, dataset: ProtocolDataset) -> ProtocolIndicator:
        latest = _latest(dataset.incentives)
        if latest is None or latest.emissions_usd is None:
            return _missing("emissions_dependence", "emissions")
        denominator = max(latest.revenue_usd or 0.0, latest.emissions_usd)
        dependence = _ratio(latest.emissions_usd, denominator)
        direction = "negative" if dependence >= self.configuration.emissions_dependence_threshold else "positive"
        return _indicator(
            "emissions_dependence",
            1.0 - dependence,
            direction,
            "Lower emissions dependence indicates healthier activity.",
        )

    def value_capture_efficiency(self, dataset: ProtocolDataset) -> ProtocolIndicator:
        revenue = _latest(dataset.revenues)
        tvl = _latest(dataset.tvl)
        if revenue is None or tvl is None or revenue.protocol_income_usd is None or tvl.tvl_usd in {None, 0}:
            return _missing("value_capture_efficiency", "revenue_or_tvl")
        return _indicator(
            "value_capture_efficiency",
            min(revenue.protocol_income_usd / tvl.tvl_usd * 20, 1.0),
            "positive",
            "Protocol income relative to TVL.",
        )

    def ecosystem_expansion(self, dataset: ProtocolDataset) -> ProtocolIndicator:
        chain_score = min(len(dataset.chains()) / 3, 1.0)
        application_score = self.application_breadth(dataset).value
        return _indicator(
            "ecosystem_expansion", mean((chain_score, application_score)), "positive", "Chain and application breadth."
        )

    def protocol_resilience(self, dataset: ProtocolDataset) -> ProtocolIndicator:
        reliability = self.network_reliability(dataset)
        validator = self.validator_health(dataset)
        liquidity = self.liquidity_stability(dataset)
        values = [item.value for item in (reliability, validator, liquidity) if not item.missing_evidence]
        if not values:
            return _missing("protocol_resilience", "reliability")
        return _indicator(
            "protocol_resilience", mean(values), "positive", "Reliability, validator health, and liquidity stability."
        )

    def protocol_acceleration(self, dataset: ProtocolDataset) -> ProtocolIndicator:
        user = self.user_growth(dataset)
        tx = self.transaction_growth(dataset)
        revenue = self.revenue_growth(dataset)
        values = [item.value for item in (user, tx, revenue) if not item.missing_evidence]
        if not values:
            return _missing("protocol_acceleration", "growth")
        return _indicator(
            "protocol_acceleration", mean(values), "positive", "Combined user, transaction, and revenue growth."
        )

    def protocol_deterioration(self, dataset: ProtocolDataset) -> ProtocolIndicator:
        acceleration = self.protocol_acceleration(dataset)
        resilience = self.protocol_resilience(dataset)
        value = 1.0 - mean((acceleration.value, resilience.value))
        return _indicator(
            "protocol_deterioration",
            value,
            "negative" if value >= 0.55 else "neutral",
            "Risk of weakening growth and resilience.",
        )

    def _recent_incidents(self, dataset: ProtocolDataset):
        if not dataset.incidents:
            return ()
        latest = max(item.timestamp for item in dataset.incidents)
        return tuple(
            item
            for item in dataset.incidents
            if item.timestamp >= latest - timedelta(days=self.configuration.recent_window_days)
        )


def _growth_indicator(name: str, values: list[int | float | None], description: str) -> ProtocolIndicator:
    numeric = [float(value) for value in values if value is not None]
    if len(numeric) < 2:
        return _missing(name, name)
    return _indicator(name, _growth_score(numeric), "positive", description)


def _growth_score(values: list[float]) -> float:
    first = values[0]
    last = values[-1]
    if first <= 0:
        return 0.7 if last > 0 else 0.0
    change = (last - first) / first
    return _clamp(0.5 + change)


def _indicator(name: str, value: float, direction: str, description: str) -> ProtocolIndicator:
    return ProtocolIndicator(
        name=name,
        value=round(_clamp(value), 4),
        direction=direction,
        confidence=0.85,
        description=description,
    )


def _missing(name: str, evidence_name: str) -> ProtocolIndicator:
    return ProtocolIndicator(
        name=name,
        value=0.0,
        direction="unknown",
        confidence=0.0,
        description=f"Missing {evidence_name} evidence.",
        missing_evidence=(evidence_name,),
    )


def _latest(items):
    if not items:
        return None
    return sorted(items, key=lambda item: item.timestamp)[-1]


def _ratio(numerator: float | int, denominator: float | int) -> float:
    if denominator <= 0:
        return 0.0
    return _clamp(float(numerator) / float(denominator))


def _clamp(value: float) -> float:
    return min(max(value, 0.0), 1.0)
