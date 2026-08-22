from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ARCHITECTURE_INDEX_PATH = ROOT / "docs" / "architecture-index.md"
ADPR_0006 = "ADPR-0006"
ADPR_0006_RUNTIME_PATHS = (
    ROOT / "src" / "hunter" / "evidence_intelligence" / "pre_model.py",
    ROOT / "src" / "hunter" / "evidence_intelligence" / "pre_model_persistence.py",
)

NEGATIVE_IMPLEMENTATION_RE = re.compile(
    r"(?i)\b(?:not\s+started|not\s+implemented|not\s+authorized|unimplemented)\b"
)
DECISION_HEADERS = (
    "adpr",
    "title",
    "status",
    "epic",
    "issue",
    "adr",
    "implementation pr",
    "merge commit",
    "release",
    "supersedes",
    "superseded by",
)
APPROVED_HEADERS = ("adpr", "adr", "status", "implementation", "validation")


def _markdown_cells(line: str) -> list[str]:
    stripped = line.strip()
    if not stripped.startswith("|"):
        return []
    return [cell.strip() for cell in stripped.strip("|").split("|")]


def _normalized_cell(value: str) -> str:
    return re.sub(r"[`*_]", "", value).strip().lower()


def _normalized_cells(line: str) -> tuple[str, ...]:
    return tuple(_normalized_cell(cell) for cell in _markdown_cells(line))


def _schema_present(text: str, headers: tuple[str, ...]) -> bool:
    return any(_normalized_cells(line) == headers for line in text.splitlines() if line.strip().startswith("|"))


def _adpr_rows_with_width(text: str, adpr: str, width: int) -> list[list[str]]:
    matched: list[list[str]] = []
    for line in text.splitlines():
        cells = _markdown_cells(line)
        if len(cells) != width:
            continue
        if re.search(rf"(?i)\b{re.escape(adpr)}\b", _normalized_cell(cells[0])):
            matched.append(cells)
    return matched


def _display_path(path: Path) -> str:
    try:
        return path.relative_to(ROOT).as_posix()
    except ValueError:
        return path.as_posix()


def validate_architecture_index(
    text: str,
    *,
    runtime_paths: tuple[Path, ...] = ADPR_0006_RUNTIME_PATHS,
) -> list[str]:
    """Validate the known lifecycle/runtime consistency invariant from ADPR-0009 case 14."""
    errors: list[str] = []

    if not _schema_present(text, DECISION_HEADERS):
        errors.append("Decision Registry must contain the canonical ADPR/Status table schema.")
        return errors
    if not _schema_present(text, APPROVED_HEADERS):
        errors.append(
            "Approved and Implemented Records must separate ADPR lifecycle Status from downstream Implementation."
        )
        return errors

    decision_matches = _adpr_rows_with_width(text, ADPR_0006, len(DECISION_HEADERS))
    approved_matches = _adpr_rows_with_width(text, ADPR_0006, len(APPROVED_HEADERS))
    if len(decision_matches) != 1:
        errors.append(f"Decision Registry must contain exactly one {ADPR_0006} row; found {len(decision_matches)}.")
        return errors
    if len(approved_matches) != 1:
        errors.append(
            f"Approved and Implemented Records must contain exactly one {ADPR_0006} row; found {len(approved_matches)}."
        )
        return errors

    decision_status = _normalized_cell(decision_matches[0][2]).upper()
    approved_status = _normalized_cell(approved_matches[0][2]).upper()
    if decision_status != "APPROVED":
        errors.append(f"{ADPR_0006} Decision Registry lifecycle status must remain APPROVED; found {decision_status!r}.")
    if approved_status != decision_status:
        errors.append(
            f"{ADPR_0006} lifecycle status disagrees between Decision Registry ({decision_status}) "
            f"and Approved and Implemented Records ({approved_status})."
        )

    missing_runtime = [_display_path(path) for path in runtime_paths if not path.is_file()]
    if missing_runtime:
        errors.append(
            f"{ADPR_0006} canonical provider-free pre-model runtime evidence is missing: " + ", ".join(missing_runtime) + "."
        )
        return errors

    implementation_cell = approved_matches[0][3]
    implementation = _normalized_cell(implementation_cell)
    if NEGATIVE_IMPLEMENTATION_RE.search(implementation):
        errors.append(f"{ADPR_0006} implementation state contradicts canonical runtime evidence: {implementation_cell}")
    if "provider-free" not in implementation or "pre-model" not in implementation:
        errors.append(f"{ADPR_0006} implementation state must identify the provider-free pre-model runtime.")
    if "implemented" not in implementation or "current source" not in implementation:
        errors.append(
            f"{ADPR_0006} implementation state must positively record that the runtime is implemented in current source."
        )

    return errors


def run_architecture_index_preflight() -> int:
    errors = validate_architecture_index(ARCHITECTURE_INDEX_PATH.read_text(encoding="utf-8"))
    if errors:
        for error in errors:
            print(f"[Hunter Architecture Index Preflight] FAIL: {error}", flush=True)
        return 1
    print("[Hunter Architecture Index Preflight] PASS: lifecycle/runtime status consistency", flush=True)
    return 0


def main() -> int:
    return run_architecture_index_preflight()


if __name__ == "__main__":
    raise SystemExit(main())
