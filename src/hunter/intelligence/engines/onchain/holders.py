from __future__ import annotations

from statistics import mean

from hunter.intelligence.engines.onchain.models import OnchainDataset


class OnchainHolderAnalyzer:
    def holder_growth(self, dataset: OnchainDataset) -> float:
        values = [item.holder_count for item in dataset.holders if item.holder_count is not None]
        return _growth(values)

    def holder_retention(self, dataset: OnchainDataset) -> float:
        latest = dataset.holders[-1] if dataset.holders else None
        if latest is None or not latest.holder_count:
            return 0.0
        return _ratio(latest.retained_holders or 0, latest.holder_count)

    def long_term_holder_growth(self, dataset: OnchainDataset) -> float:
        values = [item.long_term_holders for item in dataset.holders if item.long_term_holders is not None]
        return _growth(values)

    def concentration(self, dataset: OnchainDataset) -> float:
        holder_values = [item.top_holder_share for item in dataset.holders if item.top_holder_share is not None]
        supply_values = [item.top_10_share for item in dataset.supply if item.top_10_share is not None]
        values = [*holder_values, *supply_values]
        return round(mean(values), 4) if values else 0.0

    def supply_distribution_quality(self, dataset: OnchainDataset) -> float:
        values = [item.distribution_quality for item in dataset.supply if item.distribution_quality is not None]
        if values:
            return round(mean(values), 4)
        concentration = self.concentration(dataset)
        return round(1.0 - concentration, 4) if concentration else 0.0

    def accumulation_breadth(self, dataset: OnchainDataset) -> float:
        latest = dataset.holders[-1] if dataset.holders else None
        if latest is None:
            return 0.0
        return _ratio(latest.accumulation_wallets or 0, latest.holder_count or 0)

    def distribution_breadth(self, dataset: OnchainDataset) -> float:
        latest = dataset.holders[-1] if dataset.holders else None
        if latest is None:
            return 0.0
        return _ratio(latest.distribution_wallets or 0, latest.holder_count or 0)

    def dormancy(self, dataset: OnchainDataset) -> float:
        values = [item.dormant_supply_ratio for item in dataset.holders if item.dormant_supply_ratio is not None]
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
