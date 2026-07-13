# On-chain Capital Flow

Project Hunter treats capital flow as movement through explicitly verified project surfaces: protocol contracts, treasuries, vaults, lending markets, liquidity pools, bridges, fee collectors, revenue collectors, burn contracts, and other documented production contracts.

Hunter does not infer ownership from wallet behavior and does not require wallet-identity providers. Unknown ownership remains unknown.

Token movement is not automatically economic capital. Internal transfers between registered surfaces are excluded from net external flow. Project-token minting is not inflow, project-token burning is not outflow, and market-price appreciation is recorded separately from capital movement.

Supported chain family: shared EVM JSON-RPC. Configured EVM networks are Ethereum, Arbitrum One, Optimism, Base, Polygon, BNB Smart Chain, and Avalanche C-Chain. A chain is only live-validated after the chain id, latest finalized block, and at least one configured surface query succeed.

Provider pools are ordered and deterministic. Hunter checks `eth_chainId` and `eth_blockNumber`, records latency, capabilities, latest finalized block, failure type, cooldown, and last success, then fails over around forbidden, rate-limited, timed-out, invalid, and wrong-chain providers. Environment variables such as `HUNTER_ETHEREUM_RPC_URL`, `HUNTER_ARBITRUM_RPC_URL`, `HUNTER_OPTIMISM_RPC_URL`, `HUNTER_BASE_RPC_URL`, `HUNTER_POLYGON_RPC_URL`, `HUNTER_BNB_RPC_URL`, and `HUNTER_AVALANCHE_RPC_URL` override committed public defaults without storing secrets.

Public RPC limitations are treated as provider state, not zero activity. HTTP 403 is `forbidden`, HTTP 429 is `rate_limited`, timeouts are `timed_out`, invalid payloads are `invalid_response`, and chain-id mismatches are `wrong_chain`. Optional private RPC endpoints can be supplied through the environment variables above; no paid provider is mandatory.

Verified surfaces are contract-level only when an official source documents the production role. Network-level projects, non-EVM systems, token-only surfaces, and off-chain revenue remain explicitly unavailable until an appropriate adapter or documented surface exists.

Automation is installed with `hunter onchain automation install` and is idempotent. The operational worker command is `.venv/bin/hunter --config configs/automation.yaml automation start --max-iterations 0`. On-chain automation metadata lives in ignored runtime storage, records provider health, incremental sync, hourly snapshots, daily consolidation, weekly revalidation, failed retries, checkpoints, and missed windows. Restart recovery resumes from persisted checkpoints and never backfills unavailable history as recovered data.

Coverage is reported separately for registry coverage, verified-surface coverage, adapter-supported chains, provider-reachable chains, live acquisition, raw observations, capital-flow snapshots, USD-valued flows, automation installation, freshness, and historical availability. A registered surface is not live coverage, a provider failure is not a zero-flow result, and an old snapshot is not fresh.

Historical replay may consume only evidence available at the cutoff. If public historical RPC data is unavailable, Hunter reports `HISTORICAL_ONCHAIN_DATA_UNAVAILABLE`.

Runtime storage is under `data/onchain/runtime/`, which is ignored by Git. Files are compacted by deterministic evidence id, provider id, checkpoint id, and job id so repeated syncs and repeated automation installation are idempotent and do not append unbounded raw blockchain data.
