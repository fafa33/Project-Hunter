"""Shared hostile-review request constants for the Issue #585 focused tests.

One copy of the writer judgement and defect-registry fixtures so the recovery
and finalizer test suites describe the same governed candidate without
duplicating the constants between files.
"""

from __future__ import annotations

import hunter_pre_ready_review as review

ISSUE = "585"
CRITERION = "Required proof"
JUDGEMENT = {
    "acceptance_criteria": [
        {"id": "AC-1", "criterion": CRITERION, "evidence": "verified against the change", "verdict": "satisfied"}
    ],
    "defect_families": [{"family": "FAM-SRC", "evidence": "adversarial sweep complete", "outcome": "clear"}],
    "findings": [],
    "adversarial_dimensions": review.REQUIRED_ADVERSARIAL_DIMENSIONS,
}
REGISTRY = {
    "version": 1,
    "families": [
        {
            "id": "FAM-SRC",
            "title": "source-scope",
            "invariant": "test scope",
            "applicability": {"changed_paths": ["src/"], "rationale": "test"},
            "prevention": {"mechanism": "test"},
        }
    ],
}
