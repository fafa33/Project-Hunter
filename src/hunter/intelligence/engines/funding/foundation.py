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

FUNDING_ANALYSIS_TRACE_VERSION = "funding-analysis-trace-v1"
FUNDING_EVIDENCE_CONTRACT = "funding_evidence"
FUNDING_FINDING_TYPES = (
    "capital_source",
    "ecosystem_funding",
    "funding_observation",
    "funding_round",
    "fundraising_event",
    "grant_funding",
    "investor_participation",
    "lead_investor",
    "strategic_investor",
    "treasury_funding",
)


@dataclass(frozen=True)
class FundingEvidenceRecord:
    evidence: Evidence
    payload: dict[str, Any]


@dataclass(frozen=True)
class FundingContext:
    context_type: str
    context_id: str
    records: tuple[FundingEvidenceRecord, ...]


class FundingFoundationIntelligenceEngine:
    def __init__(self, definition: EngineDefinition | None = None) -> None:
        self._definition = definition or funding_engine_definition()

    @property
    def definition(self) -> EngineDefinition:
        return self._definition

    def analyze(self, evidence: EvidenceBundle, context: EngineContext) -> FindingBatch:
        records = _funding_records(evidence)
        generators = (
            _funding_round,
            _investor_participation,
            _lead_investor,
            _strategic_investor,
            _treasury_funding,
            _grant_funding,
            _ecosystem_funding,
            _fundraising_event,
            _capital_source,
            _funding_observation,
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


def funding_engine_definition() -> EngineDefinition:
    metadata = EngineMetadata(
        id="funding-intelligence-foundation",
        name="Funding Intelligence Foundation",
        category="funding",
        version="1.0.0",
        priority=60,
        required_inputs=(FUNDING_EVIDENCE_CONTRACT,),
        produced_outputs=("funding_findings",),
        capabilities=("analyze", "funding-intelligence", "finding-generation"),
    )
    return (
        HunterIntelligenceEngineBuilder(metadata)
        .with_evidence_contracts(FUNDING_EVIDENCE_CONTRACT)
        .with_supported_evidence_types(
            "funding_capital_source_observation",
            "funding_ecosystem_observation",
            "funding_evidence_claim",
            "funding_fundraising_event",
            "funding_grant_observation",
            "funding_investor_participation",
            "funding_round_observation",
            "funding_strategic_investor_observation",
            "funding_treasury_observation",
        )
        .with_analysis_stages(
            "normalize-evidence",
            "isolate-funding-contexts",
            "aggregate-same-context-observations",
            "derive-findings",
            "explain-findings",
        )
        .with_finding_types(*FUNDING_FINDING_TYPES)
        .with_output_schema(
            schema_version="intelligence-finding-v1",
            analysis_trace_version=FUNDING_ANALYSIS_TRACE_VERSION,
        )
        .build()
    )


def _funding_records(bundle: EvidenceBundle) -> tuple[FundingEvidenceRecord, ...]:
    records = []
    for evidence in bundle.evidence:
        if not evidence_satisfies_contract(evidence, FUNDING_EVIDENCE_CONTRACT):
            continue
        payload = _payload(evidence)
        if not payload:
            continue
        records.append(FundingEvidenceRecord(evidence=evidence, payload=payload))
    return tuple(sorted(records, key=lambda record: (_record_sort_key(record), record.evidence.id)))


def _funding_round(
    definition: EngineDefinition,
    bundle: EvidenceBundle,
    context: EngineContext,
    records: tuple[FundingEvidenceRecord, ...],
) -> tuple[Finding, ...]:
    contexts = _contexts(
        records,
        context_type="funding_round",
        keys=_FUNDING_ROUND_CONTEXT_KEYS,
        required_keys=_FUNDING_ROUND_EVIDENCE_KEYS,
    )
    return _context_findings(
        definition,
        bundle,
        context,
        "funding_round",
        contexts,
        _FUNDING_ROUND_EVIDENCE_KEYS,
        confidence_basis="funding round is supported by persisted funding round evidence",
    )


def _investor_participation(
    definition: EngineDefinition,
    bundle: EvidenceBundle,
    context: EngineContext,
    records: tuple[FundingEvidenceRecord, ...],
) -> tuple[Finding, ...]:
    contexts = _contexts(
        records,
        context_type="investor",
        keys=_INVESTOR_CONTEXT_KEYS,
        required_keys=_INVESTOR_PARTICIPATION_EVIDENCE_KEYS,
    )
    return _context_findings(
        definition,
        bundle,
        context,
        "investor_participation",
        contexts,
        _INVESTOR_PARTICIPATION_EVIDENCE_KEYS,
        confidence_basis="investor participation is supported by persisted investor participation evidence",
    )


def _lead_investor(
    definition: EngineDefinition,
    bundle: EvidenceBundle,
    context: EngineContext,
    records: tuple[FundingEvidenceRecord, ...],
) -> tuple[Finding, ...]:
    contexts = _contexts(
        records,
        context_type="investor",
        keys=_INVESTOR_CONTEXT_KEYS,
        required_keys=_LEAD_INVESTOR_EVIDENCE_KEYS,
    )
    return _context_findings(
        definition,
        bundle,
        context,
        "lead_investor",
        contexts,
        _LEAD_INVESTOR_EVIDENCE_KEYS,
        confidence_basis="lead investor is supported by explicit persisted lead-investor evidence",
    )


def _strategic_investor(
    definition: EngineDefinition,
    bundle: EvidenceBundle,
    context: EngineContext,
    records: tuple[FundingEvidenceRecord, ...],
) -> tuple[Finding, ...]:
    contexts = _contexts(
        records,
        context_type="investor",
        keys=_INVESTOR_CONTEXT_KEYS,
        required_keys=_STRATEGIC_INVESTOR_EVIDENCE_KEYS,
    )
    return _context_findings(
        definition,
        bundle,
        context,
        "strategic_investor",
        contexts,
        _STRATEGIC_INVESTOR_EVIDENCE_KEYS,
        confidence_basis="strategic investor is supported by explicit persisted strategic-investor evidence",
    )


def _treasury_funding(
    definition: EngineDefinition,
    bundle: EvidenceBundle,
    context: EngineContext,
    records: tuple[FundingEvidenceRecord, ...],
) -> tuple[Finding, ...]:
    contexts = _contexts(
        records,
        context_type="treasury_funding_event",
        keys=_TREASURY_FUNDING_CONTEXT_KEYS,
        required_keys=_TREASURY_FUNDING_EVIDENCE_KEYS,
    )
    return _context_findings(
        definition,
        bundle,
        context,
        "treasury_funding",
        contexts,
        _TREASURY_FUNDING_EVIDENCE_KEYS,
        confidence_basis="treasury funding is supported by explicit persisted treasury-funding evidence",
    )


def _grant_funding(
    definition: EngineDefinition,
    bundle: EvidenceBundle,
    context: EngineContext,
    records: tuple[FundingEvidenceRecord, ...],
) -> tuple[Finding, ...]:
    contexts = _contexts(records, context_type="grant", keys=_GRANT_CONTEXT_KEYS, required_keys=_GRANT_EVIDENCE_KEYS)
    return _context_findings(
        definition,
        bundle,
        context,
        "grant_funding",
        contexts,
        _GRANT_EVIDENCE_KEYS,
        confidence_basis="grant funding is supported by explicit persisted grant or program evidence",
    )


def _ecosystem_funding(
    definition: EngineDefinition,
    bundle: EvidenceBundle,
    context: EngineContext,
    records: tuple[FundingEvidenceRecord, ...],
) -> tuple[Finding, ...]:
    contexts = _contexts(
        records,
        context_type="ecosystem_program",
        keys=_ECOSYSTEM_PROGRAM_CONTEXT_KEYS,
        required_keys=_ECOSYSTEM_FUNDING_EVIDENCE_KEYS,
    )
    return _context_findings(
        definition,
        bundle,
        context,
        "ecosystem_funding",
        contexts,
        _ECOSYSTEM_FUNDING_EVIDENCE_KEYS,
        confidence_basis="ecosystem funding is supported by persisted ecosystem-program funding evidence",
    )


def _fundraising_event(
    definition: EngineDefinition,
    bundle: EvidenceBundle,
    context: EngineContext,
    records: tuple[FundingEvidenceRecord, ...],
) -> tuple[Finding, ...]:
    contexts = _contexts(
        records,
        context_type="fundraising_event",
        keys=_FUNDRAISING_EVENT_CONTEXT_KEYS,
        required_keys=_FUNDRAISING_EVENT_EVIDENCE_KEYS,
    )
    return _context_findings(
        definition,
        bundle,
        context,
        "fundraising_event",
        contexts,
        _FUNDRAISING_EVENT_EVIDENCE_KEYS,
        confidence_basis="fundraising event is supported by persisted fundraising event evidence",
    )


def _capital_source(
    definition: EngineDefinition,
    bundle: EvidenceBundle,
    context: EngineContext,
    records: tuple[FundingEvidenceRecord, ...],
) -> tuple[Finding, ...]:
    contexts = _contexts(
        records,
        context_type="capital_source",
        keys=_CAPITAL_SOURCE_CONTEXT_KEYS,
        required_keys=_CAPITAL_SOURCE_EVIDENCE_KEYS,
    )
    return _context_findings(
        definition,
        bundle,
        context,
        "capital_source",
        contexts,
        _CAPITAL_SOURCE_EVIDENCE_KEYS,
        confidence_basis="capital source is supported by persisted capital-source evidence",
    )


def _funding_observation(
    definition: EngineDefinition,
    bundle: EvidenceBundle,
    context: EngineContext,
    records: tuple[FundingEvidenceRecord, ...],
) -> tuple[Finding, ...]:
    contexts = _observation_contexts(records)
    return _context_findings(
        definition,
        bundle,
        context,
        "funding_observation",
        contexts,
        _FUNDING_OBSERVATION_EVIDENCE_KEYS,
        confidence_basis="funding observation is supported by persisted funding evidence",
    )


def _context_findings(
    definition: EngineDefinition,
    bundle: EvidenceBundle,
    engine_context: EngineContext,
    finding_type: str,
    contexts: tuple[FundingContext, ...],
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
            funding_context,
            _observed_qualifying_names(funding_context.records, fields),
            confidence_basis=confidence_basis,
        )
        for funding_context in contexts
    )


def _context_finding(
    definition: EngineDefinition,
    bundle: EvidenceBundle,
    engine_context: EngineContext,
    finding_type: str,
    funding_context: FundingContext,
    fields: tuple[str, ...],
    *,
    confidence_basis: str,
) -> Finding:
    explanation = (
        f"{finding_type} is descriptively evidenced within {funding_context.context_type} "
        f"context {funding_context.context_id} by persisted funding fields: {', '.join(fields)}; "
        f"evidence content: {_content_fingerprint(funding_context.records)}."
    )
    return _finding(
        definition,
        bundle,
        engine_context,
        finding_type,
        explanation,
        funding_context.records,
        confidence_basis=confidence_basis,
    )


def _finding(
    definition: EngineDefinition,
    bundle: EvidenceBundle,
    context: EngineContext,
    finding_type: str,
    explanation: str,
    records: tuple[FundingEvidenceRecord, ...],
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
    records: tuple[FundingEvidenceRecord, ...],
    *,
    context_type: str,
    keys: tuple[str, ...],
    required_keys: tuple[str, ...],
) -> tuple[FundingContext, ...]:
    grouped: dict[str, list[FundingEvidenceRecord]] = {}
    affirmed_contexts: set[str] = set()
    for record in records:
        context_id = _context_id(record, context_type=context_type, keys=keys)
        if not context_id:
            continue
        grouped.setdefault(context_id, []).append(record)
        if _has_qualifying_any(record, required_keys):
            affirmed_contexts.add(context_id)
    grouped = {
        context_id: group_records for context_id, group_records in grouped.items() if context_id in affirmed_contexts
    }
    return _sorted_contexts(context_type, grouped)


def _observation_contexts(records: tuple[FundingEvidenceRecord, ...]) -> tuple[FundingContext, ...]:
    grouped: dict[tuple[str, str], list[FundingEvidenceRecord]] = {}
    affirmed_contexts: set[tuple[str, str]] = set()
    for record in records:
        context = _primary_context(record)
        if context is None:
            continue
        context_key = (context.context_type, context.context_id)
        grouped.setdefault(context_key, []).append(record)
        if _has_qualifying_any(record, _FUNDING_OBSERVATION_EVIDENCE_KEYS):
            affirmed_contexts.add(context_key)
    contexts = [
        FundingContext(
            context_type=context_type,
            context_id=context_id,
            records=tuple(sorted(group_records, key=lambda item: (_record_sort_key(item), item.evidence.id))),
        )
        for (context_type, context_id), group_records in grouped.items()
        if (context_type, context_id) in affirmed_contexts
    ]
    return tuple(sorted(contexts, key=lambda item: (item.context_type, item.context_id)))


def _primary_context(record: FundingEvidenceRecord) -> FundingContext | None:
    for context_type, keys in _PRIMARY_CONTEXT_PRECEDENCE:
        context_id = _context_id(record, context_type=context_type, keys=keys)
        if context_id:
            return FundingContext(context_type=context_type, context_id=context_id, records=(record,))
    return None


def _sorted_contexts(
    context_type: str,
    grouped: dict[str, list[FundingEvidenceRecord]],
) -> tuple[FundingContext, ...]:
    contexts = [
        FundingContext(
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


def _record_sort_key(record: FundingEvidenceRecord) -> str:
    return "|".join(
        (
            _first_text(record, _FUNDING_ROUND_CONTEXT_KEYS),
            _first_text(record, _INVESTOR_CONTEXT_KEYS),
            _first_text(record, _SYNDICATE_CONTEXT_KEYS),
            _first_text(record, _TREASURY_FUNDING_CONTEXT_KEYS),
            _first_text(record, _GRANT_CONTEXT_KEYS),
            _first_text(record, _ECOSYSTEM_PROGRAM_CONTEXT_KEYS),
            _first_text(record, _FUNDRAISING_EVENT_CONTEXT_KEYS),
            _first_text(record, _CAPITAL_SOURCE_CONTEXT_KEYS),
            _first_text(record, _ORGANIZATION_CONTEXT_KEYS),
            _first_text(record, _PROJECT_CONTEXT_KEYS),
            _text(record.payload.get("claim_id")),
            record.evidence.id,
        )
    )


def _first_text(record: FundingEvidenceRecord, keys: tuple[str, ...]) -> str:
    for key in keys:
        value = _text(record.payload.get(key))
        if value:
            return value
    return ""


def _context_id(record: FundingEvidenceRecord, *, context_type: str, keys: tuple[str, ...]) -> str:
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


def _first_normalized_identifier(record: FundingEvidenceRecord, keys: tuple[str, ...]) -> str:
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


def _has_qualifying_any(record: FundingEvidenceRecord, keys: tuple[str, ...]) -> bool:
    return any(_affirmative_present(key, record.payload.get(key)) for key in keys)


def _observed_qualifying_names(
    records: tuple[FundingEvidenceRecord, ...],
    keys: tuple[str, ...],
) -> tuple[str, ...]:
    names = set()
    for record in records:
        for key in keys:
            if _affirmative_present(key, record.payload.get(key)):
                names.add(key)
    return tuple(sorted(names))


def _lineage(record: FundingEvidenceRecord) -> tuple[str, ...]:
    values = (record.evidence.reference,)
    return tuple(sorted({value.strip() for value in values if value.strip()}))


def _conflicts(record: FundingEvidenceRecord) -> tuple[str, ...]:
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


def _content_fingerprint(records: tuple[FundingEvidenceRecord, ...]) -> str:
    return fingerprint(
        "funding-finding-evidence",
        tuple((record.evidence.id, record.payload) for record in records),
    )


def _confidence(records: tuple[FundingEvidenceRecord, ...]) -> float:
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


def _affirmative_present(key: str, value: object) -> bool:
    if not _present(value):
        return False
    if isinstance(value, bool):
        return value
    if isinstance(value, int | float):
        return value != 0
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in _NEGATIVE_FUNDING_VALUES:
            return False
        if _requires_explicit_positive_value(key):
            return normalized in _AFFIRMATIVE_FUNDING_VALUES
        return True
    return True


def _requires_explicit_positive_value(key: str) -> bool:
    return key.startswith("is_") or key.endswith("_status") or key in _EXPLICIT_AFFIRMATIVE_KEYS


def _text(value: object) -> str:
    if not isinstance(value, str):
        return ""
    return value.strip()


_FUNDING_ROUND_CONTEXT_KEYS = ("funding_round_id", "round_id")
_INVESTOR_CONTEXT_KEYS = ("investor_id",)
_SYNDICATE_CONTEXT_KEYS = ("syndicate_id",)
_TREASURY_FUNDING_CONTEXT_KEYS = ("treasury_funding_event_id",)
_GRANT_CONTEXT_KEYS = ("grant_id",)
_ECOSYSTEM_PROGRAM_CONTEXT_KEYS = ("ecosystem_program_id",)
_FUNDRAISING_EVENT_CONTEXT_KEYS = ("fundraising_event_id",)
_CAPITAL_SOURCE_CONTEXT_KEYS = ("capital_source_id",)
_ORGANIZATION_CONTEXT_KEYS = ("organization_id",)
_PROJECT_CONTEXT_KEYS = ("project_id",)

_AFFIRMATIVE_FUNDING_VALUES = frozenset(
    {
        "accepted",
        "active",
        "allocated",
        "announced",
        "approved",
        "awarded",
        "closed",
        "committed",
        "complete",
        "completed",
        "confirmed",
        "funded",
        "grant",
        "granted",
        "lead",
        "participated",
        "participating",
        "provided",
        "raised",
        "received",
        "reported",
        "secured",
        "strategic",
        "true",
        "yes",
    }
)
_NEGATIVE_FUNDING_VALUES = frozenset(
    {
        "0",
        "cancelled",
        "canceled",
        "declined",
        "denied",
        "failed",
        "false",
        "inactive",
        "no",
        "none",
        "null",
        "pending",
        "rejected",
        "unknown",
        "withdrawn",
    }
)
_EXPLICIT_AFFIRMATIVE_KEYS = frozenset(
    {
        "ecosystem_funding",
        "fundraising_event",
        "funding_round",
        "grant_funding",
        "is_lead_investor",
        "lead_investor",
        "strategic_investor",
        "treasury_funding",
    }
)

_FUNDING_ROUND_KEYS = (
    "funding_round_id",
    "round_id",
    "funding_round",
    "funding_round_observation",
    "round_stage",
    "round_amount",
    "raised_amount",
    "round_currency",
    "round_date",
)
_FUNDING_ROUND_EVIDENCE_KEYS = tuple(key for key in _FUNDING_ROUND_KEYS if key not in _FUNDING_ROUND_CONTEXT_KEYS)
_INVESTOR_PARTICIPATION_KEYS = (
    "investor_id",
    "investor_name",
    "investor_alias",
    "investor_domain",
    "investor_label",
    "investor_participation",
    "participation_amount",
    "participation_role",
    "investment_amount",
    "participation_status",
)
_INVESTOR_PARTICIPATION_EVIDENCE_KEYS = tuple(
    key
    for key in _INVESTOR_PARTICIPATION_KEYS
    if key not in {*_INVESTOR_CONTEXT_KEYS, "investor_name", "investor_alias", "investor_domain", "investor_label"}
)
_LEAD_INVESTOR_KEYS = (
    "investor_id",
    "lead_investor",
    "lead_investor_status",
    "lead_role",
    "is_lead_investor",
)
_LEAD_INVESTOR_EVIDENCE_KEYS = tuple(key for key in _LEAD_INVESTOR_KEYS if key not in _INVESTOR_CONTEXT_KEYS)
_STRATEGIC_INVESTOR_KEYS = (
    "investor_id",
    "strategic_investor",
    "strategic_status",
    "strategic_investor_status",
    "strategic_partnership",
)
_STRATEGIC_INVESTOR_EVIDENCE_KEYS = tuple(key for key in _STRATEGIC_INVESTOR_KEYS if key not in _INVESTOR_CONTEXT_KEYS)
_TREASURY_FUNDING_KEYS = (
    "treasury_funding_event_id",
    "treasury_funding",
    "treasury_funding_amount",
    "treasury_funding_source",
    "treasury_funding_observation",
)
_TREASURY_FUNDING_EVIDENCE_KEYS = tuple(
    key for key in _TREASURY_FUNDING_KEYS if key not in _TREASURY_FUNDING_CONTEXT_KEYS
)
_GRANT_KEYS = (
    "grant_id",
    "grant_funding",
    "grant_amount",
    "grant_program",
    "grant_award",
    "grant_status",
)
_GRANT_EVIDENCE_KEYS = tuple(key for key in _GRANT_KEYS if key not in _GRANT_CONTEXT_KEYS)
_ECOSYSTEM_FUNDING_KEYS = (
    "ecosystem_program_id",
    "ecosystem_funding",
    "ecosystem_funding_amount",
    "program_budget",
    "program_funding",
)
_ECOSYSTEM_FUNDING_EVIDENCE_KEYS = tuple(
    key for key in _ECOSYSTEM_FUNDING_KEYS if key not in _ECOSYSTEM_PROGRAM_CONTEXT_KEYS
)
_FUNDRAISING_EVENT_KEYS = (
    "fundraising_event_id",
    "fundraising_event",
    "fundraising_amount",
    "fundraising_status",
    "fundraising_announcement",
)
_FUNDRAISING_EVENT_EVIDENCE_KEYS = tuple(
    key for key in _FUNDRAISING_EVENT_KEYS if key not in _FUNDRAISING_EVENT_CONTEXT_KEYS
)
_CAPITAL_SOURCE_KEYS = (
    "capital_source_id",
    "capital_source",
    "capital_source_type",
    "capital_commitment",
    "capital_allocation",
)
_CAPITAL_SOURCE_EVIDENCE_KEYS = tuple(key for key in _CAPITAL_SOURCE_KEYS if key not in _CAPITAL_SOURCE_CONTEXT_KEYS)
_ORGANIZATION_KEYS = ("organization_id", "organization_funding", "organization_funding_observation")
_PROJECT_KEYS = ("project_id", "project_funding", "project_funding_observation")
_FUNDING_OBSERVATION_KEYS = tuple(
    sorted(
        {
            *_FUNDING_ROUND_KEYS,
            *_INVESTOR_PARTICIPATION_KEYS,
            *_LEAD_INVESTOR_KEYS,
            *_STRATEGIC_INVESTOR_KEYS,
            *_TREASURY_FUNDING_KEYS,
            *_GRANT_KEYS,
            *_ECOSYSTEM_FUNDING_KEYS,
            *_FUNDRAISING_EVENT_KEYS,
            *_CAPITAL_SOURCE_KEYS,
            *_ORGANIZATION_KEYS,
            *_PROJECT_KEYS,
            "funding_claim",
            "funding_observation",
        }
    )
)
_FUNDING_CONTEXT_IDENTIFIER_KEYS = frozenset(
    {
        *_FUNDING_ROUND_CONTEXT_KEYS,
        *_INVESTOR_CONTEXT_KEYS,
        *_SYNDICATE_CONTEXT_KEYS,
        *_TREASURY_FUNDING_CONTEXT_KEYS,
        *_GRANT_CONTEXT_KEYS,
        *_ECOSYSTEM_PROGRAM_CONTEXT_KEYS,
        *_FUNDRAISING_EVENT_CONTEXT_KEYS,
        *_CAPITAL_SOURCE_CONTEXT_KEYS,
        *_ORGANIZATION_CONTEXT_KEYS,
        *_PROJECT_CONTEXT_KEYS,
        "investor_name",
        "investor_alias",
        "investor_domain",
        "investor_label",
        "organization_name",
        "project_name",
        "source_label",
    }
)
_FUNDING_OBSERVATION_EVIDENCE_KEYS = tuple(
    key for key in _FUNDING_OBSERVATION_KEYS if key not in _FUNDING_CONTEXT_IDENTIFIER_KEYS
)
_PRIMARY_CONTEXT_PRECEDENCE = (
    ("funding_round", _FUNDING_ROUND_CONTEXT_KEYS),
    ("investor", _INVESTOR_CONTEXT_KEYS),
    ("syndicate", _SYNDICATE_CONTEXT_KEYS),
    ("treasury_funding_event", _TREASURY_FUNDING_CONTEXT_KEYS),
    ("grant", _GRANT_CONTEXT_KEYS),
    ("ecosystem_program", _ECOSYSTEM_PROGRAM_CONTEXT_KEYS),
    ("fundraising_event", _FUNDRAISING_EVENT_CONTEXT_KEYS),
    ("capital_source", _CAPITAL_SOURCE_CONTEXT_KEYS),
    ("organization", _ORGANIZATION_CONTEXT_KEYS),
    ("project", _PROJECT_CONTEXT_KEYS),
)
_SYNONYM_CONTEXT_KEY_GROUPS = {
    "funding_round": (("funding_round_id", "round_id"),),
    "investor": (("investor_id",),),
    "syndicate": (("syndicate_id",),),
    "treasury_funding_event": (("treasury_funding_event_id",),),
    "grant": (("grant_id",),),
    "ecosystem_program": (("ecosystem_program_id",),),
    "fundraising_event": (("fundraising_event_id",),),
    "capital_source": (("capital_source_id",),),
    "organization": (("organization_id",),),
    "project": (("project_id",),),
}
