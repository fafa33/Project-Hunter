from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, replace
from datetime import datetime
from decimal import Decimal
from typing import Protocol

from hunter.evidence_assembly.models import (
    ASSEMBLED_EVIDENCE_SCHEMA_VERSION,
    ASSEMBLY_RULE_VERSION,
    DETERMINISTIC_ORDER,
    AssembledFundamentalEvidenceRecord,
    AssemblyConflictRecord,
    AssemblyConstituent,
    AuthoritativeEvidenceSemantics,
    MethodologyEvidenceInputContract,
)
from hunter.evidence_assembly.registry import EvidenceShapeRegistry, EvidenceShapeRegistryError
from hunter.evidence_assembly.repository import AssembledEvidenceRepository
from hunter.value_capture.models import FundamentalEvidenceRecord


class CanonicalEvidenceAssemblyError(ValueError):
    pass


class NativeEvidenceQuery(Protocol):
    def overlapping_evidence(
        self,
        *,
        entity_id: str,
        economic_claim_id: str,
        accounting_window_start: datetime,
        accounting_window_end: datetime,
        known_by: datetime,
    ) -> tuple[FundamentalEvidenceRecord, ...]: ...


class MethodologyContractAuthority(Protocol):
    def strict_known_contract(
        self,
        *,
        contract_id: str,
        contract_version: str,
        effective_as_of: datetime,
        known_by: datetime,
    ) -> MethodologyEvidenceInputContract | None: ...


class EvidenceShapeRegistryAuthority(Protocol):
    def strict_known_registry(self, *, version: str, known_by: datetime) -> EvidenceShapeRegistry | None: ...


class EvidenceSemanticsAuthority(Protocol):
    def strict_known_semantics(
        self, *, evidence_record_id: str, evidence_record_version: str, known_by: datetime
    ) -> AuthoritativeEvidenceSemantics | None: ...


class CanonicalEvidenceAssemblyService:
    """Sole construction, assembly-validation, and persistence-eligibility authority.

    This service deliberately has no valuation service dependency and neither selects nor
    evaluates a valuation methodology. Methodology contract identity/version are preserved
    provenance supplied by the caller that already owns that contract.
    """

    def __init__(
        self,
        *,
        repository: AssembledEvidenceRepository,
        native_evidence_query: NativeEvidenceQuery,
        methodology_contract_authority: MethodologyContractAuthority,
        evidence_shape_registry_authority: EvidenceShapeRegistryAuthority,
        evidence_semantics_authority: EvidenceSemanticsAuthority,
    ) -> None:
        self.repository = repository
        self.native_evidence_query = native_evidence_query
        self.methodology_contract_authority = methodology_contract_authority
        self.evidence_shape_registry_authority = evidence_shape_registry_authority
        self.evidence_semantics_authority = evidence_semantics_authority

    def assemble(
        self,
        *,
        constituents: tuple[AssemblyConstituent, ...],
        accounting_window_start: datetime,
        accounting_window_end: datetime,
        recorded_at: datetime,
        replay_cutoff: datetime,
        methodology_contract_id: str,
        methodology_contract_version: str,
        evidence_shape_registry_version: str,
        semantic_version: str = "1.0.0",
        supersedes_record_id: str | None = None,
        correction_reason: str = "",
    ) -> AssembledFundamentalEvidenceRecord:
        if len(constituents) < 2:
            raise CanonicalEvidenceAssemblyError(
                "assembly requires multiple granular records; a qualifying native interval takes precedence"
            )
        # Composability is determined exclusively from the fetched, strict-known
        # MethodologyEvidenceInputContract below (see _validate_methodology_contract's
        # `accepts_assembled_evidence` check) -- never from methodology_contract_id
        # identity alone. Evidence Assembly must not anticipate or duplicate the
        # methodology-owned eligibility decision (ADR 0025 "Methodology contract").
        methodology_contract = self.methodology_contract_authority.strict_known_contract(
            contract_id=methodology_contract_id,
            contract_version=methodology_contract_version,
            effective_as_of=accounting_window_end,
            known_by=replay_cutoff,
        )
        if methodology_contract is None:
            raise CanonicalEvidenceAssemblyError("no exact strict-known methodology evidence contract")
        if (
            methodology_contract.contract_id != methodology_contract_id
            or methodology_contract.contract_version != methodology_contract_version
            or methodology_contract.effective_at > replay_cutoff
            or methodology_contract.recorded_at > replay_cutoff
            or methodology_contract.known_at > replay_cutoff
            or methodology_contract.quality_state != "accepted"
            or methodology_contract.conflict_state not in {"none", "resolved"}
        ):
            raise CanonicalEvidenceAssemblyError("methodology evidence contract is not exact and strict-known")
        registry = self.evidence_shape_registry_authority.strict_known_registry(
            version=evidence_shape_registry_version,
            known_by=replay_cutoff,
        )
        if registry is None:
            raise CanonicalEvidenceAssemblyError("no exact strict-known Evidence Shape Registry snapshot")
        if (
            registry.version != evidence_shape_registry_version
            or registry.effective_at > replay_cutoff
            or registry.recorded_at > replay_cutoff
            or registry.known_at > replay_cutoff
            or registry.quality_state != "accepted"
            or registry.conflict_state not in {"none", "resolved"}
        ):
            raise CanonicalEvidenceAssemblyError("Evidence Shape Registry snapshot is not exact and strict-known")
        self._validate_methodology_contract(
            methodology_contract,
            constituents=constituents,
            accounting_window_start=accounting_window_start,
            accounting_window_end=accounting_window_end,
        )
        ordered = tuple(
            sorted(
                constituents,
                key=lambda item: (item.record.accounting_period_start, item.record.record_id),
            )
        )
        self._validate_authoritative_semantics(ordered, replay_cutoff=replay_cutoff)
        deduplicated = self._deduplicate_identical(ordered)
        if len(deduplicated) < 2:
            raise CanonicalEvidenceAssemblyError("assembly cannot relabel one native record as assembled")
        try:
            shapes = registry.require_exact_sum_compatible(tuple(item.shape_id for item in deduplicated))
        except EvidenceShapeRegistryError as exc:
            raise CanonicalEvidenceAssemblyError(str(exc)) from exc
        self._validate_authority(deduplicated, shapes=shapes, replay_cutoff=replay_cutoff)
        self._validate_scope(deduplicated)
        self._validate_authoritative_universe(
            deduplicated,
            accounting_window_start=accounting_window_start,
            accounting_window_end=accounting_window_end,
            replay_cutoff=replay_cutoff,
            recorded_at=recorded_at,
        )
        self._validate_coverage(
            deduplicated,
            accounting_window_start=accounting_window_start,
            accounting_window_end=accounting_window_end,
        )
        if recorded_at > replay_cutoff:
            raise CanonicalEvidenceAssemblyError("assembly time is after requested replay cutoff")

        latest_known = max(item.record.known_at for item in deduplicated)
        if recorded_at < accounting_window_end:
            raise CanonicalEvidenceAssemblyError(
                "assembly recorded_at must not precede accounting_window_end (effective_at); "
                "a record cannot be durably recorded before its own interval is observed"
            )
        if recorded_at < latest_known:
            raise CanonicalEvidenceAssemblyError(
                "assembly recorded_at must not precede the latest constituent known_at; "
                "a record cannot be durably recorded before the facts required to construct it "
                "were themselves knowable"
            )
        known_at = max(latest_known, recorded_at)
        amount = sum((Decimal(item.record.amount or "") for item in deduplicated), start=Decimal(0))
        weakest_confidence = min(Decimal(item.record.evidence_confidence) for item in deduplicated)
        content_basis = {
            "assembly_rule_version": ASSEMBLY_RULE_VERSION,
            "registry_version": registry.version,
            "methodology_contract_id": methodology_contract.contract_id,
            "methodology_contract_version": methodology_contract.contract_version,
            "constituents": [
                {
                    "record_id": item.record.record_id,
                    "logical_id": item.record.logical_id,
                    "semantic_version": item.record.semantic_version,
                    "content_hash": item.record.content_hash,
                    "shape_id": item.shape_id,
                }
                for item in deduplicated
            ],
        }
        assembly_hash = _hash(content_basis)
        identity = deduplicated[0].record.identity
        logical_basis = {
            "identity": asdict(identity),
            "pathway": deduplicated[0].pathway_id,
            "supply_basis": deduplicated[0].supply_basis_id,
            "currency": deduplicated[0].currency,
            "unit": deduplicated[0].raw_unit,
            "start": accounting_window_start.isoformat(),
            "end": accounting_window_end.isoformat(),
            "methodology_contract_id": methodology_contract.contract_id,
            "methodology_contract_version": methodology_contract.contract_version,
        }
        logical_id = f"assembled-evidence:{_hash(logical_basis)}"
        record_id = f"assembled-evidence:{assembly_hash}"
        record = AssembledFundamentalEvidenceRecord(
            record_id=record_id,
            logical_id=logical_id,
            schema_version=ASSEMBLED_EVIDENCE_SCHEMA_VERSION,
            semantic_version=semantic_version,
            assembly_rule_version=ASSEMBLY_RULE_VERSION,
            evidence_shape_registry_version=registry.version,
            methodology_contract_id=methodology_contract.contract_id,
            methodology_contract_version=methodology_contract.contract_version,
            identity=identity,
            representation_continuity_proof_id=deduplicated[0].representation_continuity_proof_id,
            value_capture_pathway_id=deduplicated[0].pathway_id,
            pathway_continuity_proof_id=deduplicated[0].pathway_continuity_proof_id,
            supply_basis_id=deduplicated[0].supply_basis_id,
            supply_basis_continuity_proof_id=deduplicated[0].supply_basis_continuity_proof_id,
            currency=deduplicated[0].currency,
            unit=deduplicated[0].raw_unit,
            accounting_meaning=deduplicated[0].accounting_meaning,
            accounting_window_start=accounting_window_start,
            accounting_window_end=accounting_window_end,
            accounting_period_days=(accounting_window_end - accounting_window_start).days,
            amount=str(amount),
            constituent_record_ids=tuple(item.record.record_id for item in deduplicated),
            constituent_logical_ids=tuple(item.record.logical_id for item in deduplicated),
            constituent_versions=tuple(item.record.semantic_version for item in deduplicated),
            constituent_content_hashes=tuple(item.record.content_hash for item in deduplicated),
            constituent_source_ids=tuple(item.record.source_id for item in deduplicated),
            constituent_source_references=tuple(item.record.source_reference for item in deduplicated),
            constituent_shape_ids=tuple(item.shape_id for item in deduplicated),
            deterministic_constituent_order=DETERMINISTIC_ORDER,
            source_count=len(deduplicated),
            assembly_content_hash=assembly_hash,
            aggregation_lineage="exact decimal summation of period-specific constituent amounts in deterministic order",
            evidence_marker="assembled",
            effective_at=accounting_window_end,
            recorded_at=recorded_at,
            known_at=known_at,
            quality_state="accepted",
            confidence_state=str(weakest_confidence),
            conflict_state="none",
            completeness_state="exact_complete",
            continuity_proof_state="same-scope-no-boundary-crossing",
            non_overlap_proof_state="gap_free_non_overlapping",
            supersedes_record_id=supersedes_record_id,
            correction_reason=correction_reason,
            content_hash="pending",
        )
        record = replace(record, content_hash=_hash(asdict(record) | {"content_hash": ""}))
        self._authorize_correction(record)
        self.repository._insert_authorized(record)
        return record

    def _validate_authoritative_semantics(
        self, constituents: tuple[AssemblyConstituent, ...], *, replay_cutoff: datetime
    ) -> None:
        for item in constituents:
            semantics = self.evidence_semantics_authority.strict_known_semantics(
                evidence_record_id=item.record.record_id,
                evidence_record_version=item.record.semantic_version,
                known_by=replay_cutoff,
            )
            if semantics is None:
                raise CanonicalEvidenceAssemblyError("no exact strict-known authoritative evidence semantics")
            declared = (
                item.shape_id,
                item.currency,
                item.raw_unit,
                item.accounting_meaning,
                item.supply_basis_id,
                item.pathway_id,
            )
            authoritative = (
                semantics.shape_id,
                semantics.currency,
                semantics.raw_unit,
                semantics.accounting_meaning,
                semantics.supply_basis_id,
                semantics.pathway_id,
            )
            if (
                semantics.evidence_record_id != item.record.record_id
                or semantics.evidence_record_version != item.record.semantic_version
                or semantics.effective_at > replay_cutoff
                or semantics.recorded_at > replay_cutoff
                or semantics.known_at > replay_cutoff
                or semantics.quality_state != "accepted"
                or semantics.conflict_state not in {"none", "resolved"}
                or declared != authoritative
            ):
                raise CanonicalEvidenceAssemblyError(
                    "constituent metadata does not match authoritative strict-known evidence semantics"
                )

            # Validate representation continuity proof if asserted by authoritative semantics
            if semantics.representation_continuity_asserted:
                if (
                    item.representation_continuity_proof_id
                    and item.representation_continuity_proof_id != semantics.representation_continuity_proof_id
                ):
                    raise CanonicalEvidenceAssemblyError(
                        "constituent representation continuity proof mismatch against authoritative semantics"
                    )

    def strict_known(
        self, *, logical_id: str, effective_as_of: datetime, known_by: datetime
    ) -> AssembledFundamentalEvidenceRecord | None:
        eligible = tuple(
            record
            for record in self.repository.history(logical_id)
            if record.effective_at <= effective_as_of
            and record.recorded_at <= known_by
            and record.known_at <= known_by
            and record.quality_state == "accepted"
        )
        tips = [
            record for record in eligible if not any(item.supersedes_record_id == record.record_id for item in eligible)
        ]
        accepted = [record for record in tips if record.conflict_state in {"none", "resolved"}]
        accepted.sort(
            key=lambda record: (record.effective_at, record.known_at, record.recorded_at, record.record_id),
            reverse=True,
        )
        return accepted[0] if accepted else None

    def is_superseded(self, record_id: str) -> bool:
        return self.repository.successor(record_id) is not None

    def unresolved_assembly_conflicts(self) -> tuple[AssemblyConflictRecord, ...]:
        return tuple(record for record in self.repository.conflict_records() if record.conflict_state == "open")

    def _validate_methodology_contract(
        self,
        contract: MethodologyEvidenceInputContract,
        *,
        constituents: tuple[AssemblyConstituent, ...],
        accounting_window_start: datetime,
        accounting_window_end: datetime,
    ) -> None:
        first = constituents[0]
        if not contract.accepts_assembled_evidence:
            raise CanonicalEvidenceAssemblyError("methodology contract has not opted into assembled evidence")
        if ASSEMBLY_RULE_VERSION not in contract.accepted_assembly_rule_versions:
            raise CanonicalEvidenceAssemblyError("methodology contract rejects assembly rule version")
        if any(item.shape_id not in contract.accepted_shape_ids for item in constituents):
            raise CanonicalEvidenceAssemblyError("methodology contract rejects evidence shape")
        if (
            contract.accounting_window_start != accounting_window_start
            or contract.accounting_window_end != accounting_window_end
        ):
            raise CanonicalEvidenceAssemblyError("methodology contract accounting window mismatch")
        required_flags = (
            contract.exact_gap_free_non_overlapping_coverage_required,
            contract.provenance_content_hash_required,
            contract.strict_known_required,
        )
        if not all(required_flags):
            raise CanonicalEvidenceAssemblyError("methodology contract omits mandatory assembly requirements")
        if (
            contract.conflict_policy != "reject"
            or contract.minimum_quality_state != "accepted"
            or contract.missingness_behavior != "unavailable"
        ):
            raise CanonicalEvidenceAssemblyError("methodology contract has incompatible fail-closed policy")
        if contract.entity_id != first.record.identity.entity_id:
            raise CanonicalEvidenceAssemblyError("methodology contract entity mismatch")
        if contract.representation_id != first.record.identity.representation_id:
            raise CanonicalEvidenceAssemblyError("methodology contract representation mismatch")
        if contract.value_capture_pathway_id != first.pathway_id or any(
            item.pathway_id != contract.value_capture_pathway_id for item in constituents
        ):
            raise CanonicalEvidenceAssemblyError("methodology contract pathway mismatch")
        if contract.currency.casefold() != first.currency.casefold() or contract.unit != first.raw_unit:
            raise CanonicalEvidenceAssemblyError("methodology contract currency or unit mismatch")
        if any(
            (
                contract.allow_representation_boundary_crossing,
                contract.allow_pathway_boundary_crossing,
                contract.allow_supply_basis_boundary_crossing,
            )
        ):
            raise CanonicalEvidenceAssemblyError(
                "boundary crossing is unavailable without a separate governed continuity-proof authority"
            )

    def _authorize_correction(self, record: AssembledFundamentalEvidenceRecord) -> None:
        predecessor_id = record.supersedes_record_id
        if predecessor_id is None:
            # A second, independent root record for the same logical_id would bypass
            # the correction mechanism entirely. Mirrors
            # hunter.valuation.service._authorize_correction's identical rule. Excludes
            # the record's own record_id so that idempotent re-submission of the exact
            # same deterministic content (handled by repository._insert_authorized's
            # insert-or-identical semantics) is never treated as a second root.
            existing_root = next(
                (item for item in self.repository.history(record.logical_id) if item.record_id != record.record_id),
                None,
            )
            if existing_root is not None:
                raise CanonicalEvidenceAssemblyError(
                    "a root record already exists for this logical_id; use a correction "
                    "(supersedes_record_id) instead of a second independent root record"
                )
            return
        predecessor = self.repository.get(predecessor_id)
        if predecessor is None:
            raise CanonicalEvidenceAssemblyError("correction predecessor does not exist")
        if predecessor.logical_id != record.logical_id:
            raise CanonicalEvidenceAssemblyError("correction must preserve logical_id")
        if predecessor.recorded_at >= record.recorded_at or predecessor.known_at >= record.known_at:
            raise CanonicalEvidenceAssemblyError("correction clocks must strictly follow predecessor")
        if self.repository.successor(predecessor_id) is not None:
            raise CanonicalEvidenceAssemblyError("branching correction lineage is prohibited")

    def _validate_authority(
        self, constituents: tuple[AssemblyConstituent, ...], *, shapes: tuple, replay_cutoff: datetime
    ) -> None:
        for constituent, shape in zip(constituents, shapes, strict=True):
            record = constituent.record
            if record.quality_state != "accepted":
                raise CanonicalEvidenceAssemblyError("non-accepted constituent is ineligible")
            if record.conflict_state not in {"none", "resolved"}:
                raise CanonicalEvidenceAssemblyError("unresolved constituent conflict")
            if (
                record.effective_at > replay_cutoff
                or record.recorded_at > replay_cutoff
                or record.known_at > replay_cutoff
            ):
                raise CanonicalEvidenceAssemblyError("constituent is not strict-known at replay cutoff")
            if shape.evidence_type != record.evidence_type:
                raise CanonicalEvidenceAssemblyError("registry evidence type is incompatible with constituent")
            if shape.accounting_meaning != constituent.accounting_meaning:
                raise CanonicalEvidenceAssemblyError("registry accounting meaning is incompatible with constituent")
            if constituent.accounting_meaning != "period_specific":
                raise CanonicalEvidenceAssemblyError("only period-specific exact summation is governed")
            if record.unit != constituent.raw_unit:
                raise CanonicalEvidenceAssemblyError("declared raw unit does not match authoritative record")
            if record.attribution_rule_id != constituent.pathway_id:
                raise CanonicalEvidenceAssemblyError("declared pathway does not match authoritative record")
            if not record.content_hash.strip():
                raise CanonicalEvidenceAssemblyError("constituent canonical content hash is required for provenance")

    def _validate_scope(self, constituents: tuple[AssemblyConstituent, ...]) -> None:
        if any(
            proof
            for item in constituents
            for proof in (
                item.representation_continuity_proof_id,
                item.pathway_continuity_proof_id,
                item.supply_basis_continuity_proof_id,
            )
        ):
            raise CanonicalEvidenceAssemblyError(
                "continuity proof references are unavailable until a governed proof authority exists"
            )
        first = constituents[0]
        for item in constituents[1:]:
            first_identity = first.record.identity
            identity = item.record.identity
            if identity.entity_id != first_identity.entity_id:
                raise CanonicalEvidenceAssemblyError("incompatible entity identity")
            if identity.economic_claim_id != first_identity.economic_claim_id:
                raise CanonicalEvidenceAssemblyError("incompatible economic claim identity")
            non_representation_scope = (
                identity.asset_id,
                identity.token_id,
                identity.chain,
                identity.contract_address,
            )
            first_non_representation_scope = (
                first_identity.asset_id,
                first_identity.token_id,
                first_identity.chain,
                first_identity.contract_address,
            )
            if non_representation_scope != first_non_representation_scope:
                raise CanonicalEvidenceAssemblyError("incompatible asset or token identity")
            if identity.representation_id != first_identity.representation_id:
                raise CanonicalEvidenceAssemblyError(
                    "representation boundary crossing lacks a governed continuity-proof authority"
                )
            if item.pathway_id != first.pathway_id:
                raise CanonicalEvidenceAssemblyError(
                    "pathway boundary crossing lacks a governed continuity-proof authority"
                )
            if item.supply_basis_id != first.supply_basis_id:
                raise CanonicalEvidenceAssemblyError(
                    "supply-basis boundary crossing lacks a governed continuity-proof authority"
                )
            if item.currency.casefold() != first.currency.casefold():
                raise CanonicalEvidenceAssemblyError("incompatible currency")
            if item.raw_unit != first.raw_unit:
                raise CanonicalEvidenceAssemblyError("incompatible unit")
            if item.accounting_meaning != first.accounting_meaning:
                raise CanonicalEvidenceAssemblyError("incompatible accounting meaning")

    def _validate_authoritative_universe(
        self,
        constituents: tuple[AssemblyConstituent, ...],
        *,
        accounting_window_start: datetime,
        accounting_window_end: datetime,
        replay_cutoff: datetime,
        recorded_at: datetime,
    ) -> None:
        selected_ids = {item.record.record_id for item in constituents}
        identity = constituents[0].record.identity
        universe = self.native_evidence_query.overlapping_evidence(
            entity_id=identity.entity_id,
            economic_claim_id=identity.economic_claim_id,
            accounting_window_start=accounting_window_start,
            accounting_window_end=accounting_window_end,
            known_by=replay_cutoff,
        )
        universe_ids = {record.record_id for record in universe}
        if not selected_ids <= universe_ids:
            raise CanonicalEvidenceAssemblyError("constituent is absent from authoritative candidate universe")
        omitted = tuple(
            record
            for record in universe
            if record.record_id not in selected_ids
            and record.quality_state == "accepted"
            and record.conflict_state in {"none", "resolved"}
            and record.recorded_at <= replay_cutoff
            and record.known_at <= replay_cutoff
            and not any(_native_duplicate_equivalent(record, item.record) for item in constituents)
        )
        # Scope compatibility is evaluated first: an omitted record that shares only
        # entity_id/economic_claim_id but belongs to an incompatible representation,
        # pathway, supply basis, currency, unit, or evidence shape is not a qualifying
        # competing disclosure for this assembly and must never block it or persist a
        # false conflict, in either the native-precedence check below or the general
        # competing/overlapping check that follows it.
        compatible_omitted = tuple(
            record
            for record in omitted
            if self._omitted_record_is_scope_compatible(record, selected=constituents[0], replay_cutoff=replay_cutoff)
        )
        native_full_interval = tuple(
            record
            for record in compatible_omitted
            if record.accounting_period_start == accounting_window_start
            and record.accounting_period_end == accounting_window_end
        )
        if native_full_interval:
            self._persist_conflict(
                identity=identity,
                accounting_window_start=accounting_window_start,
                accounting_window_end=accounting_window_end,
                candidate_record_ids=tuple(sorted(universe_ids)),
                reason="qualifying native evidence takes precedence over assembly",
                recorded_at=recorded_at,
            )
            raise CanonicalEvidenceAssemblyError("qualifying native evidence takes precedence over assembly")
        alternative_paths = _complete_coverage_paths(
            compatible_omitted,
            accounting_window_start=accounting_window_start,
            accounting_window_end=accounting_window_end,
        )
        if alternative_paths:
            finest_alternative = max(len(path) for path in alternative_paths)
            if len(constituents) < finest_alternative:
                raise CanonicalEvidenceAssemblyError(
                    "selected constituent series violates governed finer-granularity preference"
                )
            path_record_ids = {record.record_id for path in alternative_paths for record in path}
            if len(constituents) > finest_alternative and path_record_ids == {
                record.record_id for record in compatible_omitted
            }:
                return
        for record in compatible_omitted:
            if any(
                record.accounting_period_start < item.record.accounting_period_end
                and item.record.accounting_period_start < record.accounting_period_end
                for item in constituents
            ):
                self._persist_conflict(
                    identity=identity,
                    accounting_window_start=accounting_window_start,
                    accounting_window_end=accounting_window_end,
                    candidate_record_ids=tuple(sorted(universe_ids)),
                    reason="omitted competing or overlapping authoritative evidence",
                    recorded_at=recorded_at,
                )
                raise CanonicalEvidenceAssemblyError(
                    "omitted competing or overlapping authoritative evidence creates unresolved conflict"
                )

    def _omitted_record_is_scope_compatible(
        self,
        record: FundamentalEvidenceRecord,
        *,
        selected: AssemblyConstituent,
        replay_cutoff: datetime,
    ) -> bool:
        semantics = self.evidence_semantics_authority.strict_known_semantics(
            evidence_record_id=record.record_id,
            evidence_record_version=record.semantic_version,
            known_by=replay_cutoff,
        )
        if semantics is None:
            return False
        return (
            record.identity == selected.record.identity
            and semantics.evidence_record_id == record.record_id
            and semantics.evidence_record_version == record.semantic_version
            and semantics.effective_at <= replay_cutoff
            and semantics.recorded_at <= replay_cutoff
            and semantics.known_at <= replay_cutoff
            and semantics.quality_state == "accepted"
            and semantics.conflict_state in {"none", "resolved"}
            and semantics.shape_id == selected.shape_id
            and semantics.currency.casefold() == selected.currency.casefold()
            and semantics.raw_unit == selected.raw_unit
            and semantics.accounting_meaning == selected.accounting_meaning
            and semantics.supply_basis_id == selected.supply_basis_id
            and semantics.pathway_id == selected.pathway_id
        )

    def _persist_conflict(
        self,
        *,
        identity,
        accounting_window_start: datetime,
        accounting_window_end: datetime,
        candidate_record_ids: tuple[str, ...],
        reason: str,
        recorded_at: datetime,
    ) -> None:
        basis = {
            "entity_id": identity.entity_id,
            "economic_claim_id": identity.economic_claim_id,
            "start": accounting_window_start.isoformat(),
            "end": accounting_window_end.isoformat(),
            "candidates": candidate_record_ids,
            "reason": reason,
        }
        conflict_hash = _hash(basis)
        conflict = AssemblyConflictRecord(
            record_id=f"assembly-conflict:{conflict_hash}",
            entity_id=identity.entity_id,
            economic_claim_id=identity.economic_claim_id,
            accounting_window_start=accounting_window_start,
            accounting_window_end=accounting_window_end,
            candidate_record_ids=candidate_record_ids,
            reason=reason,
            recorded_at=recorded_at,
            known_at=recorded_at,
            content_hash=conflict_hash,
        )
        self.repository._insert_conflict_authorized(conflict)

    def _validate_coverage(
        self,
        constituents: tuple[AssemblyConstituent, ...],
        *,
        accounting_window_start: datetime,
        accounting_window_end: datetime,
    ) -> None:
        if constituents[0].record.accounting_period_start != accounting_window_start:
            raise CanonicalEvidenceAssemblyError("constituents do not start at target accounting window")
        if constituents[-1].record.accounting_period_end != accounting_window_end:
            raise CanonicalEvidenceAssemblyError("constituents do not end at target accounting window")
        previous = constituents[0].record
        for item in constituents[1:]:
            current = item.record
            if current.accounting_period_start < previous.accounting_period_end:
                raise CanonicalEvidenceAssemblyError("constituent accounting windows overlap")
            if current.accounting_period_start > previous.accounting_period_end:
                raise CanonicalEvidenceAssemblyError("gap in constituent accounting windows")
            previous = current

    def _deduplicate_identical(self, constituents: tuple[AssemblyConstituent, ...]) -> tuple[AssemblyConstituent, ...]:
        result: list[AssemblyConstituent] = []
        by_window: dict[tuple[datetime, datetime], AssemblyConstituent] = {}
        for item in constituents:
            window = (item.record.accounting_period_start, item.record.accounting_period_end)
            prior = by_window.get(window)
            if prior is None:
                by_window[window] = item
                result.append(item)
                continue
            relevant = asdict(item)
            prior_relevant = asdict(prior)
            relevant["record"].pop("record_id")
            relevant["record"].pop("logical_id")
            relevant["record"].pop("source_record_id")
            relevant["record"].pop("acquisition_id")
            relevant["record"].pop("content_hash")
            prior_relevant["record"].pop("record_id")
            prior_relevant["record"].pop("logical_id")
            prior_relevant["record"].pop("source_record_id")
            prior_relevant["record"].pop("acquisition_id")
            prior_relevant["record"].pop("content_hash")
            if relevant != prior_relevant:
                raise CanonicalEvidenceAssemblyError("divergent duplicate accounting interval")
            # Byte-identical economic duplicates are deterministically represented by the
            # lower record ID because input was sorted by start then record ID.
        return tuple(result)


def _hash(value: object) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()
    return hashlib.sha256(encoded).hexdigest()


def _native_duplicate_equivalent(left: FundamentalEvidenceRecord, right: FundamentalEvidenceRecord) -> bool:
    left_payload = asdict(left)
    right_payload = asdict(right)
    for payload in (left_payload, right_payload):
        for field in ("record_id", "logical_id", "source_record_id", "acquisition_id", "content_hash"):
            payload.pop(field)
    return left_payload == right_payload


def _complete_coverage_paths(
    records: tuple[FundamentalEvidenceRecord, ...],
    *,
    accounting_window_start: datetime,
    accounting_window_end: datetime,
) -> tuple[tuple[FundamentalEvidenceRecord, ...], ...]:
    if not records:
        return ()
    by_start: dict[datetime, list[FundamentalEvidenceRecord]] = {}
    for record in records:
        by_start.setdefault(record.accounting_period_start, []).append(record)
    for candidates in by_start.values():
        candidates.sort(key=lambda record: (record.accounting_period_end, record.record_id))

    paths: list[tuple[FundamentalEvidenceRecord, ...]] = []

    def visit(
        cursor: datetime,
        path: tuple[FundamentalEvidenceRecord, ...],
        used: frozenset[str],
    ) -> None:
        if cursor == accounting_window_end:
            paths.append(path)
            return
        for record in by_start.get(cursor, []):
            if record.record_id in used or record.accounting_period_end > accounting_window_end:
                continue
            visit(record.accounting_period_end, path + (record,), used | {record.record_id})

    visit(accounting_window_start, (), frozenset())
    return tuple(sorted(paths, key=lambda path: (len(path), tuple(record.record_id for record in path))))
