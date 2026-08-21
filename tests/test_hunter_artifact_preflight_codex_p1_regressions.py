from __future__ import annotations

import hunter_artifact_preflight


RAW_HTML_LITERAL_CASES = [
    ("<pre>", "</pre>"),
    ('<SCRIPT type="text/plain">', "</SCRIPT>"),
    ('<style media="all">', "</style>"),
    ('<textarea name="audit">', "</textarea>"),
]


def _audit() -> str:
    return """# Independent Architecture Audit

## Metadata

- Reviewed artifact: `docs/example.md`
- Reviewed revision: `0123456789abcdef0123456789abcdef01234567`
- Audit type: `FULL`
- Auditor: Jules — independent architecture audit agent

## Audit Scope

Full independent architecture audit.

## Evidence Sources Examined

- Immutable repository evidence at the reviewed revision.

## Accepted ADR Coverage

- ADR 0001: reviewed; out of scope for this audit.

## Dimension Results

| Dimension | Result | Evidence and rationale | Finding IDs |
|---|---|---|---|
| Problem correctness | PASS | Evidence-backed. | |

## Findings

No blocking findings.

## Findings Matrix

| Finding | Class | Decision impact | Consequence if ignored | Blocks ADR | Evidence |
|---|---|---|---|---|---|
| F-001 | A | None | None | NO | Evidence |

## Verdict Derivation

Highest unresolved severity: A.

## Final Verdict

- `READY_FOR_ADR`

## Required Corrections or Conditions

None.

## Non-Blocking Follow-Up

None.

## Audit Completion Check

- [x] Complete
"""


def _validate(text: str) -> list[str]:
    return hunter_artifact_preflight.validate_audit_text(text, accepted_adrs=["0001"])


def _blocking_record(*, finding_id: str = "F-001", severity: str = "C") -> str:
    return f"""### {finding_id} — Material finding

- **Evidence:** Direct repository evidence.
- **Location:** `docs/example.md`, decision boundary.
- **Category:** Architecture decision quality.
- **Severity:** `{severity}`
- **Decision impact:** The decision basis can become unreliable.
- **Consequence if ignored:** An unsupported architectural conclusion could be selected.
- **Required action:** Correct the material defect before progression.
- **Blocks ADR:** `YES`
"""


def test_raw_html_literal_blocks_cannot_impersonate_audit_structure() -> None:
    for opening, closing in RAW_HTML_LITERAL_CASES:
        errors = _validate(f"{opening}\n{_audit()}\n{closing}\n")

        assert any("Missing mandatory audit heading: ## Metadata" in error for error in errors), opening
        assert any("canonical declared audit verdict" in error for error in errors), opening


def test_unclosed_raw_html_literal_blocks_mask_to_eof() -> None:
    for opening, _closing in RAW_HTML_LITERAL_CASES:
        errors = _validate(f"{opening}\n{_audit()}")

        assert any("Missing mandatory audit heading: ## Metadata" in error for error in errors), opening
        assert any("canonical declared audit verdict" in error for error in errors), opening


def test_raw_html_decoys_do_not_hide_real_rendered_audit() -> None:
    for opening, closing in RAW_HTML_LITERAL_CASES:
        decoy = f"{opening}\n{_audit().replace('Jules', 'PENDING')}\n{closing}\n"

        assert _validate(_audit() + decoy) == [], opening


def test_blocking_matrix_row_requires_complete_matching_finding_record() -> None:
    text = (
        _audit()
        .replace("- `READY_FOR_ADR`", "- `ADPR_REVISION_REQUIRED`")
        .replace(
            "| F-001 | A | None | None | NO | Evidence |",
            "| F-001 | C | Impact | Consequence | YES | Evidence |",
        )
    )

    errors = _validate(text)
    assert any("Blocking finding F-001 must have a complete finding record" in error for error in errors)

    mismatched = text.replace("No blocking findings.", _blocking_record(finding_id="F-002"))
    errors = _validate(mismatched)
    assert any("Blocking finding F-001 must have a complete finding record" in error for error in errors)


def test_blocking_record_requires_all_fields_and_matrix_consistency() -> None:
    valid = (
        _audit()
        .replace("No blocking findings.", _blocking_record())
        .replace("- `READY_FOR_ADR`", "- `ADPR_REVISION_REQUIRED`")
        .replace(
            "| F-001 | A | None | None | NO | Evidence |",
            "| F-001 | C | Impact | Consequence | YES | Evidence |",
        )
    )
    assert _validate(valid) == []

    incomplete = valid.replace("- **Required action:** Correct the material defect before progression.\n", "")
    errors = _validate(incomplete)
    assert any("record is incomplete" in error and "required action" in error for error in errors)

    severity_mismatch = valid.replace("- **Severity:** `C`", "- **Severity:** `D`")
    errors = _validate(severity_mismatch)
    assert any("severity disagrees" in error for error in errors)

    blocks_mismatch = valid.replace("- **Blocks ADR:** `YES`", "- **Blocks ADR:** `NO`")
    errors = _validate(blocks_mismatch)
    assert any("Blocks ADR disagrees" in error for error in errors)


def test_ready_for_adr_rejects_class_b_but_minor_verdict_accepts_it() -> None:
    class_b = _audit().replace(
        "| F-001 | A | None | None | NO | Evidence |",
        "| F-001 | B | Quality | Reduced auditability | NO | Evidence |",
    )

    errors = _validate(class_b)
    assert any("Unresolved Class B finding requires" in error for error in errors)

    minor = class_b.replace("- `READY_FOR_ADR`", "- `READY_FOR_ADR_WITH_MINOR_FINDINGS`")
    assert _validate(minor) == []


def test_conditional_adr_ready_accepts_class_b_only_with_conditions() -> None:
    class_b = (
        _audit()
        .replace(
            "| F-001 | A | None | None | NO | Evidence |",
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
