from __future__ import annotations

from hunter.historical.cutoff import reject_future_evidence
from hunter.historical.models import HistoricalBiasValidation, HistoricalEvidenceSnapshot, HistoricalValidationCase


def validate_bias_controls(
    case: HistoricalValidationCase,
    snapshot: HistoricalEvidenceSnapshot,
    *,
    current_universe: tuple[str, ...],
) -> HistoricalBiasValidation:
    violations = list(reject_future_evidence(snapshot.evidence))
    if case.project_lifecycle_state in {"failed", "collapsed", "abandoned"} and case.project_id in current_universe:
        violations.append("failed_project_in_current_universe_only")
    survivorship_passed = case.project_id not in current_universe or case.project_lifecycle_state in {
        "active",
        "renamed",
        "migrated",
        "failed",
        "collapsed",
        "abandoned",
    }
    return HistoricalBiasValidation(
        case_id=case.case_id,
        leakage_passed=not any("cutoff" in item for item in violations),
        survivorship_passed=survivorship_passed,
        violations=tuple(sorted(violations)),
    )


def survivorship_scan(cases: tuple[HistoricalValidationCase, ...]) -> tuple[str, ...]:
    states = {case.project_lifecycle_state for case in cases}
    required = {"active", "failed", "collapsed", "delisted", "abandoned"}
    missing = tuple(sorted(required - states))
    return tuple(f"missing_lifecycle_state:{state}" for state in missing)
