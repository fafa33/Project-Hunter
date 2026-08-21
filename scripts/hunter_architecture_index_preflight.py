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

TABLE_DELIMITER_RE = re.compile(r"^:?-{3,}:?$")
NEGATIVE_IMPLEMENTATION_RE = re.compile(
    r"(?i)\b(?:not\s+started|not\s+implemented|not\s+authorized|unimplemented)\b"
)


def _markdown_cells(line: str) -> list[str]:
    stripped = line.strip()
    if not stripped.startswith("|"):
        return []
    return [cell.strip() for cell in stripped.strip("|").split("|")]


def _normalized_cell(value: str) -> str:
    return re.sub(r"[`*_]", "", value).strip().lower()


def _section(text: str, heading: str) -> str:
    lines = text.splitlines()
    try:
        start = next(index for index, line in enumerate(lines) if line.strip() == heading)
    except StopIteration:
        return ""

    body: list[str] = []
    for line in lines[start + 1 :]:
        if line.startswith("## "):
            break
        body.append(line)
    return "\n".join(body).strip()


def _table_rows(section: str, required_headers: set[str]) -> tuple[list[str], list[list[str]]]:
    lines = section.splitlines()
    for index, line in enumerate(lines):
        if not line.strip().startswith("|"):
            continue
        headers = [_normalized_cell(cell) for cell in _markdown_cells(line)]
        if not required_headers.issubset(headers):
            continue
        if index + 1 >= len(lines):
            return [], []
        delimiter = _markdown_cells(lines[index + 1])
        if len(delimiter) != len(headers) or not all(TABLE_DELIMITER_RE.fullmatch(cell) for cell in delimiter):
            return [], []

        rows: list[list[str]] = []
        for candidate in lines[index + 2 :]:
            if not candidate.strip().startswith("|"):
                break
            cells = _markdown_cells(candidate)
            if len(cells) == len(headers):
                rows.append(cells)
        return headers, rows
    return [], []


def _adpr_rows(headers: list[str], rows: list[list[str]], adpr: str) -> list[dict[str, str]]:
    matched: list[dict[str, str]] = []
    for row in rows:
        record = dict(zip(headers, row, strict=True))
        if re.search(rf"(?i)\b{re.escape(adpr)}\b", _normalized_cell(record.get("adpr", ""))):
            matched.append(record)
    return matched


def validate_architecture_index(
    text: str,
    *,
    runtime_paths: tuple[Path, ...] = ADPR_0006_RUNTIME_PATHS,
) -> list[str]:
    """Validate the known lifecycle/runtime consistency invariant from ADPR-0009 case 14."""
    errors: list[str] = []

    decision_headers, decision_rows = _table_rows(
        _section(text, "## Decision Registry"),
        {"adpr", "status"},
    )
    if not decision_headers:
        errors.append("Decision Registry must contain a rendered ADPR/Status table.")
        return errors

    approved_headers, approved_rows = _table_rows(
        _section(text, "## Approved and Implemented Records"),
        {"adpr", "status", "implementation"},
    )
    if not approved_headers:
        errors.append(
            "Approved and Implemented Records must separate ADPR lifecycle Status from downstream Implementation."
        )
        return errors

    decision_matches = _adpr_rows(decision_headers, decision_rows, ADPR_0006)
    approved_matches = _adpr_rows(approved_headers, approved_rows, ADPR_0006)
    if len(decision_matches) != 1:
        errors.append(f"Decision Registry must contain exactly one {ADPR_0006} row; found {len(decision_matches)}.")
        return errors
    if len(approved_matches) != 1:
        errors.append(
            f"Approved and Implemented Records must contain exactly one {ADPR_0006} row; found {len(approved_matches)}."
        )
        return errors

    decision_status = _normalized_cell(decision_matches[0]["status"]).upper()
    approved_status = _normalized_cell(approved_matches[0]["status"]).upper()
    if decision_status != "APPROVED":
        errors.append(f"{ADPR_0006} Decision Registry lifecycle status must remain APPROVED; found {decision_status!r}.")
    if approved_status != decision_status:
        errors.append(
            f"{ADPR_0006} lifecycle status disagrees between Decision Registry ({decision_status}) "
            f"and Approved and Implemented Records ({approved_status})."
        )

    missing_runtime = [path.relative_to(ROOT).as_posix() for path in runtime_paths if not path.is_file()]
    if missing_runtime:
        errors.append(
            f"{ADPR_0006} canonical provider-free pre-model runtime evidence is missing: " + ", ".join(missing_runtime) + "."
        )
        return errors

    implementation = _normalized_cell(approved_matches[0]["implementation"])
    if NEGATIVE_IMPLEMENTATION_RE.search(implementation):
        errors.append(
            f"{ADPR_0006} implementation state contradicts canonical runtime evidence: "
            f"{approved_matches[0]['implementation']}"
        )
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
