from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal

from hunter.discovery.models import CandidateIdentity
from hunter.evidence_intelligence.repository import EvidenceIntelligenceRepository

InputAvailability = Literal["available", "unavailable"]
ReplayMode = Literal["current", "historical_strict_known_by_hunter", "reconstructed_after_cutoff"]

TRUSTED_IDENTITY_STATUSES: frozenset[str] = frozenset({"exact", "probable"})
UNRESOLVED_IDENTITY_STATUSES: frozenset[str] = frozenset(
    {"", "not_resolved", "unresolved", "ambiguous", "conflict", "conflicted", "rejected"}
)
USABLE_CLAIM_STATUSES: frozenset[str] = frozenset({"active", "historical_only"})
USABLE_DOCUMENT_STATUSES: frozenset[str] = frozenset({"active", "historical_only"})
USABLE_AUTHORITY_STATUSES: frozenset[str] = frozenset(
    {
        "verified_official",
        "verified_affiliated",
        "verified_governance",
        "verified_repository",
        "third_party",
        "community",
        "ambiguous",
        "unverified",
    }
)


@dataclass(frozen=True)
class InputSelectionContext:
    cutoff: datetime | None = None
    strict_known_by_hunter: bool = False

    @property
    def replay_mode(self) -> ReplayMode:
        if self.cutoff is None:
            return "current"
        if self.strict_known_by_hunter:
            return "historical_strict_known_by_hunter"
        return "reconstructed_after_cutoff"

    @property
    def known_at_cutoff(self) -> bool:
        return self.cutoff is None or self.strict_known_by_hunter


@dataclass(frozen=True)
class TrustedCandidateInput:
    candidate_id: str
    identity_status: str
    lifecycle_status: str
    confidence: float
    availability: InputAvailability
    reason: str
    evidence_ids: tuple[str, ...]
    source_ids: tuple[str, ...]

    @property
    def available(self) -> bool:
        return self.availability == "available"


@dataclass(frozen=True)
class EvidenceClaimInput:
    claim_id: str
    subject_candidate_id: str
    predicate_id: str
    claim_status: str
    confidence: float
    availability: InputAvailability
    reason: str
    source_evidence_ids: tuple[str, ...]
    span_ids: tuple[str, ...]
    document_ids: tuple[str, ...]
    document_statuses: tuple[str, ...]
    authority_statuses: tuple[str, ...]
    replay_mode: ReplayMode
    known_at_cutoff: bool
    predicate_schema_version: str = ""
    scope: str = ""
    polarity: str = ""
    modality: str = ""
    valid_from: datetime | None = None
    valid_to: datetime | None = None
    observed_at: datetime | None = None
    processed_at: datetime | None = None
    freshness: float = 0.0

    @property
    def available(self) -> bool:
        return self.availability == "available"


@dataclass(frozen=True)
class RelationshipProjectionInput:
    relationship_id: str
    claim_id: str
    subject_entity_id: str
    object_entity_id: str
    predicate_id: str
    projection_status: str
    claim_status: str
    confidence: float
    availability: InputAvailability
    reason: str
    replay_mode: ReplayMode
    known_at_cutoff: bool
    scope: str = ""
    polarity: str = ""
    modality: str = ""
    valid_from: datetime | None = None
    valid_to: datetime | None = None
    created_at: datetime | None = None
    object_candidate_id: str = ""

    @property
    def available(self) -> bool:
        return self.availability == "available"


@dataclass(frozen=True)
class CompetitiveInputSelection:
    candidates: tuple[TrustedCandidateInput, ...]
    claims: tuple[EvidenceClaimInput, ...]
    relationship_projections: tuple[RelationshipProjectionInput, ...]

    @property
    def unavailable_candidates(self) -> tuple[TrustedCandidateInput, ...]:
        return tuple(item for item in self.candidates if not item.available)

    @property
    def unavailable_claims(self) -> tuple[EvidenceClaimInput, ...]:
        return tuple(item for item in self.claims if not item.available)

    @property
    def unavailable_relationship_projections(self) -> tuple[RelationshipProjectionInput, ...]:
        return tuple(item for item in self.relationship_projections if not item.available)


class CompetitiveInputSelector:
    def __init__(
        self,
        *,
        candidate_repository: Any | None = None,
        evidence_repository: EvidenceIntelligenceRepository | None = None,
    ) -> None:
        self.candidate_repository = candidate_repository
        self.evidence_repository = evidence_repository

    def select(
        self,
        *,
        candidate_ids: Iterable[str] = (),
        predicate_ids: Iterable[str] = (),
        context: InputSelectionContext | None = None,
        limit: int = 1000,
    ) -> CompetitiveInputSelection:
        selected_context = context or InputSelectionContext()
        explicit_candidate_ids = tuple(candidate_ids)
        candidates = self.select_candidate_inputs(candidate_ids=explicit_candidate_ids, limit=limit)
        available_candidate_ids = tuple(item.candidate_id for item in candidates if item.available)
        if self.candidate_repository is not None and not available_candidate_ids:
            return CompetitiveInputSelection(candidates=candidates, claims=(), relationship_projections=())
        if explicit_candidate_ids and not available_candidate_ids:
            return CompetitiveInputSelection(candidates=candidates, claims=(), relationship_projections=())
        claim_candidate_ids = available_candidate_ids
        claims = self.select_claim_inputs(
            candidate_ids=claim_candidate_ids,
            predicate_ids=predicate_ids,
            context=selected_context,
        )
        projections = self.select_relationship_projection_inputs(
            claim_inputs=claims,
            predicate_ids=predicate_ids,
            context=selected_context,
        )
        return CompetitiveInputSelection(candidates=candidates, claims=claims, relationship_projections=projections)

    def select_candidate_inputs(
        self,
        *,
        candidate_ids: Iterable[str] = (),
        limit: int = 1000,
    ) -> tuple[TrustedCandidateInput, ...]:
        if self.candidate_repository is None:
            return ()
        candidates = self._candidate_records(tuple(candidate_ids), limit=limit)
        identity_by_candidate = _latest_identity_by_candidate(self.candidate_repository)
        return tuple(
            self._candidate_input(candidate, identity_by_candidate.get(_field(candidate, "candidate_id")))
            for candidate in candidates
        )

    def select_claim_inputs(
        self,
        *,
        candidate_ids: Iterable[str] = (),
        predicate_ids: Iterable[str] = (),
        context: InputSelectionContext | None = None,
    ) -> tuple[EvidenceClaimInput, ...]:
        if self.evidence_repository is None:
            return ()
        selected_context = context or InputSelectionContext()
        candidate_filter = {str(candidate_id) for candidate_id in candidate_ids if str(candidate_id)}
        predicate_filter = {str(predicate_id) for predicate_id in predicate_ids if str(predicate_id)}
        selected: list[EvidenceClaimInput] = []
        for claim in self.evidence_repository.claims():
            subject_candidate_id = str(claim.get("subject_candidate_id") or "")
            predicate_id = str(claim.get("predicate_id") or "")
            if candidate_filter and subject_candidate_id not in candidate_filter:
                continue
            if predicate_filter and predicate_id not in predicate_filter:
                continue
            selected.append(self._claim_input(claim, selected_context))
        return tuple(selected)

    def select_relationship_projection_inputs(
        self,
        *,
        claim_inputs: Iterable[EvidenceClaimInput] | None = None,
        predicate_ids: Iterable[str] = (),
        context: InputSelectionContext | None = None,
    ) -> tuple[RelationshipProjectionInput, ...]:
        if self.evidence_repository is None:
            return ()
        selected_context = context or InputSelectionContext()
        predicate_filter = {str(predicate_id) for predicate_id in predicate_ids if str(predicate_id)}
        claim_map = {
            claim.claim_id: claim for claim in (claim_inputs or self.select_claim_inputs(context=selected_context))
        }
        selected: list[RelationshipProjectionInput] = []
        for projection in self.evidence_repository.relationship_projections():
            predicate_id = str(projection.get("predicate_id") or "")
            if predicate_filter and predicate_id not in predicate_filter:
                continue
            claim = claim_map.get(str(projection.get("claim_id") or ""))
            selected.append(self._projection_input(projection, claim, selected_context))
        return tuple(selected)

    def _candidate_records(self, candidate_ids: tuple[str, ...], *, limit: int) -> tuple[Any, ...]:
        if candidate_ids:
            records: list[Any] = []
            getter = getattr(self.candidate_repository, "get", None)
            if getter is None:
                msg = "candidate repository must expose get(candidate_id) for indexed selection"
                raise TypeError(msg)
            for candidate_id in candidate_ids:
                record = getter(candidate_id)
                if record is not None:
                    records.append(record)
            return tuple(records)
        list_candidates = getattr(self.candidate_repository, "list_candidates", None)
        if list_candidates is None:
            return ()
        return tuple(list_candidates(limit=max(1, min(limit, 100_000))))

    def _candidate_input(self, candidate: Any, identity: CandidateIdentity | None) -> TrustedCandidateInput:
        candidate_id = _field(candidate, "candidate_id")
        candidate_identity_status = _field(candidate, "identity_resolution_status", default="not_resolved")
        identity_status = identity.outcome if identity is not None else candidate_identity_status
        confidence = identity.confidence if identity is not None else _float_field(candidate, "confidence", default=0.0)
        evidence_ids = identity.evidence_ids if identity is not None else _tuple_field(candidate, "evidence_ids")
        source_ids = identity.source_ids if identity is not None else _tuple_field(candidate, "source_ids")
        lifecycle_status = _field(candidate, "lifecycle_status", default="unknown")
        if identity_status in TRUSTED_IDENTITY_STATUSES:
            return TrustedCandidateInput(
                candidate_id=candidate_id,
                identity_status=identity_status,
                lifecycle_status=lifecycle_status,
                confidence=round(float(confidence), 4),
                availability="available",
                reason="identity_trust_outcome_is_accepted",
                evidence_ids=tuple(evidence_ids),
                source_ids=tuple(source_ids),
            )
        reason = "identity_unresolved_or_conflicted"
        if identity_status not in UNRESOLVED_IDENTITY_STATUSES:
            reason = "identity_status_not_trusted_for_competitive_inputs"
        return TrustedCandidateInput(
            candidate_id=candidate_id,
            identity_status=identity_status,
            lifecycle_status=lifecycle_status,
            confidence=round(float(confidence), 4),
            availability="unavailable",
            reason=reason,
            evidence_ids=tuple(evidence_ids),
            source_ids=tuple(source_ids),
        )

    def _claim_input(self, claim: Mapping[str, Any], context: InputSelectionContext) -> EvidenceClaimInput:
        claim_id = str(claim["claim_id"])
        status = self._claim_status(claim, context)
        lineage = self.evidence_repository.claim_lineage(claim_id) if self.evidence_repository is not None else {}
        document_ids, document_statuses, authority_statuses = self._lineage_document_states(lineage, context)
        source_evidence_ids = tuple(str(link["source_evidence_id"]) for link in lineage.get("source_evidence", ()))
        span_ids = tuple(str(span["span_id"]) for span in lineage.get("spans", ()))
        reason = _claim_unavailable_reason(status, document_statuses, authority_statuses)
        availability: InputAvailability = "available" if reason == "" else "unavailable"
        return EvidenceClaimInput(
            claim_id=claim_id,
            subject_candidate_id=str(claim.get("subject_candidate_id") or ""),
            predicate_id=str(claim.get("predicate_id") or ""),
            claim_status=status,
            confidence=round(float(claim.get("confidence") or 0.0), 4),
            availability=availability,
            reason=reason or "claim_lifecycle_document_and_authority_are_usable",
            source_evidence_ids=source_evidence_ids,
            span_ids=span_ids,
            document_ids=document_ids,
            document_statuses=document_statuses,
            authority_statuses=authority_statuses,
            replay_mode=context.replay_mode,
            known_at_cutoff=context.known_at_cutoff,
            predicate_schema_version=str(claim.get("predicate_schema_version") or ""),
            scope=str(claim.get("scope") or ""),
            polarity=str(claim.get("polarity") or ""),
            modality=str(claim.get("modality") or ""),
            valid_from=_datetime_value(claim.get("valid_from")),
            valid_to=_datetime_value(claim.get("valid_to")),
            observed_at=_datetime_value(claim.get("observed_at")),
            processed_at=_datetime_value(claim.get("processed_at")),
            freshness=_lineage_freshness(lineage),
        )

    def _claim_status(self, claim: Mapping[str, Any], context: InputSelectionContext) -> str:
        if context.cutoff is None:
            return str(claim.get("status") or "unavailable")
        if self.evidence_repository is None:
            return "unavailable"
        status = self.evidence_repository.claim_status_at(
            str(claim["claim_id"]),
            context.cutoff,
            strict_known_by_hunter=context.strict_known_by_hunter,
        )
        return status or "unavailable"

    def _lineage_document_states(
        self,
        lineage: Mapping[str, tuple[dict[str, Any], ...]],
        context: InputSelectionContext,
    ) -> tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...]]:
        document_ids: list[str] = []
        document_statuses: list[str] = []
        authority_statuses: list[str] = []
        for document in lineage.get("documents", ()):
            document_id = str(document["document_id"])
            document_ids.append(document_id)
            if context.cutoff is None:
                document_statuses.append(str(document.get("document_status") or "unavailable"))
                authority_statuses.append(str(document.get("authority_status") or "unavailable"))
                continue
            if self.evidence_repository is None:
                document_statuses.append("unavailable")
                authority_statuses.append("unavailable")
                continue
            document_status = self.evidence_repository.document_status_at(
                document_id,
                context.cutoff,
                strict_known_by_hunter=context.strict_known_by_hunter,
            )
            authority_status = self.evidence_repository.authority_status_at(
                document_id,
                context.cutoff,
                strict_known_by_hunter=context.strict_known_by_hunter,
            )
            document_statuses.append(document_status or "unavailable")
            authority_statuses.append(authority_status or "unavailable")
        return tuple(document_ids), tuple(document_statuses), tuple(authority_statuses)

    def _projection_input(
        self,
        projection: Mapping[str, Any],
        claim: EvidenceClaimInput | None,
        context: InputSelectionContext,
    ) -> RelationshipProjectionInput:
        projection_status = str(projection.get("status") or "unavailable")
        reason = ""
        if claim is None:
            reason = "canonical_claim_not_selected"
            claim_status = "unavailable"
            confidence = round(float(projection.get("confidence") or 0.0), 4)
        else:
            reason = "" if claim.available else f"canonical_claim_unavailable:{claim.reason}"
            claim_status = claim.claim_status
            confidence = min(round(float(projection.get("confidence") or 0.0), 4), claim.confidence)
        if projection_status not in USABLE_CLAIM_STATUSES:
            reason = f"relationship_projection_status_not_usable:{projection_status}"
        if _projection_leaks_after_cutoff(projection, context):
            reason = "relationship_projection_not_known_at_cutoff"
        availability: InputAvailability = "available" if reason == "" else "unavailable"
        return RelationshipProjectionInput(
            relationship_id=str(projection.get("relationship_id") or ""),
            claim_id=str(projection.get("claim_id") or ""),
            subject_entity_id=str(projection.get("subject_entity_id") or ""),
            object_entity_id=str(projection.get("object_entity_id") or ""),
            predicate_id=str(projection.get("predicate_id") or ""),
            projection_status=projection_status,
            claim_status=claim_status,
            confidence=confidence,
            availability=availability,
            reason=reason or "relationship_projection_inherits_available_claim_state",
            replay_mode=context.replay_mode,
            known_at_cutoff=context.known_at_cutoff,
            scope=str(projection.get("scope") or ""),
            polarity=str(projection.get("polarity") or ""),
            modality=str(projection.get("modality") or ""),
            valid_from=_datetime_value(projection.get("valid_from")),
            valid_to=_datetime_value(projection.get("valid_to")),
            created_at=_datetime_value(projection.get("created_at")),
            object_candidate_id=self._candidate_id_for_entity(str(projection.get("object_entity_id") or "")),
        )

    def _candidate_id_for_entity(self, entity_id: str) -> str:
        if self.evidence_repository is None:
            return ""
        entity = self.evidence_repository.entity(entity_id)
        if entity is None:
            return ""
        return str(entity.get("candidate_id") or "")


def _latest_identity_by_candidate(repository: Any) -> dict[str, CandidateIdentity]:
    getter = getattr(repository, "latest_identity_by_candidate", None)
    if getter is None:
        return {}
    identities = getter()
    if isinstance(identities, Mapping):
        return {str(key): value for key, value in identities.items()}
    return {}


def _claim_unavailable_reason(
    claim_status: str,
    document_statuses: tuple[str, ...],
    authority_statuses: tuple[str, ...],
) -> str:
    if claim_status not in USABLE_CLAIM_STATUSES:
        return f"claim_lifecycle_status_not_usable:{claim_status}"
    if not document_statuses:
        return "missing_document_lineage"
    unavailable_document_statuses = sorted(set(document_statuses) - USABLE_DOCUMENT_STATUSES)
    if unavailable_document_statuses:
        return f"document_lifecycle_status_not_usable:{','.join(unavailable_document_statuses)}"
    if not authority_statuses:
        return "missing_authority_lineage"
    unavailable_authority_statuses = sorted(set(authority_statuses) - USABLE_AUTHORITY_STATUSES)
    if unavailable_authority_statuses:
        return f"source_authority_status_not_usable:{','.join(unavailable_authority_statuses)}"
    return ""


def _projection_leaks_after_cutoff(projection: Mapping[str, Any], context: InputSelectionContext) -> bool:
    if context.cutoff is None or not context.strict_known_by_hunter:
        return False
    created_at = _datetime_value(projection.get("created_at"))
    return created_at is None or created_at > context.cutoff


def _lineage_freshness(lineage: Mapping[str, tuple[dict[str, Any], ...]]) -> float:
    document_freshness = [float(document.get("freshness") or 0.0) for document in lineage.get("documents", ())]
    if not document_freshness:
        return 0.0
    return round(min(document_freshness), 4)


def _field(item: Any, name: str, *, default: str = "") -> str:
    if isinstance(item, Mapping):
        return str(item.get(name, default) or default)
    return str(getattr(item, name, default) or default)


def _float_field(item: Any, name: str, *, default: float) -> float:
    if isinstance(item, Mapping):
        return float(item.get(name, default) or default)
    return float(getattr(item, name, default) or default)


def _tuple_field(item: Any, name: str) -> tuple[str, ...]:
    if isinstance(item, Mapping):
        values = item.get(name, ())
    else:
        values = getattr(item, name, ())
    if isinstance(values, str):
        return (values,)
    return tuple(str(value) for value in values if str(value))


def _datetime_value(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str) and value:
        return datetime.fromisoformat(value)
    return None
