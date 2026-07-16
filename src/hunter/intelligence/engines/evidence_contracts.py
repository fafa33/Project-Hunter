from __future__ import annotations

from typing import Any

from hunter.intelligence.evidence import Evidence


def evidence_contract_keys(evidence: Evidence) -> frozenset[str]:
    metadata = evidence.metadata.as_dict()
    raw_data = evidence.raw_data if isinstance(evidence.raw_data, dict) else {}
    values: set[object] = {
        metadata.get("evidence_contract"),
        metadata.get("evidence_type"),
        metadata.get("source_type"),
        metadata.get("type"),
        metadata.get("record_type"),
        raw_data.get("evidence_contract"),
        raw_data.get("evidence_type"),
        raw_data.get("source_type"),
        raw_data.get("type"),
        raw_data.get("record_type"),
        raw_data.get("metric"),
    }
    return frozenset(_normalize_contract_key(value) for value in values if _normalize_contract_key(value))


def evidence_satisfies_contract(evidence: Evidence, contract: str) -> bool:
    normalized_contract = _normalize_contract_key(contract)
    return bool(normalized_contract) and normalized_contract in evidence_contract_keys(evidence)


def _normalize_contract_key(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    return value.strip().lower()
