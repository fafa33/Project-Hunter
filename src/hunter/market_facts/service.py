from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from hashlib import sha256

from hunter.execution.canonicalization import canonicalize
from hunter.market_facts.models import (
    MarketFactAcquisitionResult,
    MarketFactAvailabilityEvent,
    MarketFactConflictResolution,
    MarketFactRequest,
    NormalizedMarketFact,
    ObservedMarketFactRecord,
    validate_fact_value,
)
from hunter.market_facts.registry import MarketFactSourceConfig, MarketFactSourceRegistry
from hunter.market_facts.repository import MarketFactWritePlan, ObservedMarketFactRepository


class MarketFactAuthorityError(ValueError):
    """Raised when a result violates source, identity, time, or semantic authority."""


class ObservedMarketFactService:
    semantic_version = "observed-market-fact-v2"
    conflict_resolution_policy_id = "highest-confidence-then-record-id"
    conflict_resolution_policy_version = "1.0.0"

    def __init__(
        self,
        repository: ObservedMarketFactRepository,
        registry: MarketFactSourceRegistry | None = None,
    ) -> None:
        self.repository = repository
        self.registry = registry or MarketFactSourceRegistry.from_file()

    def ingest(
        self,
        request: MarketFactRequest,
        result: MarketFactAcquisitionResult,
        *,
        recorded_at: datetime,
    ) -> tuple[ObservedMarketFactRecord, ...]:
        recorded_at = _utc("recorded_at", recorded_at)
        source = self._validate_result(request, result, recorded_at=recorded_at)
        if result.status != "success":
            event = self._availability_event(request, result, recorded_at)
            self.repository.apply(
                MarketFactWritePlan(
                    availability_events=(event,),
                    authority=self.repository._authority,
                )
            )
            return ()
        normalized = tuple(
            self._apply_conflict_policy(
                request,
                self._apply_quality_policy(source, result, fact),
            )
            for fact in result.facts
        )
        records = tuple(self._record(source, request, result, fact, recorded_at) for fact in normalized)
        self.repository.apply(MarketFactWritePlan(records=records, authority=self.repository._authority))
        return records

    def correct(
        self,
        predecessor_record_id: str,
        request: MarketFactRequest,
        result: MarketFactAcquisitionResult,
        *,
        recorded_at: datetime,
        reason: str,
    ) -> tuple[ObservedMarketFactRecord, ...]:
        if not reason.strip():
            raise MarketFactAuthorityError("correction reason must not be blank")
        predecessor = self.repository.record(predecessor_record_id)
        if predecessor is None:
            raise MarketFactAuthorityError("predecessor record does not exist")
        recorded_at = _utc("recorded_at", recorded_at)
        source = self._validate_result(request, result, recorded_at=recorded_at)
        if result.status != "success":
            raise MarketFactAuthorityError("correction requires successful observed facts")
        replacements: list[ObservedMarketFactRecord] = []
        for raw_fact in result.facts:
            fact = self._apply_quality_policy(source, result, raw_fact)
            logical_id = _logical_id(request, fact.fact_type, fact.quote_currency, fact.venue_scope)
            if logical_id != predecessor.logical_id:
                continue
            replacements.append(
                self._record(
                    source,
                    request,
                    result,
                    fact,
                    recorded_at,
                    supersedes_record_id=predecessor.record_id,
                    correction_reason=reason,
                )
            )
        if len(replacements) != 1:
            raise MarketFactAuthorityError("correction must contain exactly one fact matching predecessor lineage")
        records = tuple(replacements)
        self.repository.apply(MarketFactWritePlan(records=records, authority=self.repository._authority))
        return records

    def strict_known_fact(
        self,
        *,
        entity_id: str,
        representation_id: str,
        fact_type: str,
        effective_as_of: datetime,
        known_by: datetime,
        quote_currency: str | None = None,
    ) -> ObservedMarketFactRecord | None:
        return self.repository.strict_known_fact(
            entity_id=entity_id,
            representation_id=representation_id,
            fact_type=fact_type,
            effective_as_of=effective_as_of,
            known_by=known_by,
            quote_currency=quote_currency,
        )

    def resolve_conflict(
        self,
        *,
        logical_id: str,
        candidate_record_ids: tuple[str, ...],
        selected_record_id: str,
        policy_id: str,
        policy_version: str,
        rationale: str,
        effective_at: datetime,
        known_at: datetime,
        recorded_at: datetime,
    ) -> MarketFactConflictResolution:
        for name, value in (
            ("logical_id", logical_id),
            ("selected_record_id", selected_record_id),
            ("policy_id", policy_id),
            ("policy_version", policy_version),
            ("rationale", rationale),
        ):
            if not value.strip():
                raise MarketFactAuthorityError(f"{name} must not be blank")
        effective_at = _utc("effective_at", effective_at)
        known_at = _utc("known_at", known_at)
        recorded_at = _utc("recorded_at", recorded_at)
        lineage = self.repository.lineage(logical_id)
        selected = next((item for item in lineage if item.record_id == selected_record_id), None)
        if selected is None:
            raise MarketFactAuthorityError("selected conflict record does not exist in logical lineage")
        superseded_ids = {item.supersedes_record_id for item in lineage if item.supersedes_record_id is not None}
        expected_candidates = tuple(
            sorted(
                item.record_id
                for item in lineage
                if item.effective_at == selected.effective_at
                and item.quality_state == "accepted"
                and item.record_id not in superseded_ids
            )
        )
        supplied_candidates = tuple(sorted(candidate_record_ids))
        if supplied_candidates != expected_candidates:
            raise MarketFactAuthorityError("conflict resolution must include the complete candidate set")
        values = {
            (item.value, item.unit, item.quote_currency, item.venue_scope)
            for item in lineage
            if item.record_id in supplied_candidates
        }
        if len(values) < 2:
            raise MarketFactAuthorityError("conflict resolution requires divergent candidate values")
        if policy_id != self.conflict_resolution_policy_id or policy_version != self.conflict_resolution_policy_version:
            raise MarketFactAuthorityError("conflict resolution policy is not authorized")
        candidate_records = tuple(item for item in lineage if item.record_id in supplied_candidates)
        policy_selected = max(
            candidate_records,
            key=lambda item: (Decimal(item.confidence), item.record_id),
        )
        if selected_record_id != policy_selected.record_id:
            raise MarketFactAuthorityError("selected conflict record does not match the authorized policy")
        policy_fingerprint = _hash(
            "conflict-policy",
            {"policy_id": policy_id, "policy_version": policy_version},
        )
        payload = {
            "logical_id": logical_id,
            "candidate_record_ids": supplied_candidates,
            "selected_record_id": selected_record_id,
            "policy_id": policy_id,
            "policy_version": policy_version,
            "policy_fingerprint": policy_fingerprint,
            "rationale": rationale,
            "candidate_effective_at": selected.effective_at,
            "effective_at": effective_at,
            "known_at": known_at,
            "recorded_at": recorded_at,
        }
        resolution = MarketFactConflictResolution(
            resolution_id=_hash("conflict-resolution", payload),
            logical_id=logical_id,
            candidate_record_ids=supplied_candidates,
            selected_record_id=selected_record_id,
            policy_id=policy_id,
            policy_version=policy_version,
            policy_fingerprint=policy_fingerprint,
            rationale=rationale,
            candidate_effective_at=selected.effective_at,
            effective_at=effective_at,
            known_at=known_at,
            recorded_at=recorded_at,
        )
        self.repository.apply(
            MarketFactWritePlan(
                conflict_resolutions=(resolution,),
                authority=self.repository._authority,
            )
        )
        return resolution

    def _validate_result(
        self,
        request: MarketFactRequest,
        result: MarketFactAcquisitionResult,
        *,
        recorded_at: datetime,
    ) -> MarketFactSourceConfig:
        source = self.registry.require(request.source_id)
        if result.request != request:
            raise MarketFactAuthorityError("acquisition result request does not match authorized request")
        if result.source_id != source.source_id or result.provider_id != source.provider_id:
            raise MarketFactAuthorityError("acquisition source or provider mismatch")
        if request.provider_id != source.provider_id:
            raise MarketFactAuthorityError("request provider is not source-authorized")
        try:
            source.authorize_identity(request.identity)
        except ValueError as exc:
            raise MarketFactAuthorityError(str(exc)) from exc
        if result.provider_source_record_id != request.identity.provider_listing_id:
            raise MarketFactAuthorityError("provider source record does not match authorized listing")
        expected_endpoint = source.endpoint_for(request.identity.provider_listing_id)
        if result.endpoint != expected_endpoint:
            raise MarketFactAuthorityError("acquisition endpoint is not registry-authorized")
        if result.parser_version != source.parser_version:
            raise MarketFactAuthorityError("acquisition parser version mismatch")
        if result.registry_fingerprint != source.fingerprint:
            raise MarketFactAuthorityError("acquisition registry fingerprint mismatch")
        if request.quote_currency not in source.quote_currencies:
            raise MarketFactAuthorityError("quote currency is not source-authorized")
        if not set(request.requested_fact_types).issubset(set(source.capabilities)):
            raise MarketFactAuthorityError("requested capability is not source-authorized")
        if "canonical_asset_representation" not in source.supported_entity_scope:
            raise MarketFactAuthorityError("source does not authorize canonical asset representation scope")
        if request.identity.provider_listing_id in {
            request.identity.entity_id,
            request.identity.asset_id,
            request.identity.representation_id,
        }:
            raise MarketFactAuthorityError("provider listing cannot substitute for canonical identity")
        if result.known_at < request.requested_at:
            raise MarketFactAuthorityError("known_at cannot precede request time")
        if result.acquired_at < request.requested_at:
            raise MarketFactAuthorityError("acquired_at cannot precede request time")
        if recorded_at < result.known_at:
            raise MarketFactAuthorityError("recorded_at cannot precede known_at")
        if result.status == "success":
            if not result.facts:
                raise MarketFactAuthorityError("successful result contains no facts")
            for fact in result.facts:
                if fact.conflict_state == "resolved":
                    raise MarketFactAuthorityError("providers cannot assert resolved conflict state")
                if fact.fact_type not in request.requested_fact_types:
                    raise MarketFactAuthorityError("result contains an unrequested fact")
                if fact.fact_type not in source.capabilities:
                    raise MarketFactAuthorityError("result contains an unauthorized fact")
                expected_unit = source.units.get(fact.fact_type)
                if expected_unit is None or fact.unit != expected_unit:
                    raise MarketFactAuthorityError("fact unit is unsupported or ambiguous")
                quoted = fact.fact_type in {
                    "spot_price",
                    "market_capitalization",
                    "fully_diluted_valuation",
                    "trading_volume",
                }
                if quoted and fact.quote_currency != request.quote_currency:
                    raise MarketFactAuthorityError("fact quote currency mismatch")
                if not quoted and fact.quote_currency is not None:
                    raise MarketFactAuthorityError("supply fact must not carry quote currency")
                validate_fact_value(fact.fact_type, fact.value)
                if fact.observed_at > result.acquired_at:
                    raise MarketFactAuthorityError("fact observation cannot occur after acquisition")
                if fact.effective_at > result.acquired_at:
                    raise MarketFactAuthorityError("fact effective time cannot occur after acquisition")
                if result.known_at < fact.observed_at:
                    raise MarketFactAuthorityError("known_at cannot precede observation time")
                if result.known_at < fact.effective_at:
                    raise MarketFactAuthorityError("known_at cannot precede effective time")
        return source

    def _apply_quality_policy(
        self,
        source: MarketFactSourceConfig,
        result: MarketFactAcquisitionResult,
        fact: NormalizedMarketFact,
    ) -> NormalizedMarketFact:
        age_seconds = (result.known_at - fact.effective_at).total_seconds()
        if age_seconds > source.freshness_seconds and fact.quality_state == "accepted":
            return replace(fact, quality_state="stale")
        return fact

    def _apply_conflict_policy(
        self,
        request: MarketFactRequest,
        fact: NormalizedMarketFact,
    ) -> NormalizedMarketFact:
        existing = self.repository.facts(
            entity_id=request.identity.entity_id,
            asset_id=request.identity.asset_id,
            representation_id=request.identity.representation_id,
            fact_type=fact.fact_type,
            effective_at=fact.effective_at,
        )
        divergent = any(
            item.value != fact.value
            or item.unit != fact.unit
            or item.quote_currency != fact.quote_currency
            or item.venue_scope != fact.venue_scope
            for item in existing
            if item.quality_state == "accepted"
        )
        if divergent and fact.conflict_state == "none":
            return replace(fact, conflict_state="open")
        return fact

    def _record(
        self,
        source: MarketFactSourceConfig,
        request: MarketFactRequest,
        result: MarketFactAcquisitionResult,
        fact: NormalizedMarketFact,
        recorded_at: datetime,
        *,
        supersedes_record_id: str | None = None,
        correction_reason: str = "",
    ) -> ObservedMarketFactRecord:
        logical_id = _logical_id(request, fact.fact_type, fact.quote_currency, fact.venue_scope)
        identity_payload = {
            "logical_id": logical_id,
            "semantic_version": self.semantic_version,
            "source_id": source.source_id,
            "provider_id": source.provider_id,
            "endpoint": result.endpoint,
            "parser_version": source.parser_version,
            "value": fact.value,
            "unit": fact.unit,
            "quote_currency": fact.quote_currency,
            "effective_at": fact.effective_at,
            "observed_at": fact.observed_at,
            "recorded_at": recorded_at,
            "known_at": result.known_at,
            "raw_payload_hash": result.raw_payload_hash,
            "provider_source_record_id": result.provider_source_record_id,
            "provider_source_record_version": result.provider_source_record_version,
            "confidence": fact.confidence,
            "quality_state": fact.quality_state,
            "conflict_state": fact.conflict_state,
            "supersedes_record_id": supersedes_record_id,
            "correction_reason": correction_reason,
        }
        content_hash = _hash("content", identity_payload)
        record_id = _hash("record", {"logical_id": logical_id, "content_hash": content_hash})
        return ObservedMarketFactRecord(
            record_id=record_id,
            logical_id=logical_id,
            semantic_version=self.semantic_version,
            identity=request.identity,
            source_id=source.source_id,
            provider_id=source.provider_id,
            endpoint=result.endpoint,
            parser_version=source.parser_version,
            fact_type=fact.fact_type,
            value=fact.value,
            unit=fact.unit,
            quote_currency=fact.quote_currency,
            venue_scope=fact.venue_scope,
            effective_at=fact.effective_at,
            observed_at=fact.observed_at,
            recorded_at=recorded_at,
            known_at=result.known_at,
            raw_payload_hash=result.raw_payload_hash,
            provider_source_record_id=result.provider_source_record_id,
            provider_source_record_version=result.provider_source_record_version,
            confidence=fact.confidence,
            quality_state=fact.quality_state,
            conflict_state=fact.conflict_state,
            content_hash=content_hash,
            supersedes_record_id=supersedes_record_id,
            correction_reason=correction_reason,
        )

    def _availability_event(
        self,
        request: MarketFactRequest,
        result: MarketFactAcquisitionResult,
        recorded_at: datetime,
    ) -> MarketFactAvailabilityEvent:
        payload = {
            "source_id": result.source_id,
            "provider_id": result.provider_id,
            "entity_id": request.identity.entity_id,
            "representation_id": request.identity.representation_id,
            "status": result.status,
            "requested_at": request.requested_at,
            "recorded_at": recorded_at,
            "known_at": result.known_at,
            "endpoint": result.endpoint,
            "parser_version": result.parser_version,
            "raw_payload_hash": result.raw_payload_hash,
            "failure_reason": result.failure_reason,
        }
        return MarketFactAvailabilityEvent(
            event_id=_hash("availability", payload),
            source_id=result.source_id,
            provider_id=result.provider_id,
            entity_id=request.identity.entity_id,
            representation_id=request.identity.representation_id,
            status=result.status,
            requested_at=request.requested_at,
            recorded_at=recorded_at,
            known_at=result.known_at,
            endpoint=result.endpoint,
            parser_version=result.parser_version,
            raw_payload_hash=result.raw_payload_hash,
            failure_reason=result.failure_reason,
        )


def _logical_id(request: MarketFactRequest, fact_type: str, quote_currency: str | None, venue_scope: str) -> str:
    return _hash(
        "logical",
        {
            "entity_id": request.identity.entity_id,
            "asset_id": request.identity.asset_id,
            "representation_id": request.identity.representation_id,
            "fact_type": fact_type,
            "quote_currency": quote_currency,
            "venue_scope": venue_scope,
            "semantic_version": ObservedMarketFactService.semantic_version,
        },
    )


def _hash(namespace: str, payload: object) -> str:
    digest = sha256(canonicalize({"namespace": namespace, "payload": payload})).hexdigest()
    return f"{namespace}:sha256:{digest}"


def _utc(name: str, value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise MarketFactAuthorityError(f"{name} must be timezone-aware")
    return value.astimezone(UTC)
