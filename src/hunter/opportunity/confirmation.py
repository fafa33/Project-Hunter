from __future__ import annotations

from hunter.opportunity.configuration import OpportunityTimingConfig
from hunter.opportunity.models import ConfirmationState
from hunter.persistence.records import FusedIntelligenceRecord


def assess_confirmation(records: tuple[FusedIntelligenceRecord, ...], config: OpportunityTimingConfig) -> ConfirmationState:
    categories: set[str] = set()
    independent_groups: set[str] = set()
    for record in records:
        for signal in record.unified_signals:
            category = str(signal.get("category", "")).strip()
            confidence = float(signal.get("confidence", 0.0) or 0.0)
            if category and confidence >= 0.5:
                categories.add(category)
        for group in record.canonical_evidence_groups:
            if str(group.get("dependency_classification", "")) == "single-source":
                continue
            key = str(group.get("canonical_key", ""))
            if key:
                independent_groups.add(key)
    required = set(config.required_categories)
    missing = tuple(sorted(required - categories))
    confirmed = len(independent_groups) >= config.min_confirmation_groups and bool(categories)
    score = min(1.0, (len(categories) / max(1, len(required))) * 0.5 + (len(independent_groups) / max(1, config.min_confirmation_groups)) * 0.5)
    return ConfirmationState(
        confirmed_categories=tuple(categories),
        missing_categories=missing,
        independent_group_count=len(independent_groups),
        required_group_count=config.min_confirmation_groups,
        confirmed=confirmed,
        score=score,
        summary=f"{len(categories)} categories and {len(independent_groups)} independent evidence groups confirmed.",
    )
