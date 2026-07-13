from __future__ import annotations

import json
from dataclasses import asdict
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from hunter.onchain.models import CapitalFlowRecord, CapitalFlowSnapshot, RawOnChainObservation


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

    def raw(self) -> tuple[dict[str, Any], ...]:
        return _read_jsonl(self.root / "raw_observations.jsonl")

    def flows(self) -> tuple[dict[str, Any], ...]:
        return _read_jsonl(self.root / "capital_flows.jsonl")

    def snapshots(self) -> tuple[dict[str, Any], ...]:
        return _read_jsonl(self.root / "snapshots.jsonl")

    def _upsert(self, name: str, key: str, rows: object) -> None:
        path = self.root / name
        existing = {str(row[key]): row for row in _read_jsonl(path) if key in row}
        for row in rows:  # type: ignore[union-attr]
            existing[str(row[key])] = row
        with path.open("w", encoding="utf-8") as handle:
            for row in sorted(existing.values(), key=lambda item: str(item[key])):
                handle.write(json.dumps(row, sort_keys=True, default=_json_default))
                handle.write("\n")


def _payload(row: RawOnChainObservation | CapitalFlowRecord | CapitalFlowSnapshot) -> dict[str, Any]:
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
