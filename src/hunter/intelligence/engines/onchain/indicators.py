from __future__ import annotations

from statistics import mean

from hunter.intelligence.engines.onchain.anomalies import OnchainAnomalyModel
from hunter.intelligence.engines.onchain.configuration import OnchainEngineConfiguration
from hunter.intelligence.engines.onchain.contracts import OnchainContractAnalyzer
from hunter.intelligence.engines.onchain.flows import OnchainFlowAnalyzer
from hunter.intelligence.engines.onchain.holders import OnchainHolderAnalyzer
from hunter.intelligence.engines.onchain.models import OnchainDataset, OnchainIndicator


class OnchainIndicatorCalculator:
    def __init__(self, configuration: OnchainEngineConfiguration | None = None) -> None:
        self.configuration = configuration or OnchainEngineConfiguration()
        self._flows = OnchainFlowAnalyzer()
        self._holders = OnchainHolderAnalyzer()
        self._contracts = OnchainContractAnalyzer()
        self._anomalies = OnchainAnomalyModel(self.configuration)

    def calculate(self, dataset: OnchainDataset) -> tuple[OnchainIndicator, ...]:
        return (
            self.active_address_momentum(dataset),
            self.new_address_growth(dataset),
            self.returning_address_ratio(dataset),
            self.address_retention(dataset),
            self.adjusted_transaction_growth(dataset),
            self.adjusted_volume_growth(dataset),
            self.net_capital_flow(dataset),
            self.exchange_netflow(dataset),
            self.bridge_netflow(dataset),
            self.staking_netflow(dataset),
            self.capital_retention(dataset),
            self.holder_growth(dataset),
            self.holder_retention(dataset),
            self.long_term_holder_growth(dataset),
            self.holder_concentration(dataset),
            self.supply_distribution_quality(dataset),
            self.accumulation_breadth(dataset),
            self.distribution_breadth(dataset),
            self.contract_activity_growth(dataset),
            self.contract_diversity(dataset),
            self.application_concentration(dataset),
            self.token_velocity(dataset),
            self.dormancy(dataset),
            self.churn(dataset),
            self.network_participation(dataset),
            self.validator_concentration(dataset),
            self.governance_participation(dataset),
            self.circular_flow_risk(dataset),
            self.wash_activity_risk(dataset),
            self.sybil_risk(dataset),
            self.bot_activity_risk(dataset),
            self.bridge_pass_through_risk(dataset),
            self.anomaly_severity(dataset),
            self.onchain_acceleration(dataset),
            self.onchain_deterioration(dataset),
        )

    def active_address_momentum(self, dataset: OnchainDataset) -> OnchainIndicator:
        values = [item.active_addresses for item in dataset.addresses if item.active_addresses is not None]
        return _growth_indicator("active_address_momentum", values, "Active address growth.")

    def new_address_growth(self, dataset: OnchainDataset) -> OnchainIndicator:
        values = [item.new_addresses for item in dataset.addresses if item.new_addresses is not None]
        return _growth_indicator("new_address_growth", values, "New address growth.")

    def returning_address_ratio(self, dataset: OnchainDataset) -> OnchainIndicator:
        latest = dataset.addresses[-1] if dataset.addresses else None
        if latest is None or not latest.active_addresses:
            return _missing("returning_address_ratio", "addresses")
        return _indicator(
            "returning_address_ratio",
            _ratio(latest.returning_addresses or 0, latest.active_addresses),
            "positive",
            "Returning address ratio.",
        )

    def address_retention(self, dataset: OnchainDataset) -> OnchainIndicator:
        latest = dataset.addresses[-1] if dataset.addresses else None
        if latest is None or not latest.active_addresses:
            return _missing("address_retention", "addresses")
        return _indicator(
            "address_retention",
            _ratio(latest.retained_addresses or 0, latest.active_addresses),
            "positive",
            "Retained address ratio.",
        )

    def adjusted_transaction_growth(self, dataset: OnchainDataset) -> OnchainIndicator:
        values = [
            item.adjusted_transaction_count
            for item in dataset.transactions
            if item.adjusted_transaction_count is not None
        ]
        return _growth_indicator("adjusted_transaction_growth", values, "Adjusted transaction growth.")

    def adjusted_volume_growth(self, dataset: OnchainDataset) -> OnchainIndicator:
        values = [item.adjusted_volume for item in dataset.transactions if item.adjusted_volume is not None]
        return _growth_indicator("adjusted_volume_growth", values, "Adjusted volume growth.")

    def net_capital_flow(self, dataset: OnchainDataset) -> OnchainIndicator:
        return _signed_indicator("net_capital_flow", self._flows.net_capital_flow(dataset), "Net capital flow.")

    def exchange_netflow(self, dataset: OnchainDataset) -> OnchainIndicator:
        return _signed_indicator(
            "exchange_netflow", self._flows.exchange_netflow(dataset), "Exchange outflow minus inflow balance."
        )

    def bridge_netflow(self, dataset: OnchainDataset) -> OnchainIndicator:
        return _signed_indicator(
            "bridge_netflow", self._flows.bridge_netflow(dataset), "Bridge inflow minus outflow balance."
        )

    def staking_netflow(self, dataset: OnchainDataset) -> OnchainIndicator:
        return _signed_indicator("staking_netflow", self._flows.staking_netflow(dataset), "Staking inflow balance.")

    def capital_retention(self, dataset: OnchainDataset) -> OnchainIndicator:
        return _indicator(
            "capital_retention", self._flows.capital_retention(dataset), "positive", "Capital retained after inflow."
        )

    def holder_growth(self, dataset: OnchainDataset) -> OnchainIndicator:
        return _indicator("holder_growth", self._holders.holder_growth(dataset), "positive", "Holder growth.")

    def holder_retention(self, dataset: OnchainDataset) -> OnchainIndicator:
        return _indicator("holder_retention", self._holders.holder_retention(dataset), "positive", "Holder retention.")

    def long_term_holder_growth(self, dataset: OnchainDataset) -> OnchainIndicator:
        return _indicator(
            "long_term_holder_growth",
            self._holders.long_term_holder_growth(dataset),
            "positive",
            "Long-term holder growth.",
        )

    def holder_concentration(self, dataset: OnchainDataset) -> OnchainIndicator:
        value = self._holders.concentration(dataset)
        return _indicator(
            "holder_concentration",
            value,
            "negative" if value >= self.configuration.holder_concentration_threshold else "positive",
            "Top-holder concentration.",
        )

    def supply_distribution_quality(self, dataset: OnchainDataset) -> OnchainIndicator:
        return _indicator(
            "supply_distribution_quality",
            self._holders.supply_distribution_quality(dataset),
            "positive",
            "Supply distribution quality.",
        )

    def accumulation_breadth(self, dataset: OnchainDataset) -> OnchainIndicator:
        return _indicator(
            "accumulation_breadth", self._holders.accumulation_breadth(dataset), "positive", "Accumulation breadth."
        )

    def distribution_breadth(self, dataset: OnchainDataset) -> OnchainIndicator:
        value = self._holders.distribution_breadth(dataset)
        return _indicator(
            "distribution_breadth", value, "negative" if value >= 0.35 else "positive", "Distribution breadth."
        )

    def contract_activity_growth(self, dataset: OnchainDataset) -> OnchainIndicator:
        return _indicator(
            "contract_activity_growth",
            self._contracts.active_contract_growth(dataset),
            "positive",
            "Active contract growth.",
        )

    def contract_diversity(self, dataset: OnchainDataset) -> OnchainIndicator:
        return _indicator(
            "contract_diversity",
            self._contracts.contract_diversity(dataset),
            "positive",
            "Contract and chain diversity.",
        )

    def application_concentration(self, dataset: OnchainDataset) -> OnchainIndicator:
        value = self._contracts.application_concentration(dataset)
        return _indicator(
            "application_concentration",
            value,
            "negative" if value >= self.configuration.application_concentration_threshold else "positive",
            "Application activity concentration.",
        )

    def token_velocity(self, dataset: OnchainDataset) -> OnchainIndicator:
        latest_supply = (
            dataset.supply[-1].circulating_supply if dataset.supply and dataset.supply[-1].circulating_supply else None
        )
        latest_volume = (
            dataset.transactions[-1].adjusted_volume
            if dataset.transactions and dataset.transactions[-1].adjusted_volume
            else None
        )
        if not latest_supply or latest_volume is None:
            return _missing("token_velocity", "supply_or_volume")
        return _indicator(
            "token_velocity",
            min(latest_volume / latest_supply, 1.0),
            "neutral",
            "Adjusted volume relative to circulating supply.",
        )

    def dormancy(self, dataset: OnchainDataset) -> OnchainIndicator:
        return _indicator("dormancy", self._holders.dormancy(dataset), "positive", "Dormant supply ratio.")

    def churn(self, dataset: OnchainDataset) -> OnchainIndicator:
        retention = self.holder_retention(dataset).value
        return _indicator("churn", 1.0 - retention, "negative" if retention else "unknown", "Holder churn.")

    def network_participation(self, dataset: OnchainDataset) -> OnchainIndicator:
        address = self.returning_address_ratio(dataset).value
        contract = self.contract_diversity(dataset).value
        governance = self.governance_participation(dataset).value
        return _indicator(
            "network_participation", mean((address, contract, governance)), "positive", "Network participation breadth."
        )

    def validator_concentration(self, dataset: OnchainDataset) -> OnchainIndicator:
        values = [
            item.top_validator_share or item.staker_concentration
            for item in dataset.validators
            if (item.top_validator_share or item.staker_concentration) is not None
        ]
        value = mean(values) if values else 0.0
        return _indicator(
            "validator_concentration",
            value,
            "negative" if value >= 0.45 else "positive",
            "Validator or staker concentration.",
        )

    def governance_participation(self, dataset: OnchainDataset) -> OnchainIndicator:
        values = [item.participation_ratio for item in dataset.governance if item.participation_ratio is not None]
        return _indicator(
            "governance_participation",
            mean(values) if values else 0.0,
            "positive",
            "On-chain governance participation.",
        )

    def circular_flow_risk(self, dataset: OnchainDataset) -> OnchainIndicator:
        return _indicator(
            "circular_flow_risk", self._flows.circular_flow_risk(dataset), "negative", "Circular flow risk."
        )

    def wash_activity_risk(self, dataset: OnchainDataset) -> OnchainIndicator:
        return _indicator(
            "wash_activity_risk", self._anomalies.wash_activity_risk(dataset), "negative", "Wash activity risk."
        )

    def sybil_risk(self, dataset: OnchainDataset) -> OnchainIndicator:
        return _indicator("sybil_risk", self._anomalies.sybil_risk(dataset), "negative", "Sybil activity risk.")

    def bot_activity_risk(self, dataset: OnchainDataset) -> OnchainIndicator:
        return _indicator(
            "bot_activity_risk", self._anomalies.bot_activity_risk(dataset), "negative", "Bot activity risk."
        )

    def bridge_pass_through_risk(self, dataset: OnchainDataset) -> OnchainIndicator:
        return _indicator(
            "bridge_pass_through_risk",
            self._flows.bridge_pass_through_risk(dataset),
            "negative",
            "Bridge pass-through risk.",
        )

    def anomaly_severity(self, dataset: OnchainDataset) -> OnchainIndicator:
        assessment = self._anomalies.assess(dataset)
        return _indicator("anomaly_severity", assessment.severity, "negative", assessment.explanation)

    def onchain_acceleration(self, dataset: OnchainDataset) -> OnchainIndicator:
        value = mean(
            (
                self.active_address_momentum(dataset).value,
                self.adjusted_transaction_growth(dataset).value,
                self.net_capital_flow(dataset).value,
                self.holder_growth(dataset).value,
                self.contract_activity_growth(dataset).value,
            )
        )
        return _indicator("onchain_acceleration", value, "positive", "Combined on-chain acceleration.")

    def onchain_deterioration(self, dataset: OnchainDataset) -> OnchainIndicator:
        value = mean(
            (
                self.churn(dataset).value,
                self.holder_concentration(dataset).value,
                self.anomaly_severity(dataset).value,
                1.0 - self.net_capital_flow(dataset).value,
            )
        )
        return _indicator(
            "onchain_deterioration",
            value,
            "negative" if value >= 0.5 else "positive",
            "Combined on-chain deterioration.",
        )


def _growth_indicator(name: str, values: list[int | float], description: str) -> OnchainIndicator:
    if len(values) < 2 or values[0] <= 0:
        return _missing(name, name)
    return _indicator(name, 0.5 + ((values[-1] - values[0]) / values[0]), "positive", description)


def _signed_indicator(name: str, signed_value: float, description: str) -> OnchainIndicator:
    normalized = (signed_value + 1.0) / 2.0
    return _indicator(name, normalized, "positive" if signed_value >= 0 else "negative", description)


def _indicator(name: str, value: float, direction: str, description: str) -> OnchainIndicator:
    return OnchainIndicator(
        name=name, value=round(_clamp(value), 4), direction=direction, confidence=0.85, description=description
    )


def _missing(name: str, evidence_name: str) -> OnchainIndicator:
    return OnchainIndicator(
        name=name,
        value=0.0,
        direction="unknown",
        confidence=0.0,
        description=f"Missing {evidence_name} evidence.",
        missing_evidence=(evidence_name,),
    )


def _ratio(numerator: int | float, denominator: int | float) -> float:
    if denominator <= 0:
        return 0.0
    return _clamp(float(numerator) / float(denominator))


def _clamp(value: float) -> float:
    return min(max(float(value), 0.0), 1.0)
