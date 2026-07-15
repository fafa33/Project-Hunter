from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from hunter.sufficiency.identity import data_requirement_id, proxy_signal_policy_id
from hunter.sufficiency.models import DataRequirement
from hunter.sufficiency.policies import (
    DEFAULT_SUFFICIENCY_POLICY_ID,
    DEFAULT_SUFFICIENCY_POLICY_VERSION,
    DEFAULT_SUFFICIENCY_SCHEMA_VERSION,
    DegradedModePolicy,
    ProxySignalPolicy,
    default_degraded_mode_policy,
)


@dataclass(frozen=True)
class DataRequirementRegistry:
    requirements: tuple[DataRequirement, ...]
    degraded_mode_policy: DegradedModePolicy
    proxy_policies: tuple[ProxySignalPolicy, ...]
    registry_version: str = "data-requirement-registry-v1"

    def __post_init__(self) -> None:
        if not self.requirements:
            msg = "requirements are required"
            raise ValueError(msg)
        requirement_ids = [requirement.requirement_id for requirement in self.requirements]
        if len(requirement_ids) != len(set(requirement_ids)):
            msg = "duplicate requirement_id"
            raise ValueError(msg)
        proxy_types = [policy.proxy_type for policy in self.proxy_policies]
        if len(proxy_types) != len(set(proxy_types)):
            msg = "duplicate proxy policy type"
            raise ValueError(msg)
        if not str(self.registry_version).strip():
            msg = "registry_version is required"
            raise ValueError(msg)

    def get(self, requirement_id: str) -> DataRequirement:
        for requirement in self.requirements:
            if requirement.requirement_id == requirement_id:
                return requirement
        msg = f"unknown requirement_id: {requirement_id}"
        raise KeyError(msg)

    def by_engine(self, engine_id: str, *, analysis_purpose: str | None = None) -> tuple[DataRequirement, ...]:
        matches = tuple(
            requirement
            for requirement in self.requirements
            if requirement.engine_id == engine_id
            and (analysis_purpose is None or requirement.analysis_purpose == analysis_purpose)
        )
        return matches

    def proxy_policy(self, proxy_type: str) -> ProxySignalPolicy:
        for policy in self.proxy_policies:
            if policy.proxy_type == proxy_type:
                return policy
        msg = f"unknown proxy_type: {proxy_type}"
        raise KeyError(msg)


def default_data_requirement_registry(*, created_at: datetime) -> DataRequirementRegistry:
    policy = default_degraded_mode_policy(effective_at=created_at, recorded_at=created_at)
    proxy_policies = (
        ProxySignalPolicy(
            policy_id=proxy_signal_policy_id(
                proxy_type="market_proxy",
                policy_version=DEFAULT_SUFFICIENCY_POLICY_VERSION,
                schema_version=DEFAULT_SUFFICIENCY_SCHEMA_VERSION,
            ),
            policy_version=DEFAULT_SUFFICIENCY_POLICY_VERSION,
            proxy_type="market_proxy",
            allowed_requirement_kinds=("proxy_context", "source_quality", "freshness"),
            limitation_text="Market proxy signals are context only and do not satisfy missing direct observations.",
            confidence_impact=0.25,
            may_satisfy_direct_observation=False,
            effective_at=created_at,
            recorded_at=created_at,
            schema_version=DEFAULT_SUFFICIENCY_SCHEMA_VERSION,
        ),
        ProxySignalPolicy(
            policy_id=proxy_signal_policy_id(
                proxy_type="competitive_proxy",
                policy_version=DEFAULT_SUFFICIENCY_POLICY_VERSION,
                schema_version=DEFAULT_SUFFICIENCY_SCHEMA_VERSION,
            ),
            policy_version=DEFAULT_SUFFICIENCY_POLICY_VERSION,
            proxy_type="competitive_proxy",
            allowed_requirement_kinds=("proxy_context", "source_quality"),
            limitation_text="Competitive proxy signals explain context only and are not evidence-backed competition.",
            confidence_impact=0.2,
            may_satisfy_direct_observation=False,
            effective_at=created_at,
            recorded_at=created_at,
            schema_version=DEFAULT_SUFFICIENCY_SCHEMA_VERSION,
        ),
    )
    requirements = (
        _requirement(
            engine_id="evidence_intelligence",
            analysis_purpose="claim_explainability",
            output_field="source_authority",
            requirement_kind="direct_observation",
            evidence_domain="evidence_intelligence",
            required_source_types=("source_authority_verification_event",),
            direct_observation_required=True,
            proxy_allowed=False,
            accepted_proxy_types=(),
            blocking_level="required_for_output",
            created_at=created_at,
        ),
        _requirement(
            engine_id="competitive",
            analysis_purpose="peer_set_report",
            output_field="evidence_backed_competitors",
            requirement_kind="direct_observation",
            evidence_domain="competitive",
            required_source_types=("competitive_relationship", "knowledge_claim"),
            direct_observation_required=True,
            proxy_allowed=True,
            accepted_proxy_types=("competitive_proxy",),
            blocking_level="required_for_high_confidence",
            created_at=created_at,
        ),
        _requirement(
            engine_id="market_validation",
            analysis_purpose="market_context_report",
            output_field="market_data_freshness",
            requirement_kind="freshness",
            evidence_domain="market",
            required_source_types=("market_provider_observation",),
            direct_observation_required=False,
            proxy_allowed=True,
            accepted_proxy_types=("market_proxy",),
            blocking_level="required_for_full_report",
            created_at=created_at,
        ),
    )
    return DataRequirementRegistry(
        requirements=requirements, degraded_mode_policy=policy, proxy_policies=proxy_policies
    )


def _requirement(
    *,
    engine_id: str,
    analysis_purpose: str,
    output_field: str,
    requirement_kind: str,
    evidence_domain: str,
    required_source_types: tuple[str, ...],
    direct_observation_required: bool,
    proxy_allowed: bool,
    accepted_proxy_types: tuple[str, ...],
    blocking_level: str,
    created_at: datetime,
) -> DataRequirement:
    return DataRequirement(
        requirement_id=data_requirement_id(
            engine_id=engine_id,
            analysis_purpose=analysis_purpose,
            output_field=output_field,
            requirement_kind=requirement_kind,
            policy_id=DEFAULT_SUFFICIENCY_POLICY_ID,
            policy_version=DEFAULT_SUFFICIENCY_POLICY_VERSION,
            schema_version=DEFAULT_SUFFICIENCY_SCHEMA_VERSION,
        ),
        engine_id=engine_id,
        analysis_purpose=analysis_purpose,
        output_field=output_field,
        requirement_kind=requirement_kind,  # type: ignore[arg-type]
        evidence_domain=evidence_domain,  # type: ignore[arg-type]
        required_entity_type="candidate",
        required_source_types=required_source_types,
        direct_observation_required=direct_observation_required,
        proxy_allowed=proxy_allowed,
        accepted_proxy_types=accepted_proxy_types,  # type: ignore[arg-type]
        minimum_freshness_seconds=86_400,
        minimum_source_authority="verified_or_persisted_hunter_evidence",
        minimum_lineage_depth=1,
        minimum_confidence=0.0,
        historical_required=True,
        blocking_level=blocking_level,  # type: ignore[arg-type]
        policy_id=DEFAULT_SUFFICIENCY_POLICY_ID,
        policy_version=DEFAULT_SUFFICIENCY_POLICY_VERSION,
        effective_at=created_at,
        recorded_at=created_at,
        schema_version=DEFAULT_SUFFICIENCY_SCHEMA_VERSION,
    )
