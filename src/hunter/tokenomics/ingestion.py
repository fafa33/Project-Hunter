from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from hunter.tokenomics.identity import tokenomics_id
from hunter.tokenomics.models import (
    TOKENOMICS_SCHEMA_VERSION,
    AllocationDefinition,
    ClaimArtifactLink,
    ObservationConflict,
    ObservationConflictMember,
    SupplyDefinitionReconciliation,
    SupplyObservation,
    TokenAsset,
    TokenomicsAcquisitionAttempt,
    TokenomicsAcquisitionOutcome,
    TokenomicsEvidenceArtifact,
    TokenomicsEvidenceClaim,
    TokenRepresentation,
    UnlockEvent,
    VestingSchedule,
    VestingScheduleSegment,
)
from hunter.tokenomics.providers import TokenomicsIngestionResult, TokenomicsSourceConfig, TokenomicsSourceRegistry
from hunter.tokenomics.repository import TokenomicsIntegrityError, TokenomicsRepository


@dataclass(frozen=True)
class TokenomicsIngestionSummary:
    attempt_id: str
    outcome_id: str
    artifact_count: int
    claim_count: int
    supply_observation_count: int
    allocation_count: int
    vesting_schedule_count: int
    unlock_event_count: int
    conflict_count: int


class TokenomicsIngestionService:
    def __init__(self, repository: TokenomicsRepository, registry: TokenomicsSourceRegistry | None = None) -> None:
        self.repository = repository
        self.registry = registry or TokenomicsSourceRegistry.from_file()

    def ingest(self, result: TokenomicsIngestionResult) -> TokenomicsIngestionSummary:
        self._validate_registry_source(result)
        request = result.request
        recorded_at = request.requested_at.astimezone(UTC)
        effective_at = request.effective_at or recorded_at
        observed_at = result.observed_at or recorded_at
        self.repository.save_token_asset(
            TokenAsset(
                asset_id=request.asset_id,
                candidate_id=request.candidate_id,
                symbol=request.symbol,
                name=request.name,
                effective_at=effective_at,
                recorded_at=recorded_at,
            )
        )
        if request.contract_address:
            self.repository.save_token_representation(
                TokenRepresentation(
                    representation_id=_representation_id(request.asset_id, request.chain, request.contract_address),
                    asset_id=request.asset_id,
                    chain=request.chain,
                    contract_address=request.contract_address,
                    decimals=0,
                    effective_at=effective_at,
                    recorded_at=recorded_at,
                )
            )
        attempt = TokenomicsAcquisitionAttempt(
            attempt_id=tokenomics_id(
                "acquisition-attempt",
                {
                    "provider_id": result.provider.provider_id,
                    "asset_id": request.asset_id,
                    "capability": ",".join(sorted(result.provider.capabilities)),
                    "source_uri": request.source_uri,
                    "started_at": recorded_at,
                },
            ),
            provider_id=result.provider.provider_id,
            adapter_version=result.provider.adapter_version,
            asset_id=request.asset_id,
            capability=",".join(sorted(result.provider.capabilities)),
            source_uri=request.source_uri,
            started_at=recorded_at,
            recorded_at=recorded_at,
            authority_tier=result.provider.authority_tier,
        )
        outcome = TokenomicsAcquisitionOutcome(
            outcome_id=tokenomics_id(
                "acquisition-outcome",
                {
                    "attempt_id": attempt.attempt_id,
                    "status": result.status,
                    "observed_at": observed_at,
                    "recorded_at": recorded_at,
                },
            ),
            attempt_id=attempt.attempt_id,
            provider_id=result.provider.provider_id,
            asset_id=request.asset_id,
            availability_outcome=result.status,
            coverage_state=result.coverage_state,
            observed_at=observed_at,
            recorded_at=recorded_at,
            failure_reason=result.failure_reason,
            source_limitations=result.source_limitations or result.provider.source_limitations,
        )
        self.repository.save_acquisition_attempt(attempt)
        self.repository.save_acquisition_outcome(outcome)

        artifacts = tuple(_artifact(row, recorded_at) for row in result.artifacts)
        for artifact in artifacts:
            self.repository.save_evidence_artifact(artifact)

        supply_claim_ids = self._save_supply_claims(result, artifacts, recorded_at)
        allocation_claim_ids = self._save_allocations(result, artifacts, recorded_at)
        vesting_claim_ids, unlock_count = self._save_vesting(result, artifacts, allocation_claim_ids, recorded_at)
        conflict_count = self._save_supply_conflicts(result, supply_claim_ids, recorded_at)

        return TokenomicsIngestionSummary(
            attempt_id=attempt.attempt_id,
            outcome_id=outcome.outcome_id,
            artifact_count=len(artifacts),
            claim_count=len(supply_claim_ids) + len(allocation_claim_ids) + len(vesting_claim_ids),
            supply_observation_count=len(supply_claim_ids),
            allocation_count=len(allocation_claim_ids),
            vesting_schedule_count=len(result.vesting_schedules),
            unlock_event_count=unlock_count,
            conflict_count=conflict_count,
        )

    def _validate_registry_source(self, result: TokenomicsIngestionResult) -> None:
        if not _has_canonical_evidence(result):
            return
        source = self.registry.resolve(provider_id=result.provider.provider_id, asset_id=result.request.asset_id)
        if source is None:
            raise TokenomicsIntegrityError("tokenomics evidence source is not registry-approved")
        if result.source_config_id != source.source_id:
            raise TokenomicsIntegrityError("tokenomics evidence source identifier does not match registry")
        _validate_result_matches_source(result, source)

    def _save_supply_claims(
        self,
        result: TokenomicsIngestionResult,
        artifacts: tuple[TokenomicsEvidenceArtifact, ...],
        recorded_at: datetime,
    ) -> tuple[str, ...]:
        representation_id = _representation_id(
            result.request.asset_id,
            result.request.chain,
            result.request.contract_address,
        )
        claim_ids = []
        for position, supply in enumerate(result.supply_claims):
            claim = _claim(
                result,
                subject=result.request.asset_id,
                predicate=supply.metric,
                value=supply.amount,
                unit=supply.unit,
                effective_at=supply.effective_at,
                recorded_at=recorded_at,
                status=supply.status,
            )
            self.repository.save_evidence_claim(claim)
            claim_ids.append(claim.claim_id)
            self._link_artifacts(claim.claim_id, artifacts, role=supply.source_role)
            self.repository.save_supply_observation(
                SupplyObservation(
                    observation_id=tokenomics_id(
                        "supply-observation",
                        {
                            "claim_id": claim.claim_id,
                            "metric": supply.metric,
                            "observed_at": supply.observed_at,
                            "recorded_at": recorded_at,
                        },
                    ),
                    representation_id=representation_id,
                    supply_metric=supply.metric,
                    amount=supply.amount,
                    unit=supply.unit,
                    effective_at=supply.effective_at,
                    observed_at=supply.observed_at,
                    recorded_at=recorded_at,
                    availability_state=result.availability_state,
                    coverage_state=result.coverage_state,
                )
            )
            reconciliation = SupplyDefinitionReconciliation(
                reconciliation_id=tokenomics_id(
                    "supply-definition-reconciliation",
                    {
                        "claim_id": claim.claim_id,
                        "metric": supply.metric,
                        "recorded_at": recorded_at,
                    },
                ),
                asset_id=result.request.asset_id,
                supply_metric=supply.metric,
                definition_state="contested" if result.status == "conflicting" else supply.status,
                effective_at=supply.effective_at,
                recorded_at=recorded_at,
            )
            self.repository.save_supply_definition_reconciliation(reconciliation)
            self.repository.save_supply_reconciliation_claim_link(
                link_id=tokenomics_id(
                    "supply-reconciliation-claim-link",
                    {"reconciliation_id": reconciliation.reconciliation_id, "claim_id": claim.claim_id},
                ),
                reconciliation_id=reconciliation.reconciliation_id,
                claim_id=claim.claim_id,
                role="source_claim",
                position=position,
                schema_version=TOKENOMICS_SCHEMA_VERSION,
            )
        return tuple(claim_ids)

    def _save_allocations(
        self,
        result: TokenomicsIngestionResult,
        artifacts: tuple[TokenomicsEvidenceArtifact, ...],
        recorded_at: datetime,
    ) -> dict[str, str]:
        claim_ids: dict[str, str] = {}
        for position, allocation in enumerate(result.allocations):
            value = allocation.amount or ("" if allocation.percentage is None else str(allocation.percentage))
            claim = _claim(
                result,
                subject=f"{result.request.asset_id}:allocation:{allocation.category}",
                predicate="allocation_definition",
                value=value,
                unit=allocation.unit,
                effective_at=allocation.effective_start_at,
                recorded_at=recorded_at,
                status="active",
            )
            self.repository.save_evidence_claim(claim)
            self._link_artifacts(claim.claim_id, artifacts, role="allocation_source")
            allocation_id = tokenomics_id(
                "allocation-definition",
                {
                    "asset_id": result.request.asset_id,
                    "category": allocation.category,
                    "effective_start_at": allocation.effective_start_at,
                    "recorded_at": recorded_at,
                },
            )
            self.repository.save_allocation_definition(
                AllocationDefinition(
                    allocation_id=allocation_id,
                    asset_id=result.request.asset_id,
                    category=allocation.category,
                    percentage=allocation.percentage,
                    amount=allocation.amount,
                    unit=allocation.unit,
                    effective_start_at=allocation.effective_start_at,
                    effective_end_at=allocation.effective_end_at,
                    recorded_at=recorded_at,
                    availability_state=result.availability_state,
                )
            )
            self.repository.save_allocation_evidence_link(
                link_id=tokenomics_id(
                    "allocation-evidence-link", {"allocation_id": allocation_id, "claim_id": claim.claim_id}
                ),
                allocation_id=allocation_id,
                claim_id=claim.claim_id,
                role="source_claim",
                position=position,
                schema_version=TOKENOMICS_SCHEMA_VERSION,
            )
            claim_ids[allocation.category] = claim.claim_id
        return claim_ids

    def _save_vesting(
        self,
        result: TokenomicsIngestionResult,
        artifacts: tuple[TokenomicsEvidenceArtifact, ...],
        allocation_claim_ids: dict[str, str],
        recorded_at: datetime,
    ) -> tuple[tuple[str, ...], int]:
        claim_ids = []
        unlock_count = 0
        for position, schedule in enumerate(result.vesting_schedules):
            allocation_id = tokenomics_id(
                "allocation-definition",
                {
                    "asset_id": result.request.asset_id,
                    "category": schedule.allocation_category,
                    "effective_start_at": schedule.effective_start_at,
                    "recorded_at": recorded_at,
                },
            )
            if schedule.allocation_category not in allocation_claim_ids:
                self.repository.save_allocation_definition(
                    AllocationDefinition(
                        allocation_id=allocation_id,
                        asset_id=result.request.asset_id,
                        category=schedule.allocation_category,
                        percentage=None,
                        amount=None,
                        unit=result.request.symbol,
                        effective_start_at=schedule.effective_start_at,
                        effective_end_at=schedule.effective_end_at,
                        recorded_at=recorded_at,
                        availability_state=result.availability_state,
                    )
                )
            claim = _claim(
                result,
                subject=f"{result.request.asset_id}:vesting:{schedule.schedule_key}",
                predicate="vesting_schedule",
                value=schedule.schedule_state,
                unit=result.request.symbol,
                effective_at=schedule.effective_start_at,
                recorded_at=recorded_at,
                status="active",
            )
            self.repository.save_evidence_claim(claim)
            self._link_artifacts(claim.claim_id, artifacts, role="vesting_source")
            schedule_id = tokenomics_id(
                "vesting-schedule",
                {
                    "asset_id": result.request.asset_id,
                    "schedule_key": schedule.schedule_key,
                    "effective_start_at": schedule.effective_start_at,
                    "recorded_at": recorded_at,
                },
            )
            self.repository.save_vesting_schedule(
                VestingSchedule(
                    schedule_id=schedule_id,
                    asset_id=result.request.asset_id,
                    allocation_id=allocation_id,
                    schedule_state=schedule.schedule_state,  # type: ignore[arg-type]
                    effective_start_at=schedule.effective_start_at,
                    effective_end_at=schedule.effective_end_at,
                    recorded_at=recorded_at,
                )
            )
            self.repository.save_vesting_schedule_evidence_link(
                link_id=tokenomics_id(
                    "vesting-evidence-link", {"schedule_id": schedule_id, "claim_id": claim.claim_id}
                ),
                schedule_id=schedule_id,
                claim_id=claim.claim_id,
                role="source_claim",
                position=position,
                schema_version=TOKENOMICS_SCHEMA_VERSION,
            )
            if schedule.allocation_category in allocation_claim_ids:
                claim_ids.append(allocation_claim_ids[schedule.allocation_category])
            claim_ids.append(claim.claim_id)
            for segment in schedule.segments:
                self.repository.save_vesting_schedule_segment(
                    VestingScheduleSegment(
                        segment_id=tokenomics_id(
                            "vesting-segment",
                            {
                                "schedule_id": schedule_id,
                                "segment_key": segment.segment_key,
                                "start_at": segment.start_at,
                                "recorded_at": recorded_at,
                            },
                        ),
                        schedule_id=schedule_id,
                        segment_state=segment.segment_state,  # type: ignore[arg-type]
                        start_at=segment.start_at,
                        end_at=segment.end_at,
                        amount=segment.amount,
                        percentage=segment.percentage,
                        recorded_at=recorded_at,
                    )
                )
            for unlock in schedule.unlocks:
                self.repository.save_unlock_event(
                    UnlockEvent(
                        unlock_event_id=tokenomics_id(
                            "unlock-event",
                            {
                                "schedule_id": schedule_id,
                                "event_key": unlock.event_key,
                                "unlock_at": unlock.unlock_at,
                                "recorded_at": recorded_at,
                            },
                        ),
                        schedule_id=schedule_id,
                        unlock_state=unlock.unlock_state,  # type: ignore[arg-type]
                        unlock_at=unlock.unlock_at,
                        amount=unlock.amount,
                        percentage=unlock.percentage,
                        recorded_at=recorded_at,
                    )
                )
                unlock_count += 1
        return tuple(claim_ids), unlock_count

    def _save_supply_conflicts(
        self,
        result: TokenomicsIngestionResult,
        claim_ids: tuple[str, ...],
        recorded_at: datetime,
    ) -> int:
        if result.status != "conflicting" or len(claim_ids) < 2:
            return 0
        conflicting_metrics = {metric for metric, amounts in _supply_metric_amounts(result).items() if len(amounts) > 1}
        conflict_claim_ids = tuple(
            claim_id
            for claim_id, supply in zip(claim_ids, result.supply_claims, strict=True)
            if supply.metric in conflicting_metrics
        )
        if len(conflict_claim_ids) < 2:
            return 0
        conflict = ObservationConflict(
            conflict_id=tokenomics_id(
                "supply-conflict",
                {
                    "asset_id": result.request.asset_id,
                    "claim_ids": sorted(conflict_claim_ids),
                    "recorded_at": recorded_at,
                },
            ),
            asset_id=result.request.asset_id,
            conflict_state="open",
            detected_at=recorded_at,
            recorded_at=recorded_at,
        )
        self.repository.save_observation_conflict(conflict)
        for position, claim_id in enumerate(conflict_claim_ids):
            self.repository.save_observation_conflict_member(
                ObservationConflictMember(
                    member_id=tokenomics_id(
                        "supply-conflict-member",
                        {"conflict_id": conflict.conflict_id, "claim_id": claim_id, "position": position},
                    ),
                    conflict_id=conflict.conflict_id,
                    observation_table="tokenomics_evidence_claims",
                    observation_id=claim_id,
                    role="conflicting_supply_claim",
                )
            )
        return 1

    def _link_artifacts(self, claim_id: str, artifacts: tuple[TokenomicsEvidenceArtifact, ...], *, role: str) -> None:
        for position, artifact in enumerate(artifacts):
            self.repository.save_claim_artifact_link(
                ClaimArtifactLink(
                    link_id=tokenomics_id(
                        "claim-artifact-link",
                        {"claim_id": claim_id, "artifact_id": artifact.artifact_id, "role": role},
                    ),
                    claim_id=claim_id,
                    artifact_id=artifact.artifact_id,
                    role=role,
                    position=position,
                )
            )


def _artifact(row: object, recorded_at: datetime) -> TokenomicsEvidenceArtifact:
    return TokenomicsEvidenceArtifact(
        artifact_id=row.artifact_id,  # type: ignore[attr-defined]
        source_type=row.source_type,  # type: ignore[attr-defined]
        source_uri=row.source_uri,  # type: ignore[attr-defined]
        content_hash=row.content_hash,  # type: ignore[attr-defined]
        observed_at=row.observed_at,  # type: ignore[attr-defined]
        recorded_at=recorded_at,
        lifecycle_status="active",
        source_authority=row.source_authority,  # type: ignore[attr-defined]
        parser_version=row.parser_version,  # type: ignore[attr-defined]
    )


def _claim(
    result: TokenomicsIngestionResult,
    *,
    subject: str,
    predicate: str,
    value: str,
    unit: str,
    effective_at: datetime,
    recorded_at: datetime,
    status: str,
) -> TokenomicsEvidenceClaim:
    return TokenomicsEvidenceClaim(
        claim_id=tokenomics_id(
            "evidence-claim",
            {
                "provider_id": result.provider.provider_id,
                "source_uri": result.request.source_uri,
                "asset_id": result.request.asset_id,
                "subject": subject,
                "predicate": predicate,
                "value": value,
                "unit": unit,
                "effective_at": effective_at,
                "recorded_at": recorded_at,
            },
        ),
        asset_id=result.request.asset_id,
        subject=subject,
        predicate=predicate,
        value=value,
        unit=unit,
        evidence_status=status,  # type: ignore[arg-type]
        confidence_state="high" if result.provider.authority_tier == "authoritative" else "medium",
        effective_at=effective_at,
        recorded_at=recorded_at,
    )


def _representation_id(asset_id: str, chain: str, contract_address: str) -> str:
    return tokenomics_id(
        "token-representation", {"asset_id": asset_id, "chain": chain, "contract_address": contract_address}
    )


def _supply_metric_amounts(result: TokenomicsIngestionResult) -> dict[str, set[str]]:
    values: dict[str, set[str]] = {}
    for claim in result.supply_claims:
        values.setdefault(claim.metric, set()).add(claim.amount)
    return values


def _has_canonical_evidence(result: TokenomicsIngestionResult) -> bool:
    return bool(result.artifacts or result.supply_claims or result.allocations or result.vesting_schedules)


def _validate_result_matches_source(result: TokenomicsIngestionResult, source: TokenomicsSourceConfig) -> None:
    request = result.request
    if result.provider.provider_id != source.provider_id:
        raise TokenomicsIntegrityError("tokenomics evidence provider does not match registry")
    expected_capabilities = set(source.capabilities)
    emitted_capabilities: set[str] = set()
    if result.supply_claims:
        emitted_capabilities.add("supply")
    if result.allocations:
        emitted_capabilities.add("allocation")
    if result.vesting_schedules:
        emitted_capabilities.add("vesting")
    if any(schedule.unlocks for schedule in result.vesting_schedules):
        emitted_capabilities.add("unlock")
    if not emitted_capabilities.issubset(expected_capabilities):
        raise TokenomicsIntegrityError("tokenomics evidence capability is not registry-authorized")
    if (
        request.asset_id != source.asset_id
        or request.candidate_id != source.candidate_id
        or request.symbol != source.symbol
        or request.name != source.name
        or request.chain != source.chain
        or request.contract_address != source.contract_address
        or request.source_uri != source.endpoint
    ):
        raise TokenomicsIntegrityError("tokenomics evidence request does not match registry source")
    for artifact in result.artifacts:
        if (
            artifact.source_uri != source.endpoint
            or artifact.source_authority != source.authority_tier
            or artifact.parser_version != source.parser_version
        ):
            raise TokenomicsIntegrityError("tokenomics evidence provenance does not match registry source")
