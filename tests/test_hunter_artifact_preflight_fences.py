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
| Problem correctness | PASS | Evidence-backed. | F-001 |

## Findings

### F-001 — Minor fixture finding

- **Evidence:** Direct repository evidence.
- **Location:** `docs/example.md`, wording boundary.
- **Category:** Audit clarity.
- **Severity:** `A`
- **Decision impact:** No material decision impact.
- **Consequence if ignored:** Minor auditability debt remains.
- **Required action:** Track as non-blocking follow-up.
- **Blocks ADR:** `NO`

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


def test_complete_audit_inside_fenced_code_cannot_impersonate_real_structure() -> None:
    for opening, closing in (("```markdown", "```"), ("~~~~md", "~~~~")):
        text = f"{opening}\n{_good_audit()}\n{closing}\n"
        errors = hunter_artifact_preflight.validate_audit_text(
            text,
            accepted_adrs=["0001", "0002"],
        )

        assert any("Missing mandatory audit heading: ## Metadata" in error for error in errors)
        assert any("Missing mandatory audit heading: ## Final Verdict" in error for error in errors)
        assert any("canonical declared audit verdict" in error for error in errors)


def test_real_audit_ignores_fenced_decoy_structure_and_metadata() -> None:
    decoy = """
```markdown
## Metadata
- Reviewed revision: `ffffffffffffffffffffffffffffffffffffffff`
- Audit type: `TARGETED`
- Auditor: PENDING

## Final Verdict
- `ARCHITECTURE_NOT_READY`

- ADR 9999: reviewed; applicable.
- **Blocks ADR:** `YES`
`PENDING — template content only.`
```
"""

    errors = hunter_artifact_preflight.validate_audit_text(
        _good_audit() + decoy,
        accepted_adrs=["0001", "0002"],
    )

    assert errors == []
