"""Canonical scoping regressions for the architecture-readiness (ADPR) validator.

`docs/ARCHITECTURE_AUDIT_PROTOCOL.md` limits its own applicability to independent
review of ADPRs, architecture-decision readiness assessments, substantive
revisions to architecture preparation records, option-set completeness reviews,
and audits required by the Development Governance lifecycle. The same protocol
states that "Implementation reviews remain governed by
`docs/AI_REVIEW_PROTOCOL.md`".

Location under `docs/ARCHITECTURE_AUDITS/` is therefore not the governing
predicate. These tests pin both directions of the classification boundary: a
genuine readiness audit must still be validated, and an implementation review
must not be forced through the readiness template.
"""

from __future__ import annotations

from pathlib import Path

import hunter_artifact_preflight

IMPLEMENTATION_AUDIT = Path("docs/ARCHITECTURE_AUDITS/v3.5.0-supply-basis-value-capture.md")
HISTORICAL_IMPLEMENTATION_AUDITS = (
    "issue-166-valuation-family-blocker-remediation.md",
    "issue-190-evidence-assembly-authority-gap.md",
    "v3.5.0-final-main-audit.md",
    "v3.5.0-final-remediation-audit.md",
    "v3.5.0-post-merge-final-audit.md",
    "v3.5.0-remediation-status.md",
    "v3.5.0-supply-basis-value-capture.md",
    "v3.5.1-issue-95-post-merge-audit.md",
)


def _readiness_audit(*, governing_protocol: bool = True, final_verdict: bool = True) -> str:
    protocol_line = "- Governing protocol: `docs/ARCHITECTURE_AUDIT_PROTOCOL.md`\n" if governing_protocol else ""
    verdict_section = "## Final Verdict\n\n- `READY_FOR_ADR`\n\n" if final_verdict else ""
    return f"""# Independent Architecture Audit

## Metadata

- Reviewed artifact: `docs/example.md`
- Reviewed revision: `0123456789abcdef0123456789abcdef01234567`
- Audit type: `TARGETED`
- Auditor: Jules — independent architecture audit agent
- Audit date: 2026-08-21
{protocol_line}
## Audit Scope

Targeted independent architecture-readiness audit.

## Evidence Sources Examined

- Immutable repository evidence at the reviewed revision.

## Prior Review Finding Re-Verification

- No prior review findings are in scope for this fixture.

## Dimension Results

| Dimension | Result | Evidence and rationale | Finding IDs |
|---|---|---|---|
| Problem correctness | PASS | Evidence-backed. | F-001 |

## Findings

### F-001 — Non-blocking fixture finding

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

{verdict_section}## Required Corrections or Conditions

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


def test_governed_readiness_audit_is_classified_as_adpr_governed() -> None:
    assert hunter_artifact_preflight.is_architecture_readiness_audit(_readiness_audit())


def test_historical_implementation_audit_is_not_classified_as_adpr_governed() -> None:
    text = (hunter_artifact_preflight.ROOT / IMPLEMENTATION_AUDIT).read_text(encoding="utf-8")

    assert not hunter_artifact_preflight.is_architecture_readiness_audit(text)


def test_changed_implementation_audit_raises_no_adpr_heading_errors() -> None:
    errors = hunter_artifact_preflight.validate_changed_artifacts([IMPLEMENTATION_AUDIT])

    assert errors == []


def test_every_existing_architecture_audit_artifact_is_currently_mergeable() -> None:
    directory = hunter_artifact_preflight.ROOT / "docs" / "ARCHITECTURE_AUDITS"
    paths = sorted(path.relative_to(hunter_artifact_preflight.ROOT) for path in directory.glob("*.md"))

    assert paths, "expected governed architecture-audit artifacts to exist"
    assert hunter_artifact_preflight.validate_changed_artifacts(paths) == []


def test_malformed_readiness_audit_cannot_escape_by_deleting_the_protocol_declaration() -> None:
    """Deleting one convenient discriminator must not disable readiness validation.

    The classifier is a disjunction over independent canonical readiness signals,
    so removing the `Governing protocol` metadata line still leaves the canonical
    verdict declaration, the mandatory Findings Matrix, and canonical A-D finding
    records as governing evidence that the artifact claims readiness.
    """
    malformed = _readiness_audit(governing_protocol=False).replace(
        "- Reviewed revision: `0123456789abcdef0123456789abcdef01234567`\n", ""
    )

    assert hunter_artifact_preflight.is_architecture_readiness_audit(malformed)
    errors = hunter_artifact_preflight.validate_audit_text(malformed, accepted_adrs=[])
    assert any("Reviewed revision" in error for error in errors), errors


def test_malformed_readiness_audit_cannot_escape_by_deleting_the_final_verdict() -> None:
    malformed = _readiness_audit(final_verdict=False)

    assert hunter_artifact_preflight.is_architecture_readiness_audit(malformed)
    errors = hunter_artifact_preflight.validate_audit_text(malformed, accepted_adrs=[])
    assert any("Final Verdict" in error for error in errors), errors


def test_readiness_signals_inside_nonsemantic_regions_do_not_govern_an_implementation_review() -> None:
    implementation_review = """# Implementation Review

Severity: Critical

An example of a readiness audit is:

```markdown
## Final Verdict

- `READY_FOR_ADR`
```

<!--
- Governing protocol: `docs/ARCHITECTURE_AUDIT_PROTOCOL.md`
-->
"""

    assert not hunter_artifact_preflight.is_architecture_readiness_audit(implementation_review)


def test_nonreadiness_governed_artifacts_still_receive_baseline_integrity_validation() -> None:
    empty_artifact = "\n<!-- nothing rendered here -->\n"

    assert not hunter_artifact_preflight.is_architecture_readiness_audit(empty_artifact)
    assert hunter_artifact_preflight.validate_governed_artifact_text(empty_artifact, accepted_adrs=[])


def test_all_audits_diagnostic_uses_the_same_classification_rule() -> None:
    directory = hunter_artifact_preflight.ROOT / "docs" / "ARCHITECTURE_AUDITS"
    paths = sorted(path.relative_to(hunter_artifact_preflight.ROOT) for path in directory.glob("*.md"))

    assert hunter_artifact_preflight.run_artifact_preflight(paths) == 0


def test_negated_verdict_mention_does_not_classify_an_implementation_review() -> None:
    """`docs/AI_REVIEW_PROTOCOL.md` governs implementation review.

    A verdict token appearing in prose is not a readiness declaration, so it
    must not drag an implementation review into the ADPR readiness template.
    """
    review = """# Implementation Review

Severity: Critical

This review does not issue `READY_FOR_ADR`; readiness remains out of scope here.
"""

    assert not hunter_artifact_preflight.is_architecture_readiness_audit(review)


def test_incidental_or_historical_verdict_mentions_do_not_classify_an_artifact() -> None:
    mentions = (
        "# Notes\n\n`READY_FOR_ADR` is one possible protocol verdict.\n",
        "# Notes\n\nThe prior audit recorded ARCHITECTURE_NOT_READY before remediation.\n",
        "# Notes\n\nVerdicts include READY_FOR_ADR_WITH_MINOR_FINDINGS and CONDITIONAL_ADR_READY.\n",
    )
    for text in mentions:
        assert not hunter_artifact_preflight.is_architecture_readiness_audit(text), text


def test_canonical_final_verdict_declaration_is_still_a_readiness_signal() -> None:
    declaration = """# Independent Architecture Audit

## Final Verdict

- `READY_FOR_ADR`
"""

    assert hunter_artifact_preflight.is_architecture_readiness_audit(declaration)


def test_readiness_classification_reuses_the_validator_verdict_parser() -> None:
    """One canonical declared-verdict parser, not a second inconsistent one."""
    negated = "# Report\n\n## Final Verdict\n\nThis report does not issue READY_FOR_ADR.\n"

    assert hunter_artifact_preflight._selected_verdicts(negated) == []
    # Still governed: the rendered Final Verdict heading is an independent signal.
    assert hunter_artifact_preflight.is_architecture_readiness_audit(negated)


def test_readiness_audit_without_a_verdict_declaration_remains_governed() -> None:
    malformed = _readiness_audit(governing_protocol=False, final_verdict=False)

    assert hunter_artifact_preflight.is_architecture_readiness_audit(malformed)
    errors = hunter_artifact_preflight.validate_audit_text(malformed, accepted_adrs=[])
    assert any("Final Verdict" in error for error in errors), errors


def test_historical_implementation_audits_remain_outside_readiness_enforcement() -> None:
    directory = hunter_artifact_preflight.ROOT / "docs" / "ARCHITECTURE_AUDITS"
    paths = [directory / name for name in HISTORICAL_IMPLEMENTATION_AUDITS]

    assert all(path.is_file() for path in paths), "historical implementation-audit corpus changed"
    for path in paths:
        text = path.read_text(encoding="utf-8")
        assert not hunter_artifact_preflight.is_architecture_readiness_audit(text), path.name
