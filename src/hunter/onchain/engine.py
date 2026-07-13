from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from hunter.execution.identity import identity
from hunter.market_validation.configuration import load_market_validation_config
from hunter.onchain.adapters import EVMJsonRpcAdapter, EVMProviderUnavailable
from hunter.onchain.configuration import OnChainConfig, load_onchain_config
from hunter.onchain.models import (
    CapitalFlowRecord,
    CapitalFlowSnapshot,
    FlowCategory,
    OnChainSurface,
    RawOnChainObservation,
)
from hunter.onchain.registry import SurfaceRegistry
from hunter.onchain.repository import OnChainRepository

TRANSFER_TOPIC = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"
ZERO_ADDRESS = "0x0000000000000000000000000000000000000000"


class CapitalFlowEngine:
    def __init__(
        self,
        config: OnChainConfig | None = None,
        repository: OnChainRepository | None = None,
        adapters: dict[int, EVMJsonRpcAdapter] | None = None,
    ) -> None:
        self.config = config or load_onchain_config()
        self.repository = repository or OnChainRepository(
            str(self.config.retention.get("runtime_root", "data/onchain/runtime"))
        )
        self.registry = SurfaceRegistry(self.config)
        self.adapters = adapters or {
            chain.chain_id: EVMJsonRpcAdapter(chain)
            for chain in self.config.chains
            if chain.enabled and chain.family == "evm"
        }

    def sync(self, project: str | None = None) -> tuple[CapitalFlowSnapshot, ...]:
        if not self.config.enabled:
            return ()
        surfaces = tuple(
            surface
            for surface in self.config.surfaces
            if surface.active and (project is None or surface.project == project)
        )
        snapshots: list[CapitalFlowSnapshot] = []
        for surface in surfaces:
            adapter = self.adapters.get(surface.chain_id)
            if adapter is None:
                snapshot = _unavailable_snapshot(surface, "unsupported_chain", ("chain_adapter",))
                self.repository.save_snapshots((snapshot,))
                snapshots.append(snapshot)
                continue
            try:
                end_block = adapter.latest_finalized_block()
                block = adapter.block(end_block)
                block_hash = str(block.get("hash", ""))
                end_timestamp = adapter.block_timestamp(end_block)
                start_block = max(0, end_block - self._block_window(surface.chain_id, "24h"))
                observations = self._observations(surface, adapter, start_block, end_block, block_hash, end_timestamp)
            except EVMProviderUnavailable:
                snapshot = _unavailable_snapshot(surface, "provider_unavailable", ("live_provider",))
                self.repository.save_snapshots((snapshot,))
                snapshots.append(snapshot)
                continue
            flows = normalize_flows(observations, (surface,))
            snapshot = snapshot_from_flows(surface, "24h", start_block, end_block, flows, observations, end_timestamp)
            self.repository.save_raw(observations)
            self.repository.save_flows(flows)
            self.repository.save_snapshots((snapshot,))
            snapshots.append(snapshot)
        return tuple(snapshots)

    def coverage(self) -> dict[str, object]:
        projects = tuple(project.project_id for project in load_market_validation_config().project_universe)
        with_surface = {surface.project for surface in self.config.surfaces if surface.active}
        snapshots = self.repository.snapshots()
        live = {str(row["project"]) for row in snapshots if row.get("status") == "live"}
        unavailable = sorted(set(projects) - with_surface)
        return {
            "projects": len(projects),
            "verified_surfaces": len(self.config.surfaces),
            "projects_with_surface": len(with_surface),
            "live_projects": len(live),
            "coverage": round((len(live) / max(len(projects), 1)) * 100, 2),
            "unavailable": tuple(unavailable),
        }

    def _observations(
        self,
        surface: OnChainSurface,
        adapter: EVMJsonRpcAdapter,
        start_block: int,
        end_block: int,
        block_hash: str,
        end_timestamp: datetime,
    ) -> tuple[RawOnChainObservation, ...]:
        raw_balance = adapter.native_balance(surface.address, end_block)
        rows = [
            RawOnChainObservation(
                project=surface.project,
                chain_id=surface.chain_id,
                provider=adapter.provider,
                endpoint_identity=adapter.endpoint_identity,
                block_number=end_block,
                block_hash=block_hash,
                block_timestamp=end_timestamp,
                observed_address=surface.address,
                acquisition_timestamp=datetime.now(tz=UTC),
                finality_status="finalized",
                source_reference=surface.source_url,
                evidence_id=identity(
                    "onchain-native-balance",
                    {"chain_id": surface.chain_id, "address": surface.address, "block": end_block},
                ),
                raw_amount=str(raw_balance),
                decimals=18,
                normalized_amount=_amount(raw_balance, 18),
                direction="balance",
                event_type="native_balance",
            )
        ]
        return tuple(rows)

    def _block_window(self, chain_id: int, window: str) -> int:
        chain = next((item for item in self.config.chains if item.chain_id == chain_id), None)
        max_range = chain.max_block_range if chain else 5000
        if window == "1h":
            return min(300, max_range)
        if window == "7d":
            return max_range
        if window == "30d":
            return max_range
        return min(7200, max_range)


def normalize_flows(
    observations: tuple[RawOnChainObservation, ...],
    surfaces: tuple[OnChainSurface, ...],
    *,
    project_token_contracts: tuple[str, ...] = (),
) -> tuple[CapitalFlowRecord, ...]:
    surface_addresses = {surface.address.lower() for surface in surfaces}
    project_token_set = {token.lower() for token in project_token_contracts}
    rows = []
    for observation in observations:
        category = _category(observation, surface_addresses, project_token_set)
        native_amount = observation.normalized_amount
        rows.append(
            CapitalFlowRecord(
                flow_id=identity(
                    "capital-flow",
                    {
                        "raw": observation.evidence_id,
                        "category": category,
                    },
                ),
                project=observation.project,
                chain_id=observation.chain_id,
                category=category,
                asset_contract=observation.asset_contract,
                asset_symbol=observation.asset_symbol,
                native_amount=native_amount,
                usd_value=None,
                usd_valuation_status="unavailable",
                direction=observation.direction,
                raw_evidence_ids=(observation.evidence_id,),
                block_number=observation.block_number,
                block_timestamp=observation.block_timestamp,
                confidence=0.7 if category == "unknown_flow" else 0.9,
                unavailable_fields=("usd_value",),
            )
        )
    return tuple(rows)


def snapshot_from_flows(
    surface: OnChainSurface,
    window: str,
    start_block: int,
    end_block: int,
    flows: tuple[CapitalFlowRecord, ...],
    observations: tuple[RawOnChainObservation, ...],
    end_timestamp: datetime,
) -> CapitalFlowSnapshot:
    gross_inflow = _sum(
        flows, ("external_inflow", "treasury_inflow", "bridge_inflow", "fee_revenue", "protocol_revenue")
    )
    gross_outflow = _sum(flows, ("external_outflow", "treasury_outflow", "bridge_outflow"))
    return CapitalFlowSnapshot(
        snapshot_id=identity(
            "capital-flow-snapshot",
            {"project": surface.project, "chain_id": surface.chain_id, "window": window, "end": end_block},
        ),
        project=surface.project,
        chain_id=surface.chain_id,
        window=window,
        start_block=start_block,
        end_block=end_block,
        start_timestamp=end_timestamp - timedelta(days=1),
        end_timestamp=end_timestamp,
        gross_inflow=gross_inflow,
        gross_outflow=gross_outflow,
        net_external_flow=gross_inflow - gross_outflow,
        liquidity_added=_sum(flows, ("liquidity_added",)),
        liquidity_removed=_sum(flows, ("liquidity_removed",)),
        deposits=_sum(flows, ("staking_deposit", "vault_deposit")),
        withdrawals=_sum(flows, ("staking_withdrawal", "vault_withdrawal")),
        bridge_inflow=_sum(flows, ("bridge_inflow",)),
        bridge_outflow=_sum(flows, ("bridge_outflow",)),
        fees_revenue=_sum(flows, ("fee_revenue", "protocol_revenue")),
        treasury_flows=_sum(flows, ("treasury_inflow",)) - _sum(flows, ("treasury_outflow",)),
        native_token_movement=_sum(flows, ("token_mint", "token_burn")),
        unavailable_fields=(
            ("usd_value", "point_in_time_price") if flows else ("flows", "usd_value", "point_in_time_price")
        ),
        evidence_ids=tuple(observation.evidence_id for observation in observations),
        confidence=0.8 if observations else 0.0,
        freshness=1.0,
        completeness=0.7 if observations else 0.0,
        status="live",
    )


def _category(
    observation: RawOnChainObservation,
    surface_addresses: set[str],
    project_token_contracts: set[str],
) -> FlowCategory:
    counterparty = (observation.counterparty_address or "").lower()
    asset = (observation.asset_contract or "").lower()
    if observation.event_type == "native_balance":
        return "unknown_flow"
    if counterparty in surface_addresses:
        return "internal_transfer"
    if observation.direction == "mint" or (counterparty == ZERO_ADDRESS and observation.direction == "in"):
        return "token_mint" if asset in project_token_contracts else "unknown_flow"
    if observation.direction == "burn" or (counterparty == ZERO_ADDRESS and observation.direction == "out"):
        return "token_burn" if asset in project_token_contracts else "unknown_flow"
    if observation.direction == "in":
        return "external_inflow"
    if observation.direction == "out":
        return "external_outflow"
    return "unknown_flow"


def _sum(flows: tuple[CapitalFlowRecord, ...], categories: tuple[str, ...]) -> Decimal:
    total = Decimal("0")
    for flow in flows:
        if flow.category in categories and flow.native_amount is not None:
            total += flow.native_amount
    return total


def _amount(raw: int, decimals: int) -> Decimal:
    return Decimal(raw) / (Decimal(10) ** Decimal(decimals))


def evm_log_evidence_id(chain_id: int, transaction_hash: str, log_index: int) -> str:
    return identity(
        "onchain-evm-log",
        {
            "chain_id": chain_id,
            "transaction_hash": transaction_hash.lower(),
            "log_index": log_index,
        },
    )


def _unavailable_snapshot(
    surface: OnChainSurface,
    status: str,
    unavailable_fields: tuple[str, ...],
) -> CapitalFlowSnapshot:
    return CapitalFlowSnapshot(
        snapshot_id=identity(
            "capital-flow-snapshot-unavailable",
            {"project": surface.project, "chain_id": surface.chain_id, "status": status},
        ),
        project=surface.project,
        chain_id=surface.chain_id,
        window="24h",
        start_block=None,
        end_block=None,
        start_timestamp=None,
        end_timestamp=None,
        gross_inflow=Decimal("0"),
        gross_outflow=Decimal("0"),
        net_external_flow=Decimal("0"),
        liquidity_added=Decimal("0"),
        liquidity_removed=Decimal("0"),
        deposits=Decimal("0"),
        withdrawals=Decimal("0"),
        bridge_inflow=Decimal("0"),
        bridge_outflow=Decimal("0"),
        fees_revenue=Decimal("0"),
        treasury_flows=Decimal("0"),
        native_token_movement=Decimal("0"),
        unavailable_fields=unavailable_fields,
        evidence_ids=(surface.evidence_id,),
        confidence=0.0,
        freshness=0.0,
        completeness=0.0,
        status=status,
    )
