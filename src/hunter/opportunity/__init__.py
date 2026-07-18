"""Experimental Fusion-backed opportunity timing package for v2.1.x.

Production timing is implemented by hunter.timing and persisted in
TimingRepository for the current Market Validation runtime.
"""

from hunter.opportunity.authority import (
    CURRENT_OPPORTUNITY_FACTORS,
    CanonicalMarketValidationOpportunityAdapter,
    EmptyOpportunityAuthoritySource,
    OpportunityAssemblyResult,
    OpportunityAssessmentService,
    OpportunityFactorAuthority,
    OpportunityFactorDiagnostic,
    opportunity_factor_authorities,
)
from hunter.opportunity.configuration import (
    OpportunityConfig,
    OpportunityTimingConfig,
    load_opportunity_config,
    load_opportunity_timing_config,
    opportunity_config_from_mapping,
    opportunity_timing_config_from_mapping,
)
from hunter.opportunity.engine import (
    OpportunityEngine,
    OpportunityTimingEngine,
    opportunity_assessment_to_record,
    opportunity_factor_trace,
    opportunity_snapshot_from_assessment,
)
from hunter.opportunity.metrics import OpportunityMetricSnapshot
from hunter.opportunity.models import (
    AccelerationState,
    ConfirmationState,
    DivergenceState,
    HistoricalComparison,
    OpportunityAssessment,
    OpportunityFactor,
    OpportunityTimingAssessment,
    RiskState,
    TemporalComparison,
)
from hunter.opportunity.persistence_service import (
    OPPORTUNITY_ASSESSMENT_SEMANTIC_TYPE,
    OPPORTUNITY_SNAPSHOT_SEMANTIC_TYPE,
    AuthorizedOpportunityPersistencePlan,
    ExperimentalOpportunityRepository,
    OpportunityPersistenceContext,
    OpportunityPersistenceResult,
    OpportunityPersistenceService,
)
from hunter.opportunity.ranking import rank_opportunities
from hunter.opportunity.renderer import OpportunityReportRenderer

__all__ = [
    "AccelerationState",
    "ConfirmationState",
    "CURRENT_OPPORTUNITY_FACTORS",
    "CanonicalMarketValidationOpportunityAdapter",
    "DivergenceState",
    "HistoricalComparison",
    "EmptyOpportunityAuthoritySource",
    "OpportunityAssemblyResult",
    "OpportunityAssessment",
    "OpportunityAssessmentService",
    "OpportunityConfig",
    "OpportunityEngine",
    "OpportunityFactor",
    "OpportunityFactorAuthority",
    "OpportunityFactorDiagnostic",
    "OpportunityMetricSnapshot",
    "OpportunityPersistenceContext",
    "OpportunityPersistenceResult",
    "OpportunityPersistenceService",
    "OpportunityReportRenderer",
    "OpportunityTimingAssessment",
    "OpportunityTimingConfig",
    "OpportunityTimingEngine",
    "OPPORTUNITY_ASSESSMENT_SEMANTIC_TYPE",
    "OPPORTUNITY_SNAPSHOT_SEMANTIC_TYPE",
    "RiskState",
    "TemporalComparison",
    "load_opportunity_config",
    "load_opportunity_timing_config",
    "opportunity_config_from_mapping",
    "opportunity_factor_authorities",
    "opportunity_factor_trace",
    "opportunity_assessment_to_record",
    "opportunity_snapshot_from_assessment",
    "opportunity_timing_config_from_mapping",
    "rank_opportunities",
    "AuthorizedOpportunityPersistencePlan",
    "ExperimentalOpportunityRepository",
]
