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


def _completion_items(text: str) -> list[str]:
    return [line for line in text.splitlines() if line.startswith("- [x] ")]


def _with_completion_checklist(entries: list[str]) -> str:
    text = _good_audit()
    for item in _completion_items(text):
        text = text.replace(item + "\n", "")
    body = "".join(entry + "\n" for entry in entries)
    return text.replace("## Audit Completion Check\n", f"## Audit Completion Check\n\n{body}")


CANONICAL_COMPLETION_ENTRIES = _completion_items(_good_audit())
ALL_TOPIC_WORDS = (
    "- [x] artifact revision scope evidence dimension finding mandatory field "
    "consequence matrix verdict re-audit recommend"
)


def test_one_fabricated_entry_cannot_satisfy_the_whole_checklist() -> None:
    errors = _validate(_with_completion_checklist([ALL_TOPIC_WORDS]))

    assert any("missing required completed items" in error for error in errors), errors


def test_two_broad_entries_cannot_impersonate_ten_distinct_checks() -> None:
    entries = [
        "- [x] artifact revision scope evidence dimension",
        "- [x] finding mandatory field consequence matrix verdict re-audit recommend",
    ]

    errors = _validate(_with_completion_checklist(entries))
    assert any("missing required completed items" in error for error in errors), errors


def test_nine_distinct_entries_with_one_canonical_topic_missing_is_rejected() -> None:
    entries = [entry for entry in CANONICAL_COMPLETION_ENTRIES if "matrix" not in entry.lower()]
    assert len(entries) == len(CANONICAL_COMPLETION_ENTRIES) - 1

    errors = _validate(_with_completion_checklist(entries))
    assert any("findings matrix completed" in error for error in errors), errors


def test_ten_distinct_canonical_entries_pass() -> None:
    assert _validate(_with_completion_checklist(CANONICAL_COMPLETION_ENTRIES)) == []


def test_ten_entries_with_one_unchecked_is_rejected() -> None:
    entries = list(CANONICAL_COMPLETION_ENTRIES)
    entries[3] = entries[3].replace("- [x] ", "- [ ] ")

    errors = _validate(_with_completion_checklist(entries))
    assert any("is not complete" in error for error in errors), errors


def test_duplicating_one_topic_while_omitting_another_is_rejected() -> None:
    entries = [entry for entry in CANONICAL_COMPLETION_ENTRIES if "matrix" not in entry.lower()]
    entries.append("- [x] Audit scope identified")

    errors = _validate(_with_completion_checklist(entries))
    assert any("findings matrix completed" in error for error in errors), errors


def test_equivalent_wording_for_each_canonical_topic_passes() -> None:
    entries = [
        "- [x] The reviewed artifact and its exact revision are identified",
        "- [x] The audit scope is stated",
        "- [x] All evidence sources are listed",
        "- [x] Applicable dimensions were assessed",
        "- [x] Each finding carries every mandatory field",
        "- [x] Blocking findings demonstrate decision consequence",
        "- [x] The findings matrix is complete",
        "- [x] The verdict follows severity and materiality",
        "- [x] Targeted reaudit rules were followed",
        "- [x] No option was recommended or ranked without authorization",
    ]

    assert _validate(_with_completion_checklist(entries)) == []


def test_extra_legitimate_checked_entries_do_not_invalidate_the_audit() -> None:
    entries = CANONICAL_COMPLETION_ENTRIES + ["- [x] Reviewer notes archived"]

    assert _validate(_with_completion_checklist(entries)) == []


def test_reordered_canonical_entries_pass() -> None:
    entries = list(reversed(CANONICAL_COMPLETION_ENTRIES))

    assert _validate(_with_completion_checklist(entries)) == []


def test_checklist_decoys_outside_the_completion_section_do_not_count() -> None:
    text = _with_completion_checklist(
        [entry for entry in CANONICAL_COMPLETION_ENTRIES if "matrix" not in entry.lower()]
    ).replace(
        "Track F-001 as non-blocking cleanup.",
        "Track F-001 as non-blocking cleanup.\n\n- [x] Findings matrix completed",
    )

    errors = _validate(text)
    assert any("findings matrix completed" in error for error in errors), errors


def test_checklist_decoys_in_literal_regions_do_not_count() -> None:
    text = (
        _with_completion_checklist([entry for entry in CANONICAL_COMPLETION_ENTRIES if "matrix" not in entry.lower()])
        + "\n```markdown\n- [x] Findings matrix completed\n```\n"
    )

    errors = _validate(text)
    assert any("findings matrix completed" in error for error in errors), errors


def _matrix_block(delimiter: str | None, rows: list[str]) -> str:
    header = "| Finding | Class | Decision impact | Consequence if ignored | Blocks ADR | Evidence |"
    lines = [header]
    if delimiter is not None:
        lines.append(delimiter)
    lines.extend(rows)
    return "\n".join(lines)


CANONICAL_MATRIX = _matrix_block("|---|---|---|---|---|---|", [MATRIX_ROW])


def _with_matrix(block: str) -> str:
    return _good_audit().replace(CANONICAL_MATRIX, block)


def test_matrix_header_without_a_delimiter_row_is_rejected() -> None:
    errors = _validate(_with_matrix(_matrix_block(None, [MATRIX_ROW])))

    assert any("delimiter" in error for error in errors), errors


def test_matrix_header_with_an_invalid_delimiter_row_is_rejected() -> None:
    for delimiter in ("|===|===|===|===|===|===|", "|---|---|", "| a | b | c | d | e | f |"):
        errors = _validate(_with_matrix(_matrix_block(delimiter, [MATRIX_ROW])))
        assert any("delimiter" in error for error in errors), delimiter


def test_matrix_header_with_an_immediate_valid_delimiter_passes() -> None:
    assert _validate(_good_audit()) == []


def test_delimiter_appearing_after_unrelated_prose_does_not_bind_to_the_header() -> None:
    block = _matrix_block(None, []) + "\n\nUnrelated prose.\n\n|---|---|---|---|---|---|\n" + MATRIX_ROW

    errors = _validate(_with_matrix(block))
    assert any("delimiter" in error for error in errors), errors


def test_an_unrelated_pipe_table_cannot_rescue_a_malformed_findings_matrix() -> None:
    block = _matrix_block(None, [MATRIX_ROW]) + "\n\n| Other | Table |\n|---|---|\n| a | b |"

    errors = _validate(_with_matrix(block))
    assert any("delimiter" in error for error in errors), errors


def test_valid_table_with_zero_data_rows_keeps_canonical_zero_finding_semantics() -> None:
    text = _with_matrix(_matrix_block("|---|---|---|---|---|---|", [])).replace(
        _nonblocking_finding_record(), "No findings were identified."
    )

    assert _validate(text) == []


def test_rows_after_the_rendered_table_ends_are_not_matrix_rows() -> None:
    block = CANONICAL_MATRIX + "\n\nRendered prose ends the table.\n\n| F-999 | C | a | b | YES | c |"

    errors = _validate(_with_matrix(block))
    assert not any("F-999" in error for error in errors), errors


def test_pipe_prefixed_prose_outside_the_matrix_does_not_enter_matrix_semantics() -> None:
    text = _good_audit().replace(
        "Track F-001 as non-blocking cleanup.",
        "Track F-001 as non-blocking cleanup.\n\n| F-999 | D | a | b | YES | c |",
    )

    assert _validate(text) == []


def test_delimiter_row_is_never_treated_as_a_data_row() -> None:
    errors = _validate(_good_audit())

    assert errors == [], errors


def test_fake_matrix_inside_literal_regions_does_not_count() -> None:
    fake = "\n```markdown\n" + CANONICAL_MATRIX + "\n```\n"

    assert _validate(_good_audit() + fake) == []


def test_canonically_valid_delimiter_forms_are_accepted() -> None:
    """Alignment colons, single dashes, and padding are all valid delimiters."""
    delimiters = (
        "|-|-|-|-|-|-|",
        "|:---|:---:|---:|---|---|---|",
        "| --- | --- | --- | --- | --- | --- |",
    )
    for delimiter in delimiters:
        assert _validate(_with_matrix(_matrix_block(delimiter, [MATRIX_ROW]))) == [], delimiter


def test_delimiter_must_be_adjacent_not_merely_present() -> None:
    header = "| Finding | Class | Decision impact | Consequence if ignored | Blocks ADR | Evidence |"
    separated = f"{header}\n\n|---|---|---|---|---|---|\n{MATRIX_ROW}"

    errors = _validate(_with_matrix(separated))
    assert any("delimiter" in error for error in errors), errors


def test_alternative_checklist_markers_and_indentation_remain_valid() -> None:
    variants = (
        [entry.replace("- [x]", "* [x]") for entry in CANONICAL_COMPLETION_ENTRIES],
        [entry.replace("[x]", "[X]") for entry in CANONICAL_COMPLETION_ENTRIES],
        ["   " + entry for entry in CANONICAL_COMPLETION_ENTRIES],
    )
    for entries in variants:
        assert _validate(_with_completion_checklist(entries)) == [], entries[0]


def test_repeating_one_topic_ten_times_cannot_satisfy_the_checklist() -> None:
    errors = _validate(_with_completion_checklist(["- [x] Audit scope identified"] * 10))

    assert any("missing required completed items" in error for error in errors), errors
