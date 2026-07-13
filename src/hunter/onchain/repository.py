from __future__ import annotations

import json
from dataclasses import asdict
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from hunter.onchain.models import CapitalFlowRecord, CapitalFlowSnapshot, ProviderState, RawOnChainObservation


class OnChainRepository:
    def __init__(self, root: str | Path = "data/onchain/runtime") -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def save_raw(self, rows: tuple[RawOnChainObservation, ...]) -> None:
        self._upsert("raw_observations.jsonl", "evidence_id", (_payload(row) for row in rows))

    def save_flows(self, rows: tuple[CapitalFlowRecord, ...]) -> None:
        self._upsert("capital_flows.jsonl", "flow_id", (_payload(row) for row in rows))

    def save_snapshots(self, rows: tuple[CapitalFlowSnapshot, ...]) -> None:
        self._upsert("snapshots.jsonl", "snapshot_id", (_payload(row) for row in rows))

    def save_provider_states(self, rows: tuple[ProviderState, ...]) -> None:
        payloads = []
        for row in rows:
            payload = _payload(row)
            payload["state_id"] = f"{row.chain_id}:{row.endpoint_identity}"
            payloads.append(payload)
        self._upsert("provider_status.jsonl", "state_id", payloads)

    def save_checkpoint(self, chain_id: int, project: str, block_number: int, block_hash: str) -> None:
        self._upsert(
            "checkpoints.jsonl",
            "checkpoint_id",
            (
                {
                    "checkpoint_id": f"{chain_id}:{project}",
                    "chain_id": chain_id,
                    "project": project,
                    "block_number": block_number,
                    "block_hash": block_hash,
                    "updated_at": datetime.now().astimezone().isoformat(),
                },
            ),
        )

    def raw(self) -> tuple[dict[str, Any], ...]:
        return _read_jsonl(self.root / "raw_observations.jsonl")

    def flows(self) -> tuple[dict[str, Any], ...]:
        return _read_jsonl(self.root / "capital_flows.jsonl")

    def snapshots(self) -> tuple[dict[str, Any], ...]:
        return _read_jsonl(self.root / "snapshots.jsonl")

    def provider_states(self) -> tuple[dict[str, Any], ...]:
        return _read_jsonl(self.root / "provider_status.jsonl")

    def checkpoints(self) -> tuple[dict[str, Any], ...]:
        return _read_jsonl(self.root / "checkpoints.jsonl")

    def _upsert(self, name: str, key: str, rows: object) -> None:
        path = self.root / name
        existing = {str(row[key]): row for row in _read_jsonl(path) if key in row}
        for row in rows:  # type: ignore[union-attr]
            existing[str(row[key])] = row
        with path.open("w", encoding="utf-8") as handle:
            for row in sorted(existing.values(), key=lambda item: str(item[key])):
                handle.write(json.dumps(row, sort_keys=True, default=_json_default))
                handle.write("\n")


def _payload(row: RawOnChainObservation | CapitalFlowRecord | CapitalFlowSnapshot | ProviderState) -> dict[str, Any]:
    return asdict(row)


def _read_jsonl(path: Path) -> tuple[dict[str, Any], ...]:
    if not path.exists():
        return ()
    return tuple(json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip())


def _json_default(value: object) -> str:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    return str(value)
