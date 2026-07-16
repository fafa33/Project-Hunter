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

GOVERNANCE_ANALYSIS_TRACE_VERSION = "governance-analysis-trace-v1"
GOVERNANCE_FINDING_TYPES = (
    "delegate_distribution",
    "governance_activity",
    "governance_execution_observation",
    "governance_observation",
    "governance_parameter_observation",
    "proposal_lifecycle",
    "quorum_observation",
    "voting_participation",
)
GOVERNANCE_EVIDENCE_CONTRACT = "governance_evidence"


@dataclass(frozen=True)
class GovernanceEvidenceRecord:
    evidence: Evidence
    payload: dict[str, Any]


@dataclass(frozen=True)
class GovernanceContext:
    context_type: str
    context_id: str
    records: tuple[GovernanceEvidenceRecord, ...]


class GovernanceFoundationIntelligenceEngine:
    def __init__(self, definition: EngineDefinition | None = None) -> None:
        self._definition = definition or governance_engine_definition()

    @property
    def definition(self) -> EngineDefinition:
        return self._definition

    def analyze(self, evidence: EvidenceBundle, context: EngineContext) -> FindingBatch:
        records = _governance_records(evidence)
        generators = (
            _governance_activity,
            _proposal_lifecycle,
            _voting_participation,
            _quorum_observation,
            _delegate_distribution,
            _governance_parameter_observation,
            _governance_execution_observation,
            _governance_observation,
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


def governance_engine_definition() -> EngineDefinition:
    metadata = EngineMetadata(
        id="governance-intelligence-foundation",
        name="Governance Intelligence Foundation",
        category="governance",
        version="1.0.0",
        priority=30,
        required_inputs=(GOVERNANCE_EVIDENCE_CONTRACT,),
        produced_outputs=("governance_findings",),
        capabilities=("analyze", "governance-intelligence", "finding-generation"),
    )
    return (
        HunterIntelligenceEngineBuilder(metadata)
        .with_evidence_contracts(GOVERNANCE_EVIDENCE_CONTRACT)
        .with_supported_evidence_types(
            "governance_delegate_record",
            "governance_evidence_claim",
            "governance_execution_record",
            "governance_parameter_record",
            "governance_proposal_record",
            "governance_quorum_record",
            "governance_space_record",
            "governance_vote_record",
        )
        .with_analysis_stages("normalize-evidence", "isolate-contexts", "derive-findings", "explain-findings")
        .with_finding_types(*GOVERNANCE_FINDING_TYPES)
        .with_output_schema(
            schema_version="intelligence-finding-v1",
            analysis_trace_version=GOVERNANCE_ANALYSIS_TRACE_VERSION,
        )
        .build()
    )


def _governance_records(bundle: EvidenceBundle) -> tuple[GovernanceEvidenceRecord, ...]:
    records = []
    for evidence in bundle.evidence:
        if not evidence_satisfies_contract(evidence, GOVERNANCE_EVIDENCE_CONTRACT):
            continue
        payload = _payload(evidence)
        if not payload:
            continue
        records.append(GovernanceEvidenceRecord(evidence=evidence, payload=payload))
    return tuple(sorted(records, key=lambda record: (_record_sort_key(record), record.evidence.id)))


def _governance_activity(
    definition: EngineDefinition,
    bundle: EvidenceBundle,
    context: EngineContext,
    records: tuple[GovernanceEvidenceRecord, ...],
) -> tuple[Finding, ...]:
    contexts = _contexts(records, context_type="space", keys=_SPACE_CONTEXT_KEYS, required_keys=_SPACE_KEYS)
    return tuple(
        _context_finding(
            definition,
            bundle,
            context,
            "governance_activity",
            governance_context,
            _observed_names(governance_context.records, _SPACE_KEYS),
            confidence_basis="governance activity is supported by persisted governance space evidence",
        )
        for governance_context in contexts
    )


def _proposal_lifecycle(
    definition: EngineDefinition,
    bundle: EvidenceBundle,
    context: EngineContext,
    records: tuple[GovernanceEvidenceRecord, ...],
) -> tuple[Finding, ...]:
    contexts = _contexts(
        records,
        context_type="proposal",
        keys=_PROPOSAL_CONTEXT_KEYS,
        required_keys=_PROPOSAL_EVIDENCE_KEYS,
    )
    return tuple(
        _context_finding(
            definition,
            bundle,
            context,
            "proposal_lifecycle",
            governance_context,
            _observed_names(governance_context.records, _PROPOSAL_EVIDENCE_KEYS),
            confidence_basis="proposal lifecycle is supported by persisted proposal state or timestamp evidence",
        )
        for governance_context in contexts
    )


def _voting_participation(
    definition: EngineDefinition,
    bundle: EvidenceBundle,
    context: EngineContext,
    records: tuple[GovernanceEvidenceRecord, ...],
) -> tuple[Finding, ...]:
    contexts = _contexts(records, context_type="vote", keys=_VOTE_CONTEXT_KEYS, required_keys=_VOTE_KEYS)
    return tuple(
        _context_finding(
            definition,
            bundle,
            context,
            "voting_participation",
            governance_context,
            _observed_names(governance_context.records, _VOTE_KEYS),
            confidence_basis="voting participation is supported by persisted vote or participation evidence",
        )
        for governance_context in contexts
    )


def _quorum_observation(
    definition: EngineDefinition,
    bundle: EvidenceBundle,
    context: EngineContext,
    records: tuple[GovernanceEvidenceRecord, ...],
) -> tuple[Finding, ...]:
    contexts = _contexts(records, context_type="quorum", keys=_QUORUM_CONTEXT_KEYS, required_keys=_QUORUM_KEYS)
    return tuple(
        _context_finding(
            definition,
            bundle,
            context,
            "quorum_observation",
            governance_context,
            _observed_names(governance_context.records, _QUORUM_KEYS),
            confidence_basis="quorum observation is supported by persisted quorum evidence",
        )
        for governance_context in contexts
    )


def _delegate_distribution(
    definition: EngineDefinition,
    bundle: EvidenceBundle,
    context: EngineContext,
    records: tuple[GovernanceEvidenceRecord, ...],
) -> tuple[Finding, ...]:
    contexts = _contexts(records, context_type="delegate", keys=_DELEGATE_CONTEXT_KEYS, required_keys=_DELEGATE_KEYS)
    return tuple(
        _context_finding(
            definition,
            bundle,
            context,
            "delegate_distribution",
            governance_context,
            _observed_names(governance_context.records, _DELEGATE_KEYS),
            confidence_basis="delegate distribution is supported by persisted delegate or voting power evidence",
        )
        for governance_context in contexts
    )


def _governance_parameter_observation(
    definition: EngineDefinition,
    bundle: EvidenceBundle,
    context: EngineContext,
    records: tuple[GovernanceEvidenceRecord, ...],
) -> tuple[Finding, ...]:
    contexts = _contexts(records, context_type="parameter", keys=_PARAMETER_CONTEXT_KEYS, required_keys=_PARAMETER_KEYS)
    return tuple(
        _context_finding(
            definition,
            bundle,
            context,
            "governance_parameter_observation",
            governance_context,
            _observed_names(governance_context.records, _PARAMETER_KEYS),
            confidence_basis="governance parameter observation is supported by persisted governance parameter evidence",
        )
        for governance_context in contexts
    )


def _governance_execution_observation(
    definition: EngineDefinition,
    bundle: EvidenceBundle,
    context: EngineContext,
    records: tuple[GovernanceEvidenceRecord, ...],
) -> tuple[Finding, ...]:
    contexts = _contexts(records, context_type="execution", keys=_EXECUTION_CONTEXT_KEYS, required_keys=_EXECUTION_KEYS)
    return tuple(
        _context_finding(
            definition,
            bundle,
            context,
            "governance_execution_observation",
            governance_context,
            _observed_names(governance_context.records, _EXECUTION_KEYS),
            confidence_basis="governance execution observation is supported by persisted execution metadata",
        )
        for governance_context in contexts
    )


def _governance_observation(
    definition: EngineDefinition,
    bundle: EvidenceBundle,
    context: EngineContext,
    records: tuple[GovernanceEvidenceRecord, ...],
) -> tuple[Finding, ...]:
    contexts = _observation_contexts(records)
    return tuple(
        _context_finding(
            definition,
            bundle,
            context,
            "governance_observation",
            governance_context,
            _observed_names(governance_context.records, _GOVERNANCE_OBSERVATION_KEYS),
            confidence_basis="governance observation is supported by persisted governance evidence",
        )
        for governance_context in contexts
    )


def _context_finding(
    definition: EngineDefinition,
    bundle: EvidenceBundle,
    engine_context: EngineContext,
    finding_type: str,
    governance_context: GovernanceContext,
    fields: tuple[str, ...],
    *,
    confidence_basis: str,
) -> Finding:
    explanation = (
        f"{finding_type} is descriptively evidenced within {governance_context.context_type} "
        f"context {governance_context.context_id} by persisted governance fields: {', '.join(fields)}; "
        f"evidence content: {_content_fingerprint(governance_context.records)}."
    )
    return _finding(
        definition,
        bundle,
        engine_context,
        finding_type,
        explanation,
        governance_context.records,
        confidence_basis=confidence_basis,
    )


def _finding(
    definition: EngineDefinition,
    bundle: EvidenceBundle,
    context: EngineContext,
    finding_type: str,
    explanation: str,
    records: tuple[GovernanceEvidenceRecord, ...],
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
    records: tuple[GovernanceEvidenceRecord, ...],
    *,
    context_type: str,
    keys: tuple[str, ...],
    required_keys: tuple[str, ...],
) -> tuple[GovernanceContext, ...]:
    grouped: dict[str, list[GovernanceEvidenceRecord]] = {}
    for record in records:
        if not _has_any(record, required_keys):
            continue
        context_id = _context_id(record, context_type=context_type, keys=keys)
        if not context_id:
            continue
        grouped.setdefault(context_id, []).append(record)
    return _sorted_contexts(context_type, grouped)


def _observation_contexts(records: tuple[GovernanceEvidenceRecord, ...]) -> tuple[GovernanceContext, ...]:
    grouped: dict[tuple[str, str], list[GovernanceEvidenceRecord]] = {}
    for record in records:
        if not _has_any(record, _GOVERNANCE_OBSERVATION_KEYS):
            continue
        context = _primary_context(record)
        if context is None:
            continue
        grouped.setdefault((context.context_type, context.context_id), []).append(record)
    contexts = [
        GovernanceContext(
            context_type=context_type,
            context_id=context_id,
            records=tuple(sorted(group_records, key=lambda item: (_record_sort_key(item), item.evidence.id))),
        )
        for (context_type, context_id), group_records in grouped.items()
    ]
    return tuple(sorted(contexts, key=lambda item: (item.context_type, item.context_id)))


def _primary_context(record: GovernanceEvidenceRecord) -> GovernanceContext | None:
    for context_type, keys in _PRIMARY_CONTEXT_PRECEDENCE:
        context_id = _context_id(record, context_type=context_type, keys=keys)
        if context_id:
            return GovernanceContext(context_type=context_type, context_id=context_id, records=(record,))
    return None


def _sorted_contexts(
    context_type: str,
    grouped: dict[str, list[GovernanceEvidenceRecord]],
) -> tuple[GovernanceContext, ...]:
    contexts = [
        GovernanceContext(
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


def _record_sort_key(record: GovernanceEvidenceRecord) -> str:
    return "|".join(
        (
            _first_text(record, _SPACE_CONTEXT_KEYS),
            _first_text(record, _PROPOSAL_CONTEXT_KEYS),
            _first_text(record, _VOTE_CONTEXT_KEYS),
            _first_text(record, _DELEGATE_CONTEXT_KEYS),
            _first_text(record, _EXECUTION_CONTEXT_KEYS),
            _first_text(record, _PARAMETER_CONTEXT_KEYS),
            _text(record.payload.get("claim_id")),
            record.evidence.id,
        )
    )


def _first_text(record: GovernanceEvidenceRecord, keys: tuple[str, ...]) -> str:
    for key in keys:
        value = _text(record.payload.get(key))
        if value:
            return value
    return ""


def _context_id(record: GovernanceEvidenceRecord, *, context_type: str, keys: tuple[str, ...]) -> str:
    if context_type in _SYNONYM_CONTEXT_KEY_GROUPS:
        for group in _SYNONYM_CONTEXT_KEY_GROUPS[context_type]:
            values = {_text(record.payload.get(key)) for key in group if _text(record.payload.get(key))}
            if len(values) > 1:
                return ""
            if len(values) == 1:
                return next(iter(values))
    return _first_text(record, keys)


def _has_any(record: GovernanceEvidenceRecord, keys: tuple[str, ...]) -> bool:
    return any(_present(record.payload.get(key)) for key in keys)


def _observed_names(
    records: tuple[GovernanceEvidenceRecord, ...],
    keys: tuple[str, ...],
) -> tuple[str, ...]:
    names = set()
    for record in records:
        for key in keys:
            if _present(record.payload.get(key)):
                names.add(key)
    return tuple(sorted(names))


def _lineage(record: GovernanceEvidenceRecord) -> tuple[str, ...]:
    values = (record.evidence.reference,)
    return tuple(sorted({value.strip() for value in values if value.strip()}))


def _conflicts(record: GovernanceEvidenceRecord) -> tuple[str, ...]:
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


def _content_fingerprint(records: tuple[GovernanceEvidenceRecord, ...]) -> str:
    return fingerprint(
        "governance-finding-evidence",
        tuple((record.evidence.id, record.payload) for record in records),
    )


def _confidence(records: tuple[GovernanceEvidenceRecord, ...]) -> float:
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


_SPACE_CONTEXT_KEYS = ("governance_space_id", "space_id")
_PROPOSAL_CONTEXT_KEYS = ("proposal_id",)
_VOTE_CONTEXT_KEYS = ("vote_id", "proposal_id")
_QUORUM_CONTEXT_KEYS = ("proposal_id", "quorum_id")
_DELEGATE_CONTEXT_KEYS = ("delegate_id", "governance_space_id", "space_id")
_EXECUTION_CONTEXT_KEYS = ("execution_id", "execution_record_id")
_PARAMETER_CONTEXT_KEYS = ("parameter_id", "governance_space_id", "space_id")

_SPACE_KEYS = (
    "governance_space_id",
    "space_id",
    "space_name",
    "space_url",
    "governance_space",
)
_PROPOSAL_KEYS = (
    "proposal_id",
    "proposal_state",
    "proposal_created_at",
    "proposal_start_at",
    "proposal_end_at",
    "proposal_executed_at",
    "proposal_timestamp",
)
_PROPOSAL_EVIDENCE_KEYS = tuple(key for key in _PROPOSAL_KEYS if key != "proposal_id")
_VOTE_KEYS = (
    "vote_id",
    "vote_count",
    "votes",
    "voter_count",
    "participation",
    "participation_rate",
    "voting_power",
)
_QUORUM_KEYS = (
    "quorum",
    "quorum_required",
    "quorum_reached",
    "quorum_value",
)
_DELEGATE_KEYS = (
    "delegate_id",
    "delegate_count",
    "delegate_address",
    "delegates",
    "delegated_voting_power",
    "voting_power",
)
_PARAMETER_KEYS = (
    "governance_parameter",
    "governance_parameters",
    "parameter_id",
    "parameter_name",
    "parameter_value",
    "proposal_threshold",
    "quorum_threshold",
    "voting_delay",
    "voting_period",
)
_EXECUTION_KEYS = (
    "execution_id",
    "execution_record_id",
    "execution_state",
    "execution_timestamp",
    "execution_tx",
    "execution_metadata",
)
_GOVERNANCE_OBSERVATION_KEYS = tuple(
    sorted(
        {
            *_SPACE_KEYS,
            *_PROPOSAL_KEYS,
            *_VOTE_KEYS,
            *_QUORUM_KEYS,
            *_DELEGATE_KEYS,
            *_PARAMETER_KEYS,
            *_EXECUTION_KEYS,
        }
    )
)
_PRIMARY_CONTEXT_PRECEDENCE = (
    ("execution", _EXECUTION_CONTEXT_KEYS),
    ("vote", ("vote_id",)),
    ("proposal", _PROPOSAL_CONTEXT_KEYS),
    ("delegate", ("delegate_id",)),
    ("parameter", ("parameter_id",)),
    ("space", _SPACE_CONTEXT_KEYS),
)
_SYNONYM_CONTEXT_KEY_GROUPS = {
    "execution": (("execution_id", "execution_record_id"),),
    "space": (("governance_space_id", "space_id"),),
}
