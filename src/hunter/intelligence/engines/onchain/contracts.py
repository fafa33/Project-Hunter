from __future__ import annotations

from statistics import mean

from hunter.intelligence.engines.onchain.models import OnchainDataset


class OnchainContractAnalyzer:
    def active_contract_growth(self, dataset: OnchainDataset) -> float:
        values = [item.active_contracts for item in dataset.contract_activity if item.active_contracts is not None]
        return _growth(values)

    def contract_diversity(self, dataset: OnchainDataset) -> float:
        contracts = {item.contract_address for item in dataset.contract_activity if item.contract_address}
        chains = {item.chain for item in dataset.contract_activity}
        return min((len(contracts) + len(chains)) / 10, 1.0)

    def deployment_growth(self, dataset: OnchainDataset) -> float:
        values = [item.deployments for item in dataset.contract_deployments if item.deployments is not None]
        return _growth(values)

    def interaction_breadth(self, dataset: OnchainDataset) -> float:
        latest = dataset.contract_activity[-1] if dataset.contract_activity else None
        if latest is None or not latest.interactions:
            return 0.0
        return _ratio(latest.unique_callers or 0, latest.interactions)

    def application_concentration(self, dataset: OnchainDataset) -> float:
        shares = [item.transaction_share or item.volume_share or 0.0 for item in dataset.applications]
        return round(max(shares), 4) if shares else 0.0

    def spam_contract_risk(self, dataset: OnchainDataset) -> float:
        values = [item.spam_contract_ratio for item in dataset.contract_activity if item.spam_contract_ratio is not None]
        values.extend(item.generated_contract_ratio for item in dataset.contract_activity if item.generated_contract_ratio is not None)
        return round(mean(values), 4) if values else 0.0


def _growth(values: list[int | None]) -> float:
    cleaned = [value for value in values if value is not None]
    if len(cleaned) < 2 or cleaned[0] <= 0:
        return 0.0
    return min(max(0.5 + ((cleaned[-1] - cleaned[0]) / cleaned[0]), 0.0), 1.0)


def _ratio(numerator: int | float, denominator: int | float) -> float:
    if denominator <= 0:
        return 0.0
    return min(max(float(numerator) / float(denominator), 0.0), 1.0)
