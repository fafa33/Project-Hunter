from __future__ import annotations

from collections import defaultdict

from hunter.intelligence.engines.onchain.configuration import OnchainEngineConfiguration
from hunter.intelligence.engines.onchain.models import (
    AddressSnapshot,
    ApplicationActivitySnapshot,
    BridgeFlowSnapshot,
    CapitalFlowSnapshot,
    ContractActivitySnapshot,
    ContractDeploymentSnapshot,
    ExchangeFlowSnapshot,
    GovernanceActivitySnapshot,
    HolderSnapshot,
    MintBurnSnapshot,
    OnchainDataset,
    OnchainEvent,
    OnchainInput,
    StakingFlowSnapshot,
    SupplyDistributionSnapshot,
    TransactionSnapshot,
    TransferSnapshot,
    TreasuryActivitySnapshot,
    ValidatorDistributionSnapshot,
)
from hunter.intelligence.intelligence import Intelligence


class OnchainNormalizer:
    def __init__(self, configuration: OnchainEngineConfiguration | None = None) -> None:
        self.configuration = configuration or OnchainEngineConfiguration()

    def normalize(self, records: tuple[OnchainInput, ...], intelligence: tuple[Intelligence, ...] = ()) -> OnchainDataset:
        unique: dict[str, OnchainInput] = {}
        duplicates: list[str] = []
        overlaps: list[str] = []
        windows: dict[tuple[str, str, str, str, str], list[OnchainInput]] = defaultdict(list)

        for record in records:
            key = _record_key(record)
            if self.configuration.duplicate_detection and key in unique:
                duplicates.append(record.id)
                continue
            unique[key] = record
            windows[_window_key(record)].append(record)

        if self.configuration.overlap_detection:
            for window_records in windows.values():
                if len({type(item).__name__ for item in window_records}) == 1 and len(window_records) > 1:
                    overlaps.extend(item.id for item in window_records[1:])

        normalized = tuple(sorted(unique.values(), key=lambda item: (item.timestamp.isoformat(), type(item).__name__, item.id)))
        return OnchainDataset(
            project=_project(normalized, self.configuration.project),
            records=normalized,
            addresses=_of_type(normalized, AddressSnapshot),
            transactions=_of_type(normalized, TransactionSnapshot),
            transfers=_of_type(normalized, TransferSnapshot),
            capital_flows=_of_type(normalized, CapitalFlowSnapshot),
            exchange_flows=_of_type(normalized, ExchangeFlowSnapshot),
            bridge_flows=_of_type(normalized, BridgeFlowSnapshot),
            staking_flows=_of_type(normalized, StakingFlowSnapshot),
            holders=_of_type(normalized, HolderSnapshot),
            supply=_of_type(normalized, SupplyDistributionSnapshot),
            contract_activity=_of_type(normalized, ContractActivitySnapshot),
            contract_deployments=_of_type(normalized, ContractDeploymentSnapshot),
            applications=_of_type(normalized, ApplicationActivitySnapshot),
            treasury=_of_type(normalized, TreasuryActivitySnapshot),
            mint_burn=_of_type(normalized, MintBurnSnapshot),
            validators=_of_type(normalized, ValidatorDistributionSnapshot),
            governance=_of_type(normalized, GovernanceActivitySnapshot),
            events=_of_type(normalized, OnchainEvent),
            duplicates=tuple(sorted(set(duplicates))),
            overlapping_windows=tuple(sorted(set(overlaps))),
            missing_fields=_missing(normalized),
            cross_engine_alignment=_cross_engine_alignment(intelligence),
        )


def _record_key(record: OnchainInput) -> str:
    return "|".join(
        (
            type(record).__name__,
            record.project.lower(),
            record.asset,
            record.chain,
            record.source,
            record.timestamp.isoformat(),
            record.reference,
            record.transaction_hash or "",
            record.contract_address or "",
            str(record.block_height or ""),
        )
    )


def _window_key(record: OnchainInput) -> tuple[str, str, str, str, str]:
    return (type(record).__name__, record.project.lower(), record.asset, record.chain, record.timestamp.date().isoformat())


def _project(records: tuple[OnchainInput, ...], default: str) -> str:
    projects = {record.project for record in records if record.project}
    return sorted(projects)[0] if projects else default


def _of_type(records: tuple[OnchainInput, ...], kind):
    return tuple(record for record in records if isinstance(record, kind))


def _missing(records: tuple[OnchainInput, ...]) -> tuple[str, ...]:
    required = {
        "addresses": AddressSnapshot,
        "transactions": TransactionSnapshot,
        "capital_flows": CapitalFlowSnapshot,
        "holders": HolderSnapshot,
        "contract_activity": ContractActivitySnapshot,
    }
    return tuple(sorted(name for name, kind in required.items() if not any(isinstance(record, kind) for record in records)))


def _cross_engine_alignment(intelligence: tuple[Intelligence, ...]) -> float:
    if not intelligence:
        return 0.0
    related = [item for item in intelligence if item.engine in {"whale-intelligence", "protocol-intelligence", "social-intelligence", "narrative-intelligence"}]
    if not related:
        return 0.0
    positive = sum(1 for item in related if item.confidence.score >= 0.5)
    return min(positive / len(related), 1.0)
