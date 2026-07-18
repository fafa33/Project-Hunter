# Historical Store Preservation

## Scope

Technology graphs, economic graphs, and backtest metrics use immutable run-addressed snapshots. This storage change preserves existing analytical semantics and authority boundaries; it introduces no new score, ranking, prediction, or production runtime.

## Snapshot Identity And Layout

The authorizing graph or backtest service supplies the stable `run_id`. Repositories derive only a filesystem-safe storage reference as `snapshots/<sha256(run_id)>`; they do not create run identity, timestamps, provenance, or replay policy.

Technology and economic graph roots use:

```text
runs.jsonl
snapshots/<run-id-digest>/manifest.json
snapshots/<run-id-digest>/nodes.jsonl
snapshots/<run-id-digest>/edges.jsonl
snapshots/<run-id-digest>/metrics.jsonl
```

Backtest roots use:

```text
runs.jsonl
calibration_reports.jsonl
snapshots/<run-id-digest>/manifest.json
snapshots/<run-id-digest>/engine_metrics.jsonl
snapshots/<run-id-digest>/project_metrics.jsonl
```

Each new run summary records its `snapshot_ref`. Historical reads resolve bodies exclusively through that reference. They never use root-level current files.

## Immutability And Retry

Snapshot files are created exclusively and never opened in replacement mode. A retry with the same run identity and byte-identical canonical content is idempotent. Different content addressed by the same run identity raises a deterministic conflict. Run summaries and calibration reports use the same identity-based append-or-verify rule, preventing duplicate retries and conflicting identity reuse.

No update or delete path exists for snapshot bodies. Saving a later run creates a different snapshot directory and cannot change an earlier directory.

## Current-State Reads

Calling a graph repository's `graph()` without a run selects the latest persisted run whose summary has a snapshot reference. This is a convenience read model only. `graph(run_id)` is the historical reconstruction API and reads only the referenced immutable snapshot.

Backtest `runs()` and `run(run_id)` reconstruct each new run from its own referenced metric files. Calibration reports remain independently append-preserved.

## Legacy Compatibility

Existing root-level graph `nodes.jsonl`, `edges.jsonl`, and `metrics.jsonl` files remain readable when no snapshot-backed run is available. Existing root-level backtest `engine_metrics.jsonl` and `project_metrics.jsonl` remain readable for legacy run summaries without `snapshot_ref`.

Legacy data is not rewritten, copied into invented historical snapshots, or linked to a run without evidence. Legacy graph status reports `legacy current-state files have no trustworthy run linkage`. Legacy backtest runs report `legacy run uses unversioned current metrics without trustworthy run linkage`. Such data remains usable as current/legacy state but cannot claim independently reconstructable historical replay.

## Authority Boundary

Services continue to authorize graph/backtest meaning, run identity, effective time, inputs, and lifecycle. Repositories only derive safe storage paths, serialize supplied bodies, enforce immutable identity consistency, and retrieve explicitly referenced snapshots, consistent with ADR 0009 and ADR 0010.

The Phase 2.1 SQL analytical envelope is not used because these existing rich file layouts already have natural run identities and body schemas. No database or source-of-truth migration is implied.
