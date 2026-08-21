"""Structured audit record and table cardinality regressions.

Two related contracts are pinned here:

* every candidate data row under a recognized Findings Matrix header is either
  parsed as a valid row or reported as a deterministic error -- it may never be
  silently discarded, because a discarded row disappears from cardinality
  checks, severity validation, and verdict derivation alike;
* every canonical finding field appears exactly once per record, so a later
  duplicate cannot overwrite an earlier value and erase the rendered evidence.
"""

from __future__ import annotations

import hunter_artifact_preflight
from test_hunter_artifact_preflight_semantics import _good_audit, _nonblocking_finding_record

ACCEPTED = ["0001", "0002"]
MATRIX_ROW = "| F-001 | A | None | Minor auditability debt | NO | Evidence |"


def _validate(text: str) -> list[str]:
    return hunter_artifact_preflight.validate_audit_text(text, accepted_adrs=ACCEPTED)


def _add_row(row: str) -> str:
    return _good_audit().replace(MATRIX_ROW, f"{MATRIX_ROW}\n{row}")


def test_truncated_class_c_row_is_rejected_not_skipped() -> None:
    errors = _validate(_add_row("| F-999 | C | Material impact |"))

    assert any("F-999" in error for error in errors), errors


def test_truncated_class_d_row_is_rejected_not_skipped() -> None:
    errors = _validate(_add_row("| F-998 | D | Fundamental gap |"))

    assert any("F-998" in error for error in errors), errors


def test_row_missing_only_blocks_adr_is_rejected() -> None:
    errors = _validate(_add_row("| F-997 | C | Impact | Consequence | Evidence |"))

    assert any("F-997" in error for error in errors), errors


def test_row_missing_the_finding_id_is_rejected() -> None:
    errors = _validate(_add_row("|  | C | Impact | Consequence | YES | Evidence |"))

    assert any("Finding" in error for error in errors), errors


def test_complete_row_passes() -> None:
    text = (
        _add_row("| F-002 | B | Quality | Reduced auditability | NO | Evidence |")
        .replace(
            _nonblocking_finding_record(),
            _nonblocking_finding_record() + "\n" + _nonblocking_finding_record("B").replace("F-001", "F-002"),
        )
        .replace("- `READY_FOR_ADR`", "- `READY_FOR_ADR_WITH_MINOR_FINDINGS`")
    )

    assert _validate(text) == []


def test_complete_row_with_escaped_pipe_still_passes() -> None:
    text = _good_audit().replace(
        MATRIX_ROW,
        r"| F-001 | A | Choosing A \| B is unclear | Minor auditability debt | NO | Evidence |",
    )

    assert _validate(text) == []


def test_separator_row_is_not_treated_as_malformed_data() -> None:
    assert _validate(_good_audit()) == []


def test_blank_placeholder_row_is_not_treated_as_malformed_data() -> None:
    assert _validate(_add_row("|  |  |  |  |  |  |")) == []


def test_truncated_row_cannot_escape_cardinality_severity_or_verdict_checks() -> None:
    """A hidden Class C blocker must not survive under READY_FOR_ADR."""
    errors = _validate(_add_row("| F-999 | C | Material impact |"))

    assert errors, "truncated blocking row disappeared entirely"
    assert not any("Missing mandatory audit heading" in error for error in errors), errors


def test_duplicate_contradictory_severity_field_is_rejected() -> None:
    text = _good_audit().replace("- **Severity:** `A`", "- **Severity:** `D`\n- **Severity:** `A`")

    errors = _validate(text)
    assert any("Severity" in error and "exactly once" in error for error in errors), errors


def test_duplicate_contradictory_blocks_adr_field_is_rejected() -> None:
    text = _good_audit().replace("- **Blocks ADR:** `NO`", "- **Blocks ADR:** `YES`\n- **Blocks ADR:** `NO`")

    errors = _validate(text)
    assert any("Blocks ADR" in error and "exactly once" in error for error in errors), errors


def test_duplicate_identical_field_value_is_rejected() -> None:
    text = _good_audit().replace("- **Severity:** `A`", "- **Severity:** `A`\n- **Severity:** `A`")

    errors = _validate(text)
    assert any("Severity" in error and "exactly once" in error for error in errors), errors


def test_duplicate_evidence_field_is_rejected() -> None:
    text = _good_audit().replace(
        "- **Evidence:** Direct repository evidence.",
        "- **Evidence:** Direct repository evidence.\n- **Evidence:** A second evidence claim.",
    )

    errors = _validate(text)
    assert any("Evidence" in error and "exactly once" in error for error in errors), errors


def test_case_equivalent_duplicate_labels_are_rejected() -> None:
    text = _good_audit().replace("- **Severity:** `A`", "- **SEVERITY:** `D`\n- **Severity:** `A`")

    errors = _validate(text)
    assert any("exactly once" in error for error in errors), errors


def test_exactly_one_of_each_mandatory_field_passes() -> None:
    assert _validate(_good_audit()) == []


def test_duplicate_field_in_one_record_does_not_corrupt_the_adjacent_record() -> None:
    second = _nonblocking_finding_record("B").replace("F-001", "F-002")
    text = (
        _good_audit()
        .replace(_nonblocking_finding_record(), _nonblocking_finding_record() + "\n" + second)
        .replace(MATRIX_ROW, MATRIX_ROW + "\n| F-002 | B | Quality | Reduced auditability | NO | Evidence |")
        .replace("- `READY_FOR_ADR`", "- `READY_FOR_ADR_WITH_MINOR_FINDINGS`")
        .replace("- **Category:** Audit clarity.", "- **Category:** Audit clarity.\n- **Category:** Duplicate.", 1)
    )

    errors = _validate(text)
    assert any("Category" in error and "exactly once" in error for error in errors), errors
    assert not any("F-002" in error and "exactly once" in error for error in errors), errors


def test_duplicate_field_text_outside_findings_does_not_trigger_the_rule() -> None:
    text = _good_audit().replace(
        "Track F-001 as non-blocking cleanup.",
        "Track F-001 as non-blocking cleanup.\n\n- **Severity:** `A`\n- **Severity:** `D`",
    )

    assert _validate(text) == []


def test_duplicate_fields_inside_nonsemantic_regions_do_not_count() -> None:
    decoys = (
        "\n```markdown\n- **Severity:** `A`\n- **Severity:** `D`\n```\n",
        "\n<!--\n- **Severity:** `A`\n- **Severity:** `D`\n-->\n",
        "\n<pre>\n- **Severity:** `A`\n- **Severity:** `D`\n</pre>\n",
    )
    for decoy in decoys:
        assert _validate(_good_audit() + decoy) == [], decoy
