from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

METADATA_KEY = "_record_metadata"


class JsonlContractError(ValueError):
    """Raised for malformed or unsupported versioned JSONL records."""


@dataclass(frozen=True)
class JsonlWritePlan:
    schema_version: str
    recorded_at: datetime
    known_at: datetime | None
    known_time_limitation: str | None
    effective_at: datetime

    def __post_init__(self) -> None:
        if not self.schema_version.strip():
            raise JsonlContractError("schema_version is required")
        for name in ("recorded_at", "effective_at"):
            value = getattr(self, name)
            if value.tzinfo is None:
                raise JsonlContractError(f"{name} must be timezone-aware")
            object.__setattr__(self, name, value.astimezone(UTC))
        if self.known_at is not None:
            if self.known_at.tzinfo is None:
                raise JsonlContractError("known_at must be timezone-aware")
            object.__setattr__(self, "known_at", self.known_at.astimezone(UTC))
            if self.known_at > self.recorded_at:
                raise JsonlContractError("known_at cannot be later than recorded_at")
            if self.known_time_limitation is not None:
                raise JsonlContractError("known_time_limitation must be absent when known_at is known")
        elif not self.known_time_limitation:
            raise JsonlContractError("known_time_limitation is required when known_at is unknown")


@dataclass(frozen=True)
class JsonlRecord:
    payload: dict[str, Any]
    schema_version: str | None
    recorded_at: datetime | None
    known_at: datetime | None
    effective_at: datetime | None
    replay_limitation: str | None

    @property
    def strict_known_eligible(self) -> bool:
        return (
            self.schema_version is not None
            and self.recorded_at is not None
            and self.known_at is not None
            and self.effective_at is not None
            and self.replay_limitation is None
        )


def envelope(payload: dict[str, Any], plan: JsonlWritePlan) -> dict[str, Any]:
    if METADATA_KEY in payload:
        raise JsonlContractError(f"domain payload cannot contain reserved field {METADATA_KEY}")
    return {
        **payload,
        METADATA_KEY: {
            "effective_at": plan.effective_at.isoformat(),
            "known_at": plan.known_at.isoformat() if plan.known_at is not None else None,
            "known_time_limitation": plan.known_time_limitation,
            "recorded_at": plan.recorded_at.isoformat(),
            "schema_version": plan.schema_version,
        },
    }


def read_records(path: Path, *, supported_schema: str) -> tuple[JsonlRecord, ...]:
    if not path.exists():
        return ()
    records = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            decoded = json.loads(line)
        except json.JSONDecodeError as exc:
            raise JsonlContractError(f"Malformed JSONL at {path}:{line_number}") from exc
        if not isinstance(decoded, dict):
            raise JsonlContractError(f"JSONL record must be an object at {path}:{line_number}")
        records.append(normalize_record(decoded, supported_schema=supported_schema))
    return tuple(records)


def normalize_record(payload: dict[str, Any], *, supported_schema: str) -> JsonlRecord:
    metadata = payload.get(METADATA_KEY)
    domain_payload = {key: value for key, value in payload.items() if key != METADATA_KEY}
    if metadata is None:
        return JsonlRecord(
            domain_payload,
            None,
            None,
            None,
            None,
            "legacy unversioned record has unknown recorded/known-time provenance",
        )
    if not isinstance(metadata, dict):
        raise JsonlContractError(f"{METADATA_KEY} must be an object")
    schema_version = metadata.get("schema_version")
    if schema_version != supported_schema:
        raise JsonlContractError(f"Unsupported JSONL schema version: {schema_version!r}")
    recorded_at = _required_datetime(metadata, "recorded_at")
    effective_at = _required_datetime(metadata, "effective_at")
    known_at = _optional_datetime(metadata, "known_at")
    limitation = metadata.get("known_time_limitation")
    if known_at is not None and known_at > recorded_at:
        raise JsonlContractError("known_at cannot be later than recorded_at")
    if known_at is None and not isinstance(limitation, str):
        raise JsonlContractError("unknown known_at requires known_time_limitation")
    if known_at is not None and limitation is not None:
        raise JsonlContractError("known_time_limitation must be absent when known_at is known")
    return JsonlRecord(domain_payload, str(schema_version), recorded_at, known_at, effective_at, limitation)


def strict_known(records: tuple[JsonlRecord, ...], *, as_of: datetime) -> tuple[JsonlRecord, ...]:
    if as_of.tzinfo is None:
        raise JsonlContractError("as_of must be timezone-aware")
    cutoff = as_of.astimezone(UTC)
    return tuple(
        record
        for record in records
        if record.strict_known_eligible
        and record.effective_at is not None
        and record.effective_at <= cutoff
        and record.recorded_at is not None
        and record.recorded_at <= cutoff
        and record.known_at is not None
        and record.known_at <= cutoff
    )


def _required_datetime(metadata: dict[str, Any], name: str) -> datetime:
    value = metadata.get(name)
    if not isinstance(value, str):
        raise JsonlContractError(f"{name} must be an ISO-8601 timestamp")
    return _parse_datetime(value, name)


def _optional_datetime(metadata: dict[str, Any], name: str) -> datetime | None:
    value = metadata.get(name)
    if value is None:
        return None
    if not isinstance(value, str):
        raise JsonlContractError(f"{name} must be an ISO-8601 timestamp or null")
    return _parse_datetime(value, name)


def _parse_datetime(value: str, name: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise JsonlContractError(f"Malformed {name}: {value!r}") from exc
    if parsed.tzinfo is None:
        raise JsonlContractError(f"{name} must be timezone-aware")
    return parsed.astimezone(UTC)
