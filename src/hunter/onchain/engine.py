from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation, localcontext
from typing import Any

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from hunter.execution.clock import Clock, SystemClock
from hunter.execution.identity import identity
from hunter.market_validation.configuration import load_market_validation_config
from hunter.onchain.adapters import EVMJsonRpcAdapter, EVMProviderUnavailable
from hunter.onchain.configuration import OnChainConfig, load_onchain_config
from hunter.onchain.dex import (
    V2_SWAP_TOPIC,
    V3_SWAP_TOPIC,
    decode_swap_log,
    verify_pool_identity,
)
from hunter.onchain.evidence import (
    CORROBORATION_POLICY,
    TRANSFER_TOPIC,
    CapitalFlowEvidenceError,
    as_observation,
    configured_signing_key,
    observation_comparison_value,
    receipt_log_digest,
    sign_observation,
    signature_id,
)
from hunter.onchain.models import (
    CapitalFlowRecord,
    CapitalFlowSnapshot,
    DexPoolConfig,
    FlowCategory,
    OnChainSurface,
    RawOnChainObservation,
)
from hunter.onchain.registry import SurfaceRegistry
from hunter.onchain.repository import OnChainRepository

ZERO_ADDRESS = "0x0000000000000000000000000000000000000000"
SWAP_IDENTITY_RPC_CALLS = 4
MIN_SWAP_RPC_BUDGET = SWAP_IDENTITY_RPC_CALLS + 3
_ADDRESS_PATTERN = re.compile(r"0x[0-9a-f]{40}")


class CapitalFlowEngine:
    def __init__(
        self,
        config: OnChainConfig | None = None,
        repository: OnChainRepository | None = None,
        adapters: dict[int, EVMJsonRpcAdapter] | None = None,
        evidence_signing_key: Ed25519PrivateKey | None = None,
        clock: Clock | None = None,
    ) -> None:
        self.config = config or load_onchain_config()
        self.clock = clock or SystemClock()
        self.evidence_signing_key = (
            evidence_signing_key if evidence_signing_key is not None else configured_signing_key()
        )
        self.repository = repository or OnChainRepository(
            str(self.config.retention.get("runtime_root", "data/onchain/runtime"))
        )
        self.registry = SurfaceRegistry(self.config)
        self.adapters = adapters or {
            chain.chain_id: EVMJsonRpcAdapter(chain)
            for chain in self.config.chains
            if chain.enabled and chain.family == "evm"
        }

    def sync(self, project: str | None = None, end_block: int | None = None) -> tuple[CapitalFlowSnapshot, ...]:
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
                states = adapter.check_providers()
                self.repository.save_provider_states(states)
                if not any(state.status in {"healthy", "degraded"} for state in states):
                    snapshot = _unavailable_snapshot(surface, "provider_unavailable", ("live_provider",))
                    self.repository.save_snapshots((snapshot,))
                    snapshots.append(snapshot)
                    continue
                finalized_block = adapter.finalized_block_number()
                selected_end_block = finalized_block if end_block is None else end_block
                if selected_end_block < 0 or selected_end_block > finalized_block:
                    raise EVMProviderUnavailable("requested block is not finalized")
                block = adapter.block(selected_end_block)
                block_hash = str(block.get("hash", ""))
                end_timestamp = adapter.block_timestamp(selected_end_block)
                start_block = max(0, selected_end_block - self._block_window(surface.chain_id, "24h") + 1)
                observations, signatures, acquisition_unavailable = self._observations(
                    surface, adapter, start_block, selected_end_block, block_hash, end_timestamp
                )
                swap_observations, swap_signatures, swap_unavailable = self._surface_swap_observations(
                    surface, adapter, start_block, selected_end_block, block_hash, end_timestamp
                )
                evidence = observations + swap_observations
                signatures = signatures + swap_signatures
                acquisition_unavailable = tuple(sorted({*acquisition_unavailable, *swap_unavailable}))
            except EVMProviderUnavailable:
                snapshot = _unavailable_snapshot(surface, "provider_unavailable", ("live_provider",))
                self.repository.save_snapshots((snapshot,))
                snapshots.append(snapshot)
                continue
            flows = normalize_flows(
                observations,
                tuple(
                    s
                    for s in self.config.surfaces
                    if s.active and s.project == surface.project and s.chain_id == surface.chain_id
                ),
            )
            snapshot = snapshot_from_flows(
                surface, "24h", start_block, selected_end_block, flows, evidence, end_timestamp
            )
            # Bounded tracked-token evidence is not complete economic coverage.
            snapshot = replace(
                snapshot,
                status="partial",
                completeness=0.0,
                unavailable_fields=tuple(
                    set(snapshot.unavailable_fields)
                    | {"complete_transfer_coverage", "aggregate_economic_flow"}
                    | set(acquisition_unavailable)
                ),
                gross_inflow=Decimal(0),
                gross_outflow=Decimal(0),
                net_external_flow=Decimal(0),
            )
            self.repository.save_acquired_evidence(evidence, signatures)
            self.repository.save_flows(flows)
            self.repository.save_snapshots((snapshot,))
            self.repository.save_checkpoint(surface.chain_id, surface.project, selected_end_block, block_hash)
            snapshots.append(snapshot)
        return tuple(snapshots)

    def check_providers(self, chain: str | None = None) -> tuple[object, ...]:
        states = []
        for chain_config in self.config.chains:
            if chain and chain not in {chain_config.network, str(chain_config.chain_id)}:
                continue
            adapter = self.adapters.get(chain_config.chain_id)
            if adapter is None:
                continue
            states.extend(adapter.check_providers())
        self.repository.save_provider_states(tuple(states))  # type: ignore[arg-type]
        return tuple(states)

    def reset_provider_cooldown(self, chain: str) -> None:
        for chain_config in self.config.chains:
            if chain in {chain_config.network, str(chain_config.chain_id)} and chain_config.chain_id in self.adapters:
                self.adapters[chain_config.chain_id].reset_cooldown()

    def coverage(self) -> dict[str, object]:
        projects = tuple(project.project_id for project in load_market_validation_config().project_universe)
        with_surface = {surface.project for surface in self.config.surfaces if surface.active}
        snapshots = self.repository.snapshots()
        live = {str(row["project"]) for row in snapshots if row.get("status") == "live"}
        unavailable = sorted(set(projects) - with_surface)
        raw_projects = {str(row["project"]) for row in self.repository.raw() if row.get("project")}
        provider_reachable = {
            str(row["network"])
            for row in self.repository.provider_states()
            if row.get("status") in {"healthy", "degraded"}
        }
        return {
            "projects": len(projects),
            "verified_surfaces": len(self.config.surfaces),
            "projects_with_surface": len(with_surface),
            "adapter_supported_chains": len(
                [chain for chain in self.config.chains if chain.family == "evm" and chain.enabled]
            ),
            "provider_reachable_chains": len(provider_reachable),
            "live_projects": len(live),
            "raw_observation_projects": len(raw_projects),
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
    ) -> tuple[tuple[RawOnChainObservation, ...], tuple[dict[str, object], ...], tuple[str, ...]]:
        existing_rows = {
            f"{row.get('project')}:{row.get('chain_id')}:{row.get('evidence_id')}": row for row in self.repository.raw()
        }
        existing_signatures = {str(row["signature_id"]): row for row in self.repository.evidence_signatures()}
        raw_balance = adapter.native_balance(surface.address, end_block)
        native_acquired_at = self.clock.now().astimezone(UTC)
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
                acquisition_timestamp=native_acquired_at,
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
        if self.evidence_signing_key is None:
            return tuple(rows), (), ("evidence_signing_key",)
        signatures: list[dict[str, object]] = []
        unavailable: set[str] = set()
        address_topic = "0x" + surface.address.lower()[2:].zfill(64)
        seen: set[str] = set()
        for asset in self.config.tracked_assets:
            if asset.chain_id != surface.chain_id or not asset.contract_address:
                continue
            for topics in ((TRANSFER_TOPIC, address_topic), (TRANSFER_TOPIC, None, address_topic)):
                try:
                    logs = adapter.logs(
                        address=asset.contract_address, from_block=start_block, to_block=end_block, topics=topics
                    )
                    log_acquired_at = self.clock.now().astimezone(UTC)
                    log_endpoint = adapter.endpoint_identity
                    for log in logs:
                        try:
                            topics_raw = [str(item).lower() for item in log["topics"]]
                            if (
                                len(topics_raw) != 3
                                or topics_raw[0] != TRANSFER_TOPIC
                                or log.get("removed")
                                or str(log["address"]).lower() != asset.contract_address.lower()
                            ):
                                continue
                            if any(
                                len(t) != 66 or not t.startswith("0x") or t[2:26] != "0" * 24 for t in topics_raw[1:]
                            ):
                                continue
                            sender, recipient = ("0x" + t[-40:] for t in topics_raw[1:])
                            if surface.address.lower() not in (sender, recipient):
                                continue
                            number = int(str(log["blockNumber"]), 16)
                            index = int(str(log["logIndex"]), 16)
                            tx = str(log["transactionHash"]).lower()
                            data = str(log["data"]).lower()
                            block_hash_value = str(log["blockHash"]).lower()
                            if (
                                not start_block <= number <= end_block
                                or index < 0
                                or re.fullmatch(r"0x[0-9a-f]{64}", tx) is None
                                or re.fullmatch(r"0x[0-9a-f]{64}", data) is None
                                or re.fullmatch(r"0x[0-9a-f]{64}", block_hash_value) is None
                            ):
                                continue
                            int(tx, 16)
                            int(sender, 16)
                            int(recipient, 16)
                            amount = int(data, 16)
                            evidence_id = evm_log_evidence_id(surface.chain_id, tx, index)
                            if evidence_id in seen:
                                continue
                            block = adapter.block(number)
                            if str(block.get("hash", "")).lower() != block_hash_value:
                                continue
                            receipt = adapter.transaction_receipt(tx)
                            if not receipt_matches_log(receipt, {**log, "topics": topics_raw, "data": data}):
                                continue
                            receipt_endpoint = adapter.endpoint_identity
                            corroborations = adapter.corroborating_receipts(
                                tx, exclude=(log_endpoint, receipt_endpoint)
                            )
                            if not corroborations or not all(
                                receipt_matches_log(other, {**log, "topics": topics_raw, "data": data})
                                for _, other in corroborations
                            ):
                                continue
                            verified_at = self.clock.now().astimezone(UTC)
                            timestamp = datetime.fromtimestamp(int(str(block["timestamp"]), 16), tz=UTC)
                            incoming = recipient == surface.address.lower()
                            row = RawOnChainObservation(
                                project=surface.project,
                                chain_id=surface.chain_id,
                                provider=adapter.provider,
                                endpoint_identity=adapter.endpoint_identity,
                                block_number=number,
                                block_hash=block_hash_value,
                                block_timestamp=timestamp,
                                observed_address=surface.address,
                                acquisition_timestamp=log_acquired_at,
                                finality_status="finalized",
                                source_reference=surface.source_url,
                                evidence_id=evidence_id,
                                transaction_hash=tx,
                                log_index=index,
                                counterparty_address=sender if incoming else recipient,
                                asset_contract=asset.contract_address.lower(),
                                asset_symbol=asset.symbol,
                                raw_amount=str(amount),
                                decimals=asset.decimals,
                                normalized_amount=_amount(amount, asset.decimals),
                                direction="in" if incoming else "out",
                                event_type="erc20_transfer",
                                log_endpoint=log_endpoint,
                                receipt_endpoint=receipt_endpoint,
                                corroborating_endpoints=tuple(sorted(name for name, _ in corroborations)),
                                receipt_validation_policy=CORROBORATION_POLICY,
                                receipt_verified_at=verified_at,
                                log_topics=tuple(topics_raw),
                                log_data=data,
                            )
                            row = replace(row, receipt_log_digest=receipt_log_digest(row))
                            existing = existing_rows.get(f"{row.project}:{row.chain_id}:{row.evidence_id}")
                            if existing is not None and signature_id(row) in existing_signatures:
                                preserved = _preserved_signed_row(row, existing)
                                if preserved is not None:
                                    row = preserved
                                    verified_at = row.receipt_verified_at or verified_at
                            signed = sign_observation(
                                row, surface, self.evidence_signing_key, verification_timestamp=verified_at
                            )
                            rows.append(row)
                            signatures.append(signed)
                            seen.add(evidence_id)
                        except (
                            KeyError,
                            TypeError,
                            ValueError,
                            AttributeError,
                            OverflowError,
                            CapitalFlowEvidenceError,
                        ):
                            unavailable.add("transfer_evidence_signing_failed")
                            continue
                except EVMProviderUnavailable:
                    unavailable.add("transfer_corroboration_unavailable")
                    continue
        return tuple(rows), tuple(signatures), tuple(sorted(unavailable))

    def _surface_swap_observations(
        self,
        surface: OnChainSurface,
        adapter: EVMJsonRpcAdapter,
        start_block: int,
        end_block: int,
        block_hash: str,
        end_timestamp: datetime,
    ) -> tuple[tuple[RawOnChainObservation, ...], tuple[dict[str, object], ...], tuple[str, ...]]:
        pools = tuple(
            pool_row
            for pool_row in self.config.dex_pools
            if pool_row.active and pool_row.chain_id == surface.chain_id and pool_row.project == surface.project
        )
        observations: list[RawOnChainObservation] = []
        signatures: list[dict[str, object]] = []
        unavailable: set[str] = set()
        for pool_row in pools:
            rows, signed, reasons = self._swap_observations(
                pool_row,
                adapter,
                start_block=start_block,
                end_block=end_block,
                block_hash=block_hash,
                end_timestamp=end_timestamp,
            )
            observations.extend(rows)
            signatures.extend(signed)
            unavailable.update(reasons)
        return tuple(observations), tuple(signatures), tuple(sorted(unavailable))

    def _swap_observations(
        self,
        pool_row: DexPoolConfig,
        adapter: EVMJsonRpcAdapter,
        *,
        start_block: int,
        end_block: int,
        block_hash: str,
        end_timestamp: datetime,
    ) -> tuple[tuple[RawOnChainObservation, ...], tuple[dict[str, object], ...], tuple[str, ...]]:
        chain = next((item for item in self.config.chains if item.chain_id == pool_row.chain_id), None)
        budget = chain.swap_rpc_budget if chain else 12
        if budget < MIN_SWAP_RPC_BUDGET:
            return (), (), ("swap_rpc_budget_exhausted",)
        if self.evidence_signing_key is None:
            return (), (), ("evidence_signing_key",)
        unavailable: set[str] = set()
        try:
            identity_error = verify_pool_identity(pool_row, adapter, end_block)
        except (EVMProviderUnavailable, ValueError, TypeError):
            return (), (), ("pool_identity_unavailable",)
        if identity_error is not None:
            return (), (), (identity_error,)
        used = SWAP_IDENTITY_RPC_CALLS
        topic = V2_SWAP_TOPIC if pool_row.version == "v2" else V3_SWAP_TOPIC if pool_row.version == "v3" else None
        if topic is None:
            return (), (), ("unsupported_dex_schema",)
        try:
            logs = adapter.logs(address=pool_row.address, from_block=start_block, to_block=end_block, topics=(topic,))
        except EVMProviderUnavailable:
            return (), (), ("swap_log_provider_unavailable",)
        existing_rows = {
            f"{row.get('project')}:{row.get('chain_id')}:{row.get('evidence_id')}": row for row in self.repository.raw()
        }
        existing_signatures = {str(row["signature_id"]): row for row in self.repository.evidence_signatures()}
        tx_counts: dict[str, int] = {}
        for log in logs:
            tx = str(log.get("transactionHash", "")).lower()
            tx_counts[tx] = tx_counts.get(tx, 0) + 1
        observations: list[RawOnChainObservation] = []
        signatures: list[dict[str, object]] = []
        seen: set[str] = set()
        surface = pool_row.as_surface()
        for log in logs:
            try:
                if log.get("removed") or str(log.get("address", "")).lower() != pool_row.address:
                    continue
                topics_raw = tuple(str(item).lower() for item in log["topics"])
                decoded = decode_swap_log(pool_row, {**log, "topics": topics_raw})
                if decoded.status != "AVAILABLE" or decoded.sender is None:
                    continue
                number = int(str(log["blockNumber"]), 16)
                index = int(str(log["logIndex"]), 16)
                tx = str(log["transactionHash"]).lower()
                block_hash_value = str(log["blockHash"]).lower()
                data = str(log["data"]).lower()
                if (
                    not start_block <= number <= end_block
                    or index < 0
                    or block_hash_value.lower() != block_hash.lower()
                    or re.fullmatch(r"0x[0-9a-f]{64}", tx) is None
                    or topics_raw[0] != topic
                ):
                    continue
                evidence_id = evm_log_evidence_id(pool_row.chain_id, tx, index)
                if evidence_id in seen:
                    continue
                if used + 1 > budget:
                    unavailable.add("swap_rpc_budget_exhausted")
                    break
                receipt = adapter.transaction_receipt(tx)
                used += 1
                if not receipt_matches_log(receipt, {**log, "topics": topics_raw, "data": data}):
                    continue
                receipt_from = str(receipt.get("from", "")).lower()
                if _ADDRESS_PATTERN.fullmatch(receipt_from) is None:
                    continue
                if used + 1 > budget:
                    unavailable.add("swap_rpc_budget_exhausted")
                    break
                wallet_code = adapter.code(receipt_from, number)
                used += 1
                corroborations: tuple[tuple[str, dict[str, Any]], ...] = ()
                if used + 1 <= budget:
                    corroborations = adapter.corroborating_receipts(
                        tx, exclude=(adapter.endpoint_identity, adapter.endpoint_identity)
                    )
                    used += len(corroborations)
                if not corroborations or not all(
                    receipt_matches_log(other, {**log, "topics": topics_raw, "data": data})
                    for _, other in corroborations
                ):
                    unavailable.add("swap_corroboration_unavailable")
                    continue
                excluded = {pool_row.address, *pool_row.routers}
                attributable = (
                    wallet_code == "0x"
                    and receipt_from not in excluded
                    and str(receipt.get("status", "")).lower() == "0x1"
                )
                ambiguous = tx_counts.get(tx, 0) > 1
                target_amount = decoded.amount_out if decoded.trade_direction == "buy" else decoded.amount_in
                target_raw = decoded.raw_amount_out if decoded.trade_direction == "buy" else decoded.raw_amount_in
                acquired_at = self.clock.now().astimezone(UTC)
                row = RawOnChainObservation(
                    project=pool_row.project,
                    chain_id=pool_row.chain_id,
                    provider=adapter.provider,
                    endpoint_identity=adapter.endpoint_identity,
                    block_number=number,
                    block_hash=block_hash_value,
                    block_timestamp=end_timestamp,
                    observed_address=pool_row.address,
                    acquisition_timestamp=acquired_at,
                    finality_status="finalized",
                    source_reference=pool_row.source_url,
                    evidence_id=evidence_id,
                    transaction_hash=tx,
                    log_index=index,
                    counterparty_address=decoded.sender,
                    asset_contract=pool_row.target_asset,
                    asset_symbol=pool_row.target_asset_symbol,
                    raw_amount=str(target_raw),
                    decimals=(
                        pool_row.token0_decimals
                        if pool_row.target_asset == pool_row.token0
                        else pool_row.token1_decimals
                    ),
                    normalized_amount=target_amount,
                    direction="unknown",
                    event_type="dex_swap",
                    log_endpoint=adapter.endpoint_identity,
                    receipt_endpoint=adapter.endpoint_identity,
                    corroborating_endpoints=tuple(sorted(name for name, _ in corroborations)),
                    receipt_validation_policy=CORROBORATION_POLICY,
                    receipt_verified_at=acquired_at,
                    log_address=pool_row.address,
                    swap_pool_id=pool_row.pool_id,
                    swap_dex=pool_row.dex,
                    swap_version=pool_row.version,
                    swap_event_topic=topic,
                    token_in_contract=decoded.token_in,
                    token_out_contract=decoded.token_out,
                    token_in_raw_amount=str(decoded.raw_amount_in),
                    token_out_raw_amount=str(decoded.raw_amount_out),
                    token_in_amount=decoded.amount_in,
                    token_out_amount=decoded.amount_out,
                    target_asset_contract=pool_row.target_asset,
                    trade_direction=decoded.trade_direction,
                    attributed_wallet=receipt_from if attributable else None,
                    attribution_status="AVAILABLE" if attributable else "UNAVAILABLE",
                    attribution_reason=None if attributable else "router_or_contract_attribution_unavailable",
                    receipt_from=receipt_from,
                    receipt_status=str(receipt.get("status", "")).lower(),
                    wallet_code=wallet_code if attributable else None,
                    swap_status="AMBIGUOUS" if ambiguous else "AVAILABLE",
                    rpc_request_count=used,
                    log_topics=topics_raw,
                    log_data=data,
                )
                row = replace(row, receipt_log_digest=receipt_log_digest(row))
                existing = existing_rows.get(f"{row.project}:{row.chain_id}:{row.evidence_id}")
                if existing is not None and signature_id(row) in existing_signatures:
                    preserved = _preserved_signed_row(row, existing)
                    if preserved is not None:
                        row = preserved
                signed = sign_observation(row, surface, self.evidence_signing_key)
                observations.append(row)
                signatures.append(signed)
                seen.add(evidence_id)
            except (
                KeyError,
                TypeError,
                ValueError,
                AttributeError,
                OverflowError,
                CapitalFlowEvidenceError,
                EVMProviderUnavailable,
            ):
                unavailable.add("swap_evidence_signing_failed")
                continue
        return tuple(observations), tuple(signatures), tuple(sorted(unavailable))

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
    with localcontext() as context:
        context.prec = max(len(str(raw)) + decimals + 1, 28)
        return Decimal(raw).scaleb(-decimals)


def _preserved_signed_row(row: RawOnChainObservation, existing: Mapping[str, object]) -> RawOnChainObservation | None:
    try:
        previous = as_observation(existing)
    except (
        AttributeError,
        CapitalFlowEvidenceError,
        InvalidOperation,
        KeyError,
        TypeError,
        ValueError,
        OverflowError,
    ):
        return None
    current_value = dict(observation_comparison_value(row))
    previous_value = dict(observation_comparison_value(previous))
    for value in (current_value, previous_value):
        value.pop("acquisition_timestamp", None)
        value.pop("receipt_verified_at", None)
    if current_value != previous_value:
        return None
    return replace(
        row,
        acquisition_timestamp=previous.acquisition_timestamp,
        receipt_verified_at=previous.receipt_verified_at,
    )


def receipt_matches_log(receipt: dict[str, object], log: dict[str, object]) -> bool:
    keys = ("address", "topics", "data", "transactionHash", "logIndex", "blockNumber", "blockHash")
    entries = receipt.get("logs")
    return (
        str(receipt.get("status", "")).lower() == "0x1"
        and all(
            _receipt_value(receipt.get(key)) == _receipt_value(log.get(key))
            for key in ("transactionHash", "blockNumber", "blockHash")
        )
        and isinstance(entries, list)
        and any(
            isinstance(entry, dict)
            and not entry.get("removed")
            and all(
                key in entry and key in log and _receipt_value(entry[key]) == _receipt_value(log[key]) for key in keys
            )
            for entry in entries
        )
    )


def _receipt_value(value: object) -> object:
    if isinstance(value, list):
        return tuple(_receipt_value(item) for item in value)
    if isinstance(value, str) and value.startswith("0x"):
        return value.lower()
    return value


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
