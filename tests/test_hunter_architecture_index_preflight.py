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

    assert any("contradicts canonical runtime evidence" in error for error in errors), errors


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


def test_runtime_evidence_disappearance_fails_closed(tmp_path: Path) -> None:
    missing = (tmp_path / "pre_model.py", tmp_path / "pre_model_persistence.py")

    errors = hunter_architecture_index_preflight.validate_architecture_index(
        _fixture(),
        runtime_paths=missing,
    )

    assert any("runtime evidence is missing" in error for error in errors), errors


def test_case_14_defect_class_is_permanently_registered() -> None:
    registry = json.loads((ROOT / "docs" / "DEFECT_REGISTRY.json").read_text(encoding="utf-8"))
    matching = [item for item in registry["defects"] if item.get("id") == "ARCH-AUD-008"]

    assert len(matching) == 1
    assert matching[0]["status"] == "guarded"
    assert matching[0]["test"] == (
        "tests/test_hunter_architecture_index_preflight.py::" "test_known_not_started_contradiction_is_rejected"
    )
