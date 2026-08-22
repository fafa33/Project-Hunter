from __future__ import annotations

import json
from pathlib import Path

import hunter_architecture_index_preflight

ROOT = Path(__file__).resolve().parents[1]


def _fixture(
    *,
    decision_status: str = "APPROVED",
    approved_status: str = "APPROVED",
    implementation: str = "Provider-free pre-model runtime is implemented in current source.",
    include_implementation_column: bool = True,
) -> str:
    if include_implementation_column:
        approved_header = "| ADPR | ADR | Status | Implementation | Validation |"
        approved_delimiter = "|---|---|---|---|---|"
        approved_row = f"| [ADPR-0006](example.md) | ADR 0031 | {approved_status} | {implementation} | Historical |"
    else:
        approved_header = "| ADPR | ADR | Status | Validation |"
        approved_delimiter = "|---|---|---|---|"
        approved_row = f"| [ADPR-0006](example.md) | ADR 0031 | {approved_status} | Historical |"

    return f"""# Architecture Decision Index

## Decision Registry

| ADPR | Title | Status | Epic | Issue | ADR | Implementation PR | Merge Commit | Release | Supersedes | Superseded By |
|---|---|---|---|---|---|---|---|---|---|---|
| [ADPR-0006](example.md) | AI Context | {decision_status} | none | #1 | ADR 0031 | #2 | abc123 | v3.6.0 | none | none |

## Approved and Implemented Records

{approved_header}
{approved_delimiter}
{approved_row}

## ADR Mapping

| ADR | Status |
|---|---|
| 0031 | Accepted |
"""


def test_current_architecture_index_passes_lifecycle_runtime_guard() -> None:
    text = (ROOT / "docs" / "architecture-index.md").read_text(encoding="utf-8")

    assert hunter_architecture_index_preflight.validate_architecture_index(text) == []


def test_known_not_started_contradiction_is_rejected() -> None:
    errors = hunter_architecture_index_preflight.validate_architecture_index(
        _fixture(implementation="Provider-free pre-model runtime is not started or authorized."),
    )

    assert any("must explicitly assert" in error for error in errors), errors


def test_lifecycle_status_must_agree_across_canonical_tables() -> None:
    errors = hunter_architecture_index_preflight.validate_architecture_index(
        _fixture(approved_status="IMPLEMENTED"),
    )

    assert any("lifecycle status disagrees" in error for error in errors), errors


def test_decision_registry_cannot_rewrite_approved_lifecycle_as_implemented() -> None:
    errors = hunter_architecture_index_preflight.validate_architecture_index(
        _fixture(decision_status="IMPLEMENTED", approved_status="IMPLEMENTED"),
    )

    assert any("must remain APPROVED" in error for error in errors), errors


def test_lifecycle_and_runtime_must_have_separate_columns() -> None:
    errors = hunter_architecture_index_preflight.validate_architecture_index(
        _fixture(include_implementation_column=False),
    )

    assert any("separate ADPR lifecycle Status" in error for error in errors), errors


def test_same_width_decoy_rows_outside_canonical_tables_cannot_satisfy_guard() -> None:
    text = _fixture().replace(
        "| [ADPR-0006](example.md) | AI Context | APPROVED | none | #1 | ADR 0031 | #2 | abc123 | v3.6.0 | none | none |",
        "| [ADPR-0005](example.md) | AI Context | APPROVED | none | #1 | ADR 0031 | #2 | abc123 | v3.6.0 | none | none |",
    )
    text += """

## Unrelated Evidence

| A | B | C | D | E | F | G | H | I | J | K |
|---|---|---|---|---|---|---|---|---|---|---|
| ADPR-0006 | decoy | APPROVED | x | x | x | x | x | x | x | x |
"""

    errors = hunter_architecture_index_preflight.validate_architecture_index(text)

    assert any("Decision Registry must contain exactly one ADPR-0006 row; found 0" in error for error in errors), errors


def test_commented_out_canonical_tables_cannot_satisfy_guard() -> None:
    text = _fixture()
    text = text.replace(
        "## Decision Registry\n\n| ADPR | Title | Status | Epic | Issue | ADR | Implementation PR | Merge Commit | Release | Supersedes | Superseded By |",
        "<!--\n## Decision Registry\n\n| ADPR | Title | Status | Epic | Issue | ADR | Implementation PR | Merge Commit | Release | Supersedes | Superseded By |",
    )
    text = text.replace(
        "| [ADPR-0006](example.md) | AI Context | APPROVED | none | #1 | ADR 0031 | #2 | abc123 | v3.6.0 | none | none |\n\n## Approved and Implemented Records",
        "| [ADPR-0006](example.md) | AI Context | APPROVED | none | #1 | ADR 0031 | #2 | abc123 | v3.6.0 | none | none |\n-->\n\n## Approved and Implemented Records",
    )

    errors = hunter_architecture_index_preflight.validate_architecture_index(text)

    assert any("Decision Registry must contain the rendered canonical" in error for error in errors), errors


def test_fenced_canonical_tables_cannot_satisfy_guard() -> None:
    text = _fixture()
    start = text.index("## Decision Registry")
    end = text.index("## ADR Mapping")
    fenced = text[:start] + "```markdown\n" + text[start:end] + "```\n\n" + text[end:]

    errors = hunter_architecture_index_preflight.validate_architecture_index(fenced)

    assert any("Decision Registry must contain the rendered canonical" in error for error in errors), errors


def test_stale_runtime_claim_that_was_later_removed_is_rejected() -> None:
    errors = hunter_architecture_index_preflight.validate_architecture_index(
        _fixture(
            implementation=(
                "Provider-free pre-model runtime is implemented in current source but has since been removed."
            )
        ),
    )

    assert any("revokes or contradicts" in error for error in errors), errors


def test_separate_provider_invocation_not_authorized_does_not_negate_runtime() -> None:
    errors = hunter_architecture_index_preflight.validate_architecture_index(
        _fixture(
            implementation=(
                "Provider-free pre-model runtime is implemented in current source; "
                "separate provider invocation remains not authorized."
            )
        ),
    )

    assert errors == []


def test_runtime_evidence_disappearance_fails_closed(tmp_path: Path) -> None:
    missing = (tmp_path / "pre_model.py", tmp_path / "pre_model_persistence.py")

    errors = hunter_architecture_index_preflight.validate_architecture_index(
        _fixture(),
        runtime_paths=missing,
    )

    assert any("runtime evidence is missing" in error for error in errors), errors


def _fixture_row(prefix: str) -> str:
    """Read a canonical ADPR-0006 row out of the fixture so the two cannot drift."""
    return next(line for line in _fixture().splitlines() if line.startswith(prefix))


DECISION_ROW = _fixture_row("| [ADPR-0006](example.md) | AI Context |")
APPROVED_ROW = _fixture_row("| [ADPR-0006](example.md) | ADR 0031 |")


def test_tilde_fenced_canonical_tables_cannot_satisfy_guard() -> None:
    """`~~~` opens a fenced block just as `` ``` `` does."""
    text = _fixture()
    start = text.index("## Decision Registry")
    end = text.index("## ADR Mapping")
    fenced = text[:start] + "~~~markdown\n" + text[start:end] + "~~~\n\n" + text[end:]

    errors = hunter_architecture_index_preflight.validate_architecture_index(fenced)

    assert any("Decision Registry must contain the rendered canonical" in error for error in errors), errors


def test_multiline_html_comment_around_the_approved_row_is_not_rendered() -> None:
    """A commented-out row does not render, so it cannot supply the ADPR-0006 record."""
    text = _fixture().replace(APPROVED_ROW, f"<!--\n{APPROVED_ROW}\n-->")

    errors = hunter_architecture_index_preflight.validate_architecture_index(text)

    assert any(
        "Approved and Implemented Records must contain exactly one ADPR-0006 row; found 0" in error for error in errors
    ), errors


def test_multiline_html_comment_around_the_whole_approved_table_is_not_rendered() -> None:
    approved_table = "| ADPR | ADR | Status | Implementation | Validation |\n" "|---|---|---|---|---|\n" + APPROVED_ROW
    text = _fixture().replace(approved_table, f"<!--\n{approved_table}\n-->")

    errors = hunter_architecture_index_preflight.validate_architecture_index(text)

    assert any("separate ADPR lifecycle Status" in error for error in errors), errors


def test_decoy_table_with_exact_canonical_headers_outside_the_section_is_ignored() -> None:
    """Row discovery is bound to the canonical section, not to a matching schema."""
    text = _fixture().replace(DECISION_ROW, DECISION_ROW.replace("ADPR-0006", "ADPR-0005"))
    text += (
        "\n\n## Unrelated Evidence\n\n"
        "| ADPR | Title | Status | Epic | Issue | ADR | Implementation PR | Merge Commit "
        "| Release | Supersedes | Superseded By |\n"
        "|---|---|---|---|---|---|---|---|---|---|---|\n" + DECISION_ROW + "\n"
    )

    errors = hunter_architecture_index_preflight.validate_architecture_index(text)

    assert any("exactly one ADPR-0006 row; found 0" in error for error in errors), errors


def test_decoy_approved_table_outside_the_section_cannot_supply_the_runtime_claim() -> None:
    text = _fixture().replace(APPROVED_ROW, APPROVED_ROW.replace("ADPR-0006", "ADPR-0005"))
    text += (
        "\n\n## Unrelated Evidence\n\n"
        "| ADPR | ADR | Status | Implementation | Validation |\n"
        "|---|---|---|---|---|\n" + APPROVED_ROW + "\n"
    )

    errors = hunter_architecture_index_preflight.validate_architecture_index(text)

    assert any(
        "Approved and Implemented Records must contain exactly one ADPR-0006 row; found 0" in error for error in errors
    ), errors


def test_prose_decoy_outside_canonical_sections_does_not_interfere() -> None:
    text = _fixture() + (
        "\n\n## Notes\n\nADPR-0006 is APPROVED and the provider-free pre-model runtime "
        "is implemented in current source, as discussed above.\n"
    )

    assert hunter_architecture_index_preflight.validate_architecture_index(text) == []


def test_past_tense_runtime_claim_that_was_since_removed_is_rejected() -> None:
    """`was implemented ... but has since been removed` asserts a revoked state."""
    errors = hunter_architecture_index_preflight.validate_architecture_index(
        _fixture(implementation="Provider-free pre-model runtime was implemented but has since been removed."),
    )

    assert errors, "stale past-tense runtime claim must not satisfy the current-state assertion"


def test_revocation_wordings_are_rejected_deterministically() -> None:
    wordings = (
        "Provider-free pre-model runtime is no longer implemented.",
        "Provider-free pre-model runtime is not implemented.",
        "Provider-free pre-model runtime is unimplemented.",
        "Provider-free pre-model runtime is disabled.",
        "Provider-free pre-model runtime is absent.",
        "Provider-free pre-model runtime was removed.",
        "Provider-free pre-model runtime is removed.",
    )
    for wording in wordings:
        errors = hunter_architecture_index_preflight.validate_architecture_index(
            _fixture(implementation=wording),
        )
        assert errors, wording


def test_asserted_runtime_with_revocation_clause_is_rejected() -> None:
    errors = hunter_architecture_index_preflight.validate_architecture_index(
        _fixture(
            implementation=(
                "Provider-free pre-model runtime is implemented in current source, " "however the runtime is disabled."
            )
        ),
    )

    assert any("revokes or contradicts" in error for error in errors), errors


def test_duplicate_adpr_0006_rows_are_rejected_in_both_canonical_tables() -> None:
    duplicated_decision = _fixture().replace(DECISION_ROW, f"{DECISION_ROW}\n{DECISION_ROW}")
    errors = hunter_architecture_index_preflight.validate_architecture_index(duplicated_decision)
    assert any("Decision Registry must contain exactly one ADPR-0006 row; found 2" in e for e in errors), errors

    duplicated_approved = _fixture().replace(APPROVED_ROW, f"{APPROVED_ROW}\n{APPROVED_ROW}")
    errors = hunter_architecture_index_preflight.validate_architecture_index(duplicated_approved)
    assert any(
        "Approved and Implemented Records must contain exactly one ADPR-0006 row; found 2" in e for e in errors
    ), errors


def test_missing_adpr_0006_rows_are_rejected_in_both_canonical_tables() -> None:
    missing_decision = _fixture().replace(DECISION_ROW + "\n", "")
    errors = hunter_architecture_index_preflight.validate_architecture_index(missing_decision)
    assert any("Decision Registry must contain exactly one ADPR-0006 row; found 0" in e for e in errors), errors

    missing_approved = _fixture().replace(APPROVED_ROW + "\n", "")
    errors = hunter_architecture_index_preflight.validate_architecture_index(missing_approved)
    assert any(
        "Approved and Implemented Records must contain exactly one ADPR-0006 row; found 0" in e for e in errors
    ), errors


CANONICAL_HEADINGS = ("## Decision Registry", "## Approved and Implemented Records")


def test_four_space_indented_canonical_headings_render_as_code_not_headings() -> None:
    """Four or more leading spaces makes the line an indented code block."""
    for heading in CANONICAL_HEADINGS:
        text = _fixture().replace(heading, "    " + heading)
        errors = hunter_architecture_index_preflight.validate_architecture_index(text)
        assert any("must contain the rendered canonical" in error for error in errors), heading


def test_deeply_space_indented_canonical_headings_are_not_headings() -> None:
    for heading in CANONICAL_HEADINGS:
        text = _fixture().replace(heading, "      " + heading)
        errors = hunter_architecture_index_preflight.validate_architecture_index(text)
        assert any("must contain the rendered canonical" in error for error in errors), heading


def test_tab_indented_canonical_headings_are_not_headings() -> None:
    """A leading tab also opens indented code, so it is not a rendered heading."""
    for heading in CANONICAL_HEADINGS:
        text = _fixture().replace(heading, "\t" + heading)
        errors = hunter_architecture_index_preflight.validate_architecture_index(text)
        assert any("must contain the rendered canonical" in error for error in errors), heading


def test_multiple_tab_indented_canonical_headings_are_not_headings() -> None:
    for heading in CANONICAL_HEADINGS:
        text = _fixture().replace(heading, "\t\t" + heading)
        errors = hunter_architecture_index_preflight.validate_architecture_index(text)
        assert any("must contain the rendered canonical" in error for error in errors), heading


def test_unindented_canonical_headings_pass() -> None:
    assert hunter_architecture_index_preflight.validate_architecture_index(_fixture()) == []


def test_canonical_headings_with_legal_commonmark_indentation_pass() -> None:
    """CommonMark permits up to three leading spaces before the opening hashes."""
    for indent in (1, 2, 3):
        text = _fixture()
        for heading in CANONICAL_HEADINGS:
            text = text.replace(heading, " " * indent + heading)
        assert hunter_architecture_index_preflight.validate_architecture_index(text) == [], indent


def test_each_canonical_heading_independently_accepts_legal_indentation() -> None:
    for heading in CANONICAL_HEADINGS:
        for indent in (1, 2, 3):
            text = _fixture().replace(heading, " " * indent + heading)
            assert hunter_architecture_index_preflight.validate_architecture_index(text) == [], (heading, indent)


def test_case_14_defect_class_is_permanently_registered() -> None:
    registry = json.loads((ROOT / "docs" / "DEFECT_REGISTRY.json").read_text(encoding="utf-8"))
    matching = [item for item in registry["defects"] if item.get("id") == "ARCH-AUD-008"]

    assert len(matching) == 1
    assert matching[0]["status"] == "guarded"
    assert matching[0]["test"] == (
        "tests/test_hunter_architecture_index_preflight.py::" "test_known_not_started_contradiction_is_rejected"
    )
