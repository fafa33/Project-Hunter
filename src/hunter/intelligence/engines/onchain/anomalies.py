from __future__ import annotations

from statistics import mean

from hunter.intelligence.engines.onchain.configuration import OnchainEngineConfiguration
from hunter.intelligence.engines.onchain.flows import OnchainFlowAnalyzer
from hunter.intelligence.engines.onchain.models import AnomalyAssessment, OnchainDataset


class OnchainAnomalyModel:
    def __init__(self, configuration: OnchainEngineConfiguration | None = None) -> None:
        self.configuration = configuration or OnchainEngineConfiguration()
        self._flows = OnchainFlowAnalyzer()

    def assess(self, dataset: OnchainDataset) -> AnomalyAssessment:
        circular = self._flows.circular_flow_risk(dataset)
        wash = self.wash_activity_risk(dataset)
        sybil = self.sybil_risk(dataset)
        bot = self.bot_activity_risk(dataset)
        bridge = self._flows.bridge_pass_through_risk(dataset)
        severity = max(circular, wash, sybil, bot, bridge)
        if not dataset.records:
            level = "insufficient_evidence"
        elif severity >= self.configuration.detected_anomaly_threshold:
            level = "detected"
        elif severity >= self.configuration.anomaly_threshold:
            level = "suspected"
        else:
            level = "insufficient_evidence"
        return AnomalyAssessment(
            level=level,
            circular_flow_risk=circular,
            wash_activity_risk=wash,
            sybil_risk=sybil,
            bot_activity_risk=bot,
            bridge_pass_through_risk=bridge,
            severity=severity,
            explanation=f"circular={circular:.2f}; wash={wash:.2f}; sybil={sybil:.2f}; bot={bot:.2f}; bridge_pass_through={bridge:.2f}",
        )

    def wash_activity_risk(self, dataset: OnchainDataset) -> float:
        values = [item.low_value_ratio for item in dataset.transactions if item.low_value_ratio is not None]
        values.extend(item.repeated_pattern_ratio for item in dataset.transactions if item.repeated_pattern_ratio is not None)
        return round(mean(values), 4) if values else 0.0

    def sybil_risk(self, dataset: OnchainDataset) -> float:
        values = [item.sybil_ratio for item in dataset.addresses if item.sybil_ratio is not None]
        values.extend(item.wallet_creation_cluster_ratio for item in dataset.addresses if item.wallet_creation_cluster_ratio is not None)
        return round(mean(values), 4) if values else 0.0

    def bot_activity_risk(self, dataset: OnchainDataset) -> float:
        values = [item.bot_ratio for item in dataset.addresses if item.bot_ratio is not None]
        values.extend(item.gas_anomaly_ratio for item in dataset.transactions if item.gas_anomaly_ratio is not None)
        return round(mean(values), 4) if values else 0.0
