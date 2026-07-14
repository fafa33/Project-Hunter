from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from hunter.competitive.models import (
    ALGORITHMIC_PEER_RELATIONSHIP_TYPES,
    COMPARISON_DIMENSION_TYPES,
    AlgorithmicPeerRelationshipType,
    ComparisonDimensionType,
    DimensionMatchStatus,
)

FORBIDDEN_SIMILARITY_DIMENSIONS = frozenset(
    {
        "co_mention",
        "ticker",
        "symbol",
        "popularity",
        "provider_rank",
        "market_cap_proximity",
        "price_movement",
    }
)
DEFAULT_POLICY_ID = "competitive-algorithmic-peer-policy"
DEFAULT_POLICY_VERSION = "competitive-peer-policy-v1"


@dataclass(frozen=True)
class DimensionComparisonDecision:
    dimension_type: ComparisonDimensionType
    subject_value: str
    peer_value: str
    match_status: DimensionMatchStatus
    reason: str


@dataclass(frozen=True)
class AlgorithmicPeerDecision:
    relationship_type: AlgorithmicPeerRelationshipType
    accepted: bool
    similarity: float
    compared_dimension_count: int
    matched_dimension_count: int
    missing_dimension_count: int
    policy_id: str
    policy_version: str
    reason: str
    dimension_results: tuple[DimensionComparisonDecision, ...]


@dataclass(frozen=True)
class AlgorithmicPeerPolicy:
    policy_id: str = DEFAULT_POLICY_ID
    policy_version: str = DEFAULT_POLICY_VERSION
    relationship_type: AlgorithmicPeerRelationshipType = "same_category_similarity"
    dimensions: tuple[ComparisonDimensionType, ...] = ("market_category", "protocol_category", "use_case")
    minimum_similarity: float = 0.5
    minimum_matched_dimensions: int = 1

    def __post_init__(self) -> None:
        _text("policy_id", self.policy_id)
        _text("policy_version", self.policy_version)
        if self.relationship_type not in ALGORITHMIC_PEER_RELATIONSHIP_TYPES:
            msg = f"relationship_type must be one of {sorted(ALGORITHMIC_PEER_RELATIONSHIP_TYPES)}"
            raise ValueError(msg)
        if not self.dimensions:
            msg = "dimensions is required"
            raise ValueError(msg)
        normalized = tuple(dict.fromkeys(self.dimensions))
        for dimension in normalized:
            if dimension not in COMPARISON_DIMENSION_TYPES:
                msg = f"unsupported comparison dimension: {dimension}"
                raise ValueError(msg)
            if dimension in FORBIDDEN_SIMILARITY_DIMENSIONS:
                msg = f"forbidden similarity dimension: {dimension}"
                raise ValueError(msg)
        if not 0.0 <= self.minimum_similarity <= 1.0:
            msg = "minimum_similarity must be between 0 and 1"
            raise ValueError(msg)
        if self.minimum_matched_dimensions <= 0:
            msg = "minimum_matched_dimensions must be positive"
            raise ValueError(msg)
        object.__setattr__(self, "dimensions", normalized)

    def evaluate(
        self,
        *,
        subject_dimensions: Mapping[str, str | None],
        peer_dimensions: Mapping[str, str | None],
    ) -> AlgorithmicPeerDecision:
        dimension_results = self.compare_dimensions(
            subject_dimensions=subject_dimensions,
            peer_dimensions=peer_dimensions,
        )
        compared = len(dimension_results)
        matched = sum(1 for result in dimension_results if result.match_status == "matched")
        missing = sum(1 for result in dimension_results if result.match_status == "missing")
        available = compared - missing
        similarity = 0.0 if available <= 0 else round(matched / available, 4)
        accepted = matched >= self.minimum_matched_dimensions and similarity >= self.minimum_similarity
        if missing == compared:
            reason = "all_dimensions_missing"
        elif accepted:
            reason = "deterministic_similarity_policy_matched"
        else:
            reason = "deterministic_similarity_policy_below_threshold"
        return AlgorithmicPeerDecision(
            relationship_type=self.relationship_type,
            accepted=accepted,
            similarity=similarity,
            compared_dimension_count=compared,
            matched_dimension_count=matched,
            missing_dimension_count=missing,
            policy_id=self.policy_id,
            policy_version=self.policy_version,
            reason=reason,
            dimension_results=dimension_results,
        )

    def compare_dimensions(
        self,
        *,
        subject_dimensions: Mapping[str, str | None],
        peer_dimensions: Mapping[str, str | None],
    ) -> tuple[DimensionComparisonDecision, ...]:
        results: list[DimensionComparisonDecision] = []
        for dimension in self.dimensions:
            subject_value = _normalized(subject_dimensions.get(dimension))
            peer_value = _normalized(peer_dimensions.get(dimension))
            if subject_value is None or peer_value is None:
                results.append(
                    DimensionComparisonDecision(
                        dimension_type=dimension,
                        subject_value=subject_value or "unavailable",
                        peer_value=peer_value or "unavailable",
                        match_status="missing",
                        reason="dimension_value_missing",
                    )
                )
                continue
            if subject_value == peer_value:
                results.append(
                    DimensionComparisonDecision(
                        dimension_type=dimension,
                        subject_value=subject_value,
                        peer_value=peer_value,
                        match_status="matched",
                        reason="normalized_dimension_values_match",
                    )
                )
                continue
            results.append(
                DimensionComparisonDecision(
                    dimension_type=dimension,
                    subject_value=subject_value,
                    peer_value=peer_value,
                    match_status="different",
                    reason="normalized_dimension_values_differ",
                )
            )
        return tuple(results)


def _normalized(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip().casefold()
    return normalized or None


def _text(name: str, value: str) -> None:
    if not str(value).strip():
        msg = f"{name} is required"
        raise ValueError(msg)
