from __future__ import annotations

from typing import Protocol

from hunter.intelligence.engines.onchain.models import OnchainInput
from hunter.plugins.contracts import PipelineContext


class OnchainCollector(Protocol):
    def collect(self, context: PipelineContext) -> tuple[OnchainInput, ...]:
        raise NotImplementedError


class ContextOnchainCollector:
    def collect(self, context: PipelineContext) -> tuple[OnchainInput, ...]:
        records = context.get("onchain_records", ())
        return tuple(record for record in records if _is_onchain_record(record))


def _is_onchain_record(record: object) -> bool:
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
        OnchainEvent,
        StakingFlowSnapshot,
        SupplyDistributionSnapshot,
        TransactionSnapshot,
        TransferSnapshot,
        TreasuryActivitySnapshot,
        ValidatorDistributionSnapshot,
    )

    return isinstance(
        record,
        (
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
            OnchainEvent,
            StakingFlowSnapshot,
            SupplyDistributionSnapshot,
            TransactionSnapshot,
            TransferSnapshot,
            TreasuryActivitySnapshot,
            ValidatorDistributionSnapshot,
        ),
    )
