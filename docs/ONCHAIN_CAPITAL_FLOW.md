# On-chain Capital Flow

Project Hunter treats capital flow as movement through explicitly verified project surfaces: protocol contracts, treasuries, vaults, lending markets, liquidity pools, bridges, fee collectors, revenue collectors, burn contracts, and other documented production contracts.

Hunter does not infer ownership from wallet behavior and does not require wallet-identity providers. Unknown ownership remains unknown.

Token movement is not automatically economic capital. Internal transfers between registered surfaces are excluded from net external flow. Project-token minting is not inflow, project-token burning is not outflow, and market-price appreciation is recorded separately from capital movement.

Supported chain family in Phase 5: EVM JSON-RPC. Public RPC endpoints are configurable in `configs/onchain.yaml`, and environment variables such as `HUNTER_ETHEREUM_RPC_URL` override committed defaults without storing secrets.

Historical replay may consume only evidence available at the cutoff. If public historical RPC data is unavailable, Hunter reports `HISTORICAL_ONCHAIN_DATA_UNAVAILABLE`.

Runtime storage is under `data/onchain/runtime/`, which is ignored by Git. Files are compacted by deterministic evidence id so repeated syncs are idempotent and do not append unbounded raw blockchain data.
