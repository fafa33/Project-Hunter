from __future__ import annotations

import copy
from typing import Any

import hunter_artifact_preflight


def _minor_finding_record() -> str:
    return """### F-001 — Minor fixture finding

- **Evidence:** Direct repository evidence.
- **Location:** `docs/example.md`, wording boundary.
- **Category:** Audit clarity.
- **Severity:** `A`
- **Decision impact:** No material decision impact.
- **Consequence if ignored:** Minor auditability debt remains.
- **Required action:** Track as non-blocking follow-up.
- **Blocks ADR:** `NO`
"""


def _good_audit(
    *,
    audit_type: str = "FULL",
    accepted_adrs: tuple[str, ...] = ("0001", "0002"),
    scope: str = "Full independent architecture audit.",
    evidence: str = "- Immutable repository evidence at the reviewed revision.",
    evidence_cutoff: str = "",
) -> str:
    coverage = "\n".join(f"- ADR {adr}: reviewed; out of scope for this audit." for adr in accepted_adrs)
    coverage_section = ""
    if audit_type == "FULL":
        coverage_section = f"""
## Accepted ADR Coverage

{coverage}
"""

    cutoff_line = ""
    if evidence_cutoff:
        cutoff_line = f"- Evidence cutoff: `{evidence_cutoff}`\n"

    return f"""# Independent Architecture Audit

## Metadata

- Reviewed artifact: `docs/example.md`
- Reviewed revision: `0123456789abcdef0123456789abcdef01234567`
- Audit type: `{audit_type}`
- Auditor: Jules — independent architecture audit agent
- Audit date: 2026-08-20
{cutoff_line}
## Audit Scope

{scope}

## Evidence Sources Examined

{evidence}
{coverage_section}
## Prior Review Finding Re-Verification

- No prior review findings are in scope for this fixture.

## Dimension Results

| Dimension | Result | Evidence and rationale | Finding IDs |
|---|---|---|---|
| Problem correctness | PASS | Evidence-backed. | F-001 |

## Findings

{_minor_finding_record()}

## Findings Matrix

| Finding | Class | Decision impact | Consequence if ignored | Blocks ADR | Evidence |
|---|---|---|---|---|---|
| F-001 | A | None | Minor auditability debt | NO | Evidence |

## Verdict Derivation

Highest unresolved severity: A. No blocking materiality.

## Final Verdict

- `READY_FOR_ADR`

## Required Corrections or Conditions

None.

## Non-Blocking Follow-Up

Track F-001 as non-blocking cleanup.

## Audit Completion Check

- [x] Complete
"""


def _without_prior_review_section(text: str) -> str:
    return text.replace(
        "## Prior Review Finding Re-Verification\n\n" "- No prior review findings are in scope for this fixture.\n\n",
        "",
    )


def _blocking_finding_record() -> str:
    return """### F-001 — Material finding

- **Evidence:** Direct repository evidence.
- **Location:** `docs/example.md`, decision boundary.
- **Category:** Architecture decision quality.
- **Severity:** `C`
- **Decision impact:** The decision basis can become unreliable.
- **Consequence if ignored:** An unsupported architectural conclusion could be selected.
- **Required action:** Correct the material defect before progression.
- **Blocks ADR:** `YES`
"""


def test_good_audit_passes_without_unnecessary_progression_prose_or_cutoff() -> None:
    assert (
        hunter_artifact_preflight.validate_audit_text(
            _good_audit(),
            accepted_adrs=["0001", "0002"],
        )
        == []
    )


def test_pending_audit_is_rejected() -> None:
    text = _good_audit().replace(
        "## Findings\n\n",
        "## Findings\n\n`PENDING — auditor must complete findings.`\n\n",
        1,
    )
    errors = hunter_artifact_preflight.validate_audit_text(
        text,
        accepted_adrs=["0001", "0002"],
    )
    assert any("PENDING" in error for error in errors)


def test_audit_requires_immutable_reviewed_revision() -> None:
    text = _good_audit().replace(
        "- Reviewed revision: `0123456789abcdef0123456789abcdef01234567`\n",
        "",
    )
    errors = hunter_artifact_preflight.validate_audit_text(
        text,
        accepted_adrs=["0001", "0002"],
    )
    assert any("Reviewed revision" in error for error in errors)


def test_mutable_evidence_requires_valid_cutoff() -> None:
    text = _good_audit(evidence="- PR #288 review history and Issue #287 state.")
    errors = hunter_artifact_preflight.validate_audit_text(
        text,
        accepted_adrs=["0001", "0002"],
    )
    assert any("Evidence cutoff" in error for error in errors)

    corrected = _good_audit(
        evidence="- PR #288 review history and Issue #287 state.",
        evidence_cutoff="2026-08-20T13:00:00+02:00",
    )
    assert (
        hunter_artifact_preflight.validate_audit_text(
            corrected,
            accepted_adrs=["0001", "0002"],
        )
        == []
    )


def test_full_audit_requires_every_accepted_adr() -> None:
    errors = hunter_artifact_preflight.validate_audit_text(
        _good_audit(accepted_adrs=("0001",)),
        accepted_adrs=["0001", "0002"],
    )
    assert any("Accepted ADR 0002" in error for error in errors)


def test_full_audit_rejects_arbitrary_adr_mentions_as_accounting() -> None:
    text = _good_audit().replace(
        "- ADR 0002: reviewed; out of scope for this audit.",
        "The evidence set also mentions ADR 0002, but records no review disposition.",
    )
    errors = hunter_artifact_preflight.validate_audit_text(
        text,
        accepted_adrs=["0001", "0002"],
    )
    assert any("Accepted ADR 0002" in error for error in errors)


def test_full_audit_accepts_structured_accounting_under_equivalent_heading() -> None:
    text = _good_audit().replace(
        "## Accepted ADR Coverage",
        "## Pinned Decision Accounting",
    )
    assert (
        hunter_artifact_preflight.validate_audit_text(
            text,
            accepted_adrs=["0001", "0002"],
        )
        == []
    )


def test_targeted_audit_does_not_require_repository_wide_adr_accounting() -> None:
    text = _good_audit(
        audit_type="TARGETED",
        accepted_adrs=(),
        scope="Targeted re-audit of previously blocking finding F-003 and changed sections.",
    )
    assert (
        hunter_artifact_preflight.validate_audit_text(
            text,
            accepted_adrs=["0001", "0002"],
        )
        == []
    )


def test_required_headings_must_be_real_level_two_headings() -> None:
    text = _good_audit().replace("## Findings Matrix\n", "")
    text = text.replace(
        "Full independent architecture audit.",
        "Full independent architecture audit. The phrase ## Findings Matrix may appear in prose.",
    )
    errors = hunter_artifact_preflight.validate_audit_text(
        text,
        accepted_adrs=["0001", "0002"],
    )
    assert any("Missing mandatory audit heading: ## Findings Matrix" in error for error in errors)


def test_final_verdict_requires_a_canonical_declaration_not_a_token_mention() -> None:
    text = _good_audit().replace(
        "- `READY_FOR_ADR`",
        "The audit is not READY_FOR_ADR.",
    )
    errors = hunter_artifact_preflight.validate_audit_text(
        text,
        accepted_adrs=["0001", "0002"],
    )
    assert any("canonical declared audit verdict" in error for error in errors)


def test_prior_review_scope_requires_verification_section() -> None:
    text = _without_prior_review_section(
        _good_audit(
            scope="Full audit including prior review PR #288 findings.",
        )
    )
    errors = hunter_artifact_preflight.validate_audit_text(
        text,
        accepted_adrs=["0001", "0002"],
    )
    assert any("Prior Review Finding Re-Verification" in error for error in errors)


def test_current_pr_reference_alone_does_not_trigger_prior_review_requirement() -> None:
    text = _without_prior_review_section(
        _good_audit(
            audit_type="TARGETED",
            accepted_adrs=(),
            scope="Targeted audit of the current PR #293 implementation changes.",
        )
    )
    assert (
        hunter_artifact_preflight.validate_audit_text(
            text,
            accepted_adrs=["0001", "0002"],
        )
        == []
    )


def test_prior_review_evidence_must_include_reference_evidence_and_consequence() -> None:
    text = _good_audit(scope="Full audit including prior review PR #288 findings.").replace(
        "- No prior review findings are in scope for this fixture.",
        "- PR288-F01 was checked.",
    )
    errors = hunter_artifact_preflight.validate_audit_text(
        text,
        accepted_adrs=["0001", "0002"],
    )
    assert any("evidence and decision consequence" in error for error in errors)


def test_finding_record_class_label_is_rejected_in_favor_of_canonical_severity() -> None:
    text = _good_audit().replace(
        "- **Severity:** `A`",
        "- **Class:** `A`",
    )
    errors = hunter_artifact_preflight.validate_audit_text(
        text,
        accepted_adrs=["0001", "0002"],
    )
    assert any("canonical `Severity`" in error for error in errors)


def test_canonical_severity_field_is_accepted() -> None:
    assert (
        hunter_artifact_preflight.validate_audit_text(
            _good_audit(),
            accepted_adrs=["0001", "0002"],
        )
        == []
    )


def test_conditional_verdict_requires_real_conditions_not_duplicated_gate_prose() -> None:
    text = (
        _good_audit()
        .replace("- `READY_FOR_ADR`", "- `CONDITIONAL_ADR_READY`")
        .replace(
            "## Required Corrections or Conditions\n\nNone.",
            "## Required Corrections or Conditions\n\nNone",
        )
    )
    errors = hunter_artifact_preflight.validate_audit_text(
        text,
        accepted_adrs=["0001", "0002"],
    )
    assert any("explicit mandatory conditions" in error for error in errors)


def test_blocking_verdict_requires_a_blocks_adr_yes_finding() -> None:
    text = _good_audit().replace("- `READY_FOR_ADR`", "- `ADPR_REVISION_REQUIRED`")
    errors = hunter_artifact_preflight.validate_audit_text(
        text,
        accepted_adrs=["0001", "0002"],
    )
    assert any("Blocks ADR = YES" in error for error in errors)


def test_blocks_adr_yes_must_come_from_the_blocks_adr_field_or_column() -> None:
    text = (
        _good_audit()
        .replace("- `READY_FOR_ADR`", "- `ADPR_REVISION_REQUIRED`")
        .replace(
            "| F-001 | A | None | Minor auditability debt | NO | Evidence |",
            "| F-001 | C | YES | Consequence | NO | Evidence |",
        )
    )
    errors = hunter_artifact_preflight.validate_audit_text(
        text,
        accepted_adrs=["0001", "0002"],
    )
    assert any("Blocks ADR = YES" in error for error in errors)

    corrected = text.replace(
        "| F-001 | C | YES | Consequence | NO | Evidence |",
        "| F-001 | C | Impact | Consequence | YES | Evidence |",
    ).replace(_minor_finding_record(), _blocking_finding_record())
    assert (
        hunter_artifact_preflight.validate_audit_text(
            corrected,
            accepted_adrs=["0001", "0002"],
        )
        == []
    )


def test_registry_rejects_dropped_understood_defect_class() -> None:
    data: dict[str, Any] = {
        "version": 1,
        "defects": [
            {
                "id": defect_id,
                "class": "x",
                "source": "x",
                "classification": "systemic",
                "guard_boundary": "x",
                "guard": "x",
                "test": "x",
                "status": "guarded",
            }
            for defect_id in sorted(hunter_artifact_preflight.REQUIRED_DEFECT_IDS)
        ],
    }
    assert hunter_artifact_preflight.validate_registry(data) == []

    broken = copy.deepcopy(data)
    broken["defects"] = broken["defects"][1:]
    errors = hunter_artifact_preflight.validate_registry(broken)
    assert any("dropped required understood defect classes" in error for error in errors)
