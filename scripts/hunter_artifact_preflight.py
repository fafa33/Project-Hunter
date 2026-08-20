from __future__ import annotations

import argparse
import json
import re
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
REGISTRY_PATH = ROOT / "docs" / "DEFECT_REGISTRY.json"
ADR_INDEX_PATH = ROOT / "docs" / "ADR" / "README.md"
AUDIT_PREFIX = "docs/ARCHITECTURE_AUDITS/"

REQUIRED_REGISTRY_FIELDS = {
    "id",
    "class",
    "source",
    "classification",
    "guard_boundary",
    "guard",
    "test",
    "status",
}
REQUIRED_DEFECT_IDS = {
    "PRH-001",
    "PRH-002",
    "PRH-003",
    "PRH-004",
    "PRH-005",
    "PRH-006",
    "PRH-007",
    "PRH-008",
    "PRH-009",
    "PRH-010",
    "PRH-011",
    "ARCH-AUD-001",
    "ARCH-AUD-002",
    "ARCH-AUD-003",
    "ARCH-AUD-004",
    "ARCH-AUD-005",
    "ARCH-AUD-006",
}
REQUIRED_AUDIT_HEADINGS = (
    "## Metadata",
    "## Audit Scope",
    "## Evidence Sources Examined",
    "## Dimension Results",
    "## Findings",
    "## Findings Matrix",
    "## Verdict Derivation",
    "## Final Verdict",
    "## Required Corrections or Conditions",
    "## Non-Blocking Follow-Up",
    "## Audit Completion Check",
)
ALLOWED_VERDICTS = (
    "READY_FOR_ADR",
    "READY_FOR_ADR_WITH_MINOR_FINDINGS",
    "CONDITIONAL_ADR_READY",
    "ADPR_REVISION_REQUIRED",
    "ARCHITECTURE_NOT_READY",
)
PENDING_PLACEHOLDER_RE = re.compile(r"(?im)(?:`PENDING[^`]*`|\|\s*PENDING\s*\||:\s*PENDING\b|\bPENDING\s*[—-])")
AUDITOR_RE = re.compile(r"(?im)^-\s*Auditor:\s*(.+?)\s*$")
REVISION_RE = re.compile(r"(?im)^-\s*Reviewed revision:\s*`?([0-9a-f]{40})`?\s*$")
AUDIT_TYPE_RE = re.compile(r"(?im)^-\s*Audit type:\s*`?(FULL|TARGETED)`?\s*$")
CUTOFF_RE = re.compile(r"(?im)^-\s*Evidence cutoff:\s*`?([^`\n]+)`?\s*$")
MUTABLE_EVIDENCE_RE = re.compile(r"(?i)(?:https?://|\bPR\s*#\d+\b|\bIssue\s*#\d+\b)")
PRIOR_REVIEW_SCOPE_RE = re.compile(
    r"(?i)\b(?:prior\s+(?:review|finding(?:s)?)|previous\s+finding(?:s)?|previously\s+(?:blocking\s+)?finding(?:s)?)\b"
)
NO_PRIOR_FINDINGS_RE = re.compile(r"(?i)\bno\s+prior\s+(?:review\s+)?findings?\b")
PRIOR_FINDING_REFERENCE_RE = re.compile(r"(?i)\b(?:PR\d+-F\d+|F-\d{3})\b")
FINDING_CLASS_FIELD_RE = re.compile(r"(?im)^-\s*\*\*Class:\*\*")
ADR_DISPOSITION_RE = re.compile(
    r"(?i)\b(?:reviewed|verified|applicable|in\s+scope|out\s+of\s+scope|not\s+applicable|no\s+conflict|conflict)\b"
)
FENCE_OPEN_RE = re.compile(r"^[ \t]{0,3}(?P<fence>`{3,}|~{3,})(?:[^\r\n]*)$")
FENCE_CLOSE_RE = re.compile(r"^[ \t]{0,3}(?P<fence>`{3,}|~{3,})[ \t]*$")
HTML_COMMENT_RE = re.compile(r"<!--.*?(?:-->|\Z)", re.DOTALL)
RAW_HTML_LITERAL_OPEN_RE = re.compile(
    r"^[ ]{0,3}<(?P<tag>pre|script|style|textarea)(?=[\s>/])",
    re.IGNORECASE,
)
FINDING_HEADING_RE = re.compile(r"(?im)^#{3,6}[ \t]+(?P<id>F-\d{3})\b[^\n]*$")
FINDING_FIELD_RE = re.compile(
    r"(?im)^-\s*\*\*(?P<label>Evidence|Location|Category|Severity|Decision impact|Consequence if ignored|Required action|Blocks ADR):\*\*\s*(?P<value>[^\n]*)$"
)
REQUIRED_FINDING_FIELDS = {
    "evidence",
    "location",
    "category",
    "severity",
    "decision impact",
    "consequence if ignored",
    "required action",
    "blocks adr",
}
SEVERITY_ORDER = {"A": 1, "B": 2, "C": 3, "D": 4}


def _run_git(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ("git", *args),
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )


def changed_paths() -> list[Path]:
    """Return changed paths against the proven branch merge-base with origin/main."""
    merge_base = _run_git("merge-base", "HEAD", "origin/main")
    if merge_base.returncode != 0 or not merge_base.stdout.strip():
        raise RuntimeError(
            "Unable to prove merge-base with origin/main. Fetch complete repository history "
            "before running artifact preflight."
        )

    base = merge_base.stdout.strip()
    diff = _run_git("diff", "--name-only", "--diff-filter=ACMR", f"{base}...HEAD")
    if diff.returncode != 0:
        raise RuntimeError("Unable to determine changed files from the proven branch merge-base.")
    return [Path(line.strip()) for line in diff.stdout.splitlines() if line.strip()]


def load_registry(path: Path = REGISTRY_PATH) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def validate_registry(data: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if data.get("version") != 1:
        errors.append("DEFECT_REGISTRY version must be 1.")

    defects = data.get("defects")
    if not isinstance(defects, list):
        return errors + ["DEFECT_REGISTRY defects must be a list."]

    seen: set[str] = set()
    for index, item in enumerate(defects):
        if not isinstance(item, dict):
            errors.append(f"Registry item {index} must be an object.")
            continue
        missing = sorted(REQUIRED_REGISTRY_FIELDS - set(item))
        if missing:
            errors.append(f"Registry item {index} missing fields: {', '.join(missing)}.")
            continue

        defect_id = item["id"]
        if not isinstance(defect_id, str) or not re.fullmatch(r"[A-Z]+(?:-[A-Z]+)*-\d{3}", defect_id):
            errors.append(f"Registry item {index} has invalid id {defect_id!r}.")
        elif defect_id in seen:
            errors.append(f"Duplicate defect id: {defect_id}.")
        else:
            seen.add(defect_id)

        if item["classification"] not in {"systemic", "isolated"}:
            errors.append(f"{defect_id}: classification must be systemic or isolated.")
        if item["status"] not in {"guarded", "open", "retired"}:
            errors.append(f"{defect_id}: unsupported status {item['status']!r}.")
        if item["status"] == "guarded":
            for field in ("guard_boundary", "guard", "test"):
                value = item.get(field)
                if not isinstance(value, str) or not value.strip():
                    errors.append(f"{defect_id}: guarded entry requires non-empty {field}.")

    missing_ids = sorted(REQUIRED_DEFECT_IDS - seen)
    if missing_ids:
        errors.append("Registry dropped required understood defect classes: " + ", ".join(missing_ids) + ".")
    return errors


def accepted_adr_ids(index_text: str) -> list[str]:
    ids: list[str] = []
    for line in index_text.splitlines():
        if not line.lstrip().startswith("|"):
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if len(cells) < 4:
            continue
        match = re.fullmatch(r"\[(\d{4})\]\([^)]+\)", cells[0])
        if match and cells[2].startswith("Accepted"):
            ids.append(match.group(1))
    return sorted(set(ids))


def _mask_span_preserving_newlines(value: str) -> str:
    return "".join(char if char in "\r\n" else " " for char in value)


def _mask_markdown_nonsemantic(text: str) -> str:
    """Mask Markdown regions that do not render as semantic audit structure."""
    without_comments = HTML_COMMENT_RE.sub(
        lambda match: _mask_span_preserving_newlines(match.group(0)),
        text,
    )
    masked: list[str] = []
    fence_char: str | None = None
    fence_length = 0
    raw_html_tag: str | None = None

    for line in without_comments.splitlines(keepends=True):
        body = line.rstrip("\r\n")
        ending = line[len(body) :]

        if raw_html_tag is not None:
            if re.search(rf"</{re.escape(raw_html_tag)}\s*>", body, re.IGNORECASE):
                raw_html_tag = None
            masked.append(" " * len(body) + ending)
            continue

        raw_open = RAW_HTML_LITERAL_OPEN_RE.match(body)
        if raw_open:
            tag = raw_open.group("tag").lower()
            if not re.search(rf"</{re.escape(tag)}\s*>", body, re.IGNORECASE):
                raw_html_tag = tag
            masked.append(" " * len(body) + ending)
            continue

        if fence_char is not None:
            closing = FENCE_CLOSE_RE.fullmatch(body)
            if closing:
                fence = closing.group("fence")
                if fence[0] == fence_char and len(fence) >= fence_length:
                    fence_char = None
                    fence_length = 0
            masked.append(" " * len(body) + ending)
            continue

        opening = FENCE_OPEN_RE.fullmatch(body)
        if opening:
            fence = opening.group("fence")
            fence_char = fence[0]
            fence_length = len(fence)
            masked.append(" " * len(body) + ending)
            continue

        if body.startswith("\t") or re.match(r"^ {4,}\S", body):
            masked.append(" " * len(body) + ending)
            continue

        masked.append(line)

    return "".join(masked)


def _heading_match(text: str, heading: str) -> re.Match[str] | None:
    semantic_text = _mask_markdown_nonsemantic(text)
    return re.search(rf"(?m)^{re.escape(heading)}[ \t]*$", semantic_text)


def _section(text: str, heading: str) -> str:
    semantic_text = _mask_markdown_nonsemantic(text)
    match = re.search(rf"(?m)^{re.escape(heading)}[ \t]*$", semantic_text)
    if not match:
        return ""
    remainder = semantic_text[match.end() :]
    next_heading = re.search(r"(?m)^##[ \t]+[^\n]+[ \t]*$", remainder)
    if next_heading:
        remainder = remainder[: next_heading.start()]
    return remainder.strip()


def _direct_section_lines(text: str, heading: str) -> list[str]:
    section = _section(text, heading)
    direct = re.split(r"(?m)^#{3,6}[ \t]+[^\n]+[ \t]*$", section, maxsplit=1)[0]
    return [line.strip() for line in direct.splitlines() if line.strip()]


def _normalize_markdown_scalar(value: str) -> str:
    normalized = value.strip()
    if normalized.startswith(("- ", "* ")):
        normalized = normalized[2:].strip()
    if len(normalized) >= 2 and normalized.startswith("`") and normalized.endswith("`"):
        normalized = normalized[1:-1].strip()
    return normalized


def _markdown_cells(line: str) -> list[str]:
    stripped = line.strip()
    if not stripped.startswith("|"):
        return []
    return [cell.strip() for cell in stripped.strip("|").split("|")]


def _normalized_cell(value: str) -> str:
    return re.sub(r"[`*_]", "", value).strip().lower()


def _structured_adr_accounting_ids(text: str) -> set[str]:
    semantic_text = _mask_markdown_nonsemantic(text)
    accounted: set[str] = set()
    for line in semantic_text.splitlines():
        stripped = line.strip()
        if stripped.startswith("|"):
            cells = _markdown_cells(stripped)
            if not cells:
                continue
            first_cell = _normalized_cell(cells[0])
            match = re.match(r"(?i)^ADR[ -]?(\d{4})\b", first_cell)
            remainder = " ".join(cells[1:])
        else:
            match = re.match(r"(?i)^[-*]\s+(?:[`*_]+)?ADR[ -]?(\d{4})\b", stripped)
            remainder = stripped[match.end() :] if match else ""
        if match and ADR_DISPOSITION_RE.search(remainder):
            accounted.add(match.group(1))
    return accounted


def _finding_matrix_rows(matrix: str) -> tuple[list[tuple[str, str, str]], list[str]]:
    semantic_matrix = _mask_markdown_nonsemantic(matrix)
    rows = [_markdown_cells(line) for line in semantic_matrix.splitlines() if line.strip().startswith("|")]
    for index, row in enumerate(rows):
        headers = [_normalized_cell(cell) for cell in row]
        if not {"finding", "class", "blocks adr"}.issubset(headers):
            continue
        finding_index = headers.index("finding")
        class_index = headers.index("class")
        blocks_index = headers.index("blocks adr")
        parsed: list[tuple[str, str, str]] = []
        errors: list[str] = []

        for candidate in rows[index + 1 :]:
            populated = [cell.strip() for cell in candidate if cell.strip()]
            if populated and all(re.fullmatch(r":?-{3,}:?", cell) for cell in populated):
                continue
            if max(finding_index, class_index, blocks_index) >= len(candidate):
                continue

            finding_id = _normalize_markdown_scalar(candidate[finding_index])
            severity = _normalize_markdown_scalar(candidate[class_index]).upper()
            blocks_adr = _normalize_markdown_scalar(candidate[blocks_index]).upper()
            if not finding_id:
                continue
            if not re.fullmatch(r"[A-D]", severity):
                errors.append(f"Findings Matrix row {finding_id!r} must use Class A, B, C, or D.")
                continue
            if blocks_adr not in {"YES", "NO"}:
                errors.append(f"Findings Matrix row {finding_id!r} must set Blocks ADR to YES or NO.")
                continue
            parsed.append((finding_id, severity, blocks_adr))

        return parsed, errors

    return [], ["Findings Matrix must identify Finding, Class, and Blocks ADR columns."]


def _finding_records(findings: str) -> dict[str, dict[str, str]]:
    semantic_findings = _mask_markdown_nonsemantic(findings)
    headings = list(FINDING_HEADING_RE.finditer(semantic_findings))
    records: dict[str, dict[str, str]] = {}
    for index, heading in enumerate(headings):
        finding_id = heading.group("id").upper()
        end = headings[index + 1].start() if index + 1 < len(headings) else len(semantic_findings)
        block = semantic_findings[heading.end() : end]
        fields: dict[str, str] = {}
        for field in FINDING_FIELD_RE.finditer(block):
            fields[field.group("label").lower()] = _normalize_markdown_scalar(field.group("value"))
        records[finding_id] = fields
    return records


def _validate_blocking_finding_records(findings: str, matrix: str) -> list[str]:
    rows, matrix_errors = _finding_matrix_rows(matrix)
    if matrix_errors:
        return []
    records = _finding_records(findings)
    errors: list[str] = []

    for finding_id, severity, blocks_adr in rows:
        if blocks_adr != "YES":
            continue
        record = records.get(finding_id.upper())
        if record is None:
            errors.append(f"Blocking finding {finding_id} must have a complete finding record in ## Findings.")
            continue

        missing = sorted(field for field in REQUIRED_FINDING_FIELDS if not record.get(field, "").strip())
        if missing:
            errors.append(f"Blocking finding {finding_id} record is incomplete; missing: {', '.join(missing)}.")
            continue

        record_severity = record["severity"].upper()
        record_blocks = record["blocks adr"].upper()
        if record_severity != severity:
            errors.append(
                f"Blocking finding {finding_id} severity disagrees between Findings ({record_severity}) "
                f"and Findings Matrix ({severity})."
            )
        if record_blocks != blocks_adr:
            errors.append(
                f"Blocking finding {finding_id} Blocks ADR disagrees between Findings ({record_blocks}) "
                f"and Findings Matrix ({blocks_adr})."
            )

    return errors


def _validate_iso8601(value: str) -> bool:
    candidate = value.strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError:
        return False
    return parsed.tzinfo is not None


def _selected_verdicts(text: str) -> list[str]:
    lines = _direct_section_lines(text, "## Final Verdict")
    if len(lines) != 1:
        return []
    declared = _normalize_markdown_scalar(lines[0])
    return [declared] if declared in ALLOWED_VERDICTS else []


def _validate_verdict_consistency(selected_verdict: str | None, matrix: str) -> list[str]:
    rows, errors = _finding_matrix_rows(matrix)
    if errors:
        return errors
    if not rows or selected_verdict is None:
        return []

    for finding_id, severity, blocks_adr in rows:
        if severity in {"C", "D"} and blocks_adr != "YES":
            errors.append(f"Class {severity} finding {finding_id} must set Blocks ADR = YES.")
        if severity in {"A", "B"} and blocks_adr == "YES":
            errors.append(f"Class {severity} finding {finding_id} may not set Blocks ADR = YES.")

    highest = max((severity for _, severity, _ in rows), key=SEVERITY_ORDER.__getitem__)
    if highest == "D" and selected_verdict != "ARCHITECTURE_NOT_READY":
        errors.append("Unresolved Class D finding requires ARCHITECTURE_NOT_READY.")
    elif highest == "C" and selected_verdict != "ADPR_REVISION_REQUIRED":
        errors.append("Unresolved Class C finding requires ADPR_REVISION_REQUIRED.")
    elif highest == "B" and selected_verdict not in {
        "READY_FOR_ADR_WITH_MINOR_FINDINGS",
        "CONDITIONAL_ADR_READY",
    }:
        errors.append(
            "Unresolved Class B finding requires READY_FOR_ADR_WITH_MINOR_FINDINGS " "or CONDITIONAL_ADR_READY."
        )
    elif highest == "A" and selected_verdict not in {
        "READY_FOR_ADR",
        "READY_FOR_ADR_WITH_MINOR_FINDINGS",
    }:
        errors.append("Class A-only audit requires READY_FOR_ADR or READY_FOR_ADR_WITH_MINOR_FINDINGS.")

    if selected_verdict == "ARCHITECTURE_NOT_READY" and highest != "D":
        errors.append("ARCHITECTURE_NOT_READY requires at least one unresolved Class D finding.")
    if selected_verdict == "ADPR_REVISION_REQUIRED" and highest != "C":
        errors.append(
            "ADPR_REVISION_REQUIRED requires Class C as the highest unresolved severity and no Class D finding."
        )
    if selected_verdict in {
        "READY_FOR_ADR_WITH_MINOR_FINDINGS",
        "CONDITIONAL_ADR_READY",
    } and highest not in {"A", "B"}:
        errors.append(f"{selected_verdict} is valid only when the highest unresolved severity is A or B.")

    return errors


def validate_audit_text(text: str, *, accepted_adrs: list[str]) -> list[str]:
    """Validate canonical audit requirements without inventing prose-only ceremony."""
    errors: list[str] = []
    semantic_text = _mask_markdown_nonsemantic(text)

    for heading in REQUIRED_AUDIT_HEADINGS:
        if not _heading_match(semantic_text, heading):
            errors.append(f"Missing mandatory audit heading: {heading}.")

    if PENDING_PLACEHOLDER_RE.search(semantic_text):
        errors.append("Audit contains unresolved PENDING placeholder content.")

    auditor_match = AUDITOR_RE.search(semantic_text)
    if not auditor_match or not auditor_match.group(1).strip():
        errors.append("Audit must record an independent auditor identity.")
    elif "pending" in auditor_match.group(1).lower():
        errors.append("Audit auditor identity may not be pending.")

    if not REVISION_RE.search(semantic_text):
        errors.append("Audit must pin Reviewed revision to an immutable 40-hex commit.")

    audit_type_match = AUDIT_TYPE_RE.search(semantic_text)
    if not audit_type_match:
        errors.append("Audit type must be FULL or TARGETED.")
        audit_type = None
    else:
        audit_type = audit_type_match.group(1)

    evidence = _section(semantic_text, "## Evidence Sources Examined")
    cutoff_match = CUTOFF_RE.search(semantic_text)
    if MUTABLE_EVIDENCE_RE.search(evidence):
        if not cutoff_match:
            errors.append("Audit using mutable PR/Issue/URL evidence must record an Evidence cutoff.")
        elif not _validate_iso8601(cutoff_match.group(1)):
            errors.append("Evidence cutoff must be an offset-aware ISO-8601 timestamp.")
    elif cutoff_match and not _validate_iso8601(cutoff_match.group(1)):
        errors.append("Evidence cutoff must be an offset-aware ISO-8601 timestamp when present.")

    if audit_type == "FULL":
        accounted_adrs = _structured_adr_accounting_ids(semantic_text)
        for adr in accepted_adrs:
            if adr not in accounted_adrs:
                errors.append(f"Accepted ADR {adr} is not structurally accounted for in FULL audit.")

    scope = _section(semantic_text, "## Audit Scope")
    if PRIOR_REVIEW_SCOPE_RE.search(scope):
        prior = _section(semantic_text, "## Prior Review Finding Re-Verification")
        if not prior:
            errors.append("Audit scope declares prior review history but lacks Prior Review Finding Re-Verification.")
        elif not NO_PRIOR_FINDINGS_RE.search(prior) and not (
            PRIOR_FINDING_REFERENCE_RE.search(prior)
            and re.search(r"(?i)\bevidence\b", prior)
            and re.search(r"(?i)\bconsequence\b", prior)
        ):
            errors.append(
                "Prior Review Finding Re-Verification must identify prior finding(s) with evidence and "
                "decision consequence, or explicitly state that none exist."
            )

    if FINDING_CLASS_FIELD_RE.search(semantic_text):
        errors.append("Finding records must use canonical `Severity`; `Class` is reserved for the Findings Matrix.")

    for match in re.finditer(r"(?im)^-\s*\*\*Severity:\*\*\s*`?([^`\n]+)`?\s*$", semantic_text):
        if not re.fullmatch(r"[A-D]", match.group(1).strip()):
            errors.append("Finding Severity must be exactly A, B, C, or D.")

    verdicts = _selected_verdicts(semantic_text)
    if len(verdicts) != 1:
        errors.append("Final Verdict must contain exactly one canonical declared audit verdict line.")
        selected_verdict = None
    else:
        selected_verdict = verdicts[0]

    corrections = _section(semantic_text, "## Required Corrections or Conditions")
    if selected_verdict == "CONDITIONAL_ADR_READY" and (
        not corrections or corrections.strip().lower() in {"none", "n/a", "not applicable"}
    ):
        errors.append("CONDITIONAL_ADR_READY requires explicit mandatory conditions.")

    findings = _section(semantic_text, "## Findings")
    matrix = _section(semantic_text, "## Findings Matrix")
    errors.extend(_validate_verdict_consistency(selected_verdict, matrix))
    errors.extend(_validate_blocking_finding_records(findings, matrix))

    return errors


def validate_changed_artifacts(paths: list[Path]) -> list[str]:
    errors: list[str] = []
    accepted = accepted_adr_ids(ADR_INDEX_PATH.read_text(encoding="utf-8"))
    for relative in paths:
        posix = relative.as_posix()
        if not posix.startswith(AUDIT_PREFIX) or relative.suffix.lower() != ".md":
            continue
        full_path = ROOT / relative
        if not full_path.exists():
            continue
        for error in validate_audit_text(
            full_path.read_text(encoding="utf-8"),
            accepted_adrs=accepted,
        ):
            errors.append(f"{posix}: {error}")
    return errors


def run_artifact_preflight(paths: list[Path] | None = None) -> int:
    errors = validate_registry(load_registry())
    if paths is None:
        try:
            paths = changed_paths()
        except RuntimeError as exc:
            print(f"[Hunter Artifact Preflight] FAIL: {exc}", flush=True)
            return 2
    errors.extend(validate_changed_artifacts(paths))

    if errors:
        for error in errors:
            print(f"[Hunter Artifact Preflight] FAIL: {error}", flush=True)
        return 1

    print(
        "[Hunter Artifact Preflight] PASS: defect registry and changed governed artifacts",
        flush=True,
    )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description=("Validate the defect registry and changed governed artifacts deterministically.")
    )
    parser.add_argument(
        "--all-audits",
        action="store_true",
        help="Validate all architecture-audit markdown files instead of only changed files.",
    )
    args = parser.parse_args()
    if args.all_audits:
        paths = sorted((ROOT / "docs" / "ARCHITECTURE_AUDITS").glob("*.md"))
        return run_artifact_preflight([path.relative_to(ROOT) for path in paths])
    return run_artifact_preflight()


if __name__ == "__main__":
    raise SystemExit(main())
