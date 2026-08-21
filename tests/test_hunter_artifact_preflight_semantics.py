from __future__ import annotations

import hunter_artifact_preflight


def _nonblocking_finding_record(severity: str = "A") -> str:
    return f"""### F-001 — Non-blocking fixture finding

- **Evidence:** Direct repository evidence.
- **Location:** `docs/example.md`, wording boundary.
- **Category:** Audit clarity.
- **Severity:** `{severity}`
- **Decision impact:** No material decision impact.
- **Consequence if ignored:** Minor auditability debt remains.
- **Required action:** Track as non-blocking follow-up.
- **Blocks ADR:** `NO`
"""


def _good_audit() -> str:
    return f"""# Independent Architecture Audit

## Metadata

- Reviewed artifact: `docs/example.md`
- Reviewed revision: `0123456789abcdef0123456789abcdef01234567`
- Audit type: `FULL`
- Auditor: Jules — independent architecture audit agent
- Audit date: 2026-08-20

## Audit Scope

Full independent architecture audit.

## Evidence Sources Examined

- Immutable repository evidence at the reviewed revision.

## Accepted ADR Coverage

- ADR 0001: reviewed; out of scope for this audit.
- ADR 0002: reviewed; out of scope for this audit.

## Prior Review Finding Re-Verification

- No prior review findings are in scope for this fixture.

## Dimension Results

| Dimension | Result | Evidence and rationale | Finding IDs |
|---|---|---|---|
| Problem correctness | PASS | Evidence-backed. | F-001 |

## Findings

{_nonblocking_finding_record()}

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

- [x] Exact artifact and revision identified
- [x] Audit scope identified
- [x] Evidence sources listed
- [x] Applicable dimensions assessed
- [x] Every finding includes all mandatory fields
- [x] Every Class C or D finding demonstrates decision consequence
- [x] Findings matrix completed
- [x] Verdict derived from severity and materiality
- [x] Targeted re-audit rule followed where applicable
- [x] Auditor did not recommend or rank options unless explicitly authorized
"""


def _blocking_record(severity: str) -> str:
    return f"""### F-001 — Blocking fixture finding

- **Evidence:** Direct repository evidence.
- **Location:** `docs/example.md`, decision boundary.
- **Category:** Architecture decision quality.
- **Severity:** `{severity}`
- **Decision impact:** The decision basis can become unreliable.
- **Consequence if ignored:** An unsupported architectural conclusion could be selected.
- **Required action:** Correct the material defect before progression.
- **Blocks ADR:** `YES`
"""


def _validate(text: str) -> list[str]:
    return hunter_artifact_preflight.validate_audit_text(
        text,
        accepted_adrs=["0001", "0002"],
    )


def _empty_section(text: str, heading: str) -> str:
    start = text.index(heading) + len(heading)
    next_heading = text.find("\n## ", start)
    if next_heading < 0:
        return text[:start] + "\n"
    return text[:start] + "\n" + text[next_heading:]


def test_html_comment_cannot_impersonate_audit_structure() -> None:
    errors = _validate(f"<!--\n{_good_audit()}\n-->\n")

    assert any("Missing mandatory audit heading: ## Metadata" in error for error in errors)
    assert any("canonical declared audit verdict" in error for error in errors)


def test_indented_literal_rows_cannot_supply_adr_accounting_or_blocking_matrix() -> None:
    text = _good_audit().replace(
        "- ADR 0002: reviewed; out of scope for this audit.",
        "    - ADR 0002: reviewed; out of scope for this audit.",
    )
    errors = _validate(text)
    assert any("Accepted ADR 0002" in error for error in errors)

    blocking = (
        _good_audit()
        .replace("- `READY_FOR_ADR`", "- `ADPR_REVISION_REQUIRED`")
        .replace(
            "| F-001 | A | None | Minor auditability debt | NO | Evidence |",
            "    | F-001 | C | Impact | Consequence | YES | Evidence |",
        )
    )
    errors = _validate(blocking)
    assert any("Blocks ADR = YES" in error for error in errors)


def test_rendered_audit_ignores_commented_decoy_structure() -> None:
    decoy = """
<!--
## Metadata
- Auditor: PENDING

## Findings Matrix
| Finding | Class | Blocks ADR |
|---|---|---|
| F-999 | D | YES |

## Final Verdict
- `ARCHITECTURE_NOT_READY`
-->
"""

    assert _validate(_good_audit() + decoy) == []


def test_ready_verdict_rejects_unresolved_class_c() -> None:
    text = (
        _good_audit()
        .replace(_nonblocking_finding_record(), _blocking_record("C"))
        .replace(
            "| F-001 | A | None | Minor auditability debt | NO | Evidence |",
            "| F-001 | C | Impact | Consequence | YES | Evidence |",
        )
    )

    errors = _validate(text)
    assert any("Class C finding requires ADPR_REVISION_REQUIRED" in error for error in errors)


def test_adpr_revision_required_accepts_class_c_but_not_class_d() -> None:
    class_c = (
        _good_audit()
        .replace(_nonblocking_finding_record(), _blocking_record("C"))
        .replace("- `READY_FOR_ADR`", "- `ADPR_REVISION_REQUIRED`")
        .replace(
            "| F-001 | A | None | Minor auditability debt | NO | Evidence |",
            "| F-001 | C | Impact | Consequence | YES | Evidence |",
        )
    )
    assert _validate(class_c) == []

    class_d = class_c.replace("- **Severity:** `C`", "- **Severity:** `D`").replace(
        "| F-001 | C | Impact | Consequence | YES | Evidence |",
        "| F-001 | D | Impact | Consequence | YES | Evidence |",
    )
    errors = _validate(class_d)
    assert any("Class D finding requires ARCHITECTURE_NOT_READY" in error for error in errors)


def test_architecture_not_ready_requires_class_d() -> None:
    class_c = (
        _good_audit()
        .replace(_nonblocking_finding_record(), _blocking_record("C"))
        .replace("- `READY_FOR_ADR`", "- `ARCHITECTURE_NOT_READY`")
        .replace(
            "| F-001 | A | None | Minor auditability debt | NO | Evidence |",
            "| F-001 | C | Impact | Consequence | YES | Evidence |",
        )
    )
    errors = _validate(class_c)
    assert any("ARCHITECTURE_NOT_READY requires at least one unresolved Class D" in error for error in errors)

    class_d = class_c.replace("- **Severity:** `C`", "- **Severity:** `D`").replace(
        "| F-001 | C | Impact | Consequence | YES | Evidence |",
        "| F-001 | D | Impact | Consequence | YES | Evidence |",
    )
    assert _validate(class_d) == []


def test_class_c_and_d_findings_must_block_adr() -> None:
    text = (
        _good_audit()
        .replace(_nonblocking_finding_record(), _blocking_record("C").replace("`YES`", "`NO`"))
        .replace("- `READY_FOR_ADR`", "- `ADPR_REVISION_REQUIRED`")
        .replace(
            "| F-001 | A | None | Minor auditability debt | NO | Evidence |",
            "| F-001 | C | Impact | Consequence | NO | Evidence |",
        )
    )

    errors = _validate(text)
    assert any("Class C finding F-001 must set Blocks ADR = YES" in error for error in errors)


def test_raw_html_literal_blocks_cannot_impersonate_audit_structure() -> None:
    cases = (
        ("<pre>", "</pre>"),
        ('<SCRIPT type="text/plain">', "</SCRIPT>"),
        ('<style media="all">', "</style>"),
        ('<textarea name="audit">', "</textarea>"),
    )
    for opening, closing in cases:
        errors = _validate(f"{opening}\n{_good_audit()}\n{closing}\n")
        assert any("Missing mandatory audit heading: ## Metadata" in error for error in errors), opening
        assert any("canonical declared audit verdict" in error for error in errors), opening


def test_unclosed_raw_html_literal_blocks_mask_to_eof() -> None:
    openings = (
        "<pre>",
        '<SCRIPT type="text/plain">',
        '<style media="all">',
        '<textarea name="audit">',
    )
    for opening in openings:
        errors = _validate(f"{opening}\n{_good_audit()}")
        assert any("Missing mandatory audit heading: ## Metadata" in error for error in errors), opening
        assert any("canonical declared audit verdict" in error for error in errors), opening


def test_raw_html_decoys_do_not_hide_real_rendered_audit() -> None:
    cases = (
        ("<pre>", "</pre>"),
        ('<SCRIPT type="text/plain">', "</SCRIPT>"),
        ('<style media="all">', "</style>"),
        ('<textarea name="audit">', "</textarea>"),
    )
    for opening, closing in cases:
        decoy = f"{opening}\n{_good_audit().replace('Jules', 'PENDING')}\n{closing}\n"
        assert _validate(_good_audit() + decoy) == [], opening


def test_every_matrix_finding_requires_complete_matching_record() -> None:
    missing = _good_audit().replace(_nonblocking_finding_record(), "No finding record supplied.")
    errors = _validate(missing)
    assert any("Finding F-001 must have a complete finding record" in error for error in errors)

    incomplete = _good_audit().replace("- **Required action:** Track as non-blocking follow-up.\n", "")
    errors = _validate(incomplete)
    assert any("Finding F-001 record is incomplete" in error and "required action" in error for error in errors)


def test_finding_record_and_matrix_must_agree_for_nonblocking_findings() -> None:
    severity_mismatch = _good_audit().replace("- **Severity:** `A`", "- **Severity:** `B`")
    errors = _validate(severity_mismatch)
    assert any("severity disagrees" in error for error in errors)

    blocks_mismatch = _good_audit().replace("- **Blocks ADR:** `NO`", "- **Blocks ADR:** `YES`")
    errors = _validate(blocks_mismatch)
    assert any("Blocks ADR disagrees" in error for error in errors)


def test_ready_for_adr_rejects_class_b_but_minor_verdict_accepts_it() -> None:
    class_b = (
        _good_audit()
        .replace(_nonblocking_finding_record(), _nonblocking_finding_record("B"))
        .replace(
            "| F-001 | A | None | Minor auditability debt | NO | Evidence |",
            "| F-001 | B | Quality | Reduced auditability | NO | Evidence |",
        )
    )

    errors = _validate(class_b)
    assert any("Unresolved Class B finding requires" in error for error in errors)

    minor = class_b.replace("- `READY_FOR_ADR`", "- `READY_FOR_ADR_WITH_MINOR_FINDINGS`")
    assert _validate(minor) == []


def test_conditional_adr_ready_accepts_class_b_only_with_conditions() -> None:
    class_b = (
        _good_audit()
        .replace(_nonblocking_finding_record(), _nonblocking_finding_record("B"))
        .replace(
            "| F-001 | A | None | Minor auditability debt | NO | Evidence |",
            "| F-001 | B | Quality | Reduced auditability | NO | Evidence |",
        )
        .replace("- `READY_FOR_ADR`", "- `CONDITIONAL_ADR_READY`")
    )

    errors = _validate(class_b)
    assert any("requires explicit mandatory conditions" in error for error in errors)

    conditioned = class_b.replace(
        "## Required Corrections or Conditions\n\nNone.",
        "## Required Corrections or Conditions\n\n"
        "- Resolve the cumulative Class B auditability limitation before ADR approval.",
    )
    assert _validate(conditioned) == []


def test_mandatory_substantive_sections_may_not_be_empty() -> None:
    for heading in hunter_artifact_preflight.REQUIRED_NONEMPTY_SECTIONS:
        errors = _validate(_empty_section(_good_audit(), heading))
        assert any(f"Mandatory audit section must not be empty: {heading}" in error for error in errors), heading


def test_metadata_requires_reviewed_artifact_identity() -> None:
    text = _good_audit().replace("- Reviewed artifact: `docs/example.md`\n", "")
    errors = _validate(text)
    assert any("Reviewed artifact" in error for error in errors)


def test_escaped_pipe_in_matrix_free_text_is_not_a_delimiter() -> None:
    text = _good_audit().replace(
        "| F-001 | A | None | Minor auditability debt | NO | Evidence |",
        r"| F-001 | A | Choosing A \| B is unclear | Minor auditability debt | NO | Evidence |",
    )
    assert _validate(text) == []


def test_nonfinding_severity_bullet_does_not_trigger_finding_validation() -> None:
    text = _good_audit().replace(
        "Track F-001 as non-blocking cleanup.",
        "Track F-001 as non-blocking cleanup.\n\n- **Severity:** High",
    )
    assert _validate(text) == []


def _with_finding_id(record: str, finding_id: str) -> str:
    return record.replace("F-001", finding_id)


def _add_finding_record(text: str, record: str) -> str:
    return text.replace(
        _nonblocking_finding_record(),
        f"{_nonblocking_finding_record()}\n{record}",
    )


def _add_matrix_row(text: str, row: str) -> str:
    anchor = "| F-001 | A | None | Minor auditability debt | NO | Evidence |"
    return text.replace(anchor, f"{anchor}\n{row}")


def test_rendered_class_c_blocker_may_not_be_omitted_from_the_matrix() -> None:
    text = _add_finding_record(_good_audit(), _with_finding_id(_blocking_record("C"), "F-002"))

    errors = _validate(text)
    assert any("F-002" in error and "Findings Matrix" in error for error in errors), errors


def test_rendered_class_d_blocker_may_not_be_omitted_from_the_matrix() -> None:
    text = _add_finding_record(_good_audit(), _with_finding_id(_blocking_record("D"), "F-002"))

    errors = _validate(text)
    assert any("F-002" in error and "Findings Matrix" in error for error in errors), errors


def test_duplicate_matrix_rows_for_one_finding_id_are_rejected() -> None:
    text = _add_matrix_row(_good_audit(), "| F-001 | A | None | Minor auditability debt | NO | Evidence |")

    errors = _validate(text)
    assert any("F-001" in error and "exactly once in the Findings Matrix" in error for error in errors), errors


def test_duplicate_rendered_finding_records_for_one_finding_id_are_rejected() -> None:
    text = _add_finding_record(_good_audit(), _nonblocking_finding_record())

    errors = _validate(text)
    assert any("F-001" in error and "exactly one complete finding record" in error for error in errors), errors


def test_exact_one_to_one_finding_and_matrix_mapping_passes() -> None:
    text = _add_matrix_row(
        _add_finding_record(
            _good_audit().replace("- `READY_FOR_ADR`", "- `READY_FOR_ADR_WITH_MINOR_FINDINGS`"),
            _with_finding_id(_nonblocking_finding_record("B"), "F-002"),
        ),
        "| F-002 | B | Quality | Reduced auditability | NO | Evidence |",
    )

    assert _validate(text) == []


def test_matrix_only_finding_without_a_rendered_record_is_still_rejected() -> None:
    text = _add_matrix_row(_good_audit(), "| F-002 | A | None | Minor auditability debt | NO | Evidence |")

    errors = _validate(text)
    assert any("F-002" in error and "complete finding record" in error for error in errors), errors


def test_duplicate_identical_final_verdict_sections_are_rejected() -> None:
    text = _good_audit() + "\n## Final Verdict\n\n- `READY_FOR_ADR`\n"

    errors = _validate(text)
    assert any("exactly one" in error and "Final Verdict" in error for error in errors), errors


def test_duplicate_conflicting_final_verdict_sections_are_rejected() -> None:
    text = _good_audit() + "\n## Final Verdict\n\n- `ADPR_REVISION_REQUIRED`\n"

    errors = _validate(text)
    assert any("exactly one" in error and "Final Verdict" in error for error in errors), errors


def test_fenced_duplicate_final_verdict_does_not_count_as_a_second_section() -> None:
    fenced = "\n```markdown\n## Final Verdict\n\n- `ARCHITECTURE_NOT_READY`\n```\n"

    assert _validate(_good_audit() + fenced) == []


def test_nonsemantic_duplicate_final_verdict_regions_do_not_count_as_second_sections() -> None:
    decoys = (
        "\n<!--\n## Final Verdict\n\n- `ARCHITECTURE_NOT_READY`\n-->\n",
        "\n<pre>\n## Final Verdict\n\n- `ARCHITECTURE_NOT_READY`\n</pre>\n",
        '\n<textarea name="audit">\n## Final Verdict\n\n- `ADPR_REVISION_REQUIRED`\n</textarea>\n',
        "\n    ## Final Verdict\n\n    - `ARCHITECTURE_NOT_READY`\n",
    )
    for decoy in decoys:
        assert _validate(_good_audit() + decoy) == [], decoy


def test_exactly_one_rendered_final_verdict_section_passes() -> None:
    assert _validate(_good_audit()) == []


def test_finding_id_case_variation_cannot_smuggle_a_duplicate_matrix_row() -> None:
    text = _add_matrix_row(_good_audit(), "| f-001 | A | None | Minor auditability debt | NO | Evidence |")

    errors = _validate(text)
    assert any("F-001" in error and "exactly once in the Findings Matrix" in error for error in errors), errors


def test_finding_id_case_variation_cannot_smuggle_a_duplicate_record() -> None:
    text = _add_finding_record(_good_audit(), _nonblocking_finding_record().replace("F-001", "f-001"))

    errors = _validate(text)
    assert any("F-001" in error and "exactly one complete finding record" in error for error in errors), errors


def test_finding_id_case_variation_still_matches_across_findings_and_matrix() -> None:
    text = _good_audit().replace(
        "| F-001 | A | None | Minor auditability debt | NO | Evidence |",
        "| f-001 | A | None | Minor auditability debt | NO | Evidence |",
    )

    assert _validate(text) == []


def test_nonsemantic_finding_records_do_not_create_phantom_matrix_omissions() -> None:
    decoys = (
        "\n```markdown\n### F-777 — Fenced example finding\n\n- **Severity:** `D`\n- **Blocks ADR:** `YES`\n```\n",
        "\n<!--\n### F-778 — Commented example finding\n\n- **Severity:** `C`\n-->\n",
    )
    for decoy in decoys:
        text = _good_audit().replace(
            "## Findings Matrix",
            f"{decoy}\n## Findings Matrix",
        )
        assert _validate(text) == [], decoy


def test_trailing_whitespace_cannot_hide_a_second_final_verdict_section() -> None:
    text = _good_audit() + "\n## Final Verdict   \n\n- `ARCHITECTURE_NOT_READY`\n"

    errors = _validate(text)
    assert any("exactly one" in error and "Final Verdict" in error for error in errors), errors


def test_deeper_heading_level_is_not_a_second_final_verdict_section() -> None:
    text = _good_audit() + "\n### Final Verdict\n\nHistorical restatement for readers.\n"

    assert _validate(text) == []


def _canonical_completion_items() -> list[str]:
    section = _good_audit().split("## Audit Completion Check", 1)[1]
    return [line for line in section.splitlines() if line.startswith("- [x] ")]


def _without_findings(text: str) -> str:
    return text.replace(_nonblocking_finding_record(), "No findings were identified.").replace(
        "| F-001 | A | None | Minor auditability debt | NO | Evidence |\n", ""
    )


def test_zero_findings_cannot_support_conditional_adr_ready() -> None:
    text = (
        _without_findings(_good_audit())
        .replace("- `READY_FOR_ADR`", "- `CONDITIONAL_ADR_READY`")
        .replace(
            "## Required Corrections or Conditions\n\nNone.",
            "## Required Corrections or Conditions\n\n- Resolve the cumulative limitation before approval.",
        )
    )

    errors = _validate(text)
    assert any("CONDITIONAL_ADR_READY requires at least one unresolved Class B" in e for e in errors), errors


def test_zero_findings_cannot_support_ready_for_adr_with_minor_findings() -> None:
    text = _without_findings(_good_audit()).replace("- `READY_FOR_ADR`", "- `READY_FOR_ADR_WITH_MINOR_FINDINGS`")

    errors = _validate(text)
    assert any("READY_FOR_ADR_WITH_MINOR_FINDINGS requires at least one" in e for e in errors), errors


def test_zero_findings_accepts_the_canonical_no_finding_verdict() -> None:
    assert _validate(_without_findings(_good_audit())) == []


def test_class_a_only_cannot_support_conditional_adr_ready() -> None:
    """CONDITIONAL_ADR_READY is defined by cumulative Class B findings."""
    text = (
        _good_audit()
        .replace("- `READY_FOR_ADR`", "- `CONDITIONAL_ADR_READY`")
        .replace(
            "## Required Corrections or Conditions\n\nNone.",
            "## Required Corrections or Conditions\n\n- Resolve the cumulative limitation before approval.",
        )
    )

    errors = _validate(text)
    assert any("CONDITIONAL_ADR_READY requires at least one unresolved Class B" in e for e in errors), errors


def test_header_only_matrix_cannot_bypass_verdict_derivation() -> None:
    for verdict in ("CONDITIONAL_ADR_READY", "READY_FOR_ADR_WITH_MINOR_FINDINGS"):
        text = _without_findings(_good_audit()).replace("- `READY_FOR_ADR`", f"- `{verdict}`")
        assert _validate(text), verdict


def test_unchecked_completion_item_is_rejected() -> None:
    text = _good_audit().replace("- [x] Audit scope identified", "- [ ] Audit scope identified")

    errors = _validate(text)
    assert any("Audit Completion Check item is not complete" in e for e in errors), errors


def test_single_unchecked_item_among_completed_items_is_rejected() -> None:
    text = _good_audit().replace("- [x] Findings matrix completed", "- [ ] Findings matrix completed")

    errors = _validate(text)
    assert any("Findings matrix completed" in e and "not complete" in e for e in errors), errors


def test_all_canonical_completion_items_checked_passes() -> None:
    assert _validate(_good_audit()) == []


def test_fabricated_single_completion_item_cannot_impersonate_the_checklist() -> None:
    text = _good_audit()
    for item in _canonical_completion_items():
        text = text.replace(item + "\n", "")
    text = text.replace("## Audit Completion Check\n", "## Audit Completion Check\n\n- [x] Complete\n")

    errors = _validate(text)
    assert any("missing required completed items" in e for e in errors), errors


def test_completion_prose_without_a_checklist_is_rejected() -> None:
    text = _good_audit()
    for item in _canonical_completion_items():
        text = text.replace(item + "\n", "")
    text = text.replace(
        "## Audit Completion Check\n",
        "## Audit Completion Check\n\nThe auditor confirms every required step is complete.\n",
    )

    errors = _validate(text)
    assert any("canonical completion checklist" in e for e in errors), errors


def test_unchecked_boxes_outside_the_completion_section_do_not_trigger_the_rule() -> None:
    text = _good_audit().replace(
        "Track F-001 as non-blocking cleanup.",
        "Track F-001 as non-blocking cleanup.\n\n- [ ] Optional future editorial pass",
    )

    assert _validate(text) == []


def test_nonsemantic_completion_checklists_do_not_satisfy_or_break_the_rule() -> None:
    fenced = "\n```markdown\n- [ ] Fenced unchecked example item\n```\n"
    assert _validate(_good_audit() + fenced) == []

    commented = "\n<!--\n- [ ] Commented unchecked example item\n-->\n"
    assert _validate(_good_audit() + commented) == []


def test_level_two_headings_accept_legal_commonmark_indentation() -> None:
    for indent in range(4):
        text = "\n".join(
            (" " * indent + line) if line.startswith("## ") else line for line in _good_audit().splitlines()
        )
        assert _validate(text + "\n") == [], indent


def test_four_space_indented_heading_is_not_a_rendered_heading() -> None:
    text = "\n".join(("    " + line) if line.startswith("## ") else line for line in _good_audit().splitlines())

    errors = _validate(text + "\n")
    assert any("Missing mandatory audit heading: ## Metadata" in e for e in errors), errors


def test_mixed_legal_heading_indentation_across_sections_passes() -> None:
    indents = ("", " ", "  ", "   ")
    lines = []
    index = 0
    for line in _good_audit().splitlines():
        if line.startswith("## "):
            lines.append(indents[index % len(indents)] + line)
            index += 1
        else:
            lines.append(line)

    assert _validate("\n".join(lines) + "\n") == []


def test_duplicate_final_verdict_is_detected_through_legal_indentation() -> None:
    for indent in (" ", "  ", "   "):
        text = _good_audit() + f"\n{indent}## Final Verdict\n\n- `ARCHITECTURE_NOT_READY`\n"
        errors = _validate(text)
        assert any("exactly one" in e and "Final Verdict" in e for e in errors), indent


def _with_closing_hashes(text: str, suffix: str) -> str:
    return "\n".join((line + suffix) if line.startswith("## ") else line for line in text.splitlines()) + "\n"


def test_atx_heading_without_closing_hashes_is_recognized() -> None:
    assert _validate(_good_audit()) == []


def test_atx_heading_with_a_single_closing_hash_is_the_same_heading() -> None:
    assert _validate(_with_closing_hashes(_good_audit(), " #")) == []


def test_atx_heading_with_a_closing_hash_sequence_is_the_same_heading() -> None:
    for suffix in (" ##", " ####", " ######"):
        assert _validate(_with_closing_hashes(_good_audit(), suffix)) == [], suffix


def test_closing_hashes_combine_with_legal_leading_indentation() -> None:
    text = "\n".join(("   " + line + " ###") if line.startswith("## ") else line for line in _good_audit().splitlines())

    assert _validate(text + "\n") == []


def test_four_space_indent_with_closing_hashes_is_not_a_rendered_heading() -> None:
    text = "\n".join(
        ("    " + line + " ###") if line.startswith("## ") else line for line in _good_audit().splitlines()
    )

    errors = _validate(text + "\n")
    assert any("Missing mandatory audit heading: ## Metadata" in error for error in errors), errors


def test_unseparated_trailing_hashes_stay_part_of_the_heading_content() -> None:
    """`## Metadata###` renders as the heading "Metadata###", not "Metadata"."""
    text = _good_audit().replace("## Metadata", "## Metadata###")

    errors = _validate(text)
    assert any("Missing mandatory audit heading: ## Metadata" in error for error in errors), errors


def test_duplicate_final_verdict_is_detected_across_closing_hash_forms() -> None:
    text = _good_audit() + "\n## Final Verdict ###\n\n- `ARCHITECTURE_NOT_READY`\n"

    errors = _validate(text)
    assert any("exactly one" in error and "Final Verdict" in error for error in errors), errors


def test_finding_record_heading_accepts_a_closing_hash_sequence() -> None:
    text = _good_audit().replace(
        "### F-001 — Non-blocking fixture finding", "### F-001 — Non-blocking fixture finding ###"
    )

    assert _validate(text) == []


def test_headings_with_closing_hashes_inside_nonsemantic_regions_stay_nonsemantic() -> None:
    decoys = (
        "\n```markdown\n## Final Verdict ##\n\n- `ARCHITECTURE_NOT_READY`\n```\n",
        "\n<!--\n## Final Verdict ##\n\n- `ARCHITECTURE_NOT_READY`\n-->\n",
        "\n    ## Final Verdict ##\n",
    )
    for decoy in decoys:
        assert _validate(_good_audit() + decoy) == [], decoy
