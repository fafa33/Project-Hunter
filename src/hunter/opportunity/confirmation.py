from __future__ import annotations

from hunter.opportunity.configuration import OpportunityTimingConfig
from hunter.opportunity.models import ConfirmationState
from hunter.persistence.records import FusedIntelligenceRecord


def assess_confirmation(
    records: tuple[FusedIntelligenceRecord, ...], config: OpportunityTimingConfig
) -> ConfirmationState:
    categories: set[str] = set()
    independent_groups: set[str] = set()
    dependent_groups = 0
    for record in records:
        for signal in record.unified_signals:
            category = str(signal.get("category", "")).strip()
            confidence = float(signal.get("confidence", 0.0) or 0.0)
            if category and confidence >= 0.5:
                categories.add(category)
        for group in record.canonical_evidence_groups:
            if _is_dependent(group):
                dependent_groups += 1
                continue
            key = str(group.get("canonical_key", ""))
            if key:
                independent_groups.add(key)
    required = set(config.required_categories)
    required_count = max(1, len(required))
    coverage = len(required.intersection(categories)) / required_count
    missing = tuple(sorted(required - categories))
    enough_categories = coverage >= config.min_category_coverage
    enough_groups = len(independent_groups) >= config.min_confirmation_groups
    confirmed = enough_categories and enough_groups
    score = min(1.0, coverage * 0.5 + (len(independent_groups) / max(1, config.min_confirmation_groups)) * 0.5)
    reasons: list[str] = []
    if not enough_categories:
        reasons.append(f"category coverage {coverage:.2f} below required {config.min_category_coverage:.2f}")
    if not enough_groups:
        reasons.append(f"{len(independent_groups)} independent groups below required {config.min_confirmation_groups}")
    if dependent_groups:
        reasons.append(f"{dependent_groups} dependent groups excluded")
    return ConfirmationState(
        confirmed_categories=tuple(categories),
        missing_categories=missing,
        independent_group_count=len(independent_groups),
        required_group_count=config.min_confirmation_groups,
        confirmed=confirmed,
        score=score,
        summary=f"{len(categories)} categories and {len(independent_groups)} independent evidence groups confirmed. "
        + ("; ".join(reasons) if reasons else "Confirmation requirements satisfied."),
    )


def _is_dependent(group: dict[str, object]) -> bool:
    classification = str(group.get("dependency_classification", ""))
    return classification in {
        "shared-evidence-lineage",
        "shared-evidence-reference",
        "shared-evidence-id",
        "dependent",
    }
