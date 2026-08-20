from __future__ import annotations

import copy

import hunter_artifact_preflight


def _good_audit(*, accepted_adrs: tuple[str, ...] = ("0001", "0002")) -> str:
    coverage = "\n".join(f"- ADR {adr}: reviewed; out of scope for this audit." for adr in accepted_adrs)
    return f"""# Independent Architecture Audit

## Metadata

- Reviewed artifact: `docs/example.md`
- Reviewed revision: `0123456789abcdef0123456789abcdef01234567`
- Audit type: `FULL`
- Auditor: Jules — independent architecture audit agent
- Audit date: 2026-08-20
- Evidence cutoff: `2026-08-20T13:00:00+02:00`

## Audit Scope

Full independent architecture audit.

## Evidence Sources Examined

- Immutable repository evidence at the reviewed revision.

## Accepted ADR Coverage

{coverage}

## Prior Review Finding Re-Verification

- PR #288 findings independently re-verified against the reviewed baseline.

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

Highest unresolved class: A. No blocking materiality.

## Final Verdict

READY_FOR_ADR

## Audit Completion Check

- [x] Complete

## Progression Gate

READY_FOR_ADR and READY_FOR_ADR_WITH_MINOR_FINDINGS permit clean progression.
CONDITIONAL_ADR_READY may permit drafting only under its stated conditions and does not
permit approval or merge until those conditions are satisfied.
ADPR_REVISION_REQUIRED and ARCHITECTURE_NOT_READY block ADR drafting.
"""


def test_good_audit_passes() -> None:
    assert (
        hunter_artifact_preflight.validate_audit_text(
            _good_audit(),
            accepted_adrs=["0001", "0002"],
        )
        == []
    )


def test_pending_audit_is_rejected() -> None:
    text = _good_audit().replace(
        "No blocking findings.",
        "`PENDING — auditor must complete findings.`",
    )
    errors = hunter_artifact_preflight.validate_audit_text(
        text,
        accepted_adrs=["0001", "0002"],
    )
    assert any("PENDING" in error for error in errors)


def test_audit_requires_revision_and_cutoff() -> None:
    text = _good_audit()
    text = text.replace(
        "- Reviewed revision: `0123456789abcdef0123456789abcdef01234567`\n",
        "",
    )
    text = text.replace("- Evidence cutoff: `2026-08-20T13:00:00+02:00`\n", "")
    errors = hunter_artifact_preflight.validate_audit_text(
        text,
        accepted_adrs=["0001", "0002"],
    )
    assert any("Reviewed revision" in error for error in errors)
    assert any("Evidence cutoff" in error for error in errors)


def test_audit_requires_every_accepted_adr() -> None:
    errors = hunter_artifact_preflight.validate_audit_text(
        _good_audit(accepted_adrs=("0001",)),
        accepted_adrs=["0001", "0002"],
    )
    assert any("Accepted ADR 0002" in error for error in errors)


def test_prior_review_scope_requires_verification_section() -> None:
    text = _good_audit().replace(
        "## Prior Review Finding Re-Verification\n\n"
        "- PR #288 findings independently re-verified against the reviewed baseline.\n\n",
        "",
    )
    errors = hunter_artifact_preflight.validate_audit_text(
        text,
        accepted_adrs=["0001", "0002"],
    )
    assert any("Prior Review Finding Re-Verification" in error for error in errors)


def test_severity_label_is_rejected() -> None:
    text = _good_audit().replace(
        "No blocking findings.",
        "- **Severity:** A\n- **Class:** A",
    )
    errors = hunter_artifact_preflight.validate_audit_text(
        text,
        accepted_adrs=["0001", "0002"],
    )
    assert any("canonical `Class`" in error for error in errors)


def test_progression_gate_is_explicit() -> None:
    text = _good_audit().replace(
        """READY_FOR_ADR and READY_FOR_ADR_WITH_MINOR_FINDINGS permit clean progression.
CONDITIONAL_ADR_READY may permit drafting only under its stated conditions and does not
permit approval or merge until those conditions are satisfied.
ADPR_REVISION_REQUIRED and ARCHITECTURE_NOT_READY block ADR drafting.""",
        "READY_FOR_ADR permits progression.",
    )
    errors = hunter_artifact_preflight.validate_audit_text(
        text,
        accepted_adrs=["0001", "0002"],
    )
    assert any("Progression Gate" in error for error in errors)


def test_registry_rejects_dropped_understood_defect_class() -> None:
    data = {
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
