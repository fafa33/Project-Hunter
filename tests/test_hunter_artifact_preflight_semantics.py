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


def _empty_section(text: str, heading: str) -> str:
    start = text.index(heading) + len(heading)
    next_heading = text.find("\n## ", start)
    assert next_heading >= 0
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
