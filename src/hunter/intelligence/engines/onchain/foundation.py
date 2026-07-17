from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from hunter.execution.identity import fingerprint
from hunter.intelligence.engines.builder import HunterIntelligenceEngineBuilder
from hunter.intelligence.engines.contracts import (
    EngineContext,
    EngineDefinition,
    EngineMetadata,
    EvidenceBundle,
    Finding,
    FindingBatch,
    finding_identity,
)
from hunter.intelligence.engines.evidence_contracts import evidence_satisfies_contract
from hunter.intelligence.evidence import Evidence

ONCHAIN_ANALYSIS_TRACE_VERSION = "onchain-analysis-trace-v1"
ONCHAIN_EVIDENCE_CONTRACT = "onchain_evidence"
ONCHAIN_FINDING_TYPES = (
    "bridge_activity",
    "contract_interaction",
    "holder_distribution",
    "liquidity_activity",
    "onchain_observation",
    "staking_activity",
    "transaction_pattern",
    "treasury_activity",
    "validator_activity",
    "whale_activity",
)


@dataclass(frozen=True)
class OnchainEvidenceRecord:
    evidence: Evidence
    payload: dict[str, Any]


@dataclass(frozen=True)
class OnchainContext:
    context_type: str
    context_id: str
    records: tuple[OnchainEvidenceRecord, ...]


class OnchainFoundationIntelligenceEngine:
    def __init__(self, definition: EngineDefinition | None = None) -> None:
        self._definition = definition or onchain_engine_definition()

    @property
    def definition(self) -> EngineDefinition:
        return self._definition

    def analyze(self, evidence: EvidenceBundle, context: EngineContext) -> FindingBatch:
        records = _onchain_records(evidence)
        generators = (
            _holder_distribution,
            _whale_activity,
            _treasury_activity,
            _bridge_activity,
            _liquidity_activity,
            _staking_activity,
            _validator_activity,
            _transaction_pattern,
            _contract_interaction,
            _onchain_observation,
        )
        findings = tuple(
            finding for generator in generators for finding in generator(self.definition, evidence, context, records)
        )
        return FindingBatch(
            engine_id=self.definition.metadata.id,
            engine_version=self.definition.metadata.version,
            candidate_id=evidence.candidate_id,
            as_of=context.as_of,
            evaluated_at=context.evaluated_at,
            findings=findings,
        )


def onchain_engine_definition() -> EngineDefinition:
    metadata = EngineMetadata(
        id="onchain-intelligence-foundation",
        name="On-chain Intelligence Foundation",
        category="onchain",
        version="1.0.0",
        priority=50,
        required_inputs=(ONCHAIN_EVIDENCE_CONTRACT,),
        produced_outputs=("onchain_findings",),
        capabilities=("analyze", "onchain-intelligence", "finding-generation"),
    )
    return (
        HunterIntelligenceEngineBuilder(metadata)
        .with_evidence_contracts(ONCHAIN_EVIDENCE_CONTRACT)
        .with_supported_evidence_types(
            "onchain_bridge_observation",
            "onchain_contract_interaction",
            "onchain_evidence_claim",
            "onchain_holder_distribution",
            "onchain_liquidity_observation",
            "onchain_staking_observation",
            "onchain_transaction_pattern",
            "onchain_treasury_observation",
            "onchain_validator_observation",
            "onchain_whale_observation",
        )
        .with_analysis_stages(
            "normalize-evidence",
            "isolate-onchain-contexts",
            "aggregate-same-context-observations",
            "derive-findings",
            "explain-findings",
        )
        .with_finding_types(*ONCHAIN_FINDING_TYPES)
        .with_output_schema(
            schema_version="intelligence-finding-v1",
            analysis_trace_version=ONCHAIN_ANALYSIS_TRACE_VERSION,
        )
        .build()
    )


def _onchain_records(bundle: EvidenceBundle) -> tuple[OnchainEvidenceRecord, ...]:
    records = []
    for evidence in bundle.evidence:
        if not evidence_satisfies_contract(evidence, ONCHAIN_EVIDENCE_CONTRACT):
            continue
        payload = _payload(evidence)
        if not payload:
            continue
        records.append(OnchainEvidenceRecord(evidence=evidence, payload=payload))
    return tuple(sorted(records, key=lambda record: (_record_sort_key(record), record.evidence.id)))


def _holder_distribution(
    definition: EngineDefinition,
    bundle: EvidenceBundle,
    context: EngineContext,
    records: tuple[OnchainEvidenceRecord, ...],
) -> tuple[Finding, ...]:
    contexts = _contexts(records, context_type="holder", keys=_HOLDER_CONTEXT_KEYS, required_keys=_HOLDER_EVIDENCE_KEYS)
    return _context_findings(
        definition,
        bundle,
        context,
        "holder_distribution",
        contexts,
        _HOLDER_EVIDENCE_KEYS,
        confidence_basis="holder distribution is supported by persisted holder distribution evidence",
    )


def _whale_activity(
    definition: EngineDefinition,
    bundle: EvidenceBundle,
    context: EngineContext,
    records: tuple[OnchainEvidenceRecord, ...],
) -> tuple[Finding, ...]:
    contexts = _contexts(records, context_type="wallet", keys=_WALLET_CONTEXT_KEYS, required_keys=_WHALE_EVIDENCE_KEYS)
    return _context_findings(
        definition,
        bundle,
        context,
        "whale_activity",
        contexts,
        _WHALE_EVIDENCE_KEYS,
        confidence_basis="whale activity is supported by persisted whale activity observations",
    )


def _treasury_activity(
    definition: EngineDefinition,
    bundle: EvidenceBundle,
    context: EngineContext,
    records: tuple[OnchainEvidenceRecord, ...],
) -> tuple[Finding, ...]:
    contexts = _contexts(
        records,
        context_type="treasury",
        keys=_TREASURY_CONTEXT_KEYS,
        required_keys=_TREASURY_EVIDENCE_KEYS,
    )
    return _context_findings(
        definition,
        bundle,
        context,
        "treasury_activity",
        contexts,
        _TREASURY_EVIDENCE_KEYS,
        confidence_basis="treasury activity is supported by persisted treasury activity evidence",
    )


def _bridge_activity(
    definition: EngineDefinition,
    bundle: EvidenceBundle,
    context: EngineContext,
    records: tuple[OnchainEvidenceRecord, ...],
) -> tuple[Finding, ...]:
    contexts = _contexts(records, context_type="bridge", keys=_BRIDGE_CONTEXT_KEYS, required_keys=_BRIDGE_EVIDENCE_KEYS)
    return _context_findings(
        definition,
        bundle,
        context,
        "bridge_activity",
        contexts,
        _BRIDGE_EVIDENCE_KEYS,
        confidence_basis="bridge activity is supported by persisted bridge activity evidence",
    )


def _liquidity_activity(
    definition: EngineDefinition,
    bundle: EvidenceBundle,
    context: EngineContext,
    records: tuple[OnchainEvidenceRecord, ...],
) -> tuple[Finding, ...]:
    contexts = _contexts(records, context_type="pool", keys=_POOL_CONTEXT_KEYS, required_keys=_LIQUIDITY_EVIDENCE_KEYS)
    return _context_findings(
        definition,
        bundle,
        context,
        "liquidity_activity",
        contexts,
        _LIQUIDITY_EVIDENCE_KEYS,
        confidence_basis="liquidity activity is supported by persisted liquidity activity evidence",
    )


def _staking_activity(
    definition: EngineDefinition,
    bundle: EvidenceBundle,
    context: EngineContext,
    records: tuple[OnchainEvidenceRecord, ...],
) -> tuple[Finding, ...]:
    contexts = _contexts(
        records,
        context_type="staking_position",
        keys=_STAKING_CONTEXT_KEYS,
        required_keys=_STAKING_EVIDENCE_KEYS,
    )
    return _context_findings(
        definition,
        bundle,
        context,
        "staking_activity",
        contexts,
        _STAKING_EVIDENCE_KEYS,
        confidence_basis="staking activity is supported by persisted staking activity evidence",
    )


def _validator_activity(
    definition: EngineDefinition,
    bundle: EvidenceBundle,
    context: EngineContext,
    records: tuple[OnchainEvidenceRecord, ...],
) -> tuple[Finding, ...]:
    contexts = _contexts(
        records,
        context_type="validator",
        keys=_VALIDATOR_CONTEXT_KEYS,
        required_keys=_VALIDATOR_EVIDENCE_KEYS,
    )
    return _context_findings(
        definition,
        bundle,
        context,
        "validator_activity",
        contexts,
        _VALIDATOR_EVIDENCE_KEYS,
        confidence_basis="validator activity is supported by persisted validator activity evidence",
    )


def _transaction_pattern(
    definition: EngineDefinition,
    bundle: EvidenceBundle,
    context: EngineContext,
    records: tuple[OnchainEvidenceRecord, ...],
) -> tuple[Finding, ...]:
    contexts = _contexts(
        records,
        context_type="transaction",
        keys=_TRANSACTION_CONTEXT_KEYS,
        required_keys=_TRANSACTION_EVIDENCE_KEYS,
    )
    return _context_findings(
        definition,
        bundle,
        context,
        "transaction_pattern",
        contexts,
        _TRANSACTION_EVIDENCE_KEYS,
        confidence_basis="transaction pattern is supported by persisted transaction pattern evidence",
    )


def _contract_interaction(
    definition: EngineDefinition,
    bundle: EvidenceBundle,
    context: EngineContext,
    records: tuple[OnchainEvidenceRecord, ...],
) -> tuple[Finding, ...]:
    contexts = _contexts(
        records,
        context_type="contract",
        keys=_CONTRACT_CONTEXT_KEYS,
        required_keys=_CONTRACT_EVIDENCE_KEYS,
    )
    return _context_findings(
        definition,
        bundle,
        context,
        "contract_interaction",
        contexts,
        _CONTRACT_EVIDENCE_KEYS,
        confidence_basis="contract interaction is supported by persisted contract interaction evidence",
    )


def _onchain_observation(
    definition: EngineDefinition,
    bundle: EvidenceBundle,
    context: EngineContext,
    records: tuple[OnchainEvidenceRecord, ...],
) -> tuple[Finding, ...]:
    contexts = _observation_contexts(records)
    return _context_findings(
        definition,
        bundle,
        context,
        "onchain_observation",
        contexts,
        _ONCHAIN_OBSERVATION_EVIDENCE_KEYS,
        confidence_basis="onchain observation is supported by persisted on-chain evidence",
    )


def _context_findings(
    definition: EngineDefinition,
    bundle: EvidenceBundle,
    engine_context: EngineContext,
    finding_type: str,
    contexts: tuple[OnchainContext, ...],
    fields: tuple[str, ...],
    *,
    confidence_basis: str,
) -> tuple[Finding, ...]:
    return tuple(
        _context_finding(
            definition,
            bundle,
            engine_context,
            finding_type,
            onchain_context,
            _observed_names(onchain_context.records, fields),
            confidence_basis=confidence_basis,
        )
        for onchain_context in contexts
    )


def _context_finding(
    definition: EngineDefinition,
    bundle: EvidenceBundle,
    engine_context: EngineContext,
    finding_type: str,
    onchain_context: OnchainContext,
    fields: tuple[str, ...],
    *,
    confidence_basis: str,
) -> Finding:
    explanation = (
        f"{finding_type} is descriptively evidenced within {onchain_context.context_type} "
        f"context {onchain_context.context_id} by persisted on-chain fields: {', '.join(fields)}; "
        f"evidence content: {_content_fingerprint(onchain_context.records)}."
    )
    return _finding(
        definition,
        bundle,
        engine_context,
        finding_type,
        explanation,
        onchain_context.records,
        confidence_basis=confidence_basis,
    )


def _finding(
    definition: EngineDefinition,
    bundle: EvidenceBundle,
    context: EngineContext,
    finding_type: str,
    explanation: str,
    records: tuple[OnchainEvidenceRecord, ...],
    *,
    confidence_basis: str,
) -> Finding:
    supporting = tuple(sorted({record.evidence.id for record in records}))
    lineage = tuple(sorted({lineage for record in records for lineage in _lineage(record)}))
    conflicts = tuple(sorted({conflict for record in records for conflict in _conflicts(record)}))
    confidence = _confidence(records)
    finding_id = finding_identity(
        candidate_id=bundle.candidate_id,
        engine_id=definition.metadata.id,
        engine_version=definition.metadata.version,
        finding_type=finding_type,
        explanation=explanation,
        supporting_evidence_ids=supporting,
        evidence_lineage=lineage,
        deterministic_confidence=confidence,
        confidence_basis=confidence_basis,
        evaluated_at=context.evaluated_at,
        as_of=context.as_of,
        analysis_trace_version=definition.analysis_trace_version,
        missing_evidence=bundle.missing_evidence,
        conflicts=conflicts,
        schema_version=definition.output_schema_version,
    )
    return Finding(
        finding_id=finding_id,
        candidate_id=bundle.candidate_id,
        engine_id=definition.metadata.id,
        engine_version=definition.metadata.version,
        finding_type=finding_type,
        explanation=explanation,
        supporting_evidence_ids=supporting,
        evidence_lineage=lineage,
        deterministic_confidence=confidence,
        confidence_basis=confidence_basis,
        evaluated_at=context.evaluated_at,
        as_of=context.as_of,
        analysis_trace_version=definition.analysis_trace_version,
        missing_evidence=bundle.missing_evidence,
        conflicts=conflicts,
        schema_version=definition.output_schema_version,
    )


def _contexts(
    records: tuple[OnchainEvidenceRecord, ...],
    *,
    context_type: str,
    keys: tuple[str, ...],
    required_keys: tuple[str, ...],
) -> tuple[OnchainContext, ...]:
    grouped: dict[str, list[OnchainEvidenceRecord]] = {}
    for record in records:
        if not _has_any(record, required_keys):
            continue
        context_id = _context_id(record, context_type=context_type, keys=keys)
        if not context_id:
            continue
        grouped.setdefault(context_id, []).append(record)
    return _sorted_contexts(context_type, grouped)


def _observation_contexts(records: tuple[OnchainEvidenceRecord, ...]) -> tuple[OnchainContext, ...]:
    grouped: dict[tuple[str, str], list[OnchainEvidenceRecord]] = {}
    for record in records:
        if not _has_any(record, _ONCHAIN_OBSERVATION_EVIDENCE_KEYS):
            continue
        context = _primary_context(record)
        if context is None:
            continue
        grouped.setdefault((context.context_type, context.context_id), []).append(record)
    contexts = [
        OnchainContext(
            context_type=context_type,
            context_id=context_id,
            records=tuple(sorted(group_records, key=lambda item: (_record_sort_key(item), item.evidence.id))),
        )
        for (context_type, context_id), group_records in grouped.items()
    ]
    return tuple(sorted(contexts, key=lambda item: (item.context_type, item.context_id)))


def _primary_context(record: OnchainEvidenceRecord) -> OnchainContext | None:
    for context_type, keys in _PRIMARY_CONTEXT_PRECEDENCE:
        context_id = _context_id(record, context_type=context_type, keys=keys)
        if context_id:
            return OnchainContext(context_type=context_type, context_id=context_id, records=(record,))
    return None


def _sorted_contexts(
    context_type: str,
    grouped: dict[str, list[OnchainEvidenceRecord]],
) -> tuple[OnchainContext, ...]:
    contexts = [
        OnchainContext(
            context_type=context_type,
            context_id=context_id,
            records=tuple(sorted(records, key=lambda item: (_record_sort_key(item), item.evidence.id))),
        )
        for context_id, records in grouped.items()
    ]
    return tuple(sorted(contexts, key=lambda item: item.context_id))


def _payload(evidence: Evidence) -> dict[str, Any]:
    raw = evidence.raw_data
    if not isinstance(raw, dict):
        return {}
    return {str(key): raw[key] for key in sorted(raw)}


def _record_sort_key(record: OnchainEvidenceRecord) -> str:
    return "|".join(
        (
            _first_text(record, _WALLET_CONTEXT_KEYS),
            _first_text(record, _HOLDER_CONTEXT_KEYS),
            _first_text(record, _TREASURY_CONTEXT_KEYS),
            _first_text(record, _BRIDGE_CONTEXT_KEYS),
            _first_text(record, _VALIDATOR_CONTEXT_KEYS),
            _first_text(record, _STAKING_CONTEXT_KEYS),
            _first_text(record, _POOL_CONTEXT_KEYS),
            _first_text(record, _TRANSACTION_CONTEXT_KEYS),
            _first_text(record, _CONTRACT_CONTEXT_KEYS),
            _first_text(record, _TOKEN_CONTEXT_KEYS),
            _text(record.payload.get("claim_id")),
            record.evidence.id,
        )
    )


def _first_text(record: OnchainEvidenceRecord, keys: tuple[str, ...]) -> str:
    for key in keys:
        value = _text(record.payload.get(key))
        if value:
            return value
    return ""


def _context_id(record: OnchainEvidenceRecord, *, context_type: str, keys: tuple[str, ...]) -> str:
    if context_type in _SYNONYM_CONTEXT_KEY_GROUPS:
        for group in _SYNONYM_CONTEXT_KEY_GROUPS[context_type]:
            values = {
                _normalized_identifier(record.payload.get(key))
                for key in group
                if _normalized_identifier(record.payload.get(key))
            }
            if len(values) > 1:
                return ""
            if len(values) == 1:
                return next(iter(values))
    return _first_normalized_identifier(record, keys)


def _first_normalized_identifier(record: OnchainEvidenceRecord, keys: tuple[str, ...]) -> str:
    for key in keys:
        value = _normalized_identifier(record.payload.get(key))
        if value:
            return value
    return ""


def _normalized_identifier(value: object) -> str:
    if not isinstance(value, str):
        return ""
    normalized = value.strip().lower()
    if not normalized:
        return ""
    if any(char.isspace() for char in normalized):
        return ""
    return normalized


def _has_any(record: OnchainEvidenceRecord, keys: tuple[str, ...]) -> bool:
    return any(_present(record.payload.get(key)) for key in keys)


def _observed_names(
    records: tuple[OnchainEvidenceRecord, ...],
    keys: tuple[str, ...],
) -> tuple[str, ...]:
    names = set()
    for record in records:
        for key in keys:
            if _present(record.payload.get(key)):
                names.add(key)
    return tuple(sorted(names))


def _lineage(record: OnchainEvidenceRecord) -> tuple[str, ...]:
    values = (record.evidence.reference,)
    return tuple(sorted({value.strip() for value in values if value.strip()}))


def _conflicts(record: OnchainEvidenceRecord) -> tuple[str, ...]:
    values = []
    for key in ("conflict_id", "conflict_state"):
        value = record.payload.get(key)
        if isinstance(value, str) and value.strip():
            values.append(value.strip())
    for key in ("conflicts", "conflict_ids"):
        value = record.payload.get(key)
        if isinstance(value, str) and value.strip():
            values.append(value.strip())
        elif isinstance(value, tuple | list):
            values.extend(str(item).strip() for item in value if str(item).strip())
    return tuple(sorted(set(values)))


def _content_fingerprint(records: tuple[OnchainEvidenceRecord, ...]) -> str:
    return fingerprint(
        "onchain-finding-evidence",
        tuple((record.evidence.id, record.payload) for record in records),
    )


def _confidence(records: tuple[OnchainEvidenceRecord, ...]) -> float:
    if not records:
        return 0.0
    total = sum(min(max(record.evidence.reliability, 0.0), 1.0) for record in records)
    return round(total / len(records), 4)


def _present(value: object) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, tuple | list | dict | set):
        return bool(value)
    return True


def _text(value: object) -> str:
    if not isinstance(value, str):
        return ""
    return value.strip()


_WALLET_CONTEXT_KEYS = ("wallet_address", "wallet_id", "address")
_HOLDER_CONTEXT_KEYS = ("holder_id", "holder_address", "token_address", "token_id")
_TREASURY_CONTEXT_KEYS = ("treasury_id", "treasury_address")
_BRIDGE_CONTEXT_KEYS = ("bridge_id", "bridge_address", "bridge_name")
_VALIDATOR_CONTEXT_KEYS = ("validator_id", "validator_address")
_STAKING_CONTEXT_KEYS = ("staking_position_id", "staking_pool_id", "validator_id")
_POOL_CONTEXT_KEYS = ("pool_id", "pool_address", "liquidity_pool_id")
_TRANSACTION_CONTEXT_KEYS = ("transaction_hash", "tx_hash", "transaction_id")
_CONTRACT_CONTEXT_KEYS = ("contract_address", "contract_id")
_TOKEN_CONTEXT_KEYS = ("token_address", "token_id")

_HOLDER_KEYS = (
    "holder_id",
    "holder_address",
    "holder_count",
    "holder_distribution",
    "top_holder_share",
    "top_10_share",
    "top_100_share",
    "gini",
)
_HOLDER_EVIDENCE_KEYS = tuple(key for key in _HOLDER_KEYS if key not in {"holder_id", "holder_address"})
_WHALE_KEYS = (
    "wallet_address",
    "wallet_id",
    "whale_activity",
    "whale_transfer_count",
    "large_transfer_count",
    "large_transfer_value",
)
_WHALE_EVIDENCE_KEYS = tuple(key for key in _WHALE_KEYS if key not in {"wallet_address", "wallet_id"})
_TREASURY_KEYS = (
    "treasury_id",
    "treasury_address",
    "treasury_activity",
    "treasury_inflow",
    "treasury_outflow",
    "treasury_balance",
    "treasury_label_quality",
)
_TREASURY_EVIDENCE_KEYS = tuple(key for key in _TREASURY_KEYS if key not in {"treasury_id", "treasury_address"})
_BRIDGE_KEYS = (
    "bridge_id",
    "bridge_address",
    "bridge_name",
    "bridge_activity",
    "bridge_inflow",
    "bridge_outflow",
    "source_chain",
    "target_chain",
    "bridge_label_quality",
)
_BRIDGE_EVIDENCE_KEYS = tuple(key for key in _BRIDGE_KEYS if key not in {"bridge_id", "bridge_address", "bridge_name"})
_LIQUIDITY_KEYS = (
    "pool_id",
    "pool_address",
    "liquidity_pool_id",
    "liquidity_activity",
    "liquidity_added",
    "liquidity_removed",
    "reserve0",
    "reserve1",
    "tvl",
)
_LIQUIDITY_EVIDENCE_KEYS = tuple(
    key for key in _LIQUIDITY_KEYS if key not in {"pool_id", "pool_address", "liquidity_pool_id"}
)
_STAKING_KEYS = (
    "staking_position_id",
    "staking_pool_id",
    "validator_id",
    "staking_activity",
    "staked_amount",
    "staked_inflow",
    "staked_outflow",
    "unstaked",
)
_STAKING_EVIDENCE_KEYS = tuple(
    key for key in _STAKING_KEYS if key not in {"staking_position_id", "staking_pool_id", "validator_id"}
)
_VALIDATOR_KEYS = (
    "validator_id",
    "validator_address",
    "validator_activity",
    "validator_count",
    "top_validator_share",
    "staker_count",
    "staker_concentration",
)
_VALIDATOR_EVIDENCE_KEYS = tuple(key for key in _VALIDATOR_KEYS if key not in {"validator_id", "validator_address"})
_TRANSACTION_KEYS = (
    "transaction_hash",
    "tx_hash",
    "transaction_id",
    "transaction_count",
    "transaction_pattern",
    "transfer_value",
    "adjusted_transfer_value",
    "repeated_pattern_ratio",
    "low_value_ratio",
)
_TRANSACTION_EVIDENCE_KEYS = tuple(
    key for key in _TRANSACTION_KEYS if key not in {"transaction_hash", "tx_hash", "transaction_id"}
)
_CONTRACT_KEYS = (
    "contract_address",
    "contract_id",
    "contract_interaction",
    "interaction_count",
    "interactions",
    "unique_callers",
    "method_id",
    "event_signature",
)
_CONTRACT_EVIDENCE_KEYS = tuple(key for key in _CONTRACT_KEYS if key not in {"contract_address", "contract_id"})
_TOKEN_KEYS = (
    "token_address",
    "token_id",
    "token_balance",
    "token_transfer_count",
    "supply_observation",
)
_ONCHAIN_OBSERVATION_KEYS = tuple(
    sorted(
        {
            *_HOLDER_KEYS,
            *_WHALE_KEYS,
            *_TREASURY_KEYS,
            *_BRIDGE_KEYS,
            *_LIQUIDITY_KEYS,
            *_STAKING_KEYS,
            *_VALIDATOR_KEYS,
            *_TRANSACTION_KEYS,
            *_CONTRACT_KEYS,
            *_TOKEN_KEYS,
            "onchain_claim",
            "onchain_observation",
        }
    )
)
_ONCHAIN_CONTEXT_IDENTIFIER_KEYS = frozenset(
    {
        *_WALLET_CONTEXT_KEYS,
        *_HOLDER_CONTEXT_KEYS,
        *_TREASURY_CONTEXT_KEYS,
        *_BRIDGE_CONTEXT_KEYS,
        *_VALIDATOR_CONTEXT_KEYS,
        *_STAKING_CONTEXT_KEYS,
        *_POOL_CONTEXT_KEYS,
        *_TRANSACTION_CONTEXT_KEYS,
        *_CONTRACT_CONTEXT_KEYS,
        *_TOKEN_CONTEXT_KEYS,
    }
)
_ONCHAIN_OBSERVATION_EVIDENCE_KEYS = tuple(
    key for key in _ONCHAIN_OBSERVATION_KEYS if key not in _ONCHAIN_CONTEXT_IDENTIFIER_KEYS
)
_PRIMARY_CONTEXT_PRECEDENCE = (
    ("transaction", _TRANSACTION_CONTEXT_KEYS),
    ("contract", _CONTRACT_CONTEXT_KEYS),
    ("pool", _POOL_CONTEXT_KEYS),
    ("staking_position", _STAKING_CONTEXT_KEYS),
    ("validator", _VALIDATOR_CONTEXT_KEYS),
    ("bridge", _BRIDGE_CONTEXT_KEYS),
    ("treasury", _TREASURY_CONTEXT_KEYS),
    ("holder", _HOLDER_CONTEXT_KEYS),
    ("wallet", _WALLET_CONTEXT_KEYS),
    ("token", _TOKEN_CONTEXT_KEYS),
)
_SYNONYM_CONTEXT_KEY_GROUPS = {
    "wallet": (("wallet_address", "wallet_id", "address"),),
    "holder": (("holder_id", "holder_address"), ("token_address", "token_id")),
    "treasury": (("treasury_id", "treasury_address"),),
    "bridge": (("bridge_id", "bridge_address", "bridge_name"),),
    "validator": (("validator_id", "validator_address"),),
    "staking_position": (("staking_position_id", "staking_pool_id"),),
    "pool": (("pool_id", "pool_address", "liquidity_pool_id"),),
    "transaction": (("transaction_hash", "tx_hash", "transaction_id"),),
    "contract": (("contract_address", "contract_id"),),
    "token": (("token_address", "token_id"),),
}
