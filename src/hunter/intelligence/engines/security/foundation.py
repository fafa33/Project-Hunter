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

SECURITY_ANALYSIS_TRACE_VERSION = "security-analysis-trace-v1"
SECURITY_EVIDENCE_CONTRACT = "security_evidence"
SECURITY_FINDING_TYPES = (
    "audit_observation",
    "contract_security",
    "exploit_history",
    "ownership_model",
    "privileged_permissions",
    "proxy_configuration",
    "security_observation",
    "token_control_features",
    "vulnerability_observation",
)


@dataclass(frozen=True)
class SecurityEvidenceRecord:
    evidence: Evidence
    payload: dict[str, Any]


@dataclass(frozen=True)
class SecurityContext:
    context_type: str
    context_id: str
    records: tuple[SecurityEvidenceRecord, ...]


class SecurityFoundationIntelligenceEngine:
    def __init__(self, definition: EngineDefinition | None = None) -> None:
        self._definition = definition or security_engine_definition()

    @property
    def definition(self) -> EngineDefinition:
        return self._definition

    def analyze(self, evidence: EvidenceBundle, context: EngineContext) -> FindingBatch:
        records = _security_records(evidence)
        generators = (
            _contract_security,
            _ownership_model,
            _proxy_configuration,
            _privileged_permissions,
            _token_control_features,
            _audit_observation,
            _exploit_history,
            _vulnerability_observation,
            _security_observation,
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


def security_engine_definition() -> EngineDefinition:
    metadata = EngineMetadata(
        id="security-intelligence-foundation",
        name="Security Intelligence Foundation",
        category="security",
        version="1.0.0",
        priority=40,
        required_inputs=(SECURITY_EVIDENCE_CONTRACT,),
        produced_outputs=("security_findings",),
        capabilities=("analyze", "security-intelligence", "finding-generation"),
    )
    return (
        HunterIntelligenceEngineBuilder(metadata)
        .with_evidence_contracts(SECURITY_EVIDENCE_CONTRACT)
        .with_supported_evidence_types(
            "security_audit_reference",
            "security_contract_observation",
            "security_evidence_claim",
            "security_exploit_observation",
            "security_ownership_observation",
            "security_privilege_observation",
            "security_proxy_observation",
            "security_token_control_observation",
            "security_vulnerability_observation",
            "token_security_metadata",
        )
        .with_analysis_stages(
            "normalize-evidence",
            "isolate-security-contexts",
            "aggregate-same-context-observations",
            "derive-findings",
            "explain-findings",
        )
        .with_finding_types(*SECURITY_FINDING_TYPES)
        .with_output_schema(
            schema_version="intelligence-finding-v1",
            analysis_trace_version=SECURITY_ANALYSIS_TRACE_VERSION,
        )
        .build()
    )


def _security_records(bundle: EvidenceBundle) -> tuple[SecurityEvidenceRecord, ...]:
    records = []
    for evidence in bundle.evidence:
        if not evidence_satisfies_contract(evidence, SECURITY_EVIDENCE_CONTRACT):
            continue
        payload = _payload(evidence)
        if not payload:
            continue
        records.append(SecurityEvidenceRecord(evidence=evidence, payload=payload))
    return tuple(sorted(records, key=lambda record: (_record_sort_key(record), record.evidence.id)))


def _contract_security(
    definition: EngineDefinition,
    bundle: EvidenceBundle,
    context: EngineContext,
    records: tuple[SecurityEvidenceRecord, ...],
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
        "contract_security",
        contexts,
        _CONTRACT_KEYS,
        confidence_basis="contract security is supported by persisted contract security observations",
    )


def _ownership_model(
    definition: EngineDefinition,
    bundle: EvidenceBundle,
    context: EngineContext,
    records: tuple[SecurityEvidenceRecord, ...],
) -> tuple[Finding, ...]:
    contexts = _contexts(
        records,
        context_type="ownership",
        keys=_OWNERSHIP_CONTEXT_KEYS,
        required_keys=_OWNERSHIP_EVIDENCE_KEYS,
    )
    return _context_findings(
        definition,
        bundle,
        context,
        "ownership_model",
        contexts,
        _OWNERSHIP_EVIDENCE_KEYS,
        confidence_basis="ownership model is supported by persisted ownership security observations",
    )


def _proxy_configuration(
    definition: EngineDefinition,
    bundle: EvidenceBundle,
    context: EngineContext,
    records: tuple[SecurityEvidenceRecord, ...],
) -> tuple[Finding, ...]:
    contexts = _contexts(records, context_type="proxy", keys=_PROXY_CONTEXT_KEYS, required_keys=_PROXY_EVIDENCE_KEYS)
    return _context_findings(
        definition,
        bundle,
        context,
        "proxy_configuration",
        contexts,
        _PROXY_EVIDENCE_KEYS,
        confidence_basis="proxy configuration is supported by persisted proxy or upgradeability observations",
    )


def _privileged_permissions(
    definition: EngineDefinition,
    bundle: EvidenceBundle,
    context: EngineContext,
    records: tuple[SecurityEvidenceRecord, ...],
) -> tuple[Finding, ...]:
    contexts = _contexts(
        records,
        context_type="privilege",
        keys=_PRIVILEGE_CONTEXT_KEYS,
        required_keys=_PRIVILEGE_EVIDENCE_KEYS,
    )
    return _context_findings(
        definition,
        bundle,
        context,
        "privileged_permissions",
        contexts,
        _PRIVILEGE_EVIDENCE_KEYS,
        confidence_basis="privileged permissions are supported by persisted role or permission observations",
    )


def _token_control_features(
    definition: EngineDefinition,
    bundle: EvidenceBundle,
    context: EngineContext,
    records: tuple[SecurityEvidenceRecord, ...],
) -> tuple[Finding, ...]:
    contexts = _contexts(records, context_type="token", keys=_TOKEN_CONTEXT_KEYS, required_keys=_TOKEN_CONTROL_KEYS)
    return _context_findings(
        definition,
        bundle,
        context,
        "token_control_features",
        contexts,
        _TOKEN_CONTROL_KEYS,
        confidence_basis="token control features are supported by persisted token security metadata",
    )


def _audit_observation(
    definition: EngineDefinition,
    bundle: EvidenceBundle,
    context: EngineContext,
    records: tuple[SecurityEvidenceRecord, ...],
) -> tuple[Finding, ...]:
    contexts = _contexts(records, context_type="audit", keys=_AUDIT_CONTEXT_KEYS, required_keys=_AUDIT_EVIDENCE_KEYS)
    return _context_findings(
        definition,
        bundle,
        context,
        "audit_observation",
        contexts,
        _AUDIT_EVIDENCE_KEYS,
        confidence_basis="audit observation is supported by persisted audit references or audit metadata",
    )


def _exploit_history(
    definition: EngineDefinition,
    bundle: EvidenceBundle,
    context: EngineContext,
    records: tuple[SecurityEvidenceRecord, ...],
) -> tuple[Finding, ...]:
    contexts = _contexts(
        records,
        context_type="exploit",
        keys=_EXPLOIT_CONTEXT_KEYS,
        required_keys=_EXPLOIT_EVIDENCE_KEYS,
    )
    return _context_findings(
        definition,
        bundle,
        context,
        "exploit_history",
        contexts,
        _EXPLOIT_EVIDENCE_KEYS,
        confidence_basis="exploit history is supported by persisted exploit observations",
    )


def _vulnerability_observation(
    definition: EngineDefinition,
    bundle: EvidenceBundle,
    context: EngineContext,
    records: tuple[SecurityEvidenceRecord, ...],
) -> tuple[Finding, ...]:
    contexts = _contexts(
        records,
        context_type="vulnerability",
        keys=_VULNERABILITY_CONTEXT_KEYS,
        required_keys=_VULNERABILITY_EVIDENCE_KEYS,
    )
    return _context_findings(
        definition,
        bundle,
        context,
        "vulnerability_observation",
        contexts,
        _VULNERABILITY_EVIDENCE_KEYS,
        confidence_basis="vulnerability observation is supported by persisted vulnerability observations",
    )


def _security_observation(
    definition: EngineDefinition,
    bundle: EvidenceBundle,
    context: EngineContext,
    records: tuple[SecurityEvidenceRecord, ...],
) -> tuple[Finding, ...]:
    contexts = _observation_contexts(records)
    return _context_findings(
        definition,
        bundle,
        context,
        "security_observation",
        contexts,
        _SECURITY_OBSERVATION_KEYS,
        confidence_basis="security observation is supported by persisted security evidence",
    )


def _context_findings(
    definition: EngineDefinition,
    bundle: EvidenceBundle,
    engine_context: EngineContext,
    finding_type: str,
    contexts: tuple[SecurityContext, ...],
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
            security_context,
            _observed_names(security_context.records, fields),
            confidence_basis=confidence_basis,
        )
        for security_context in contexts
    )


def _context_finding(
    definition: EngineDefinition,
    bundle: EvidenceBundle,
    engine_context: EngineContext,
    finding_type: str,
    security_context: SecurityContext,
    fields: tuple[str, ...],
    *,
    confidence_basis: str,
) -> Finding:
    explanation = (
        f"{finding_type} is descriptively evidenced within {security_context.context_type} "
        f"context {security_context.context_id} by persisted security fields: {', '.join(fields)}; "
        f"evidence content: {_content_fingerprint(security_context.records)}."
    )
    return _finding(
        definition,
        bundle,
        engine_context,
        finding_type,
        explanation,
        security_context.records,
        confidence_basis=confidence_basis,
    )


def _finding(
    definition: EngineDefinition,
    bundle: EvidenceBundle,
    context: EngineContext,
    finding_type: str,
    explanation: str,
    records: tuple[SecurityEvidenceRecord, ...],
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
    records: tuple[SecurityEvidenceRecord, ...],
    *,
    context_type: str,
    keys: tuple[str, ...],
    required_keys: tuple[str, ...],
) -> tuple[SecurityContext, ...]:
    grouped: dict[str, list[SecurityEvidenceRecord]] = {}
    for record in records:
        if not _has_any(record, required_keys):
            continue
        context_id = _context_id(record, context_type=context_type, keys=keys)
        if not context_id:
            continue
        grouped.setdefault(context_id, []).append(record)
    return _sorted_contexts(context_type, grouped)


def _observation_contexts(records: tuple[SecurityEvidenceRecord, ...]) -> tuple[SecurityContext, ...]:
    grouped: dict[tuple[str, str], list[SecurityEvidenceRecord]] = {}
    for record in records:
        if not _has_any(record, _SECURITY_OBSERVATION_EVIDENCE_KEYS):
            continue
        context = _primary_context(record)
        if context is None:
            continue
        grouped.setdefault((context.context_type, context.context_id), []).append(record)
    contexts = [
        SecurityContext(
            context_type=context_type,
            context_id=context_id,
            records=tuple(sorted(group_records, key=lambda item: (_record_sort_key(item), item.evidence.id))),
        )
        for (context_type, context_id), group_records in grouped.items()
    ]
    return tuple(sorted(contexts, key=lambda item: (item.context_type, item.context_id)))


def _primary_context(record: SecurityEvidenceRecord) -> SecurityContext | None:
    for context_type, keys in _PRIMARY_CONTEXT_PRECEDENCE:
        context_id = _context_id(record, context_type=context_type, keys=keys)
        if context_id:
            return SecurityContext(context_type=context_type, context_id=context_id, records=(record,))
    return None


def _sorted_contexts(
    context_type: str,
    grouped: dict[str, list[SecurityEvidenceRecord]],
) -> tuple[SecurityContext, ...]:
    contexts = [
        SecurityContext(
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


def _record_sort_key(record: SecurityEvidenceRecord) -> str:
    return "|".join(
        (
            _first_text(record, _CONTRACT_CONTEXT_KEYS),
            _first_text(record, _TOKEN_CONTEXT_KEYS),
            _first_text(record, _PROXY_CONTEXT_KEYS),
            _first_text(record, _OWNERSHIP_CONTEXT_KEYS),
            _first_text(record, _PRIVILEGE_CONTEXT_KEYS),
            _first_text(record, _AUDIT_CONTEXT_KEYS),
            _first_text(record, _EXPLOIT_CONTEXT_KEYS),
            _first_text(record, _VULNERABILITY_CONTEXT_KEYS),
            _text(record.payload.get("claim_id")),
            record.evidence.id,
        )
    )


def _first_text(record: SecurityEvidenceRecord, keys: tuple[str, ...]) -> str:
    for key in keys:
        value = _text(record.payload.get(key))
        if value:
            return value
    return ""


def _context_id(record: SecurityEvidenceRecord, *, context_type: str, keys: tuple[str, ...]) -> str:
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


def _first_normalized_identifier(record: SecurityEvidenceRecord, keys: tuple[str, ...]) -> str:
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


def _has_any(record: SecurityEvidenceRecord, keys: tuple[str, ...]) -> bool:
    return any(_present(record.payload.get(key)) for key in keys)


def _observed_names(
    records: tuple[SecurityEvidenceRecord, ...],
    keys: tuple[str, ...],
) -> tuple[str, ...]:
    names = set()
    for record in records:
        for key in keys:
            if _present(record.payload.get(key)):
                names.add(key)
    return tuple(sorted(names))


def _lineage(record: SecurityEvidenceRecord) -> tuple[str, ...]:
    values = (record.evidence.reference,)
    return tuple(sorted({value.strip() for value in values if value.strip()}))


def _conflicts(record: SecurityEvidenceRecord) -> tuple[str, ...]:
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


def _content_fingerprint(records: tuple[SecurityEvidenceRecord, ...]) -> str:
    return fingerprint(
        "security-finding-evidence",
        tuple((record.evidence.id, record.payload) for record in records),
    )


def _confidence(records: tuple[SecurityEvidenceRecord, ...]) -> float:
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


_CONTRACT_CONTEXT_KEYS = ("contract_address", "contract_id")
_TOKEN_CONTEXT_KEYS = ("token_address", "token_id", "contract_address", "contract_id")
_PROXY_CONTEXT_KEYS = ("proxy_address", "proxy_id")
_OWNERSHIP_CONTEXT_KEYS = ("ownership_id", "owner_address", "contract_address", "contract_id")
_PRIVILEGE_CONTEXT_KEYS = ("privilege_id", "role_id", "role_name")
_AUDIT_CONTEXT_KEYS = ("audit_id", "audit_reference")
_EXPLOIT_CONTEXT_KEYS = ("exploit_id", "incident_id")
_VULNERABILITY_CONTEXT_KEYS = ("vulnerability_id", "cve_id", "finding_reference")

_CONTRACT_KEYS = (
    "contract_address",
    "contract_id",
    "contract_verified",
    "verification_status",
    "source_code_verified",
    "security_provider",
    "goplus_security",
    "contract_security_observation",
)
_CONTRACT_EVIDENCE_KEYS = tuple(key for key in _CONTRACT_KEYS if key not in {"contract_address", "contract_id"})
_OWNERSHIP_KEYS = (
    "ownership_id",
    "owner_address",
    "ownership_status",
    "owner_type",
    "renounced_ownership",
    "admin_address",
)
_OWNERSHIP_EVIDENCE_KEYS = tuple(key for key in _OWNERSHIP_KEYS if key not in {"ownership_id", "owner_address"})
_PROXY_KEYS = (
    "proxy_address",
    "proxy_id",
    "proxy_status",
    "implementation_address",
    "upgradeability",
    "upgradeability_metadata",
)
_PROXY_EVIDENCE_KEYS = tuple(key for key in _PROXY_KEYS if key not in {"proxy_address", "proxy_id"})
_PRIVILEGE_KEYS = (
    "privilege_id",
    "role_id",
    "role_name",
    "role_holder",
    "privileged_role",
    "privileged_roles",
    "permission",
    "permissions",
)
_PRIVILEGE_EVIDENCE_KEYS = tuple(key for key in _PRIVILEGE_KEYS if key not in {"privilege_id", "role_id", "role_name"})
_TOKEN_CONTROL_KEYS = (
    "mint_capability",
    "blacklist_capability",
    "whitelist_capability",
    "pause_capability",
    "can_mint",
    "can_blacklist",
    "can_whitelist",
    "can_pause",
    "is_mintable",
    "is_blacklisted",
    "token_security_metadata",
)
_AUDIT_KEYS = (
    "audit_id",
    "audit_reference",
    "audit_url",
    "auditor",
    "audit_date",
    "audit_status",
    "audit_report",
)
_AUDIT_EVIDENCE_KEYS = tuple(key for key in _AUDIT_KEYS if key not in {"audit_id", "audit_reference"})
_EXPLOIT_KEYS = (
    "exploit_id",
    "incident_id",
    "exploit_reference",
    "exploit_date",
    "exploit_type",
    "exploit_status",
    "exploit_description",
)
_EXPLOIT_EVIDENCE_KEYS = tuple(key for key in _EXPLOIT_KEYS if key not in {"exploit_id", "incident_id"})
_VULNERABILITY_KEYS = (
    "vulnerability_id",
    "cve_id",
    "finding_reference",
    "vulnerability_reference",
    "vulnerability_status",
    "vulnerability_type",
    "vulnerability_observation",
)
_VULNERABILITY_EVIDENCE_KEYS = tuple(
    key for key in _VULNERABILITY_KEYS if key not in {"vulnerability_id", "cve_id", "finding_reference"}
)
_SECURITY_OBSERVATION_KEYS = tuple(
    sorted(
        {
            *_CONTRACT_KEYS,
            *_OWNERSHIP_KEYS,
            *_PROXY_KEYS,
            *_PRIVILEGE_KEYS,
            *_TOKEN_CONTROL_KEYS,
            *_AUDIT_KEYS,
            *_EXPLOIT_KEYS,
            *_VULNERABILITY_KEYS,
            "security_claim",
            "security_observation",
        }
    )
)
_SECURITY_CONTEXT_IDENTIFIER_KEYS = frozenset(
    {
        *_CONTRACT_CONTEXT_KEYS,
        *_TOKEN_CONTEXT_KEYS,
        *_PROXY_CONTEXT_KEYS,
        *_OWNERSHIP_CONTEXT_KEYS,
        *_PRIVILEGE_CONTEXT_KEYS,
        *_AUDIT_CONTEXT_KEYS,
        *_EXPLOIT_CONTEXT_KEYS,
        *_VULNERABILITY_CONTEXT_KEYS,
    }
)
_SECURITY_OBSERVATION_EVIDENCE_KEYS = tuple(
    key for key in _SECURITY_OBSERVATION_KEYS if key not in _SECURITY_CONTEXT_IDENTIFIER_KEYS
)
_PRIMARY_CONTEXT_PRECEDENCE = (
    ("vulnerability", _VULNERABILITY_CONTEXT_KEYS),
    ("exploit", _EXPLOIT_CONTEXT_KEYS),
    ("audit", _AUDIT_CONTEXT_KEYS),
    ("privilege", _PRIVILEGE_CONTEXT_KEYS),
    ("proxy", _PROXY_CONTEXT_KEYS),
    ("ownership", _OWNERSHIP_CONTEXT_KEYS),
    ("token", _TOKEN_CONTEXT_KEYS),
    ("contract", _CONTRACT_CONTEXT_KEYS),
)
_SYNONYM_CONTEXT_KEY_GROUPS = {
    "contract": (("contract_address", "contract_id"),),
    "proxy": (("proxy_address", "proxy_id"),),
    "ownership": (("ownership_id", "owner_address"),),
    "privilege": (("privilege_id", "role_id", "role_name"),),
    "audit": (("audit_id", "audit_reference"),),
    "exploit": (("exploit_id", "incident_id"),),
    "vulnerability": (("vulnerability_id", "cve_id", "finding_reference"),),
}
