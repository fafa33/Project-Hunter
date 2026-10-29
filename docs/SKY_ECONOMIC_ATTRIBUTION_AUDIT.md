# SKY buyback economic attribution: evidence verdict (2026-10-29)

Scope: Issue #389, existing PR #658. This is an evidence audit and does not
activate ADR-0026 Comparative Valuation or assert any historical cutoff proof.

## Authoritative published references

- MSC-12 official issuer settlement: https://forum.skyeco.com/t/msc-12-settlement-summary-august-2026/28217
- MSC-13 official issuer settlement: https://forum.skyeco.com/t/msc-13-settlement-summary-september-2026/28274
- Settlement calculation pinned commit:
  https://github.com/soterlabs/settlement-cycle/commit/536f1492089ed82fbf6eaabb956f7a194b887d47
- Pinned methodology:
  https://raw.githubusercontent.com/soterlabs/settlement-cycle/536f1492089ed82fbf6eaabb956f7a194b887d47/docs/tmf/README.md
- August and September source files:
  settlements/tmf/2026-08/summary.md and settlements/tmf/2026-09/summary.md
- Financial data: https://sky.data.blockanalitica.com/internal/msc/
  and https://sky.data.blockanalitica.com/internal/buyback/burns/monthly/

## Settlement-specific evidence

| Settlement | Burn tx | Verified burned SKY | Published Sky Net Revenue (USDS) | Published burn USD | TMF eligible Flapper spend | 10/55 derived spend | Published minus derived |
|---|---|---:|---:|---:|---:|---:|---:|
| MSC-12 | 0xcd57535f7a9e60e61d34b31f793501fa6178b4ae37fe0b2900f76efd54e192c3 | 2,860,943.76 | 15,745,296 | 186,553.806539509538247255 | 1,026,300 | 186,600 | -46.193460490461752745 |
| MSC-13 | 0x93389082b75a9e278b6b2a2d7dde53618c38214b5d71699fd60461dbe1e10275 | 7,372,288 | 14,635,947 | 507,599.999999999999789964 | 2,791,800 | 507,600 | -0.000000000000210036 |

The burn token quantities and receipt hashes were previously verified on
Ethereum. The monthly TMF calculation employs an effective 10%/55% split after
the August-17 first cast, with earlier legacy activity not attributed.
Published August burn cost is NOT exactly equal to a 10/55 split of the rounded
receipt spending figure. Do not erase that discrepancy or silently reclassify
the USD as a verified exact acquisition cost.

## Historical onchain receipts and coverage

Checkpoint: `/home/hunter/work/issue-389/sky-reconciliation-checkpoint.json`
(off-repo; 954 verified buyback receipts, 3,148,200 USDS paid,
42,029,405.165221595025723356 SKY acquired; bounds
25,968,583–26,176,859). Its 575 receipts through Sep-30's end block
26,093,737 represent 1,897,500 USDS, **not** the full 2,791,800 USDS
September report; the prior 894,300 USDS falls before checkpoint start.
Hence neither MSC-12 nor the full MSC-13 acquisition window is independently
verified by that checkpoint. It must be extended backwards with exact finalized
receipt verification before a full-chain spent-total can be claimed.

The 961-verified-transfer treasury ledger for the bounded Sep-13 to Oct-12
window covers token transfers, not revenue accounting; six transfers to
`0x675671a8756ddb69f7254afb030865388ef699ee` are to the reward
distributor, documented as `REWARDS_DIST_LSSKY_SKY` in the pinned TMF
contract map. Treat those as staking reward routing, not burns or sunk
capital. Only a zero-address transfer is a candidate token burn.

## Revenue-origin proof versus reported earned income

Published Sky Net Revenue (accrual-based accounting) and the TMF waterfall
explain how a 45/45/10 reward/buyback/burn budget is **authorized**.
The Sky MSC posts themselves explain the settlement mechanism **mints debt**
into the surplus buffer for some prime settlements (e.g. Spark and Grove).
A per-transaction ZERO→Flapper USDS mint and Flapper→pool transfer does NOT
prove those USDS units were individually sourced from realized revenue.
This distinction is fundamental for a fungible token / Vat internal ledger.

Proof levels:
1. Ethereum receipt verified = acquisition/payment evidence.
2. Pinned issuer monthly accounting = reported revenue and allocation policy.
3. Independently auditable accounting entries connecting accrued earnings,
   capital controls, surplus-buffer debits and treasury spend = unproven.
4. Exact acquisition-to-particular-burn inventory identity/FIFO/cost basis =
   unproven. Do not invent fungible-token FIFO or chronological source links.

## Decision and safe fallback

The source ledger can be closed with **EVIDENCE_GAP_DOCUMENTED**, rather than
pretending that unverified accounting has passed. It does NOT satisfy a
strict-known historical native value-capture input: those items remain
`UNPROVEN` and contribute no numeric attributable flow to the historical
Comparative Valuation/ADR-0026 service until primary transaction-level
accounting and an approved allocation policy independently confirm them.

No current backfill is valid for an earlier observed cutoff. Pin publication,
observation time and source hash per historical evidence record. Do not activate
Comparative Valuation CLI or weaken an existing governance test.

Next proof-producing work if primary data becomes available:
- Reconstruct full August-17→Aug-31 and Sep-1→Sep-30 buyback receipt sets
  (bounded archival pagination and finalized receipts, no partial-window
  extrapolation).
- Confirm exact `Kick` and `Exec` pairing, onchain regime parameters and
  distinct burn/rewards legs at each transaction.
- Read authoritative Sky accounting settlements and Vow/Vat entries, proving
  revenue accrual and approved transfer ledger; separate debt-mint mechanics.
- Link the relevant settlement spell to exact burn log and source allocation
  without double counting earlier holdings; record conflicts explicitly.

The code in `sky_economic_attribution.py` and `sky_receipt_window.py`
provides auditable, deterministic guards for these distinctions. This
document is **not** a declaration that Issue #389 or PR #658 is merge-ready.
