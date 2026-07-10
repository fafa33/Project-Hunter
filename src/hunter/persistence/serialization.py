from __future__ import annotations

import json
from dataclasses import fields
from datetime import UTC, datetime
from typing import Any

from hunter.execution.canonicalization import canonicalize
from hunter.persistence.exceptions import PersistenceSerializationError
from hunter.persistence.records import RECORD_TYPES, PersistenceRecord

DATETIME_FIELDS = {
    "created_at",
    "effective_at",
    "requested_at",
    "started_at",
    "finished_at",
    "collected_at",
    "timestamp",
    "generated_at",
}

TUPLE_FIELDS = {
    "evidence_ids",
    "observation_ids",
    "signal_ids",
    "insight_ids",
    "source_intelligence_ids",
    "source_run_ids",
    "effective_window",
    "target_refs",
    "evidence_references",
    "evidence_lineage_keys",
    "evidence_reliabilities",
    "evidence_freshness",
    "signal_categories",
    "signal_strengths",
    "signal_confidences",
    "signal_severities",
    "observation_descriptions",
    "insight_titles",
    "insight_explanations",
    "contributions",
    "canonical_evidence_groups",
    "unified_signals",
    "unified_observations",
    "unified_insights",
    "graph_nodes",
    "graph_edges",
    "record_ids",
    "engines",
}


def record_to_dict(record: PersistenceRecord) -> dict[str, Any]:
    payload = {
        "record_type": record.record_type,
        "fields": {field.name: _encode(getattr(record, field.name)) for field in fields(record)},
    }
    canonicalize(payload)
    return payload


def record_from_dict(payload: dict[str, Any]) -> PersistenceRecord:
    record_type = payload.get("record_type")
    if not isinstance(record_type, str):
        raise PersistenceSerializationError("Serialized record is missing record_type")
    record_class = RECORD_TYPES.get(record_type)
    if record_class is None:
        raise PersistenceSerializationError(f"Unknown persistence record type: {record_type}")
    raw_fields = payload.get("fields")
    if not isinstance(raw_fields, dict):
        raise PersistenceSerializationError("Serialized record is missing fields")
    values = {
        field.name: _decode(field.name, raw_fields[field.name])
        for field in fields(record_class)
        if field.name in raw_fields
    }
    return record_class(**values)


def record_to_json(record: PersistenceRecord) -> str:
    return json.dumps(record_to_dict(record), sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def record_from_json(payload: str) -> PersistenceRecord:
    try:
        decoded = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise PersistenceSerializationError("Invalid persistence record JSON") from exc
    if not isinstance(decoded, dict):
        raise PersistenceSerializationError("Persistence record JSON must decode to an object")
    return record_from_dict(decoded)


def canonical_record_bytes(record: PersistenceRecord) -> bytes:
    return canonicalize(record_to_dict(record))


def _encode(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.astimezone(UTC).isoformat().replace("+00:00", "Z")
    if isinstance(value, tuple):
        return [_encode(item) for item in value]
    if isinstance(value, list):
        return [_encode(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _encode(item) for key, item in value.items()}
    return value


def _decode(name: str, value: Any) -> Any:
    if name in DATETIME_FIELDS and isinstance(value, str):
        return _datetime(value)
    if name in TUPLE_FIELDS and isinstance(value, list):
        return tuple(value)
    return value


def _datetime(value: str) -> datetime:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)
    except ValueError as exc:
        raise PersistenceSerializationError(f"Invalid datetime value: {value}") from exc
