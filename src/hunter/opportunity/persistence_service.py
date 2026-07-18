from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, fields, is_dataclass
from datetime import UTC, datetime
from hashlib import sha256
from typing import Any, cast

from hunter.execution.hashing import stable_fingerprint, stable_identifier
from hunter.execution.identity import fingerprint
from hunter.opportunity.authority import (
    CURRENT_OPPORTUNITY_FACTORS,
    OpportunityAssemblyResult,
    OpportunityFactorDiagnostic,
    opportunity_factor_authorities,
)
from hunter.opportunity.configuration import OpportunityConfig
from hunter.opportunity.engine import OpportunityEngine, opportunity_factor_trace
from hunter.opportunity.models import OpportunityAssessment
from hunter.persistence import AnalyticalRecord, AuthorizedAnalyticalWrite
from hunter.persistence.experimental import (
    EXPERIMENTAL_RECORD_PREFIX,
    ExperimentalAnalyticalRecordRepository,
    ExperimentalDerivedReasoningStore,
)
from hunter.persistence.sql.exceptions import AnalyticalCorrectionConflictError, AnalyticalWriteAuthorizationError

OPPORTUNITY_SNAPSHOT_SEMANTIC_TYPE = "experimental.opportunity-metric-snapshot"
OPPORTUNITY_ASSESSMENT_SEMANTIC_TYPE = "experimental.opportunity-assessment"
OPPORTUNITY_PERSISTENCE_SCHEMA_VERSION = "experimental-opportunity-persistence-v1"
OPPORTUNITY_FACTOR_AUTHORITY_VERSION = "experimental-opportunity-factor-authority-v1"


@dataclass(frozen=True, slots=True)
class OpportunityPersistenceContext:
    recorded_at: datetime
    model_version: str = "opportunity-entry-v1"
    methodology_fingerprint: str = "opportunity-entry-methodology-v1"

    def __post_init__(self) -> None:
        if self.recorded_at.tzinfo is None:
            raise ValueError("recorded_at must be timezone-aware")
        object.__setattr__(self, "recorded_at", self.recorded_at.astimezone(UTC))
        if not self.model_version.strip() or not self.methodology_fingerprint.strip():
            raise ValueError("model_version and methodology_fingerprint are required")


@dataclass(frozen=True, slots=True)
class AuthorizedOpportunityPersistencePlan:
    assembly: OpportunityAssemblyResult
    assessment: OpportunityAssessment
    snapshot_write: AuthorizedAnalyticalWrite
    assessment_write: AuthorizedAnalyticalWrite
    snapshot_canonical_hash: str
    factor_authority_fingerprint: str
    configuration_fingerprint: str

    def __post_init__(self) -> None:
        snapshot = self.snapshot_write.record
        assessment = self.assessment_write.record
        if snapshot.semantic_type != OPPORTUNITY_SNAPSHOT_SEMANTIC_TYPE:
            raise ValueError("plan requires an experimental Opportunity snapshot record")
        if assessment.semantic_type != OPPORTUNITY_ASSESSMENT_SEMANTIC_TYPE:
            raise ValueError("plan requires an experimental Opportunity assessment record")
        if assessment.payload.get("metric_snapshot_record_id") != snapshot.id:
            raise ValueError("assessment must link to its exact metric snapshot")
        if assessment.payload.get("metric_snapshot_canonical_hash") != self.snapshot_canonical_hash:
            raise ValueError("assessment snapshot hash linkage is invalid")


@dataclass(frozen=True, slots=True)
class OpportunityPersistenceResult:
    assembly: OpportunityAssemblyResult
    assessment: OpportunityAssessment
    snapshot_record_id: str
    assessment_record_id: str
    snapshot_canonical_hash: str


class ExperimentalOpportunityRepository:
    def __init__(self, repository: ExperimentalAnalyticalRecordRepository) -> None:
        self._repository = repository

    def persist(self, plan: AuthorizedOpportunityPersistencePlan) -> tuple[AnalyticalRecord, AnalyticalRecord]:
        snapshot = self._repository.persist(plan.snapshot_write)
        stored = self._repository.load(snapshot.id)
        if stored != snapshot:
            raise AnalyticalWriteAuthorizationError("exact authorized snapshot must exist before assessment")
        assessment = plan.assessment_write.record
        if (
            assessment.source_record_ids != (snapshot.id,)
            or assessment.source_versions != (snapshot.schema_version,)
            or assessment.payload.get("metric_snapshot_record_id") != snapshot.id
            or assessment.payload.get("metric_snapshot_canonical_hash") != plan.snapshot_canonical_hash
        ):
            raise AnalyticalWriteAuthorizationError("assessment is incompatible with its persisted snapshot")
        return snapshot, self._repository.persist(plan.assessment_write)

    def load(self, identity: str) -> AnalyticalRecord | None:
        record = self._repository.load(identity)
        if record is None or record.semantic_type not in {
            OPPORTUNITY_SNAPSHOT_SEMANTIC_TYPE,
            OPPORTUNITY_ASSESSMENT_SEMANTIC_TYPE,
        }:
            return None
        return record

    def target_history(self, semantic_type: str, project_id: str) -> tuple[AnalyticalRecord, ...]:
        if semantic_type not in {OPPORTUNITY_SNAPSHOT_SEMANTIC_TYPE, OPPORTUNITY_ASSESSMENT_SEMANTIC_TYPE}:
            raise ValueError("unsupported Opportunity semantic type")
        records = [
            record
            for record in self._repository.by_semantic_type(semantic_type)  # type: ignore[arg-type]
            if record.payload.get("project_id") == project_id
        ]
        records.sort(key=lambda record: (record.effective_at, record.recorded_at, record.id))
        return tuple(records)

    def lineage(self, logical_identity: str) -> tuple[AnalyticalRecord, ...]:
        records = self._repository.lineage(logical_identity)
        return tuple(
            record
            for record in records
            if record.semantic_type in {OPPORTUNITY_SNAPSHOT_SEMANTIC_TYPE, OPPORTUNITY_ASSESSMENT_SEMANTIC_TYPE}
        )

    def strict_known(
        self,
        semantic_type: str,
        project_id: str,
        *,
        effective_as_of: datetime,
        known_by: datetime,
        configuration_fingerprint: str,
        methodology_fingerprint: str,
        factor_authority_fingerprint: str,
    ) -> AnalyticalRecord | None:
        effective_cutoff = _aware("effective_as_of", effective_as_of)
        known_cutoff = _aware("known_by", known_by)
        compatible = [
            record
            for record in self.target_history(semantic_type, project_id)
            if record.effective_at <= effective_cutoff
            and record.recorded_at <= known_cutoff
            and record.known_at is not None
            and record.known_time_limitation is None
            and record.known_at <= known_cutoff
            and record.payload.get("configuration_fingerprint") == configuration_fingerprint
            and record.methodology_fingerprint == methodology_fingerprint
            and record.payload.get("factor_authority_fingerprint") == factor_authority_fingerprint
        ]
        if not compatible:
            return None
        superseded = {record.supersedes_id for record in compatible if record.supersedes_id is not None}
        current = [record for record in compatible if record.id not in superseded]
        current.sort(key=lambda record: (record.effective_at, record.recorded_at, record.id), reverse=True)
        return current[0] if current else None


class OpportunityPersistenceService:
    """Authorizes and persists experimental snapshot/assessment research records."""

    def __init__(self, config: OpportunityConfig | None = None) -> None:
        self.config = config or OpportunityConfig()
        self.engine = OpportunityEngine(self.config)

    def authorize(
        self,
        assembly: OpportunityAssemblyResult,
        context: OpportunityPersistenceContext,
        *,
        predecessor_snapshot: AnalyticalRecord | None = None,
        predecessor_assessment: AnalyticalRecord | None = None,
        correction_reason: str | None = None,
    ) -> AuthorizedOpportunityPersistencePlan:
        self._validate_assembly(assembly, context)
        correcting = predecessor_snapshot is not None or predecessor_assessment is not None
        if correcting and (predecessor_snapshot is None or predecessor_assessment is None):
            raise ValueError("correction requires both snapshot and assessment predecessors")
        if correcting and (not correction_reason or not correction_reason.strip()):
            raise ValueError("correction_reason is required")
        if not correcting and correction_reason is not None:
            raise ValueError("correction_reason requires explicit predecessors")

        authority_fingerprint = stable_fingerprint(
            "experimental-opportunity-factor-authority",
            opportunity_factor_authorities(),
            schema_version=OPPORTUNITY_FACTOR_AUTHORITY_VERSION,
        )
        configuration_fingerprint = fingerprint("opportunity-configuration", _plain(self.config))
        engine_fingerprint = stable_fingerprint(
            "experimental-opportunity-engine",
            {"engine": "OpportunityEngine", "assessment_identity_schema": "opportunity-entry-v1"},
            schema_version=OPPORTUNITY_PERSISTENCE_SCHEMA_VERSION,
        )
        assembly_json = assembly.to_json()
        snapshot_hash = sha256(assembly_json.encode()).hexdigest()
        source_ids, source_versions, evidence = _assembly_sources(assembly)
        snapshot_payload = {
            "authority_classification": "experimental",
            "configuration_fingerprint": configuration_fingerprint,
            "factor_authority_fingerprint": authority_fingerprint,
            "factor_authority_version": OPPORTUNITY_FACTOR_AUTHORITY_VERSION,
            "identity_schema_version": OPPORTUNITY_PERSISTENCE_SCHEMA_VERSION,
            "engine_fingerprint": engine_fingerprint,
            "input_canonical_hash": snapshot_hash,
            "methodology_fingerprint": context.methodology_fingerprint,
            "model_version": context.model_version,
            "opportunity_snapshot": {
                "assembly": assembly.as_dict(),
                "factors": {item.factor: item.as_dict() for item in assembly.diagnostics},
            },
            "project_id": assembly.snapshot.project_id,
            "requested_effective_as_of": assembly.effective_as_of.isoformat(),
            "requested_known_by": assembly.known_by.isoformat(),
        }
        snapshot_logical = f"{OPPORTUNITY_SNAPSHOT_SEMANTIC_TYPE}:{assembly.snapshot.project_id}"
        self._validate_predecessor(predecessor_snapshot, OPPORTUNITY_SNAPSHOT_SEMANTIC_TYPE, snapshot_logical)
        snapshot_id = stable_identifier(
            EXPERIMENTAL_RECORD_PREFIX,
            {
                "canonical_hash": snapshot_hash,
                "configuration_fingerprint": configuration_fingerprint,
                "factor_authority_fingerprint": authority_fingerprint,
                "project_id": assembly.snapshot.project_id,
                "recorded_at": context.recorded_at,
                "semantic_type": OPPORTUNITY_SNAPSHOT_SEMANTIC_TYPE,
                "supersedes_id": predecessor_snapshot.id if predecessor_snapshot else None,
            },
            schema_version=OPPORTUNITY_PERSISTENCE_SCHEMA_VERSION,
        )
        snapshot_record = AnalyticalRecord(
            id=snapshot_id,
            schema_version=OPPORTUNITY_PERSISTENCE_SCHEMA_VERSION,
            created_at=context.recorded_at,
            effective_at=assembly.effective_as_of,
            logical_identity=snapshot_logical,
            semantic_type=OPPORTUNITY_SNAPSHOT_SEMANTIC_TYPE,
            known_at=assembly.known_by,
            known_time_limitation=None,
            model_version=context.model_version,
            methodology_fingerprint=context.methodology_fingerprint,
            source_record_ids=source_ids,
            source_versions=source_versions,
            evidence_references=evidence,
            confidence=None,
            missing_evidence=assembly.snapshot.missing_evidence,
            supersedes_id=predecessor_snapshot.id if predecessor_snapshot else None,
            correction_reason=correction_reason,
            payload=snapshot_payload,
        )

        assessment = self.engine.evaluate(assembly.snapshot)
        trace = opportunity_factor_trace(assembly.snapshot, self.config)
        assessment_payload = {
            "authority_classification": "experimental",
            "configuration_fingerprint": configuration_fingerprint,
            "factor_authority_fingerprint": authority_fingerprint,
            "factor_authority_version": OPPORTUNITY_FACTOR_AUTHORITY_VERSION,
            "identity_schema_version": OPPORTUNITY_PERSISTENCE_SCHEMA_VERSION,
            "engine_fingerprint": engine_fingerprint,
            "methodology_fingerprint": context.methodology_fingerprint,
            "metric_snapshot_canonical_hash": snapshot_hash,
            "metric_snapshot_record_id": snapshot_record.id,
            "model_version": context.model_version,
            "opportunity_assessment": _plain(assessment),
            "per_factor_trace": _assessment_trace(trace, assembly.diagnostics, len(self.config.factor_weights)),
            "project_id": assessment.project_id,
            "requested_effective_as_of": assembly.effective_as_of.isoformat(),
            "requested_known_by": assembly.known_by.isoformat(),
        }
        assessment_logical = f"{OPPORTUNITY_ASSESSMENT_SEMANTIC_TYPE}:{assessment.project_id}"
        self._validate_predecessor(
            predecessor_assessment,
            OPPORTUNITY_ASSESSMENT_SEMANTIC_TYPE,
            assessment_logical,
        )
        assessment_id = stable_identifier(
            EXPERIMENTAL_RECORD_PREFIX,
            {
                "configuration_fingerprint": configuration_fingerprint,
                "factor_authority_fingerprint": authority_fingerprint,
                "native_assessment": _plain(assessment),
                "project_id": assessment.project_id,
                "recorded_at": context.recorded_at,
                "semantic_type": OPPORTUNITY_ASSESSMENT_SEMANTIC_TYPE,
                "snapshot_record_id": snapshot_record.id,
                "supersedes_id": predecessor_assessment.id if predecessor_assessment else None,
            },
            schema_version=OPPORTUNITY_PERSISTENCE_SCHEMA_VERSION,
        )
        assessment_record = AnalyticalRecord(
            id=assessment_id,
            schema_version=OPPORTUNITY_PERSISTENCE_SCHEMA_VERSION,
            created_at=context.recorded_at,
            effective_at=assessment.effective_at,
            logical_identity=assessment_logical,
            semantic_type=OPPORTUNITY_ASSESSMENT_SEMANTIC_TYPE,
            known_at=assembly.known_by,
            known_time_limitation=None,
            model_version=context.model_version,
            methodology_fingerprint=context.methodology_fingerprint,
            source_record_ids=(snapshot_record.id,),
            source_versions=(snapshot_record.schema_version,),
            evidence_references=assessment.supporting_evidence,
            confidence=assessment.confidence["score"],
            missing_evidence=assessment.missing_evidence,
            supersedes_id=predecessor_assessment.id if predecessor_assessment else None,
            correction_reason=correction_reason,
            payload=assessment_payload,
        )
        operation = "correct" if correcting else "create"
        return AuthorizedOpportunityPersistencePlan(
            assembly,
            assessment,
            AuthorizedAnalyticalWrite(snapshot_record, operation),
            AuthorizedAnalyticalWrite(assessment_record, operation),
            snapshot_hash,
            authority_fingerprint,
            configuration_fingerprint,
        )

    def execute(
        self,
        assembly: OpportunityAssemblyResult,
        store: ExperimentalDerivedReasoningStore,
        context: OpportunityPersistenceContext,
        **correction: Any,
    ) -> OpportunityPersistenceResult:
        plan = self.authorize(assembly, context, **correction)
        with store.repository() as base_repository:
            snapshot, assessment = ExperimentalOpportunityRepository(base_repository).persist(plan)
        return OpportunityPersistenceResult(
            assembly,
            plan.assessment,
            snapshot.id,
            assessment.id,
            plan.snapshot_canonical_hash,
        )

    def _validate_assembly(self, assembly: OpportunityAssemblyResult, context: OpportunityPersistenceContext) -> None:
        if assembly.authority_classification != "experimental":
            raise ValueError("only Phase 3.1 experimental assembly results are accepted")
        factors = tuple(item.factor for item in assembly.diagnostics)
        if len(factors) != len(CURRENT_OPPORTUNITY_FACTORS) or set(factors) != set(CURRENT_OPPORTUNITY_FACTORS):
            raise ValueError("assembly must contain exactly one diagnostic for every current factor")
        if assembly.snapshot.effective_at != assembly.effective_as_of:
            raise ValueError("snapshot effective time must match the requested effective cutoff")
        if context.recorded_at < assembly.known_by:
            raise ValueError("recorded_at cannot precede the assembly known-by cutoff")
        expected_config = stable_fingerprint(
            "experimental-opportunity-input-assembly",
            self.config,
            schema_version="opportunity-input-authority-v1",
        )
        if assembly.configuration_fingerprint != expected_config:
            raise ValueError("assembly configuration is incompatible with the persistence service")
        metadata = assembly.snapshot.metadata.as_dict()
        if (
            metadata.get("authority_classification") != "experimental"
            or metadata.get("strict_known") is not True
            or metadata.get("configuration_fingerprint") != expected_config
            or metadata.get("known_by") != assembly.known_by.isoformat()
        ):
            raise ValueError("snapshot metadata is not a Phase 3.1 strict-known assembly")
        diagnostics = {item.factor: item for item in assembly.diagnostics}
        values = assembly.snapshot.values.as_dict()
        expected_values = {
            item.factor: item.value
            for item in assembly.diagnostics
            if item.state == "available" and item.value is not None
        }
        if diagnostics["validation_health"].state != "available":
            expected_values["validation_health"] = 0.0
        if values != expected_values:
            raise ValueError("snapshot values do not match the Phase 3.1 authority diagnostics")
        expected_missing = tuple(sorted(item.factor for item in assembly.diagnostics if item.state != "available"))
        if assembly.snapshot.missing_evidence != expected_missing:
            raise ValueError("snapshot missingness does not match the Phase 3.1 authority diagnostics")
        expected_evidence = tuple(
            sorted(
                {
                    reference
                    for item in assembly.diagnostics
                    if item.state == "available"
                    for reference in item.evidence_references
                }
            )
        )
        if assembly.snapshot.evidence_ids != expected_evidence:
            raise ValueError("snapshot evidence does not match the Phase 3.1 authority diagnostics")
        for authority in opportunity_factor_authorities():
            item = diagnostics[authority.factor]
            if authority.status == "unowned" and (
                item.state != "missing"
                or item.value is not None
                or item.record_id is not None
                or item.source_record_ids
                or item.evidence_references
            ):
                raise ValueError(f"unowned factor {authority.factor} must remain missing without provenance")
            if item.state == "available" and (
                item.value is None
                or item.record_id is None
                or item.record_version is None
                or item.effective_at is None
                or item.recorded_at is None
                or item.known_at is None
                or item.effective_at > assembly.effective_as_of
                or item.recorded_at > assembly.known_by
                or item.known_at > assembly.known_by
            ):
                raise ValueError(f"available factor {authority.factor} lacks strict-known canonical provenance")

    @staticmethod
    def _validate_predecessor(
        predecessor: AnalyticalRecord | None,
        semantic_type: str,
        logical_identity: str,
    ) -> None:
        if predecessor is not None and (
            predecessor.semantic_type != semantic_type or predecessor.logical_identity != logical_identity
        ):
            raise AnalyticalCorrectionConflictError("Opportunity correction predecessor is incompatible")


def _assembly_sources(assembly: OpportunityAssemblyResult) -> tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...]]:
    versions: dict[str, str] = {}
    evidence = set()
    for diagnostic in assembly.diagnostics:
        for source_id, source_version in zip(
            diagnostic.source_record_ids,
            diagnostic.source_versions,
            strict=True,
        ):
            if source_id in versions and versions[source_id] != source_version:
                raise ValueError("one source identity cannot have conflicting versions")
            versions[source_id] = source_version
        evidence.update(diagnostic.evidence_references)
    source_ids = tuple(sorted(versions))
    return source_ids, tuple(versions[source_id] for source_id in source_ids), tuple(sorted(evidence))


def _assessment_trace(
    trace: tuple[object, ...],
    diagnostics: tuple[OpportunityFactorDiagnostic, ...],
    weighted_count: int,
) -> list[dict[str, Any]]:
    by_factor = {item.factor: item for item in diagnostics}
    persisted = []
    for index, factor in enumerate(trace):
        native = cast(dict[str, Any], _plain(factor))
        diagnostic = by_factor[str(native["name"])]
        persisted.append(
            {
                **native,
                "calculation_role": "weighted_factor" if index < weighted_count else "penalty",
                "authority_state": diagnostic.state,
                "missing": diagnostic.state != "available",
                "diagnostic_reason": diagnostic.reason,
                "confidence": diagnostic.confidence,
                "source_record_ids": list(diagnostic.source_record_ids),
                "source_versions": list(diagnostic.source_versions),
                "evidence_references": list(diagnostic.evidence_references),
            }
        )
    return persisted


def _plain(value: object) -> Any:
    if is_dataclass(value) and not isinstance(value, type):
        return {field.name: _plain(getattr(value, field.name)) for field in fields(value)}
    if isinstance(value, datetime):
        return value.astimezone(UTC).isoformat()
    if isinstance(value, Mapping):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, tuple | list):
        return [_plain(item) for item in value]
    if value is None or isinstance(value, str | int | float | bool):
        return value
    raise TypeError(f"unsupported Opportunity persistence payload value: {type(value).__name__}")


def _aware(name: str, value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError(f"{name} must be timezone-aware")
    return value.astimezone(UTC)
