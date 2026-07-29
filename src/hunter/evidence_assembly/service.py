from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from hunter.evidence_assembly.models import (
    ASSEMBLED_EVIDENCE_SCHEMA_VERSION,
    ASSEMBLY_RULE_VERSION,
    DETERMINISTIC_ORDER,
    AssembledFundamentalEvidenceRecord,
    AssemblyConflictRecord,
    AssemblyConstituent,
    EvidenceShape,
)
from hunter.evidence_assembly.registry import (
    EvidenceShapeRegistryAuthority,
    EvidenceShapeRegistryError,
    EvidenceShapeRegistrySnapshot,
)
from hunter.evidence_assembly.repository import AssembledEvidenceRepository
from hunter.valuation_methodology.models import ValuationMethodologySnapshot
from hunter.valuation_methodology.service import (
    CanonicalValuationMethodologyAuthority,
    verify_methodology_content_hash,
)
from hunter.value_capture.models import (
    FundamentalEvidenceRecord,
    SupplyBasisSnapshot,
    ValueCaptureRuleSnapshot,
)
from hunter.value_capture.repository import SupplyAndValueCaptureRepository
from hunter.value_capture.service import verify_value_capture_content_hash


class CanonicalEvidenceAssemblyError(ValueError):
    pass


def _replay_lossless_exact_sum(records: tuple[FundamentalEvidenceRecord, ...]) -> Decimal:
    return sum((Decimal(record.amount or "") for record in records), Decimal(0))


_ASSEMBLY_RULE_REPLAYERS = {
    "lossless-exact-coverage-v1": _replay_lossless_exact_sum,
}


class CanonicalEvidenceAssemblyService:
    """ADR 0025 construction, validation, conflict, and persistence-eligibility owner."""

    def __init__(
        self,
        *,
        repository: AssembledEvidenceRepository,
        value_capture_repository: SupplyAndValueCaptureRepository,
        methodology_authority: CanonicalValuationMethodologyAuthority,
        registry_authority: EvidenceShapeRegistryAuthority,
    ) -> None:
        self.repository = repository
        self.value_capture_repository = value_capture_repository
        self.methodology_authority = methodology_authority
        self.registry_authority = registry_authority

    def assemble(
        self,
        *,
        constituents: tuple[AssemblyConstituent, ...],
        registry_id: str,
        registry_version: str,
        accounting_window_start: datetime,
        accounting_window_end: datetime,
        recorded_at: datetime,
        replay_cutoff: datetime,
        semantic_version: str = "1.0.0",
        supersedes_record_id: str | None = None,
        correction_reason: str = "",
    ) -> AssembledFundamentalEvidenceRecord:
        start, end, recorded_at, replay_cutoff = (
            _aware(accounting_window_start),
            _aware(accounting_window_end),
            _aware(recorded_at),
            _aware(replay_cutoff),
        )
        if len(constituents) < 2:
            raise CanonicalEvidenceAssemblyError("assembly requires at least two constituent records")
        if recorded_at > replay_cutoff:
            raise CanonicalEvidenceAssemblyError("assembly is not known at replay cutoff")

        methodology = self.methodology_authority.strict_known_methodology(
            effective_as_of=end,
            known_by=replay_cutoff,
        )
        if methodology is None:
            raise CanonicalEvidenceAssemblyError("no strict-known canonical methodology snapshot")
        if not verify_methodology_content_hash(methodology):
            raise CanonicalEvidenceAssemblyError("canonical methodology content hash is invalid")
        self._validate_methodology(methodology)
        if end - start != timedelta(days=methodology.horizon_days):
            raise CanonicalEvidenceAssemblyError("accounting window does not match canonical methodology horizon")
        registry = self.registry_authority.strict_known_registry(
            registry_id=registry_id,
            registry_version=registry_version,
            effective_as_of=end,
            known_by=replay_cutoff,
        )
        if registry is None:
            raise CanonicalEvidenceAssemblyError("no exact strict-known Evidence Shape Registry snapshot")
        if methodology.known_at > recorded_at or registry.known_at > recorded_at:
            raise CanonicalEvidenceAssemblyError("assembly recorded_at precedes canonical reference knowledge")

        resolved = tuple(
            self._resolve_constituent(item, registry=registry, replay_cutoff=replay_cutoff) for item in constituents
        )
        resolved = tuple(sorted(resolved, key=lambda item: (item[0].accounting_period_start, item[0].record_id)))
        resolved = self._deduplicate_identical(resolved)
        self._validate_scope(resolved)
        if any(supply.supply_basis_type != methodology.supply_basis_selection_rule for _, _, supply, _ in resolved):
            raise CanonicalEvidenceAssemblyError("canonical methodology supply-basis selection mismatch")
        if any(dependency.known_at > recorded_at for _, _, supply, rule in resolved for dependency in (supply, rule)):
            raise CanonicalEvidenceAssemblyError("assembly recorded_at precedes pathway or supply-basis knowledge")
        self._validate_coverage(resolved, start=start, end=end)
        if any(rule.applicability_start > start or rule.applicability_end < end for _, _, _, rule in resolved):
            raise CanonicalEvidenceAssemblyError("value-capture pathway does not cover assembled interval")
        self._validate_methodology_shapes(methodology, tuple(shape for _, shape, _, _ in resolved))
        selected_cadence = self._selected_cadence(registry, tuple(shape for _, shape, _, _ in resolved), methodology)
        request_id = _hash(
            {
                "constituents": [item.evidence_record_id for item in constituents],
                "registry_id": registry_id,
                "registry_version": registry_version,
                "start": start.isoformat(),
                "end": end.isoformat(),
                "cutoff": replay_cutoff.isoformat(),
            }
        )
        self._validate_candidate_universe(
            resolved,
            registry=registry,
            methodology=methodology,
            selected_cadence=selected_cadence,
            start=start,
            end=end,
            recorded_at=recorded_at,
            replay_cutoff=replay_cutoff,
            request_id=request_id,
        )

        records = tuple(item[0] for item in resolved)
        shapes = tuple(item[1] for item in resolved)
        supplies = tuple(item[2] for item in resolved)
        rules = tuple(item[3] for item in resolved)
        if any(record.known_at > recorded_at for record in records):
            raise CanonicalEvidenceAssemblyError("assembly recorded_at precedes constituent knowledge")
        known_at = max(recorded_at, *(r.known_at for r in records))
        if known_at > replay_cutoff:
            raise CanonicalEvidenceAssemblyError("assembled evidence is not known by replay cutoff")
        amount = _ASSEMBLY_RULE_REPLAYERS[ASSEMBLY_RULE_VERSION](records)
        identity = records[0].identity
        logical_digest = _hash(
            {
                "identity": asdict(identity),
                "start": start.isoformat(),
                "end": end.isoformat(),
                "pathway": rules[0].logical_id,
                "supply": supplies[0].logical_id,
                "methodology": methodology.logical_id,
            }
        )
        logical_id = f"assembled-evidence:{logical_digest}"
        assembly_basis = {
            "rule": ASSEMBLY_RULE_VERSION,
            "registry_record_id": registry.record_id,
            "methodology_record_id": methodology.record_id,
            "supply_basis": {
                "record_id": supplies[0].record_id,
                "version": supplies[0].semantic_version,
                "content_hash": supplies[0].content_hash,
            },
            "value_capture_pathway": {
                "record_id": rules[0].record_id,
                "version": rules[0].semantic_version,
                "content_hash": rules[0].content_hash,
            },
            "constituents": [
                {
                    "record_id": record.record_id,
                    "version": record.semantic_version,
                    "content_hash": record.content_hash,
                    "shape_id": shape.shape_id,
                }
                for record, shape, _, _ in resolved
            ],
        }
        assembly_hash = _hash(assembly_basis)
        pending = AssembledFundamentalEvidenceRecord(
            record_id=f"assembled-evidence:{assembly_hash}",
            logical_id=logical_id,
            schema_version=ASSEMBLED_EVIDENCE_SCHEMA_VERSION,
            semantic_version=semantic_version,
            assembly_rule_version=ASSEMBLY_RULE_VERSION,
            registry_record_id=registry.record_id,
            registry_id=registry.registry_id,
            registry_version=registry.registry_version,
            registry_content_hash=registry.content_hash,
            methodology_record_id=methodology.record_id,
            methodology_logical_id=methodology.logical_id,
            methodology_version=methodology.semantic_version,
            methodology_content_hash=methodology.content_hash,
            identity=identity,
            value_capture_pathway_record_id=rules[0].record_id,
            supply_basis_record_id=supplies[0].record_id,
            supply_basis_version=supplies[0].semantic_version,
            supply_basis_content_hash=supplies[0].content_hash,
            value_capture_pathway_version=rules[0].semantic_version,
            value_capture_pathway_content_hash=rules[0].content_hash,
            currency=methodology.currency,
            unit=records[0].unit or "",
            accounting_meaning=shapes[0].accounting_meaning,
            cadence=shapes[0].cadence,
            accounting_window_start=start,
            accounting_window_end=end,
            accounting_period_days=(end - start).days,
            amount=str(amount),
            constituent_record_ids=tuple(record.record_id for record in records),
            constituent_logical_ids=tuple(record.logical_id for record in records),
            constituent_versions=tuple(record.semantic_version for record in records),
            constituent_content_hashes=tuple(record.content_hash for record in records),
            constituent_source_ids=tuple(record.source_id for record in records),
            constituent_source_references=tuple(record.source_reference for record in records),
            constituent_shape_ids=tuple(shape.shape_id for shape in shapes),
            deterministic_constituent_order=DETERMINISTIC_ORDER,
            source_count=len(records),
            assembly_content_hash=assembly_hash,
            aggregation_lineage="lossless exact decimal sum in deterministic accounting-period order",
            evidence_marker="assembled",
            effective_at=end,
            recorded_at=recorded_at,
            known_at=known_at,
            quality_state="accepted",
            confidence_state=str(min(Decimal(record.evidence_confidence) for record in records)),
            conflict_state="none",
            completeness_state="exact_complete",
            continuity_proof_state="same-canonical-identity-pathway-and-supply-basis",
            non_overlap_proof_state="gap_free_non_overlapping",
            supersedes_record_id=supersedes_record_id,
            correction_reason=correction_reason,
            content_hash="pending",
        )
        record = replace(pending, content_hash=_hash(asdict(pending) | {"content_hash": ""}))
        self._authorize_correction(record, replay_cutoff=replay_cutoff)
        return self.repository._insert_authorized(record)

    def strict_known(
        self, *, logical_id: str, effective_as_of: datetime, known_by: datetime
    ) -> AssembledFundamentalEvidenceRecord | None:
        eligible = self.repository.strict_known_candidates(
            logical_id=logical_id, effective_as_of=effective_as_of, known_by=known_by
        )
        superseded = {record.supersedes_record_id for record in eligible if record.supersedes_record_id}
        tips = [record for record in eligible if record.record_id not in superseded]
        if not tips:
            return None
        record = tips[0]
        methodology = self.methodology_authority.get(record.methodology_record_id)
        registry = self.registry_authority.repository.get(record.registry_record_id)
        if (
            methodology is None
            or not verify_methodology_content_hash(methodology)
            or methodology.logical_id != record.methodology_logical_id
            or methodology.semantic_version != record.methodology_version
            or methodology.content_hash != record.methodology_content_hash
            or methodology.effective_at > record.effective_at
            or methodology.recorded_at > known_by
            or methodology.known_at > known_by
            or registry is None
            or registry.registry_id != record.registry_id
            or registry.registry_version != record.registry_version
            or registry.content_hash != record.registry_content_hash
            or registry.effective_start > record.effective_at
            or not record.effective_at < registry.effective_end
            or registry.recorded_at > known_by
            or registry.known_at > known_by
        ):
            raise CanonicalEvidenceAssemblyError("assembled replay provenance is unavailable or divergent")
        constituents: list[FundamentalEvidenceRecord] = []
        shapes: list[EvidenceShape] = []
        for record_id, version, content_hash, shape_id in zip(
            record.constituent_record_ids,
            record.constituent_versions,
            record.constituent_content_hashes,
            record.constituent_shape_ids,
            strict=True,
        ):
            constituent = self.value_capture_repository.evidence(record_id)
            if (
                constituent is None
                or not verify_value_capture_content_hash(constituent)
                or constituent.semantic_version != version
                or constituent.content_hash != content_hash
                or constituent.recorded_at > known_by
                or constituent.known_at > known_by
            ):
                raise CanonicalEvidenceAssemblyError("assembled replay constituent provenance is unavailable")
            constituents.append(constituent)
            shapes.append(registry.shape(shape_id))
        supply = self.value_capture_repository.supply(record.supply_basis_record_id)
        rule = self.value_capture_repository.rule(record.value_capture_pathway_record_id)
        if (
            supply is None
            or not verify_value_capture_content_hash(supply)
            or supply.semantic_version != record.supply_basis_version
            or supply.content_hash != record.supply_basis_content_hash
            or supply.recorded_at > known_by
            or supply.known_at > known_by
            or rule is None
            or not verify_value_capture_content_hash(rule)
            or rule.semantic_version != record.value_capture_pathway_version
            or rule.content_hash != record.value_capture_pathway_content_hash
            or rule.recorded_at > known_by
            or rule.known_at > known_by
        ):
            raise CanonicalEvidenceAssemblyError("assembled replay pathway or supply provenance is unavailable")
        expected_order = tuple(
            item.record_id
            for item in sorted(
                constituents,
                key=lambda item: (item.accounting_period_start, item.record_id),
            )
        )
        assembly_basis = {
            "rule": record.assembly_rule_version,
            "registry_record_id": registry.record_id,
            "methodology_record_id": methodology.record_id,
            "supply_basis": {
                "record_id": supply.record_id,
                "version": supply.semantic_version,
                "content_hash": supply.content_hash,
            },
            "value_capture_pathway": {
                "record_id": rule.record_id,
                "version": rule.semantic_version,
                "content_hash": rule.content_hash,
            },
            "constituents": [
                {
                    "record_id": constituent.record_id,
                    "version": constituent.semantic_version,
                    "content_hash": constituent.content_hash,
                    "shape_id": shape.shape_id,
                }
                for constituent, shape in zip(constituents, shapes, strict=True)
            ],
        }
        if (
            record.assembly_rule_version not in _ASSEMBLY_RULE_REPLAYERS
            or record.deterministic_constituent_order != DETERMINISTIC_ORDER
            or record.constituent_record_ids != expected_order
            or record.assembly_content_hash != _hash(assembly_basis)
            or record.record_id != f"assembled-evidence:{record.assembly_content_hash}"
            or Decimal(record.amount) != _ASSEMBLY_RULE_REPLAYERS[record.assembly_rule_version](tuple(constituents))
            or record.known_at != max(record.recorded_at, *(item.known_at for item in constituents))
        ):
            raise CanonicalEvidenceAssemblyError("assembled replay reconstruction diverges")
        return record

    def _validate_methodology(self, methodology: ValuationMethodologySnapshot) -> None:
        if not methodology.accepts_assembled_evidence:
            raise CanonicalEvidenceAssemblyError("canonical methodology is not opted into assembled evidence")
        if ASSEMBLY_RULE_VERSION not in methodology.accepted_assembly_rule_versions:
            raise CanonicalEvidenceAssemblyError("canonical methodology rejects assembly rule")
        if not (
            methodology.assembly_requires_exact_coverage
            and methodology.assembly_requires_provenance_hashes
            and methodology.assembly_strict_known_required
            and methodology.assembly_requires_entity_identity
            and methodology.assembly_requires_representation_identity
            and methodology.assembly_requires_unit_compatibility
            and methodology.assembly_requires_pathway_compatibility
            and methodology.assembly_requires_supply_basis_compatibility
        ):
            raise CanonicalEvidenceAssemblyError("canonical methodology omits mandatory assembly requirements")
        if (
            methodology.assembly_allows_boundary_crossing
            or methodology.assembly_conflict_policy != "reject"
            or methodology.assembly_minimum_quality_state != "accepted"
            or methodology.assembly_missingness_behavior != "unavailable"
        ):
            raise CanonicalEvidenceAssemblyError("canonical methodology assembly policy is not fail closed")

    def _validate_methodology_shapes(
        self, methodology: ValuationMethodologySnapshot, shapes: tuple[EvidenceShape, ...]
    ) -> None:
        if any(shape.shape_id not in methodology.accepted_evidence_shape_ids for shape in shapes):
            raise CanonicalEvidenceAssemblyError("canonical methodology rejects evidence shape")
        if any(shape.currency.casefold() != methodology.currency.casefold() for shape in shapes):
            raise CanonicalEvidenceAssemblyError("canonical methodology currency is incompatible with evidence")

    def _resolve_constituent(
        self,
        constituent: AssemblyConstituent,
        *,
        registry: EvidenceShapeRegistrySnapshot,
        replay_cutoff: datetime,
    ) -> tuple[FundamentalEvidenceRecord, EvidenceShape, SupplyBasisSnapshot, ValueCaptureRuleSnapshot]:
        record = self.value_capture_repository.evidence(constituent.evidence_record_id)
        supply = self.value_capture_repository.supply(constituent.supply_basis_record_id)
        rule = self.value_capture_repository.rule(constituent.pathway_rule_record_id)
        if record is None or supply is None or rule is None:
            raise CanonicalEvidenceAssemblyError("constituent canonical dependency is missing")
        for dependency in (record, supply, rule):
            if not verify_value_capture_content_hash(dependency):
                raise CanonicalEvidenceAssemblyError("canonical dependency content hash is invalid")
            if (
                dependency.effective_at > replay_cutoff
                or dependency.recorded_at > replay_cutoff
                or dependency.known_at > replay_cutoff
            ):
                raise CanonicalEvidenceAssemblyError("constituent dependency is not strict-known")
            if dependency.quality_state != "accepted" or dependency.conflict_state not in {"none", "resolved"}:
                raise CanonicalEvidenceAssemblyError("constituent dependency is unresolved or non-authoritative")
        shape = self._shape_for_record(registry, record)
        if shape.composition_operation != "exact_sum" or shape.accounting_meaning != "period_specific":
            raise CanonicalEvidenceAssemblyError("shape is not governed for lossless exact summation")
        if record.identity != supply.identity or record.identity != rule.identity:
            raise CanonicalEvidenceAssemblyError("canonical dependency identity mismatch")
        if record.evidence_type != shape.evidence_type:
            raise CanonicalEvidenceAssemblyError("registry evidence classification mismatch")
        if record.unit != shape.unit:
            raise CanonicalEvidenceAssemblyError("registry unit or currency classification mismatch")
        if record.attribution_rule_id != rule.mechanism_policy_id:
            raise CanonicalEvidenceAssemblyError("canonical pathway classification mismatch")
        _validate_cadence_window(shape, record)
        if not record.content_hash or not supply.content_hash or not rule.content_hash:
            raise CanonicalEvidenceAssemblyError("canonical dependency content hash is missing")
        return record, shape, supply, rule

    def _validate_scope(
        self,
        resolved: tuple[
            tuple[FundamentalEvidenceRecord, EvidenceShape, SupplyBasisSnapshot, ValueCaptureRuleSnapshot], ...
        ],
    ) -> None:
        first_record, first_shape, first_supply, first_rule = resolved[0]
        for record, shape, supply, rule in resolved[1:]:
            if record.identity != first_record.identity:
                raise CanonicalEvidenceAssemblyError("constituent identity or representation mismatch")
            if supply.record_id != first_supply.record_id:
                raise CanonicalEvidenceAssemblyError("supply-basis boundary crossing is not governed")
            if rule.record_id != first_rule.record_id:
                raise CanonicalEvidenceAssemblyError("pathway boundary crossing is not governed")
            if record.unit != first_record.unit:
                raise CanonicalEvidenceAssemblyError("constituent unit mismatch")
            if shape.accounting_meaning != resolved[0][1].accounting_meaning:
                raise CanonicalEvidenceAssemblyError("accounting meaning mismatch")
            if shape.shape_id != first_shape.shape_id and (
                shape.shape_id not in first_shape.compatible_shape_ids
                or first_shape.shape_id not in shape.compatible_shape_ids
            ):
                raise CanonicalEvidenceAssemblyError("registry shape continuity relation is absent")

    def _deduplicate_identical(
        self,
        resolved: tuple[
            tuple[FundamentalEvidenceRecord, EvidenceShape, SupplyBasisSnapshot, ValueCaptureRuleSnapshot], ...
        ],
    ) -> tuple[tuple[FundamentalEvidenceRecord, EvidenceShape, SupplyBasisSnapshot, ValueCaptureRuleSnapshot], ...]:
        result: list[tuple[FundamentalEvidenceRecord, EvidenceShape, SupplyBasisSnapshot, ValueCaptureRuleSnapshot]] = (
            []
        )
        by_window: dict[
            tuple[datetime, datetime],
            tuple[FundamentalEvidenceRecord, EvidenceShape, SupplyBasisSnapshot, ValueCaptureRuleSnapshot],
        ] = {}
        for item in resolved:
            record = item[0]
            window = (record.accounting_period_start, record.accounting_period_end)
            prior = by_window.get(window)
            if prior is None:
                by_window[window] = item
                result.append(item)
                continue
            if not _economically_identical(record, prior[0]) or item[1:] != prior[1:]:
                raise CanonicalEvidenceAssemblyError("divergent duplicate accounting interval")
        return tuple(result)

    def _validate_coverage(
        self,
        resolved: tuple[
            tuple[FundamentalEvidenceRecord, EvidenceShape, SupplyBasisSnapshot, ValueCaptureRuleSnapshot], ...
        ],
        *,
        start: datetime,
        end: datetime,
    ) -> None:
        records = tuple(item[0] for item in resolved)
        if records[0].accounting_period_start != start or records[-1].accounting_period_end != end:
            raise CanonicalEvidenceAssemblyError("constituents do not exactly cover accounting window")
        windows: set[tuple[datetime, datetime]] = set()
        previous_end = start
        for record in records:
            window = (record.accounting_period_start, record.accounting_period_end)
            if window in windows:
                raise CanonicalEvidenceAssemblyError("duplicate accounting interval")
            windows.add(window)
            if record.accounting_period_start < previous_end:
                raise CanonicalEvidenceAssemblyError("constituent accounting windows overlap")
            if record.accounting_period_start > previous_end:
                raise CanonicalEvidenceAssemblyError("gap in constituent accounting windows")
            previous_end = record.accounting_period_end

    def _selected_cadence(
        self,
        registry: EvidenceShapeRegistrySnapshot,
        shapes: tuple[EvidenceShape, ...],
        methodology: ValuationMethodologySnapshot,
    ) -> str:
        cadences = {shape.cadence for shape in shapes}
        if len(cadences) != 1:
            raise CanonicalEvidenceAssemblyError("mixed constituent cadence is not losslessly comparable")
        cadence = shapes[0].cadence
        try:
            registry.cadence_rank(shapes[0])
        except EvidenceShapeRegistryError as exc:
            override = methodology.assembled_evidence_granularity_override
            if override != cadence:
                raise CanonicalEvidenceAssemblyError(
                    "non-calendar cadence lacks canonical methodology override"
                ) from exc
        return cadence

    def _validate_candidate_universe(
        self,
        resolved: tuple[
            tuple[FundamentalEvidenceRecord, EvidenceShape, SupplyBasisSnapshot, ValueCaptureRuleSnapshot], ...
        ],
        *,
        registry: EvidenceShapeRegistrySnapshot,
        methodology: ValuationMethodologySnapshot,
        selected_cadence: str,
        start: datetime,
        end: datetime,
        recorded_at: datetime,
        replay_cutoff: datetime,
        request_id: str,
    ) -> None:
        selected_ids = {item[0].record_id for item in resolved}
        identity = resolved[0][0].identity
        universe = self.value_capture_repository.overlapping_evidence(
            identity=identity,
            attribution_rule_id=resolved[0][3].mechanism_policy_id,
            accounting_window_start=start,
            accounting_window_end=end,
            known_by=replay_cutoff,
        )
        if any(not verify_value_capture_content_hash(record) for record in universe):
            raise CanonicalEvidenceAssemblyError("candidate-universe content hash is invalid")
        if not selected_ids <= {record.record_id for record in universe}:
            raise CanonicalEvidenceAssemblyError("selected record is absent from strict-known universe")
        unresolved = tuple(
            record
            for record in universe
            if record.record_id not in selected_ids
            and record.effective_at <= replay_cutoff
            and record.recorded_at <= replay_cutoff
            and record.known_at <= replay_cutoff
            and record.quality_state == "accepted"
            and record.conflict_state in {"open", "contested"}
        )
        if unresolved:
            self._persist_conflict(
                candidates=tuple(
                    sorted((*tuple(item[0] for item in resolved), *unresolved), key=lambda record: record.record_id)
                ),
                resolved=resolved,
                registry=registry,
                methodology=methodology,
                start=start,
                end=end,
                recorded_at=recorded_at,
                replay_cutoff=replay_cutoff,
                request_id=request_id,
                category="unresolved-source-conflict",
                reason="strict-known candidate universe contains unresolved authoritative evidence",
            )
            raise CanonicalEvidenceAssemblyError("unresolved candidate evidence conflict")
        omitted = tuple(
            record
            for record in universe
            if record.record_id not in selected_ids
            and record.effective_at <= replay_cutoff
            and record.recorded_at <= replay_cutoff
            and record.known_at <= replay_cutoff
            and record.quality_state == "accepted"
            and record.conflict_state in {"none", "resolved"}
            and not any(_economically_identical(record, item[0]) for item in resolved)
        )
        native = tuple(
            record
            for record in omitted
            if record.accounting_period_start == start and record.accounting_period_end == end
            if self._candidate_is_compatible(
                record,
                registry=registry,
                methodology=methodology,
                rule=resolved[0][3],
                selected_shape=resolved[0][1],
                native=True,
            )
        )
        compatible_omitted = tuple(
            record
            for record in omitted
            if record not in native
            and self._candidate_is_compatible(
                record,
                registry=registry,
                methodology=methodology,
                rule=resolved[0][3],
                selected_shape=resolved[0][1],
                native=False,
            )
        )
        if native:
            self._persist_conflict(
                candidates=tuple(sorted((*tuple(item[0] for item in resolved), *native), key=lambda r: r.record_id)),
                resolved=resolved,
                registry=registry,
                methodology=methodology,
                start=start,
                end=end,
                recorded_at=recorded_at,
                replay_cutoff=replay_cutoff,
                request_id=request_id,
                category="native-precedence",
                reason="qualifying native evidence takes precedence over assembly",
            )
            raise CanonicalEvidenceAssemblyError("qualifying native evidence takes precedence")
        paths = _coverage_paths(compatible_omitted, start=start, end=end)
        selected_shape = resolved[0][1]
        for path in paths:
            alternative_shapes = tuple(self._shape_for_record(registry, record) for record in path)
            if len({shape.cadence for shape in alternative_shapes}) != 1:
                raise CanonicalEvidenceAssemblyError("alternative path has mixed cadence")
            alternative = alternative_shapes[0]
            try:
                comparison = registry.compare_cadence(alternative, selected_shape)
            except EvidenceShapeRegistryError as exc:
                raise CanonicalEvidenceAssemblyError(str(exc)) from exc
            override = methodology.assembled_evidence_granularity_override
            if comparison == 0:
                self._persist_conflict(
                    candidates=tuple(
                        sorted((*tuple(item[0] for item in resolved), *path), key=lambda record: record.record_id)
                    ),
                    resolved=resolved,
                    registry=registry,
                    methodology=methodology,
                    start=start,
                    end=end,
                    recorded_at=recorded_at,
                    replay_cutoff=replay_cutoff,
                    request_id=request_id,
                    category="equal-cadence-alternative",
                    reason="divergent complete equal-cadence evidence series is ambiguous",
                )
                raise CanonicalEvidenceAssemblyError("divergent equal-cadence complete series")
            if override is not None:
                if override not in {selected_cadence, alternative.cadence}:
                    raise CanonicalEvidenceAssemblyError("canonical granularity override is incompatible")
                if override == alternative.cadence and alternative.cadence != selected_cadence:
                    raise CanonicalEvidenceAssemblyError("selected series violates canonical granularity override")
            elif comparison < 0:
                raise CanonicalEvidenceAssemblyError("selected series is not the governed finest cadence")
        competitors = tuple(
            record
            for record in compatible_omitted
            if any(
                record.accounting_period_start < selected.accounting_period_end
                and selected.accounting_period_start < record.accounting_period_end
                for selected, _, _, _ in resolved
            )
        )
        if competitors and not paths:
            self._persist_conflict(
                candidates=tuple(
                    sorted((*tuple(item[0] for item in resolved), *competitors), key=lambda record: record.record_id)
                ),
                resolved=resolved,
                registry=registry,
                methodology=methodology,
                start=start,
                end=end,
                recorded_at=recorded_at,
                replay_cutoff=replay_cutoff,
                request_id=request_id,
                category="competing-evidence",
                reason="omitted competing evidence prevents exact unambiguous assembly",
            )
            raise CanonicalEvidenceAssemblyError("omitted competing evidence creates conflict")

    def _candidate_is_compatible(
        self,
        record: FundamentalEvidenceRecord,
        *,
        registry: EvidenceShapeRegistrySnapshot,
        methodology: ValuationMethodologySnapshot,
        rule: ValueCaptureRuleSnapshot,
        selected_shape: EvidenceShape,
        native: bool,
    ) -> bool:
        try:
            shape = self._shape_for_record(registry, record)
            _validate_cadence_window(shape, record)
        except CanonicalEvidenceAssemblyError:
            return False
        if native:
            return (
                shape.currency.casefold() == selected_shape.currency.casefold()
                and shape.unit == selected_shape.unit
                and shape.accounting_meaning == "period_specific"
                and record.attribution_rule_id == rule.mechanism_policy_id
            )
        return (
            shape.shape_id in methodology.accepted_evidence_shape_ids
            and shape.currency.casefold() == methodology.currency.casefold()
            and shape.accounting_meaning == "period_specific"
            and (native or shape.composition_operation == "exact_sum")
            and record.attribution_rule_id == rule.mechanism_policy_id
        )

    def _shape_for_record(
        self, registry: EvidenceShapeRegistrySnapshot, record: FundamentalEvidenceRecord
    ) -> EvidenceShape:
        matches = [
            shape
            for shape in registry.shapes
            if shape.active
            and shape.evidence_type == record.evidence_type
            and shape.source_methodology == record.source_methodology
            and shape.unit == record.unit
        ]
        if len(matches) != 1:
            raise CanonicalEvidenceAssemblyError("candidate shape classification is unknown or ambiguous")
        return matches[0]

    def _persist_conflict(
        self,
        *,
        candidates: tuple[FundamentalEvidenceRecord, ...],
        resolved: tuple[
            tuple[FundamentalEvidenceRecord, EvidenceShape, SupplyBasisSnapshot, ValueCaptureRuleSnapshot], ...
        ],
        registry: EvidenceShapeRegistrySnapshot,
        methodology: ValuationMethodologySnapshot,
        start: datetime,
        end: datetime,
        recorded_at: datetime,
        replay_cutoff: datetime,
        request_id: str,
        category: str,
        reason: str,
    ) -> None:
        known_at = max(
            recorded_at,
            registry.known_at,
            methodology.known_at,
            *(candidate.known_at for candidate in candidates),
        )
        if known_at > replay_cutoff:
            return
        shape_by_id = {record.record_id: shape.shape_id for record, shape, _, _ in resolved}
        rule_by_id = {record.record_id: rule.mechanism_policy_id for record, _, _, rule in resolved}
        for candidate in candidates:
            shape_by_id.setdefault(candidate.record_id, self._shape_for_record(registry, candidate).shape_id)
            rule_by_id.setdefault(candidate.record_id, candidate.attribution_rule_id)
        logical_digest = _hash(
            {"request": request_id, "category": category, "candidates": [r.record_id for r in candidates]}
        )
        logical_id = f"assembly-conflict:{logical_digest}"
        pending = AssemblyConflictRecord(
            record_id="pending",
            logical_id=logical_id,
            entity_id=candidates[0].identity.entity_id,
            economic_claim_id=candidates[0].identity.economic_claim_id,
            accounting_window_start=start,
            accounting_window_end=end,
            candidate_record_ids=tuple(record.record_id for record in candidates),
            candidate_logical_ids=tuple(record.logical_id for record in candidates),
            candidate_versions=tuple(record.semantic_version for record in candidates),
            candidate_content_hashes=tuple(record.content_hash for record in candidates),
            candidate_source_ids=tuple(record.source_id for record in candidates),
            candidate_effective_at=tuple(record.effective_at.isoformat() for record in candidates),
            candidate_recorded_at=tuple(record.recorded_at.isoformat() for record in candidates),
            candidate_known_at=tuple(record.known_at.isoformat() for record in candidates),
            registry_record_id=registry.record_id,
            registry_id=registry.registry_id,
            registry_version=registry.registry_version,
            registry_content_hash=registry.content_hash,
            methodology_record_id=methodology.record_id,
            methodology_logical_id=methodology.logical_id,
            methodology_version=methodology.semantic_version,
            methodology_content_hash=methodology.content_hash,
            pathway_classifications=tuple(rule_by_id[record.record_id] for record in candidates),
            shape_classifications=tuple(shape_by_id[record.record_id] for record in candidates),
            assembly_request_id=request_id,
            replay_cutoff=replay_cutoff,
            conflict_category=category,
            reason=reason,
            effective_at=end,
            recorded_at=recorded_at,
            known_at=known_at,
            content_hash="pending",
        )
        digest = _hash(asdict(pending) | {"record_id": "", "content_hash": ""})
        self.repository._insert_conflict_authorized(
            replace(pending, record_id=f"assembly-conflict:{digest}", content_hash=digest)
        )

    def _authorize_correction(self, record: AssembledFundamentalEvidenceRecord, *, replay_cutoff: datetime) -> None:
        if record.supersedes_record_id is None:
            return
        predecessor = self.repository.get(record.supersedes_record_id)
        if predecessor is None or predecessor.logical_id != record.logical_id:
            raise CanonicalEvidenceAssemblyError("correction predecessor is invalid")
        if (
            predecessor.effective_at > replay_cutoff
            or predecessor.recorded_at > replay_cutoff
            or predecessor.known_at > replay_cutoff
        ):
            raise CanonicalEvidenceAssemblyError("correction predecessor is not strict-known")
        if predecessor.recorded_at >= record.recorded_at or predecessor.known_at >= record.known_at:
            raise CanonicalEvidenceAssemblyError("correction clocks must strictly advance")
        successor = self.repository.successor(predecessor.record_id, known_by=replay_cutoff)
        if successor is not None and successor.record_id != record.record_id:
            raise CanonicalEvidenceAssemblyError("branching correction lineage is prohibited")


def _coverage_paths(
    records: tuple[FundamentalEvidenceRecord, ...], *, start: datetime, end: datetime
) -> tuple[tuple[FundamentalEvidenceRecord, ...], ...]:
    by_start: dict[datetime, list[FundamentalEvidenceRecord]] = {}
    for record in records:
        by_start.setdefault(record.accounting_period_start, []).append(record)
    paths: list[tuple[FundamentalEvidenceRecord, ...]] = []

    def visit(cursor: datetime, path: tuple[FundamentalEvidenceRecord, ...]) -> None:
        if cursor == end:
            paths.append(path)
            return
        for record in sorted(by_start.get(cursor, []), key=lambda item: (item.accounting_period_end, item.record_id)):
            if record.accounting_period_end <= end and record not in path:
                visit(record.accounting_period_end, path + (record,))

    visit(start, ())
    return tuple(sorted(paths, key=lambda path: tuple(record.record_id for record in path)))


def _validate_cadence_window(shape: EvidenceShape, record: FundamentalEvidenceRecord) -> None:
    if shape.cadence == "daily":
        if record.accounting_period_end - record.accounting_period_start != timedelta(days=1):
            raise CanonicalEvidenceAssemblyError("daily shape does not match accounting window")
        return
    required_months = {"monthly": 1, "quarterly": 3, "semiannual": 6, "annual": 12}.get(shape.cadence)
    if required_months is None:
        return
    start = record.accounting_period_start
    end = record.accounting_period_end
    months = (end.year - start.year) * 12 + end.month - start.month
    if (
        months != required_months
        or end.day != start.day
        or end.hour != start.hour
        or end.minute != start.minute
        or end.second != start.second
        or end.microsecond != start.microsecond
    ):
        raise CanonicalEvidenceAssemblyError(f"{shape.cadence} shape does not match accounting window")


def _hash(value: object) -> str:
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()
    return hashlib.sha256(raw).hexdigest()


def _economically_identical(left: FundamentalEvidenceRecord, right: FundamentalEvidenceRecord) -> bool:
    economic_fields = (
        "identity",
        "evidence_type",
        "amount",
        "unit",
        "accounting_period_start",
        "accounting_period_end",
        "attribution_rule_id",
        "source_methodology",
        "source_id",
        "source_reference",
        "source_record_id",
        "source_record_version",
        "raw_content_hash",
    )
    return all(getattr(left, field) == getattr(right, field) for field in economic_fields)


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise CanonicalEvidenceAssemblyError("datetime must be timezone-aware")
    return value.astimezone(UTC)
