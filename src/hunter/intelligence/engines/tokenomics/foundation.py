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

TOKENOMICS_ANALYSIS_TRACE_VERSION = "tokenomics-analysis-trace-v1"
TOKENOMICS_FINDING_TYPES = (
    "allocation_structure",
    "burn_activity",
    "emission_profile",
    "issuance_schedule",
    "protocol_distribution",
    "supply_structure",
    "tokenomics_observation",
    "treasury_distribution",
    "unlock_schedule",
    "vesting_schedule",
)
TOKENOMICS_EVIDENCE_CONTRACT = "tokenomics_evidence"


@dataclass(frozen=True)
class TokenomicsEvidenceRecord:
    evidence: Evidence
    payload: dict[str, Any]


class TokenomicsFoundationIntelligenceEngine:
    def __init__(self, definition: EngineDefinition | None = None) -> None:
        self._definition = definition or tokenomics_engine_definition()

    @property
    def definition(self) -> EngineDefinition:
        return self._definition

    def analyze(self, evidence: EvidenceBundle, context: EngineContext) -> FindingBatch:
        records = _tokenomics_records(evidence)
        generators = (
            _supply_structure,
            _issuance_schedule,
            _unlock_schedule,
            _vesting_schedule,
            _allocation_structure,
            _treasury_distribution,
            _emission_profile,
            _burn_activity,
            _protocol_distribution,
            _tokenomics_observation,
        )
        findings = tuple(
            finding
            for generator in generators
            for finding in (generator(self.definition, evidence, context, records),)
            if finding is not None
        )
        return FindingBatch(
            engine_id=self.definition.metadata.id,
            engine_version=self.definition.metadata.version,
            candidate_id=evidence.candidate_id,
            as_of=context.as_of,
            evaluated_at=context.evaluated_at,
            findings=findings,
        )


def tokenomics_engine_definition() -> EngineDefinition:
    metadata = EngineMetadata(
        id="tokenomics-intelligence-foundation",
        name="Tokenomics Intelligence Foundation",
        category="tokenomics",
        version="1.0.0",
        priority=20,
        required_inputs=(TOKENOMICS_EVIDENCE_CONTRACT,),
        produced_outputs=("tokenomics_findings",),
        capabilities=("analyze", "tokenomics-intelligence", "finding-generation"),
    )
    return (
        HunterIntelligenceEngineBuilder(metadata)
        .with_evidence_contracts(TOKENOMICS_EVIDENCE_CONTRACT)
        .with_supported_evidence_types(
            "tokenomics_allocation_definition",
            "tokenomics_burn_event",
            "tokenomics_evidence_claim",
            "tokenomics_fee_revenue_metric",
            "tokenomics_protocol_metric",
            "tokenomics_supply_definition",
            "tokenomics_supply_observation",
            "tokenomics_tvl_metric",
            "tokenomics_unlock_event",
            "tokenomics_vesting_schedule",
            "tokenomics_vesting_segment",
        )
        .with_analysis_stages("normalize-evidence", "derive-findings", "explain-findings")
        .with_finding_types(*TOKENOMICS_FINDING_TYPES)
        .with_output_schema(
            schema_version="intelligence-finding-v1",
            analysis_trace_version=TOKENOMICS_ANALYSIS_TRACE_VERSION,
        )
        .build()
    )


def _tokenomics_records(bundle: EvidenceBundle) -> tuple[TokenomicsEvidenceRecord, ...]:
    records = []
    for evidence in bundle.evidence:
        if not evidence_satisfies_contract(evidence, TOKENOMICS_EVIDENCE_CONTRACT):
            continue
        payload = _payload(evidence)
        if not payload:
            continue
        records.append(TokenomicsEvidenceRecord(evidence=evidence, payload=payload))
    return tuple(sorted(records, key=lambda record: (_record_sort_key(record), record.evidence.id)))


def _supply_structure(
    definition: EngineDefinition,
    bundle: EvidenceBundle,
    context: EngineContext,
    records: tuple[TokenomicsEvidenceRecord, ...],
) -> Finding | None:
    relevant = tuple(
        record for record in records if _has_any(record, _SUPPLY_KEYS) or _supply_metric(record) in _SUPPLY_METRICS
    )
    if not relevant:
        return None
    metrics = _observed_names(relevant, _SUPPLY_KEYS, metric_values=_SUPPLY_METRICS)
    explanation = (
        f"Supply structure is evidenced by persisted tokenomics supply fields: {', '.join(metrics)}; "
        f"evidence content: {_content_fingerprint(relevant)}."
    )
    return _finding(
        definition,
        bundle,
        context,
        "supply_structure",
        explanation,
        relevant,
        confidence_basis="supply structure is supported by persisted supply observations or supply definitions",
    )


def _issuance_schedule(
    definition: EngineDefinition,
    bundle: EvidenceBundle,
    context: EngineContext,
    records: tuple[TokenomicsEvidenceRecord, ...],
) -> Finding | None:
    relevant = tuple(record for record in records if _has_any(record, _ISSUANCE_KEYS))
    if not relevant:
        return None
    fields = _observed_names(relevant, _ISSUANCE_KEYS)
    explanation = (
        f"Issuance schedule is evidenced by persisted tokenomics fields: {', '.join(fields)}; "
        f"evidence content: {_content_fingerprint(relevant)}."
    )
    return _finding(
        definition,
        bundle,
        context,
        "issuance_schedule",
        explanation,
        relevant,
        confidence_basis="issuance schedule is supported by persisted issuance, inflation, or supply schedule evidence",
    )


def _unlock_schedule(
    definition: EngineDefinition,
    bundle: EvidenceBundle,
    context: EngineContext,
    records: tuple[TokenomicsEvidenceRecord, ...],
) -> Finding | None:
    relevant = tuple(record for record in records if _has_any(record, _UNLOCK_KEYS))
    if not relevant:
        return None
    fields = _observed_names(relevant, _UNLOCK_KEYS)
    explanation = (
        f"Unlock schedule is evidenced by persisted unlock fields: {', '.join(fields)}; "
        f"evidence content: {_content_fingerprint(relevant)}."
    )
    return _finding(
        definition,
        bundle,
        context,
        "unlock_schedule",
        explanation,
        relevant,
        confidence_basis="unlock schedule is supported by persisted unlock event or schedule evidence",
    )


def _vesting_schedule(
    definition: EngineDefinition,
    bundle: EvidenceBundle,
    context: EngineContext,
    records: tuple[TokenomicsEvidenceRecord, ...],
) -> Finding | None:
    relevant = tuple(record for record in records if _has_any(record, _VESTING_KEYS))
    if not relevant:
        return None
    fields = _observed_names(relevant, _VESTING_KEYS)
    explanation = (
        f"Vesting schedule is evidenced by persisted vesting fields: {', '.join(fields)}; "
        f"evidence content: {_content_fingerprint(relevant)}."
    )
    return _finding(
        definition,
        bundle,
        context,
        "vesting_schedule",
        explanation,
        relevant,
        confidence_basis="vesting schedule is supported by persisted vesting schedule or vesting segment evidence",
    )


def _allocation_structure(
    definition: EngineDefinition,
    bundle: EvidenceBundle,
    context: EngineContext,
    records: tuple[TokenomicsEvidenceRecord, ...],
) -> Finding | None:
    relevant = tuple(
        record
        for record in records
        if _has_any(record, _ALLOCATION_KEYS)
        and _category(record) != "treasury"
        and not _has_balance_only_ownership_attribution(record)
    )
    if not relevant:
        return None
    categories = _categories(relevant)
    explanation = (
        f"Allocation structure is evidenced by persisted allocation categories: {', '.join(categories) or 'declared'}; "
        f"evidence content: {_content_fingerprint(relevant)}."
    )
    return _finding(
        definition,
        bundle,
        context,
        "allocation_structure",
        explanation,
        relevant,
        confidence_basis="allocation structure is supported by persisted allocation definitions or allocation claims",
    )


def _treasury_distribution(
    definition: EngineDefinition,
    bundle: EvidenceBundle,
    context: EngineContext,
    records: tuple[TokenomicsEvidenceRecord, ...],
) -> Finding | None:
    relevant = tuple(
        record
        for record in records
        if (_has_any(record, _TREASURY_KEYS) or _category(record) == "treasury")
        and not _has_balance_only_ownership_attribution(record)
    )
    if not relevant:
        return None
    fields = _observed_names(relevant, _TREASURY_KEYS, categories=("treasury",))
    explanation = (
        f"Treasury distribution is evidenced by persisted treasury fields: {', '.join(fields)}; "
        f"evidence content: {_content_fingerprint(relevant)}."
    )
    return _finding(
        definition,
        bundle,
        context,
        "treasury_distribution",
        explanation,
        relevant,
        confidence_basis="treasury distribution is supported by persisted treasury allocation or treasury supply evidence",
    )


def _emission_profile(
    definition: EngineDefinition,
    bundle: EvidenceBundle,
    context: EngineContext,
    records: tuple[TokenomicsEvidenceRecord, ...],
) -> Finding | None:
    relevant = tuple(record for record in records if _has_any(record, _EMISSION_KEYS))
    if not relevant:
        return None
    fields = _observed_names(relevant, _EMISSION_KEYS)
    explanation = (
        f"Emission profile is evidenced by persisted emission fields: {', '.join(fields)}; "
        f"evidence content: {_content_fingerprint(relevant)}."
    )
    return _finding(
        definition,
        bundle,
        context,
        "emission_profile",
        explanation,
        relevant,
        confidence_basis="emission profile is supported by persisted emissions, rewards, or inflation evidence",
    )


def _burn_activity(
    definition: EngineDefinition,
    bundle: EvidenceBundle,
    context: EngineContext,
    records: tuple[TokenomicsEvidenceRecord, ...],
) -> Finding | None:
    relevant = tuple(
        record for record in records if _has_any(record, _BURN_KEYS) or _supply_metric(record) == "burned_supply"
    )
    if not relevant:
        return None
    fields = _observed_names(relevant, _BURN_KEYS, metric_values=("burned_supply",))
    explanation = (
        f"Burn activity is evidenced by persisted burn fields: {', '.join(fields)}; "
        f"evidence content: {_content_fingerprint(relevant)}."
    )
    return _finding(
        definition,
        bundle,
        context,
        "burn_activity",
        explanation,
        relevant,
        confidence_basis="burn activity is supported by persisted burn event or burned supply evidence",
    )


def _protocol_distribution(
    definition: EngineDefinition,
    bundle: EvidenceBundle,
    context: EngineContext,
    records: tuple[TokenomicsEvidenceRecord, ...],
) -> Finding | None:
    relevant = tuple(record for record in records if _has_any(record, _PROTOCOL_KEYS))
    if not relevant:
        return None
    fields = _observed_names(relevant, _PROTOCOL_KEYS)
    explanation = (
        f"Protocol distribution is descriptively evidenced by persisted protocol fields: {', '.join(fields)}; "
        f"evidence content: {_content_fingerprint(relevant)}."
    )
    return _finding(
        definition,
        bundle,
        context,
        "protocol_distribution",
        explanation,
        relevant,
        confidence_basis="protocol distribution is supported by persisted protocol fee, revenue, or TVL evidence",
    )


def _tokenomics_observation(
    definition: EngineDefinition,
    bundle: EvidenceBundle,
    context: EngineContext,
    records: tuple[TokenomicsEvidenceRecord, ...],
) -> Finding | None:
    relevant = tuple(record for record in records if _has_any(record, _TOKENOMICS_OBSERVATION_KEYS))
    if not relevant:
        return None
    evidence_types = _observed_names(relevant, _TOKENOMICS_OBSERVATION_KEYS)
    explanation = (
        f"Tokenomics observation is evidenced by persisted tokenomics fields: {', '.join(evidence_types)}; "
        f"evidence content: {_content_fingerprint(relevant)}."
    )
    return _finding(
        definition,
        bundle,
        context,
        "tokenomics_observation",
        explanation,
        relevant,
        confidence_basis="tokenomics observation is supported by persisted tokenomics evidence",
    )


def _finding(
    definition: EngineDefinition,
    bundle: EvidenceBundle,
    context: EngineContext,
    finding_type: str,
    explanation: str,
    records: tuple[TokenomicsEvidenceRecord, ...],
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


def _payload(evidence: Evidence) -> dict[str, Any]:
    raw = evidence.raw_data
    if not isinstance(raw, dict):
        return {}
    return {str(key): raw[key] for key in sorted(raw)}


def _record_sort_key(record: TokenomicsEvidenceRecord) -> str:
    return "|".join(
        (
            _text(record.payload.get("asset_id")),
            _text(record.payload.get("representation_id")),
            _text(record.payload.get("claim_id")),
            _text(record.payload.get("observation_id")),
            _text(record.payload.get("schedule_id")),
            _text(record.payload.get("unlock_event_id")),
            _text(record.payload.get("allocation_id")),
        )
    )


def _has_any(record: TokenomicsEvidenceRecord, keys: tuple[str, ...]) -> bool:
    return any(_present(record.payload.get(key)) for key in keys)


def _observed_names(
    records: tuple[TokenomicsEvidenceRecord, ...],
    keys: tuple[str, ...],
    *,
    metric_values: tuple[str, ...] = (),
    categories: tuple[str, ...] = (),
) -> tuple[str, ...]:
    names = set()
    for record in records:
        for key in keys:
            if _present(record.payload.get(key)):
                names.add(key)
        metric = _supply_metric(record)
        if metric in metric_values:
            names.add(metric)
        category = _category(record)
        if category in categories:
            names.add(category)
    return tuple(sorted(names))


def _categories(records: tuple[TokenomicsEvidenceRecord, ...]) -> tuple[str, ...]:
    categories = {_category(record) for record in records if _category(record)}
    return tuple(sorted(categories))


def _category(record: TokenomicsEvidenceRecord) -> str:
    return _normalize_category(
        _text(
            record.payload.get("allocation_category")
            or record.payload.get("category")
            or record.payload.get("address_category")
        )
    )


def _normalize_category(value: str) -> str:
    return value.strip().lower().replace("-", "_")


def _supply_metric(record: TokenomicsEvidenceRecord) -> str:
    return _text(record.payload.get("supply_metric") or record.payload.get("metric")).lower()


def _has_balance_only_ownership_attribution(record: TokenomicsEvidenceRecord) -> bool:
    attribution_basis = _text(record.payload.get("attribution_basis")).lower()
    if attribution_basis != "balance_only":
        return False
    return _category(record) in _OWNERSHIP_SENSITIVE_CATEGORIES or _has_ownership_sensitive_fields(record)


def _has_ownership_sensitive_fields(record: TokenomicsEvidenceRecord) -> bool:
    for key, value in record.payload.items():
        if not _present(value):
            continue
        normalized = str(key).strip().lower().replace("-", "_")
        if any(
            normalized == field or normalized.startswith(f"{field}_") for field in _OWNERSHIP_SENSITIVE_FIELD_PREFIXES
        ):
            return True
    return False


def _lineage(record: TokenomicsEvidenceRecord) -> tuple[str, ...]:
    values = (record.evidence.reference,)
    return tuple(sorted({value.strip() for value in values if value.strip()}))


def _conflicts(record: TokenomicsEvidenceRecord) -> tuple[str, ...]:
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


def _content_fingerprint(records: tuple[TokenomicsEvidenceRecord, ...]) -> str:
    return fingerprint(
        "tokenomics-finding-evidence",
        tuple((record.evidence.id, record.payload) for record in records),
    )


def _confidence(records: tuple[TokenomicsEvidenceRecord, ...]) -> float:
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


_SUPPLY_METRICS = (
    "circulating_supply",
    "fully_diluted_supply",
    "locked_supply",
    "max_supply",
    "staked_supply",
    "total_supply",
    "treasury_supply",
    "unlocked_supply",
    "vested_supply",
)
_SUPPLY_KEYS = (
    "circulating_supply",
    "fully_diluted_supply",
    "locked_supply",
    "max_supply",
    "supply_definition",
    "total_supply",
    "unlocked_supply",
    "vested_supply",
)
_ISSUANCE_KEYS = (
    "inflation",
    "inflation_rate",
    "issuance_schedule",
    "issuance_rate",
    "supply_schedule",
)
_UNLOCK_KEYS = (
    "unlock_at",
    "unlock_event_id",
    "unlock_schedule",
    "unlock_state",
    "unlocks",
)
_VESTING_KEYS = (
    "segment_state",
    "vesting_end",
    "vesting_schedule",
    "vesting_segment",
    "vesting_start",
)
_ALLOCATION_KEYS = (
    "allocation_category",
    "allocation_id",
    "allocation_percentage",
    "allocation_structure",
    "allocations",
    "category",
    "percentage",
)
_TREASURY_KEYS = (
    "treasury_allocation",
    "treasury_amount",
    "treasury_distribution",
    "treasury_supply",
)
_EMISSION_KEYS = (
    "emission_profile",
    "emission_rate",
    "emissions",
    "governance_emissions",
    "reward_emissions",
    "staking_emissions",
)
_BURN_KEYS = (
    "burn_activity",
    "burn_amount",
    "burn_event",
    "burn_events",
    "burn_tx",
    "burned_supply",
)
_PROTOCOL_KEYS = (
    "daily_revenue",
    "fees",
    "monthly_revenue",
    "protocol_distribution",
    "protocol_fees",
    "protocol_revenue",
    "revenue",
    "tvl",
)
_TOKENOMICS_OBSERVATION_KEYS = tuple(
    sorted(
        {
            *_SUPPLY_KEYS,
            *_ISSUANCE_KEYS,
            *_UNLOCK_KEYS,
            *_VESTING_KEYS,
            *_ALLOCATION_KEYS,
            *_TREASURY_KEYS,
            *_EMISSION_KEYS,
            *_BURN_KEYS,
            *_PROTOCOL_KEYS,
        }
    )
)
_OWNERSHIP_SENSITIVE_CATEGORIES = frozenset(
    {
        "exchange",
        "investor",
        "market_maker",
        "team",
        "treasury",
    }
)
_OWNERSHIP_SENSITIVE_FIELD_PREFIXES = (
    "exchange",
    "investor",
    "market_maker",
    "team",
    "treasury",
)
