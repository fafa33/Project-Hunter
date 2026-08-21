from __future__ import annotations

import hunter_artifact_preflight


def _good_audit() -> str:
    return """# Independent Architecture Audit

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
| Problem correctness | PASS | Evidence-backed. | |

## Findings

No blocking findings.

## Findings Matrix

| Finding | Class | Decision impact | Consequence if ignored | Blocks ADR | Evidence |
|---|---|---|---|---|---|
| F-001 | A | None | None | NO | Evidence |

## Verdict Derivation

Highest unresolved severity: A. No blocking materiality.

## Final Verdict

- `READY_FOR_ADR`

## Required Corrections or Conditions

None.

## Non-Blocking Follow-Up

None.

## Audit Completion Check

- [x] Complete
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
            "| F-001 | A | None | None | NO | Evidence |",
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
    text = _good_audit().replace(
        "| F-001 | A | None | None | NO | Evidence |",
        "| F-001 | C | Impact | Consequence | YES | Evidence |",
    )

    errors = _validate(text)
    assert any("Class C finding requires ADPR_REVISION_REQUIRED" in error for error in errors)


def test_adpr_revision_required_accepts_class_c_but_not_class_d() -> None:
    class_c = (
        _good_audit()
        .replace("No blocking findings.", _blocking_record("C"))
        .replace("- `READY_FOR_ADR`", "- `ADPR_REVISION_REQUIRED`")
        .replace(
            "| F-001 | A | None | None | NO | Evidence |",
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
        .replace("No blocking findings.", _blocking_record("C"))
        .replace("- `READY_FOR_ADR`", "- `ARCHITECTURE_NOT_READY`")
        .replace(
            "| F-001 | A | None | None | NO | Evidence |",
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
        .replace("- `READY_FOR_ADR`", "- `ADPR_REVISION_REQUIRED`")
        .replace(
            "| F-001 | A | None | None | NO | Evidence |",
            "| F-001 | C | Impact | Consequence | NO | Evidence |",
        )
    )

    errors = _validate(text)
    assert any("Class C finding F-001 must set Blocks ADR = YES" in error for error in errors)


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
