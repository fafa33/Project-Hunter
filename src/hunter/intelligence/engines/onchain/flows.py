from __future__ import annotations

from statistics import mean

from hunter.intelligence.engines.onchain.models import OnchainDataset


class OnchainFlowAnalyzer:
    def net_capital_flow(self, dataset: OnchainDataset) -> float:
        return _net_ratio(tuple((item.inflow or 0.0, item.outflow or 0.0) for item in dataset.capital_flows))

    def exchange_netflow(self, dataset: OnchainDataset) -> float:
        return _net_ratio(tuple((item.outflow or 0.0, item.inflow or 0.0) for item in dataset.exchange_flows))

    def bridge_netflow(self, dataset: OnchainDataset) -> float:
        return _net_ratio(tuple((item.inflow or 0.0, item.outflow or 0.0) for item in dataset.bridge_flows))

    def staking_netflow(self, dataset: OnchainDataset) -> float:
        return _net_ratio(
            tuple(
                (item.staked_inflow or 0.0, (item.staked_outflow or 0.0) + (item.unstaked or 0.0))
                for item in dataset.staking_flows
            )
        )

    def capital_retention(self, dataset: OnchainDataset) -> float:
        values = [
            _ratio(item.retained_capital or 0.0, item.inflow or 0.0) for item in dataset.capital_flows if item.inflow
        ]
        return round(mean(values), 4) if values else 0.0

    def circular_flow_risk(self, dataset: OnchainDataset) -> float:
        values = [item.circular_flow_ratio for item in dataset.capital_flows if item.circular_flow_ratio is not None]
        values.extend(
            item.circular_transfer_ratio for item in dataset.transfers if item.circular_transfer_ratio is not None
        )
        return round(mean(values), 4) if values else 0.0

    def bridge_pass_through_risk(self, dataset: OnchainDataset) -> float:
        values = [item.pass_through_ratio for item in dataset.bridge_flows if item.pass_through_ratio is not None]
        return round(mean(values), 4) if values else 0.0


def _net_ratio(pairs: tuple[tuple[float, float], ...]) -> float:
    positive = sum(pair[0] for pair in pairs)
    negative = sum(pair[1] for pair in pairs)
    total = positive + negative
    if total <= 0:
        return 0.0
    return round((positive - negative) / total, 4)


def _ratio(numerator: float, denominator: float) -> float:
    if denominator <= 0:
        return 0.0
    return min(max(numerator / denominator, 0.0), 1.0)
