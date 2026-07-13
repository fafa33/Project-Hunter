# On-chain runtime data

Project Hunter stores direct on-chain acquisition output under `data/onchain/runtime/`.
That directory is intentionally ignored by Git because raw blockchain observations can grow quickly.

Retention policy:

- runtime files are compacted by deterministic evidence id;
- repeated syncs are idempotent for the same immutable chain identifiers;
- generated raw observations and snapshots are not committed;
- the last valid local snapshot is preserved when a live provider is unavailable;
- no existing historical evidence is deleted or rewritten by the on-chain acquisition path.
