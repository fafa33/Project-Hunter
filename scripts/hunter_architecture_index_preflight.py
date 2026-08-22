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

RUNTIME_ASSERTION_RE = re.compile(
    r"(?i)\b(?:ADR\s+0031\s+)?provider-free\s+pre-model\s+runtime\s+is\s+implemented\s+in\s+current\s+source\b"
)
RUNTIME_REVOCATION_RE = re.compile(
    r"(?i)\bprovider-free\s+pre-model\s+runtime\b.{0,240}\b(?:has\s+since\s+been\s+removed|"
    r"was\s+removed|is\s+removed|no\s+longer\s+implemented|is\s+not\s+implemented|unimplemented|disabled|absent)\b"
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


def _rendered_markdown(text: str) -> str:
    """Remove fenced-code and HTML-comment content before semantic parsing."""
    rendered: list[str] = []
    in_fence = False
    in_comment = False
    fence_marker = ""

    for raw_line in text.splitlines():
        stripped = raw_line.lstrip()
        if not in_comment and (stripped.startswith("```") or stripped.startswith("~~~")):
            marker = stripped[:3]
            if not in_fence:
                in_fence = True
                fence_marker = marker
            elif marker == fence_marker:
                in_fence = False
                fence_marker = ""
            continue
        if in_fence:
            continue

        line = raw_line
        visible_parts: list[str] = []
        cursor = 0
        while cursor < len(line):
            if in_comment:
                end = line.find("-->", cursor)
                if end == -1:
                    cursor = len(line)
                    break
                in_comment = False
                cursor = end + 3
                continue

            start = line.find("<!--", cursor)
            if start == -1:
                visible_parts.append(line[cursor:])
                break
            visible_parts.append(line[cursor:start])
            end = line.find("-->", start + 4)
            if end == -1:
                in_comment = True
                cursor = len(line)
                break
            cursor = end + 3

        rendered.append("".join(visible_parts))

    return "\n".join(rendered)


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
    return "\n".join(body)


def _canonical_table(section: str, headers: tuple[str, ...]) -> list[list[str]] | None:
    lines = section.splitlines()
    for index, line in enumerate(lines):
        if _normalized_cells(line) != headers:
            continue
        if index + 1 >= len(lines):
            return None
        delimiter = _markdown_cells(lines[index + 1])
        if len(delimiter) < len(headers) or not all(re.fullmatch(r":?-{3,}:?", cell) for cell in delimiter):
            return None
        rows: list[list[str]] = []
        for candidate in lines[index + 2 :]:
            if not candidate.strip().startswith("|"):
                break
            cells = _markdown_cells(candidate)
            if len(cells) == len(headers):
                rows.append(cells)
        return rows
    return None


def _adpr_rows(rows: list[list[str]], adpr: str) -> list[list[str]]:
    return [row for row in rows if re.search(rf"(?i)\b{re.escape(adpr)}\b", _normalized_cell(row[0]))]


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
    semantic_text = _rendered_markdown(text)

    decision_table = _canonical_table(_section(semantic_text, "## Decision Registry"), DECISION_HEADERS)
    if decision_table is None:
        errors.append("Decision Registry must contain the rendered canonical ADPR/Status table schema.")
        return errors
    approved_table = _canonical_table(_section(semantic_text, "## Approved and Implemented Records"), APPROVED_HEADERS)
    if approved_table is None:
        errors.append(
            "Approved and Implemented Records must contain the rendered canonical table with separate ADPR lifecycle Status and downstream Implementation."
        )
        return errors

    decision_matches = _adpr_rows(decision_table, ADPR_0006)
    approved_matches = _adpr_rows(approved_table, ADPR_0006)
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
        errors.append(
            f"{ADPR_0006} Decision Registry lifecycle status must remain APPROVED; found {decision_status!r}."
        )
    if approved_status != decision_status:
        errors.append(
            f"{ADPR_0006} lifecycle status disagrees between Decision Registry ({decision_status}) "
            f"and Approved and Implemented Records ({approved_status})."
        )

    missing_runtime = [_display_path(path) for path in runtime_paths if not path.is_file()]
    if missing_runtime:
        errors.append(
            f"{ADPR_0006} canonical provider-free pre-model runtime evidence is missing: "
            + ", ".join(missing_runtime)
            + "."
        )
        return errors

    implementation_cell = approved_matches[0][3]
    if not RUNTIME_ASSERTION_RE.search(implementation_cell):
        errors.append(
            f"{ADPR_0006} implementation state must explicitly assert that the provider-free pre-model runtime is implemented in current source."
        )
    if RUNTIME_REVOCATION_RE.search(implementation_cell):
        errors.append(
            f"{ADPR_0006} implementation state revokes or contradicts the current provider-free pre-model runtime: {implementation_cell}"
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
