from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from hunter.evidence_intelligence.models import (
    ENTITY_TYPES,
    LITERAL_VALUE_TYPES,
    MODALITIES,
    POLARITIES,
    SUPPORT_LEVELS,
    EntityType,
    EvidenceSpan,
    LiteralValueType,
    Modality,
    Polarity,
    PredicateRegistry,
    SupportLevel,
)

LITERAL_REQUIRED_TYPES = frozenset({"integer", "decimal", "date", "datetime", "url", "address", "repository"})
FORBIDDEN_CONCLUSION_TYPES = frozenset({"inferred", "predictive", "causal", "ownership", "investment"})
WEAK_SUPPORT_TERMS = (
    "may ",
    "might ",
    "could ",
    "predict",
    "prediction",
    "expected to",
    "likely to",
    "investment",
    "undervalued",
    "overvalued",
    "because of",
    "causes ",
)


@dataclass(frozen=True)
class EvidenceClassification:
    document_id: str
    categories: tuple[str, ...]
    rationale: tuple[str, ...]
    confidence: float


@dataclass(frozen=True)
class ValidatedEntityProposal:
    name: str
    entity_type: EntityType
    span_id: str
    support_text: str
    confidence: float


@dataclass(frozen=True)
class ValidatedClaimProposal:
    predicate_id: str
    subject_name: str
    subject_type: EntityType
    object_name: str | None
    object_type: EntityType | None
    literal_value: str | int | float | bool | None
    literal_value_type: LiteralValueType | None
    span_id: str
    support_level: SupportLevel
    support_text: str
    modality: Modality
    polarity: Polarity


@dataclass(frozen=True)
class ValidationRejection:
    proposal_type: str
    index: int
    reason: str
    detail: str


@dataclass(frozen=True)
class ExtractionValidationResult:
    classification: EvidenceClassification
    entities: tuple[ValidatedEntityProposal, ...]
    claims: tuple[ValidatedClaimProposal, ...]
    rejections: tuple[ValidationRejection, ...]


class ExtractionValidationService:
    def classify(self, *, document_id: str, spans: Sequence[EvidenceSpan]) -> EvidenceClassification:
        text = " ".join(span.excerpt.lower() for span in spans)
        categories: list[str] = []
        rationale: list[str] = []
        rules = (
            ("repository", ("github", "repository", "commit", "release")),
            ("deployment", ("contract", "address", "deployed", "chain")),
            ("governance", ("governance", "dao", "proposal", "vote")),
            ("integration", ("integrates", "integration", "depends on", "runs on")),
            ("market", ("market", "tvl", "volume", "liquidity")),
        )
        for category, keywords in rules:
            matched = tuple(keyword for keyword in keywords if keyword in text)
            if matched:
                categories.append(category)
                rationale.append(f"{category}:{','.join(matched)}")
        if not categories:
            categories.append("unclassified")
            rationale.append("no deterministic category keyword matched")
        confidence = min(1.0, 0.5 + (0.1 * len(categories)))
        return EvidenceClassification(
            document_id=document_id,
            categories=tuple(categories),
            rationale=tuple(rationale),
            confidence=round(confidence, 2),
        )

    def validate(
        self,
        *,
        document_id: str,
        spans: Sequence[EvidenceSpan],
        payload: Mapping[str, Any],
        predicate_registry: PredicateRegistry,
    ) -> ExtractionValidationResult:
        spans_by_id = {span.span_id: span for span in spans}
        rejections: list[ValidationRejection] = []
        entities = self._validate_entities(payload.get("entities", []), spans_by_id, rejections)
        claims = self._validate_claims(payload.get("claims", []), spans_by_id, predicate_registry, rejections)
        return ExtractionValidationResult(
            classification=self.classify(document_id=document_id, spans=spans),
            entities=tuple(entities),
            claims=tuple(claims),
            rejections=tuple(rejections),
        )

    def _validate_entities(
        self,
        proposals: object,
        spans_by_id: Mapping[str, EvidenceSpan],
        rejections: list[ValidationRejection],
    ) -> list[ValidatedEntityProposal]:
        if not isinstance(proposals, list):
            rejections.append(_rejection("entity", 0, "invalid_entities_payload", "entities must be a list"))
            return []
        accepted: list[ValidatedEntityProposal] = []
        for index, proposal in enumerate(proposals):
            if not isinstance(proposal, Mapping):
                rejections.append(_rejection("entity", index, "invalid_entity_payload", "entity must be an object"))
                continue
            name = str(proposal.get("name", "")).strip()
            entity_type = str(proposal.get("entity_type", "")).strip()
            span_id = str(proposal.get("span_id", "")).strip()
            support_text = str(proposal.get("support_text", "")).strip()
            if not name:
                rejections.append(_rejection("entity", index, "missing_name", "entity name is required"))
                continue
            if entity_type not in ENTITY_TYPES:
                rejections.append(_rejection("entity", index, "unsupported_entity_type", entity_type))
                continue
            if not _support_text_matches_span(span_id, support_text, spans_by_id):
                rejections.append(
                    _rejection("entity", index, "missing_literal_support", "support text must appear in span")
                )
                continue
            accepted.append(
                ValidatedEntityProposal(
                    name=name,
                    entity_type=entity_type,  # type: ignore[arg-type]
                    span_id=span_id,
                    support_text=support_text,
                    confidence=_confidence(proposal.get("confidence", 0.0)),
                )
            )
        return accepted

    def _validate_claims(
        self,
        proposals: object,
        spans_by_id: Mapping[str, EvidenceSpan],
        predicate_registry: PredicateRegistry,
        rejections: list[ValidationRejection],
    ) -> list[ValidatedClaimProposal]:
        if not isinstance(proposals, list):
            rejections.append(_rejection("claim", 0, "invalid_claims_payload", "claims must be a list"))
            return []
        accepted: list[ValidatedClaimProposal] = []
        for index, proposal in enumerate(proposals):
            if not isinstance(proposal, Mapping):
                rejections.append(_rejection("claim", index, "invalid_claim_payload", "claim must be an object"))
                continue
            claim = self._validate_claim(index, proposal, spans_by_id, predicate_registry, rejections)
            if claim is not None:
                accepted.append(claim)
        return accepted

    def _validate_claim(
        self,
        index: int,
        proposal: Mapping[str, Any],
        spans_by_id: Mapping[str, EvidenceSpan],
        predicate_registry: PredicateRegistry,
        rejections: list[ValidationRejection],
    ) -> ValidatedClaimProposal | None:
        conclusion_type = str(proposal.get("conclusion_type", "")).strip().lower()
        if conclusion_type in FORBIDDEN_CONCLUSION_TYPES:
            rejections.append(_rejection("claim", index, "unsupported_conclusion_type", conclusion_type))
            return None

        predicate_id = str(proposal.get("predicate_id", "")).strip()
        try:
            predicate_registry.get(predicate_id)
        except (KeyError, ValueError):
            rejections.append(_rejection("claim", index, "unsupported_predicate", predicate_id))
            return None

        support_text = str(proposal.get("support_text", "")).strip()
        span_id = str(proposal.get("span_id", "")).strip()
        if not _support_text_matches_span(span_id, support_text, spans_by_id):
            rejections.append(_rejection("claim", index, "missing_support", "support text must appear in span"))
            return None
        if _contains_weak_support(support_text):
            rejections.append(_rejection("claim", index, "unsupported_or_inferred_support", support_text))
            return None

        support_level = str(proposal.get("support_level", "")).strip()
        if support_level not in SUPPORT_LEVELS:
            rejections.append(_rejection("claim", index, "invalid_support_level", support_level))
            return None
        if support_level == "semantic_support" and proposal.get("explicit_support") is not True:
            rejections.append(
                _rejection("claim", index, "semantic_support_not_explicit", "explicit_support must be true")
            )
            return None

        subject_name = str(proposal.get("subject_name", "")).strip()
        subject_type = str(proposal.get("subject_type", "")).strip()
        object_name = _optional_string(proposal.get("object_name"))
        object_type = _optional_string(proposal.get("object_type"))
        literal_value = proposal.get("literal_value")
        literal_value_type = _optional_string(proposal.get("literal_value_type"))
        modality = str(proposal.get("modality", "asserted")).strip()
        polarity = str(proposal.get("polarity", "positive")).strip()
        if not subject_name:
            rejections.append(_rejection("claim", index, "missing_subject", "subject_name is required"))
            return None
        if subject_type not in ENTITY_TYPES:
            rejections.append(_rejection("claim", index, "unsupported_subject_type", subject_type))
            return None
        if object_type is not None and object_type not in ENTITY_TYPES:
            rejections.append(_rejection("claim", index, "unsupported_object_type", object_type))
            return None
        if literal_value_type is not None and literal_value_type not in LITERAL_VALUE_TYPES:
            rejections.append(_rejection("claim", index, "unsupported_literal_value_type", literal_value_type))
            return None
        if modality not in MODALITIES or polarity not in POLARITIES:
            rejections.append(_rejection("claim", index, "unsupported_modality_or_polarity", f"{modality}:{polarity}"))
            return None

        if _literal_support_required(literal_value_type, proposal) and not _has_literal_support(
            literal_value, support_text
        ):
            rejections.append(_rejection("claim", index, "literal_support_required", str(literal_value)))
            return None
        try:
            predicate_registry.validate_claim_shape(
                predicate_id=predicate_id,
                subject_type=subject_type,  # type: ignore[arg-type]
                object_type=object_type,  # type: ignore[arg-type]
                literal_value_type=literal_value_type,  # type: ignore[arg-type]
                modality=modality,  # type: ignore[arg-type]
                polarity=polarity,  # type: ignore[arg-type]
            )
        except ValueError as exc:
            rejections.append(_rejection("claim", index, "predicate_registry_rejected", str(exc)))
            return None

        return ValidatedClaimProposal(
            predicate_id=predicate_id,
            subject_name=subject_name,
            subject_type=subject_type,  # type: ignore[arg-type]
            object_name=object_name,
            object_type=object_type,  # type: ignore[arg-type]
            literal_value=literal_value if isinstance(literal_value, str | int | float | bool) else None,
            literal_value_type=literal_value_type,  # type: ignore[arg-type]
            span_id=span_id,
            support_level=support_level,  # type: ignore[arg-type]
            support_text=support_text,
            modality=modality,  # type: ignore[arg-type]
            polarity=polarity,  # type: ignore[arg-type]
        )


def _support_text_matches_span(span_id: str, support_text: str, spans_by_id: Mapping[str, EvidenceSpan]) -> bool:
    if not span_id or not support_text:
        return False
    span = spans_by_id.get(span_id)
    if span is None:
        return False
    return support_text.lower() in span.excerpt.lower()


def _literal_support_required(literal_value_type: str | None, proposal: Mapping[str, Any]) -> bool:
    return literal_value_type in LITERAL_REQUIRED_TYPES or proposal.get("direct_quote") is True


def _has_literal_support(literal_value: object, support_text: str) -> bool:
    if literal_value is None:
        return False
    return str(literal_value).lower() in support_text.lower()


def _contains_weak_support(support_text: str) -> bool:
    lowered = support_text.lower()
    return any(term in lowered for term in WEAK_SUPPORT_TERMS)


def _optional_string(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _confidence(value: object) -> float:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, min(1.0, round(numeric, 4)))


def _rejection(proposal_type: str, index: int, reason: str, detail: str) -> ValidationRejection:
    return ValidationRejection(proposal_type=proposal_type, index=index, reason=reason, detail=detail)
