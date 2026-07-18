from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, Protocol

from hunter.execution.canonicalization import canonicalize
from hunter.execution.hashing import stable_identifier

CORPUS_OBSERVATION_SCHEMA_VERSION = "operational-corpus-authority-observation-v1"
AuthorityClassification = Literal["production", "production-descriptive", "canonical-evaluation", "experimental"]
ObservationStatus = Literal[
    "authority_referenced",
    "authority_not_required",
    "unverified",
    "legacy-unverified",
    "unavailable",
    "error",
]

REFERENCEABLE_SEMANTIC_TYPES: Mapping[str, AuthorityClassification] = {
    "market-validation-run": "production",
    "market-validation-project-result": "production",
    "canonical.prediction-evaluation-policy": "canonical-evaluation",
    "canonical.prediction-publication": "canonical-evaluation",
    "canonical.prediction-evaluation": "canonical-evaluation",
    "canonical.prediction-accuracy-snapshot": "canonical-evaluation",
    "canonical.prediction-calibration-snapshot": "canonical-evaluation",
    "experimental.probability-assessment": "experimental",
    "experimental.pattern-assessment": "experimental",
    "experimental.technology-necessity-assessment": "experimental",
    "experimental.standalone-committee-assessment": "experimental",
    "experimental.opportunity-metric-snapshot": "experimental",
    "experimental.opportunity-assessment": "experimental",
    "fused-intelligence": "experimental",
    "opportunity-timing-assessment": "experimental",
}

ANALYTICAL_CATEGORIES = frozenset(
    {
        "score",
        "ranking",
        "recommendation",
        "prediction",
        "assessment",
        "decision",
        "correctness",
        "accuracy",
        "calibration",
    }
)
ANALYTICAL_FIELDS = frozenset(
    {
        "score",
        "hunter_score",
        "ranking",
        "rankings",
        "recommendation",
        "recommendations",
        "prediction",
        "predictions",
        "assessment",
        "assessments",
        "decision",
        "decisions",
        "correctness",
        "accuracy",
        "calibration",
    }
)


@dataclass(frozen=True, slots=True)
class AuthorityReference:
    source_store: str
    semantic_type: str
    record_id: str
    record_version: str
    canonical_hash: str | None
    authority_classification: AuthorityClassification
    target_id: str
    entity_type: str
    effective_at: datetime | None = None
    recorded_at: datetime | None = None
    known_at: datetime | None = None

    def __post_init__(self) -> None:
        for name in ("source_store", "semantic_type", "record_id", "record_version", "target_id", "entity_type"):
            if not getattr(self, name).strip():
                raise ValueError(f"{name} is required")
        for name in ("effective_at", "recorded_at", "known_at"):
            value = getattr(self, name)
            if value is not None:
                if value.tzinfo is None:
                    raise ValueError(f"{name} must be timezone-aware")
                object.__setattr__(self, name, value.astimezone(UTC))


@dataclass(frozen=True, slots=True)
class ResolvedAuthorityRecord:
    source_store: str
    semantic_type: str
    record_id: str
    record_version: str
    canonical_hash: str | None
    authority_classification: AuthorityClassification
    target_id: str
    entity_type: str
    effective_at: datetime | None
    recorded_at: datetime | None
    known_at: datetime | None


class AuthorityReferenceResolver(Protocol):
    """Read-only exact-record resolver; implementations must have no side effects."""

    def resolve(self, reference: AuthorityReference) -> ResolvedAuthorityRecord | None: ...


@dataclass(frozen=True, slots=True)
class OperationalObservationEnvelope:
    schema_version: str
    observation_id: str
    recorded_at: datetime
    observation_category: str
    authority_classification: str
    authority_references: tuple[AuthorityReference, ...]
    target_id: str
    entity_type: str
    effective_at: datetime | None
    known_at: datetime | None
    status: ObservationStatus
    authority_diagnostic: str
    ownership_statement: str
    observation_payload: dict[str, Any]

    def __post_init__(self) -> None:
        if self.schema_version != CORPUS_OBSERVATION_SCHEMA_VERSION:
            raise ValueError("unsupported corpus observation schema")
        for name in ("observation_id", "observation_category", "target_id", "entity_type"):
            if not getattr(self, name).strip():
                raise ValueError(f"{name} is required")
        if self.recorded_at.tzinfo is None:
            raise ValueError("recorded_at must be timezone-aware")
        object.__setattr__(self, "recorded_at", self.recorded_at.astimezone(UTC))
        for name in ("effective_at", "known_at"):
            value = getattr(self, name)
            if value is not None:
                if value.tzinfo is None:
                    raise ValueError(f"{name} must be timezone-aware")
                object.__setattr__(self, name, value.astimezone(UTC))
        canonicalize(self.observation_payload)
        object.__setattr__(self, "observation_payload", dict(self.observation_payload))
        if self.ownership_statement != "downstream operational observation; not analytical authority":
            raise ValueError("corpus ownership statement is fixed")

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "observation_id": self.observation_id,
            "recorded_at": self.recorded_at.isoformat(),
            "observation_category": self.observation_category,
            "authority_classification": self.authority_classification,
            "authority_references": [_reference_dict(item) for item in self.authority_references],
            "target_id": self.target_id,
            "entity_type": self.entity_type,
            "effective_at": self.effective_at.isoformat() if self.effective_at else None,
            "known_at": self.known_at.isoformat() if self.known_at else None,
            "status": self.status,
            "authority_diagnostic": self.authority_diagnostic,
            "ownership_statement": self.ownership_statement,
            "observation_payload": dict(self.observation_payload),
        }


def authorize_corpus_observation(
    *,
    observation_category: str,
    target_id: str,
    entity_type: str,
    payload: Mapping[str, Any],
    recorded_at: datetime,
    authority_references: tuple[AuthorityReference, ...] = (),
    resolver: AuthorityReferenceResolver | None = None,
) -> OperationalObservationEnvelope:
    if recorded_at.tzinfo is None:
        raise ValueError("recorded_at must be timezone-aware")
    caller_payload = dict(payload)
    canonicalize(caller_payload)
    analytical = _analytical(observation_category, caller_payload)
    status: ObservationStatus = "authority_not_required"
    diagnostic = "operational observation contains no analytical authority claim"
    classification = "operational-only"
    effective_at = None
    known_at = None

    if analytical:
        status, diagnostic, classification, effective_at, known_at = _validate_references(
            target_id,
            entity_type,
            authority_references,
            resolver,
        )
    elif authority_references:
        status, diagnostic, classification, effective_at, known_at = _validate_references(
            target_id,
            entity_type,
            authority_references,
            resolver,
        )

    observation_id = stable_identifier(
        "operational-corpus-authority-observation",
        {
            "category": observation_category,
            "target_id": target_id,
            "entity_type": entity_type,
            "payload": caller_payload,
            "references": tuple(_reference_dict(item) for item in authority_references),
            "recorded_at": recorded_at,
        },
        schema_version=CORPUS_OBSERVATION_SCHEMA_VERSION,
    )
    return OperationalObservationEnvelope(
        CORPUS_OBSERVATION_SCHEMA_VERSION,
        observation_id,
        recorded_at,
        observation_category,
        classification,
        tuple(authority_references),
        target_id,
        entity_type,
        effective_at,
        known_at,
        status,
        diagnostic,
        "downstream operational observation; not analytical authority",
        caller_payload,
    )


def classify_legacy_corpus_record(record: Mapping[str, Any]) -> dict[str, Any]:
    """Return a read projection without mutating or rewriting the legacy row."""

    return {
        "schema_version": None,
        "status": "legacy-unverified",
        "authority_classification": "unverified",
        "authority_references": [],
        "ownership_statement": "downstream operational observation; not analytical authority",
        "observation_payload": dict(record),
    }


def append_corpus_observation(path: str | Path, envelope: OperationalObservationEnvelope) -> None:
    observation_path = Path(path)
    observation_path.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(envelope.as_dict(), sort_keys=True, separators=(",", ":"))
    existing = {}
    if observation_path.exists():
        for line in observation_path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                row = json.loads(line)
                existing[str(row.get("observation_id"))] = json.dumps(row, sort_keys=True, separators=(",", ":"))
    current = existing.get(envelope.observation_id)
    if current == encoded:
        return
    if current is not None:
        raise ValueError("immutable corpus observation identity conflict")
    with observation_path.open("a", encoding="utf-8") as handle:
        handle.write(encoded)
        handle.write("\n")


def _validate_references(
    target_id: str,
    entity_type: str,
    references: tuple[AuthorityReference, ...],
    resolver: AuthorityReferenceResolver | None,
) -> tuple[ObservationStatus, str, str, datetime | None, datetime | None]:
    if not references:
        return "unverified", "analytical-looking observation has no authority reference", "unverified", None, None
    expected_classes = []
    resolved_rows = []
    for reference in references:
        expected = REFERENCEABLE_SEMANTIC_TYPES.get(reference.semantic_type)
        if expected is None:
            return "unverified", "authority semantic type is not allowlisted", "unverified", None, None
        expected_classes.append(expected)
        if reference.authority_classification != expected:
            return (
                "unverified",
                "declared authority classification conflicts with the allowlisted classification",
                expected,
                None,
                None,
            )
        if reference.target_id != target_id or reference.entity_type != entity_type:
            return "unverified", "authority reference target/entity mismatch", expected, None, None
        if resolver is None:
            return "unverified", "authority reference was not resolved", expected, None, None
        try:
            resolved = resolver.resolve(reference)
        except Exception as exc:
            return "unavailable", f"authority resolver unavailable: {type(exc).__name__}", expected, None, None
        if resolved is None:
            return "unavailable", "authority record/store is unavailable", expected, None, None
        if not _reference_matches(reference, resolved, expected):
            return "unverified", "resolved authority record does not exactly match the reference", expected, None, None
        resolved_rows.append(resolved)
    classification = "experimental" if "experimental" in expected_classes else expected_classes[0]
    effective = max((item.effective_at for item in resolved_rows if item.effective_at is not None), default=None)
    known = max((item.known_at for item in resolved_rows if item.known_at is not None), default=None)
    return (
        "authority_referenced",
        "exact immutable authority reference resolved read-only",
        classification,
        effective,
        known,
    )


def _reference_matches(
    reference: AuthorityReference,
    resolved: ResolvedAuthorityRecord,
    expected: AuthorityClassification,
) -> bool:
    return all(
        (
            resolved.source_store == reference.source_store,
            resolved.semantic_type == reference.semantic_type,
            resolved.record_id == reference.record_id,
            resolved.record_version == reference.record_version,
            resolved.canonical_hash == reference.canonical_hash,
            resolved.authority_classification == expected,
            resolved.target_id == reference.target_id,
            resolved.entity_type == reference.entity_type,
            resolved.effective_at == reference.effective_at,
            resolved.recorded_at == reference.recorded_at,
            resolved.known_at == reference.known_at,
        )
    )


def _analytical(category: str, payload: Mapping[str, Any]) -> bool:
    normalized = category.strip().lower().replace("_", "-")
    return normalized in ANALYTICAL_CATEGORIES or any(str(key).lower() in ANALYTICAL_FIELDS for key in payload)


def _reference_dict(reference: AuthorityReference) -> dict[str, Any]:
    return {
        "source_store": reference.source_store,
        "semantic_type": reference.semantic_type,
        "record_id": reference.record_id,
        "record_version": reference.record_version,
        "canonical_hash": reference.canonical_hash,
        "authority_classification": reference.authority_classification,
        "target_id": reference.target_id,
        "entity_type": reference.entity_type,
        "effective_at": reference.effective_at.isoformat() if reference.effective_at else None,
        "recorded_at": reference.recorded_at.isoformat() if reference.recorded_at else None,
        "known_at": reference.known_at.isoformat() if reference.known_at else None,
    }
